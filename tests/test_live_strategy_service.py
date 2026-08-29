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
        service.save_config({"qmt_account": "testS", "enabled": True, "parameters": {"max_positions": 1}})
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
