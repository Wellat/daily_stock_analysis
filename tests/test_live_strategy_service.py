import json
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import desc, select

from src.services.live_strategy_service import LiveStrategyService
from src.services.qmt_position_service import QmtPositionService
from src.storage import DatabaseManager, LiveRebalanceBatch, LiveStrategyConfig, LiveStrategyRun
from src.storage import StrategyLabCbBasic, StrategyLabCbDailyFactor, StrategyDecisionRecord, TradingOrder


def _seed_cb_universe(db: DatabaseManager, trade_date: date, bonds: list[dict]) -> None:
    """写入策略上下文依赖的 cb_basic + 当日因子行（含可选强赎告警/最后交易日）。"""
    with db.get_session() as session:
        for b in bonds:
            session.add(StrategyLabCbBasic(bond_code=b["code"], bond_name=b["name"],
                                           stock_code=f"SH{b['code']}", market="cn", status="active",
                                           terms_json=json.dumps({"last_trading_date": b["ltd"].isoformat()})
                                           if b.get("ltd") else None))
        session.commit()
        for b in bonds:
            session.add(StrategyLabCbDailyFactor(bond_code=b["code"], trade_date=trade_date,
                                                 close=b["close"], premium_rate=b["premium"],
                                                 remaining_size=5.0, redeem_alert=bool(b.get("alert"))))
        session.commit()


def _weekdays_only(monkeypatch: pytest.MonkeyPatch, holidays: set[date] | None = None) -> None:
    """固定交易日历：周一至周五开市，可指定额外休市日（模拟节假日）。"""
    closed = holidays or set()
    monkeypatch.setattr(
        "src.core.trading_calendar.is_market_open",
        lambda market, d: d.weekday() < 5 and d not in closed,
    )


def _seed_completed_rebalance(db: DatabaseManager, trade_date: date) -> None:
    with db.get_session() as session:
        config = session.execute(select(LiveStrategyConfig).order_by(desc(LiveStrategyConfig.id))).scalars().first()
        session.add(LiveStrategyRun(run_uid=uuid4().hex, config_id=config.id, qmt_account=config.qmt_account,
                                    trade_date=trade_date, status="completed", mode="rebalance"))
        session.commit()


def _latest_run(db: DatabaseManager) -> LiveStrategyRun:
    with db.get_session() as session:
        return session.execute(select(LiveStrategyRun).order_by(desc(LiveStrategyRun.id))).scalars().first()


def test_live_strategy_preview_uses_qmt_positions_and_is_idempotent():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False, "parameters": {"max_positions": 1}})
        QmtPositionService(db).report_positions(account="testS", positions=[])
        preview = service.run(trade_date=date(2024, 1, 2), preview=True)
        assert "rebalance" in preview
        result = service.run(trade_date=date(2024, 1, 2))
        again = service.run(trade_date=date(2024, 1, 2))
        assert result["run_uid"] == again["run_uid"]
    finally:
        DatabaseManager.reset_instance()


def test_live_strategy_ignores_non_convertible_bond_positions():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "parameters": {"max_positions": 1}})
        with db.get_session() as session:
            session.add(StrategyLabCbBasic(bond_code="113002", bond_name="测试转债", stock_code="600000", market="cn", status="active"))
            session.commit()
        QmtPositionService(db).report_positions(account="testS", positions=[
            {"symbol": "600000", "volume": 100, "can_use_volume": 100},
        ])
        preview = service.run(trade_date=date(2024, 1, 2), preview=True)
        assert all(item["symbol"] != "600000" for item in preview["rebalance"])
    finally:
        DatabaseManager.reset_instance()


