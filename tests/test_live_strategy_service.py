from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import desc, select

from src.services.live_strategy_service import LiveStrategyService
from src.services.qmt_position_service import QmtPositionService
from src.storage import DatabaseManager, LiveStrategyConfig, LiveStrategyRun
from src.storage import StrategyLabCbBasic


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
            session.add(StrategyLabCbBasic(bond_code="113002", bond_name="测试转债", stock_code="600000", market="cn"))
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
