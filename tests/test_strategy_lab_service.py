# -*- coding: utf-8 -*-
"""Strategy Lab service and repository tests."""

from __future__ import annotations

from datetime import date

import pytest

from src.core.strategy_lab.models import StrategyLabRunConfig
from src.services.strategy_lab.data_sync_service import StrategyLabDataSyncService
from src.services.strategy_lab.service import StrategyLabService
from src.storage import DatabaseManager


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def test_strategy_lab_service_persists_completed_run(db_manager: DatabaseManager) -> None:
    service = StrategyLabService(db_manager)

    payload = service.create_run(
        StrategyLabRunConfig(
            strategy_id="double-low",
            market="cn",
            instrument_type="convertible_bond",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            initial_cash=100000,
            benchmark_symbol="113001",
            parameters={"max_positions": 2},
        )
    )

    assert payload["id"] > 0
    assert payload["status"] == "completed"
    assert payload["metrics"]["trade_count"] == 4
    assert payload["benchmark_return_pct"] is not None
    assert len(payload["equity_curve"]) == 3

    saved = service.get_run(payload["id"])
    assert saved is not None
    assert saved["metrics"]["diagnostics"]["selected_symbols"] == ["113002", "113001"]

    trades = service.list_trades(payload["id"])
    assert len(trades) == 4
    assert trades[0]["canonical_id"].startswith("cn.convertible_bond.")


def test_strategy_lab_service_requires_existing_portfolio_account(db_manager: DatabaseManager) -> None:
    service = StrategyLabService(db_manager)

    with pytest.raises(ValueError, match="Portfolio account not found"):
        service.create_run(
            StrategyLabRunConfig(
                strategy_id="double-low",
                market="cn",
                instrument_type="convertible_bond",
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 4),
                initial_cash=100000,
                portfolio_account_id=999,
            )
        )


def test_strategy_lab_service_uses_synchronized_convertible_bond_data(db_manager: DatabaseManager) -> None:
    repo = StrategyLabDataSyncService(db_manager).repository
    repo.upsert_cb_basic(
        [
            {"bond_code": "123001", "bond_name": "样例一", "stock_code": "600001"},
            {"bond_code": "123002", "bond_name": "样例二", "stock_code": "600002"},
        ],
        source="sample",
    )
    repo.upsert_cb_daily_factors(
        [
            {"bond_code": "123001", "trade_date": date(2024, 1, 2), "close": 100, "premium_rate": 15},
            {"bond_code": "123001", "trade_date": date(2024, 1, 3), "close": 105, "premium_rate": 14},
            {"bond_code": "123002", "trade_date": date(2024, 1, 2), "close": 80, "premium_rate": 20},
            {"bond_code": "123002", "trade_date": date(2024, 1, 3), "close": 81, "premium_rate": 19},
        ],
        source="sample",
    )

    payload = StrategyLabService(db_manager).create_run(
        StrategyLabRunConfig(
            strategy_id="double-low",
            market="cn",
            instrument_type="convertible_bond",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            initial_cash=100000,
            parameters={"max_positions": 1},
        )
    )

    assert payload["engine_name"] == "database_double_low_v1"
    assert payload["metrics"]["diagnostics"]["selected_symbols"] == ["123002"]
    assert StrategyLabService(db_manager).list_trades(payload["id"])[0]["symbol"] == "123002"