def test_live_strategy_gate_aligns_with_intraday_sync_runs():
    """盘中同步落列后，实盘数据检查按 run_kind+trade_date 命中，不再恒拦截。"""
    import pytest

    from src.services.strategy_lab.data_sync_service import StrategyLabDataSyncService

    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    sync_service = StrategyLabDataSyncService(db)
    calls: list[str] = []

    def _stage(name: str):
        def _run(**kwargs):
            calls.append(name)
            return {"ok": name}
        return _run

    sync_service.sync_cb_ohlc = _stage("cb_ohlc")
    sync_service.sync_cb_factors = _stage("cb_factors")

    live = LiveStrategyService(db)
    live.save_config({"qmt_account": "testS", "enabled": True, "parameters": {"max_positions": 1}})
    QmtPositionService(db).report_positions(account="testS", positions=[])

    try:
        # 当日无盘中同步记录：门控拦截
        blocked = live.run(trade_date=date(2024, 1, 2), preview=True)
        assert blocked["skip_reason"] == "intraday_sync_unavailable"

        # 盘中链路同步完成后：同一交易日门控放行
        sync_service.run_scheduled_sync(run_kind="intraday", trade_date=date(2024, 1, 2))
        assert calls == ["cb_ohlc", "cb_factors"]
        passed = live.run(trade_date=date(2024, 1, 2), preview=True)
        assert passed.get("skip_reason") != "intraday_sync_unavailable"

        # 其他交易日仍无记录，仍被拦截
        other = live.run(trade_date=date(2024, 1, 3), preview=True)
        assert other["skip_reason"] == "intraday_sync_unavailable"
    finally:
        DatabaseManager.reset_instance()


def test_auto_mode_rebalances_first_time_without_history(monkeypatch: pytest.MonkeyPatch):
    """无成功调仓记录时 auto 立即到期（先建仓），next_rebalance_date 为空。"""
    _weekdays_only(monkeypatch)
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "rebalance_frequency_days": 3, "parameters": {"max_positions": 1}})
        QmtPositionService(db).report_positions(account="testS", positions=[])

        preview = service.run(trade_date=date(2024, 1, 2), preview=True)
        assert preview["mode"] == "rebalance"
        assert preview["requested_mode"] == "auto"
        assert service.get_config()["next_rebalance_date"] is None
    finally:
        DatabaseManager.reset_instance()


def test_auto_mode_resolves_by_trading_day_frequency(monkeypatch: pytest.MonkeyPatch):
    """锚点周二、频率 3：周五（第 3 个交易日）到期，之前一律解析为 event_check。"""
    _weekdays_only(monkeypatch)
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "rebalance_frequency_days": 3, "parameters": {"max_positions": 1}})
        QmtPositionService(db).report_positions(account="testS", positions=[])
        _seed_completed_rebalance(db, date(2024, 1, 2))  # 周二

        not_due = service.run(trade_date=date(2024, 1, 4), preview=True)  # 周四
        assert not_due["mode"] == "event_check"
        due = service.run(trade_date=date(2024, 1, 5), preview=True)  # 周五
        assert due["mode"] == "rebalance"
        assert service.get_config()["next_rebalance_date"] == "2024-01-05"
    finally:
        DatabaseManager.reset_instance()


def test_auto_skips_event_check_when_disabled(monkeypatch: pytest.MonkeyPatch):
    """事件检查总闸关闭：非调仓日 auto 直接返回跳过载荷且不落 run；显式 event_check、调仓日不受影响。"""
    _weekdays_only(monkeypatch)
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "rebalance_frequency_days": 3, "event_check_enabled": False,
                             "parameters": {"max_positions": 1}})
        QmtPositionService(db).report_positions(account="testS", positions=[])
        _seed_completed_rebalance(db, date(2024, 1, 2))  # 周二，频率 3 → 周五到期
        _seed_cb_universe(db, date(2024, 1, 4), [
            {"code": "113001", "name": "低溢价", "close": 100.0, "premium": 5.0},
        ])

        # 非调仓日 + 总闸关闭：跳过且不产生新 run 记录
        skipped = service.run(trade_date=date(2024, 1, 4))  # 周四，preview=False 验证真实执行路径
        assert skipped["skip_reason"] == "event_check_disabled"
        assert skipped["rebalance"] == []
        with db.get_session() as session:
            runs = session.execute(select(LiveStrategyRun)).scalars().all()
        assert len(runs) == 1 and runs[0].trade_date == date(2024, 1, 2)  # 仅剩锚点种子

        # 显式 event_check 不受总闸限制；调仓日照常调仓
        explicit = service.run(trade_date=date(2024, 1, 4), mode="event_check", preview=True)
        assert explicit["mode"] == "event_check"
        assert "skip_reason" not in explicit
        due = service.run(trade_date=date(2024, 1, 5), preview=True)  # 周五到期
        assert due["mode"] == "rebalance"
    finally:
        DatabaseManager.reset_instance()


