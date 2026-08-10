# -*- coding: utf-8 -*-
"""Strategy Lab convertible-bond event-study tests."""

from __future__ import annotations

import pytest

from src.services.strategy_lab.data_sync_service import StrategyLabDataSyncService
from src.services.strategy_lab.event_study_service import StrategyLabEventStudyService
from src.storage import DatabaseManager


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def test_event_study_calculates_trading_day_offset_returns(db_manager: DatabaseManager) -> None:
    StrategyLabDataSyncService(db_manager).sync_payload_convertible_bonds(
        market="cn",
        source="sample",
        cb_basic=[{"bond_code": "123001", "bond_name": "测试转债", "stock_code": "600001"}],
        cb_terms=[],
        cb_daily_factors=[
            {"bond_code": "123001", "trade_date": "2024-01-02", "close": 100},
            {"bond_code": "123001", "trade_date": "2024-01-03", "close": 110},
            {"bond_code": "123001", "trade_date": "2024-01-04", "close": 99},
        ],
        cb_events=[{"bond_code": "123001", "event_date": "2024-01-03", "event_type": "down_revise"}],
    )

    result = StrategyLabEventStudyService(db_manager).study_convertible_bond_events(
        market="cn", event_type="down_revise", offsets=[-1, 1], symbols=[]
    )

    assert result["total"] == 1
    assert result["items"][0]["returns_pct"] == {"-1": -9.0909, "1": -10.0}
    assert result["summary"]["1"]["average_return_pct"] == -10.0
