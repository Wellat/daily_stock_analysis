from datetime import date

from src.services.live_strategy_service import LiveStrategyService
from src.services.qmt_position_service import QmtPositionService
from src.storage import DatabaseManager
from src.storage import StrategyLabCbBasic


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