def test_auto_mode_skips_holidays_in_frequency_counting(monkeypatch: pytest.MonkeyPatch):
    """频率按交易日计：周四节假日休市时，第 3 个交易日顺延到下周一。"""
    _weekdays_only(monkeypatch, holidays={date(2024, 1, 4)})
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "rebalance_frequency_days": 3, "parameters": {"max_positions": 1}})
        QmtPositionService(db).report_positions(account="testS", positions=[])
        _seed_completed_rebalance(db, date(2024, 1, 2))  # 周二

        friday = service.run(trade_date=date(2024, 1, 5), preview=True)  # 只数到 2 个交易日
        assert friday["mode"] == "event_check"
        monday = service.run(trade_date=date(2024, 1, 8), preview=True)  # 第 3 个交易日
        assert monday["mode"] == "rebalance"
        assert service.get_config()["next_rebalance_date"] == "2024-01-08"
    finally:
        DatabaseManager.reset_instance()


def test_auto_mode_self_heals_after_failed_rebalance(monkeypatch: pytest.MonkeyPatch):
    """到期日下单失败：run 落 failed、锚点不动；当日可重试，重试成功后节奏继续。"""
    _weekdays_only(monkeypatch)
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")

    class _FailingExecutor:
        def __init__(self, orders): ...
        def execute(self, *args, **kwargs):
            raise RuntimeError("qmt submit failed")

    monkeypatch.setattr("src.services.live_strategy_service.LiveExecutor", _FailingExecutor)
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "rebalance_frequency_days": 3, "parameters": {"max_positions": 1}})
        QmtPositionService(db).report_positions(account="testS", positions=[])
        _seed_completed_rebalance(db, date(2024, 1, 2))  # 周二 → 周五到期

        with pytest.raises(RuntimeError):
            service.run(trade_date=date(2024, 1, 5))
        failed = _latest_run(db)
        assert failed.status == "failed"
        assert failed.error_message == "qmt submit failed"

        # 同日重试：幂等只认成功 run，复用原行重新执行
        monkeypatch.setattr("src.services.live_strategy_service.LiveExecutor",
                            lambda orders: type("_Ok", (), {"execute": staticmethod(lambda *a, **k: [])})())
        retried = service.run(trade_date=date(2024, 1, 5))
        assert retried["mode"] == "rebalance"
        with db.get_session() as session:
            rows = session.execute(select(LiveStrategyRun).where(
                LiveStrategyRun.trade_date == date(2024, 1, 5))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "completed"
        assert rows[0].completed_at is not None

        # 锚点前移到 1/5（周五），频率 3 → 下周三 1/10 到期，之前是事件检查
        assert service.get_config()["next_rebalance_date"] == "2024-01-10"
        assert service.run(trade_date=date(2024, 1, 9), preview=True)["mode"] == "event_check"
        assert service.run(trade_date=date(2024, 1, 10), preview=True)["mode"] == "rebalance"
    finally:
        DatabaseManager.reset_instance()


def test_explicit_rebalance_respects_frequency(monkeypatch: pytest.MonkeyPatch):
    """显式 rebalance 未到期跳过；显式 event_check 不查频率。"""
    _weekdays_only(monkeypatch)
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "rebalance_frequency_days": 3, "parameters": {"max_positions": 1}})
        QmtPositionService(db).report_positions(account="testS", positions=[])
        _seed_completed_rebalance(db, date(2024, 1, 2))

        skipped = service.run(trade_date=date(2024, 1, 3), preview=True, mode="rebalance")
        assert skipped["skip_reason"] == "rebalance_frequency"
        event = service.run(trade_date=date(2024, 1, 3), preview=True, mode="event_check")
        assert event["mode"] == "event_check"
        assert "skip_reason" not in event
        forced = service.run(trade_date=date(2024, 1, 5), preview=True, mode="rebalance")
        assert forced["mode"] == "rebalance"
        assert "skip_reason" not in forced
    finally:
        DatabaseManager.reset_instance()


def test_run_rejects_invalid_mode_and_missing_config():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        with pytest.raises(ValueError, match="mode must be"):
            service.run(trade_date=date(2024, 1, 2), mode="bogus")
        with pytest.raises(ValueError, match="not configured"):
            service.run(trade_date=date(2024, 1, 2))
    finally:
        DatabaseManager.reset_instance()


def test_gate_blocks_non_preview_run_with_exception():
    """门禁未过时：preview 返回跳过载荷，非 preview 直接抛错阻断下单。"""
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "parameters": {"max_positions": 1}})
        QmtPositionService(db).report_positions(account="testS", positions=[])

        blocked = service.run(trade_date=date(2024, 1, 2), preview=True)
        assert blocked["skip_reason"] == "intraday_sync_unavailable"
        with pytest.raises(ValueError, match="intraday data sync is not completed"):
            service.run(trade_date=date(2024, 1, 2))
    finally:
        DatabaseManager.reset_instance()


def test_rebalance_pipeline_buys_lowest_premium_and_exits_offtarget_holding(caplog):
    """全链路：因子选债买入 + 对账补卖 + 订单/决策/run/batch 落库。"""
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "parameters": {"max_positions": 1, "per_position_cash": 10000, "lot_size": 10}})
        trade_date = date(2024, 1, 2)
        _seed_cb_universe(db, trade_date, [
            {"code": "113001", "name": "低溢价", "close": 100.0, "premium": 5.0},
            {"code": "113002", "name": "高溢价", "close": 110.0, "premium": 50.0},
        ])
        # 持有高溢价债 100 张，不在目标内 → 应被对账补卖
        QmtPositionService(db).report_positions(account="testS", positions=[
            {"symbol": "113002", "volume": 100, "can_use_volume": 100},
        ])

        import logging as _logging
        with caplog.at_level(_logging.INFO, logger="src.services.live_strategy_service"):
            result = service.run(trade_date=trade_date, mode="rebalance")

        # 三阶段输出日志（evaluate → plan → execute）与完成日志均落盘，可按日志排查
        assert "[LiveStrategy] evaluate:" in caplog.text
        assert "[LiveStrategy] plan:" in caplog.text
        assert "[LiveStrategy] execute:" in caplog.text
        assert "[LiveStrategy] run completed" in caplog.text

        # 目标组合只含低溢价债；价格/数量按目标资金和手数推导
        assert list(result["target"]) == ["113001"]
        assert result["target"]["113001"]["price"] == 100.0
        assert result["target"]["113001"]["quantity"] == 100
        assert result["current"] == {"113002": 100.0}
        by_side = {(o["side"], o["symbol"]): o for o in result["rebalance"]}
        assert by_side[("buy", "113001")]["quantity"] == 100
        assert by_side[("sell", "113002")]["quantity"] == 100
        assert by_side[("sell", "113002")]["reason"] == "live_target_exit"
        # 调仓明细附带展示字段：名称与当日溢价率（策略买入与对账卖出同样补齐）
        assert by_side[("buy", "113001")]["symbol_name"] == "低溢价"
        assert by_side[("buy", "113001")]["premium_rate"] == 5.0
        assert by_side[("sell", "113002")]["symbol_name"] == "高溢价"
        assert by_side[("sell", "113002")]["premium_rate"] == 50.0

        with db.get_session() as session:
            run = session.execute(select(LiveStrategyRun).order_by(desc(LiveStrategyRun.id))).scalars().first()
            orders = session.execute(select(TradingOrder).where(TradingOrder.live_run_id == run.id)).scalars().all()
            decisions = session.execute(select(StrategyDecisionRecord).where(
                StrategyDecisionRecord.live_run_id == run.id)).scalars().all()
            batches = session.execute(select(LiveRebalanceBatch).where(LiveRebalanceBatch.run_id == run.id)).scalars().all()
        assert run.status == "completed" and run.mode == "rebalance"
        assert run.decision_count == 2 and run.order_count == 2
        assert {(o.side, o.symbol) for o in orders} == {("buy", "113001"), ("sell", "113002")}
        assert all(o.live_run_id == run.id for o in orders)
        # 买卖订单与决策记录的名称完整（对账卖出单此前无名称）
        assert {o.symbol_name for o in orders} == {"低溢价", "高溢价"}
        assert {d.action for d in decisions} == {"buy", "sell"}
        decision_names = {d.symbol: d.symbol_name for d in decisions}
        assert decision_names == {"113001": "低溢价", "113002": "高溢价"}
        assert all(d.mode == "rebalance" and d.live_run_id == run.id for d in decisions)
        assert len(batches) == 1 and batches[0].status == "pending"
    finally:
        DatabaseManager.reset_instance()


def test_rebalance_blocked_when_premium_coverage_low():
    """溢价率覆盖率门禁：缺失过多时 preview 标注返回、真实 run 抛错；event_check 不受限。"""
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "parameters": {"max_positions": 5, "per_position_cash": 10000, "lot_size": 10}})
        trade_date = date(2024, 1, 2)
        # 20 只因子行、10 只溢价率缺失（50% < 95% 阈值）
        bonds = (
            [{"code": f"1130{i:02d}", "name": f"有溢价{i}", "close": 100.0, "premium": 5.0} for i in range(10)]
            + [{"code": f"1131{i:02d}", "name": f"缺溢价{i}", "close": 110.0, "premium": None} for i in range(10)]
        )
        _seed_cb_universe(db, trade_date, bonds)
        QmtPositionService(db).report_positions(account="testS", positions=[])

        preview = service.run(trade_date=trade_date, mode="rebalance", preview=True)
        assert preview["skip_reason"] == "premium_coverage_low"
        assert preview["risk"]["reason"] == "premium_coverage_low"
        assert preview["risk"]["total"] == 20 and preview["risk"]["missing"] == 10
        assert preview["rebalance"] == []

        with pytest.raises(ValueError, match="premium coverage"):
            service.run(trade_date=trade_date, mode="rebalance")

        # event_check 只扫持仓事件，不依赖溢价率，同一数据不受门禁限制
        result = service.run(trade_date=trade_date, mode="event_check")
        assert result["mode"] == "event_check"
        assert result.get("skip_reason") != "premium_coverage_low"

        # 边界：缺失 1/20 = 95% 正好达标，门禁放行（preview 正常给出目标组合）
        _seed_cb_universe(db, date(2024, 1, 3), (
            [{"code": f"1140{i:02d}", "name": f"达标{i}", "close": 100.0, "premium": 5.0} for i in range(19)]
            + [{"code": "114100", "name": "缺一只", "close": 110.0, "premium": None}]
        ))
        passed = service.run(trade_date=date(2024, 1, 3), mode="rebalance", preview=True)
        assert passed.get("skip_reason") is None
        assert len(passed["target"]) == 5
    finally:
        DatabaseManager.reset_instance()


def test_event_check_exits_blocked_holding_without_buying():
    """event_check 只扫持仓：强赎持仓退出、无事件持仓 hold，绝不选债买入。"""
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "parameters": {"max_positions": 1, "per_position_cash": 10000, "lot_size": 10}})
        trade_date = date(2024, 1, 2)
        _seed_cb_universe(db, trade_date, [
            {"code": "113001", "name": "正常债", "close": 100.0, "premium": 5.0},
            {"code": "113002", "name": "强赎债", "close": 110.0, "premium": 50.0, "alert": True},
        ])
        QmtPositionService(db).report_positions(account="testS", positions=[
            {"symbol": "113001", "volume": 50, "can_use_volume": 50},
            {"symbol": "113002", "volume": 100, "can_use_volume": 100},
        ])

        result = service.run(trade_date=trade_date, mode="event_check")

        assert result["mode"] == "event_check"
        assert result["target"] == {}  # 不选债、不建目标组合
        assert [(o["side"], o["symbol"], o["quantity"]) for o in result["rebalance"]] == [("sell", "113002", 100)]
        assert result["rebalance"][0]["reason"] == "event_blocked"
        actions = {d["symbol"]: d["action"] for d in result["decisions"]}
        assert actions == {"113001": "hold", "113002": "exit"}
        # 事件检查路径的策略决策只有代码，名称由 service 从上下文补齐
        names = {d["symbol"]: d["symbol_name"] for d in result["decisions"]}
        assert names == {"113001": "正常债", "113002": "强赎债"}

        run = _latest_run(db)
        assert run.mode == "event_check" and run.status == "completed" and run.order_count == 1
    finally:
        DatabaseManager.reset_instance()


def test_auto_falls_back_to_completed_event_check_same_day(monkeypatch: pytest.MonkeyPatch):
    """未到期日 auto 解析为 event_check，当日重复触发幂等返回同一条 run。"""
    _weekdays_only(monkeypatch)
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "rebalance_frequency_days": 5, "parameters": {"max_positions": 1}})
        QmtPositionService(db).report_positions(account="testS", positions=[])
        _seed_completed_rebalance(db, date(2024, 1, 1))  # 周一，频率 5 → 下周一 1/8 到期

        first = service.run(trade_date=date(2024, 1, 2))  # 未到期 → event_check
        assert first["mode"] == "event_check"
        again = service.run(trade_date=date(2024, 1, 2))
        assert again["run_uid"] == first["run_uid"]

        with db.get_session() as session:
            rows = session.execute(select(LiveStrategyRun).where(LiveStrategyRun.trade_date == date(2024, 1, 2))).scalars().all()
        assert len(rows) == 1 and rows[0].mode == "event_check" and rows[0].status == "completed"
    finally:
        DatabaseManager.reset_instance()


def test_frequency_one_rebalances_every_trading_day(monkeypatch: pytest.MonkeyPatch):
    """频率 1：每个交易日到期；锚点后无交易日间隔（周末）才跳过，过期则补跑。"""
    _weekdays_only(monkeypatch)
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "rebalance_frequency_days": 1, "parameters": {"max_positions": 1}})
        QmtPositionService(db).report_positions(account="testS", positions=[])
        _seed_completed_rebalance(db, date(2024, 1, 2))  # 周二

        # 周三：隔 1 个交易日，到期
        assert service.run(trade_date=date(2024, 1, 3), preview=True)["mode"] == "rebalance"

        # 锚点前移到周五 1/5：周六 0 个交易日间隔 → 未到期跳过；
        # 但若锚点停在周二（漏跑多日），周六按“已过期”照样补跑
        _seed_completed_rebalance(db, date(2024, 1, 5))
        saturday = service.run(trade_date=date(2024, 1, 6), preview=True, mode="rebalance")
        assert saturday["skip_reason"] == "rebalance_frequency"
        assert service.run(trade_date=date(2024, 1, 8), preview=True)["mode"] == "rebalance"
    finally:
        DatabaseManager.reset_instance()


def test_last_trading_day_forces_exit_even_without_event_alert(monkeypatch: pytest.MonkeyPatch):
    """最后交易日风控独立于事件告警：无 redeem_alert 但临近/已过最后交易日的持仓强制退出。"""
    _weekdays_only(monkeypatch)
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "parameters": {"max_positions": 1}})
        trade_date = date(2024, 1, 2)  # 周二
        _seed_cb_universe(db, trade_date, [
            {"code": "113001", "name": "正常债", "close": 100.0, "premium": 5.0},  # 无最后交易日约束
            {"code": "113003", "name": "临末期债", "close": 105.0, "premium": 8.0, "ltd": date(2024, 1, 3)},  # 周三止交易
            {"code": "113004", "name": "已过期债", "close": 102.0, "premium": 6.0, "ltd": date(2023, 12, 20)},  # 已过最后交易日仍 active
        ])
        QmtPositionService(db).report_positions(account="testS", positions=[
            {"symbol": "113001", "volume": 50, "can_use_volume": 50},
            {"symbol": "113003", "volume": 80, "can_use_volume": 80},
            {"symbol": "113004", "volume": 30, "can_use_volume": 30},
        ])

        result = service.run(trade_date=trade_date, mode="event_check", preview=True)

        actions = {d["symbol"]: (d["action"], d.get("reason")) for d in result["decisions"]}
        assert actions["113001"] == ("hold", "no_blocking_event")  # 正常持仓不受影响
        assert actions["113003"] == ("exit", "last_trading_day_exit")
        assert actions["113004"] == ("exit", "last_trading_day_exit")  # 已过最后交易日 → 剩余 0 天
        orders = {(o["symbol"], o["side"], o["quantity"]) for o in result["rebalance"]}
        assert orders == {("113003", "sell", 80), ("113004", "sell", 30)}
        # 决策附带最后交易日信息，便于运行详情对账
        detail = next(d for d in result["decisions"] if d["symbol"] == "113003")
        assert detail["decision_data"]["last_trading_date"] == "2024-01-03"
    finally:
        DatabaseManager.reset_instance()


def test_last_trading_day_blocks_buy_and_dedupes_reconciliation_sell(monkeypatch: pytest.MonkeyPatch):
    """rebalance：买入排除临近最后交易日的标的，强制退出与对账补卖不重复下单。"""
    _weekdays_only(monkeypatch)
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "parameters": {"max_positions": 1, "per_position_cash": 10000, "lot_size": 10}})
        trade_date = date(2024, 1, 2)  # 周二
        _seed_cb_universe(db, trade_date, [
            # 最低溢价但周五止交易（剩余 4 个交易日 = 提前量1+3）→ 买入排除，不得入选
            {"code": "113005", "name": "临期低溢价", "close": 100.0, "premium": 2.0, "ltd": date(2024, 1, 5)},
            # 次低溢价、下周一止交易（剩余 5 个交易日）→ 可正常买入
            {"code": "113006", "name": "次低溢价", "close": 100.0, "premium": 8.0, "ltd": date(2024, 1, 8)},
        ])
        # 持有周三止交易的债（剩余 2 个交易日 = 提前量1+1）→ 强制退出窗口内
        with db.get_session() as session:
            session.add(StrategyLabCbBasic(bond_code="113003", bond_name="强赎临期", stock_code="SH113003",
                                           market="cn", status="active",
                                           terms_json=json.dumps({"last_trading_date": "2024-01-03"})))
            session.add(StrategyLabCbDailyFactor(bond_code="113003", trade_date=trade_date,
                                                 close=105.0, premium_rate=4.0, remaining_size=5.0))
            session.commit()
        QmtPositionService(db).report_positions(account="testS", positions=[
            {"symbol": "113003", "volume": 100, "can_use_volume": 100},
        ])

        result = service.run(trade_date=trade_date, mode="rebalance")

        # 买入：排除了剩余 4 个交易日的 113005，选中剩余 5 个交易日的 113006
        assert list(result["target"]) == ["113006"]
        sells = [o for o in result["rebalance"] if o["side"] == "sell" and o["symbol"] == "113003"]
        # 强制退出仅一张卖单（对账补卖跳过同一标的），不重复、不超卖
        assert len(sells) == 1
        assert sells[0]["quantity"] == 100
        assert sells[0]["reason"] == "last_trading_day_exit"
        with db.get_session() as session:
            run = session.execute(select(LiveStrategyRun).order_by(desc(LiveStrategyRun.id))).scalars().first()
            orders = session.execute(select(TradingOrder).where(TradingOrder.live_run_id == run.id)).scalars().all()
        sell_orders = [o for o in orders if o.side == "sell" and o.symbol == "113003"]
        assert len(sell_orders) == 1 and sell_orders[0].quantity == 100
    finally:
        DatabaseManager.reset_instance()


def test_last_trading_day_exit_disabled_by_config(monkeypatch: pytest.MonkeyPatch):
    """风控开关关闭：临近最后交易日的持仓不强制退出（回到纯事件/对账语义）。"""
    _weekdays_only(monkeypatch)
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "last_trading_day_exit_enabled": False,
                             "parameters": {"max_positions": 1}})
        trade_date = date(2024, 1, 2)
        _seed_cb_universe(db, trade_date, [
            {"code": "113003", "name": "临末期债", "close": 105.0, "premium": 8.0, "ltd": date(2024, 1, 3)},
        ])
        QmtPositionService(db).report_positions(account="testS", positions=[
            {"symbol": "113003", "volume": 80, "can_use_volume": 80},
        ])

        result = service.run(trade_date=trade_date, mode="event_check", preview=True)

        assert result["rebalance"] == []
        actions = {d["symbol"]: d["action"] for d in result["decisions"]}
        assert actions == {"113003": "hold"}
    finally:
        DatabaseManager.reset_instance()


def test_last_trading_day_exit_respects_buffer_days(monkeypatch: pytest.MonkeyPatch):
    """提前量按交易日生效：buffer=0 时仅最后交易日当天触发；提前量增大则提前触发。"""
    _weekdays_only(monkeypatch)
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "last_trading_day_exit_buffer_days": 0,
                             "parameters": {"max_positions": 1}})
        _seed_cb_universe(db, date(2024, 1, 2), [
            {"code": "113003", "name": "临末期债", "close": 105.0, "premium": 8.0, "ltd": date(2024, 1, 3)},
        ])
        with db.get_session() as session:  # 补最后交易日当天的因子行（主表不重复插）
            session.add(StrategyLabCbDailyFactor(bond_code="113003", trade_date=date(2024, 1, 3),
                                                 close=105.0, premium_rate=8.0, remaining_size=5.0))
            session.commit()
        QmtPositionService(db).report_positions(account="testS", positions=[
            {"symbol": "113003", "volume": 80, "can_use_volume": 80},
        ])

        # 周二（最后交易日前 1 个交易日）：buffer=0 → 不触发
        tuesday = service.run(trade_date=date(2024, 1, 2), mode="event_check", preview=True)
        assert tuesday["rebalance"] == []
        # 周三（最后交易日当天）：剩余 1 个交易日 → 触发
        wednesday = service.run(trade_date=date(2024, 1, 3), mode="event_check", preview=True)
        assert [(o["symbol"], o["side"]) for o in wednesday["rebalance"]] == [("113003", "sell")]
    finally:
        DatabaseManager.reset_instance()
