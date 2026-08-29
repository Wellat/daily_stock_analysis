# -*- coding: utf-8 -*-
"""QMT 上报持仓 Service 测试。"""

from __future__ import annotations

import pytest

from src.services.qmt_position_service import QmtPositionService
from src.storage import DatabaseManager


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def _positions(**overrides) -> list:
    item = {
        "symbol": "000858",
        "name": "五粮液",
        "volume": 900,
        "can_use_volume": 900,
        "open_price": 103.657,
        "float_profit": 123.4,
    }
    item.update(overrides)
    return [item]


def test_report_positions_success(db_manager: DatabaseManager) -> None:
    service = QmtPositionService(db_manager)
    result = service.report_positions(account="testS", positions=_positions())
    assert result == {"account": "testS", "reported": 1}

    listed = service.list_positions(account="testS")
    assert len(listed["items"]) == 1
    assert listed["items"][0]["symbol"] == "000858"
    assert listed["items"][0]["name"] == "五粮液"


def test_report_positions_upsert_idempotent(db_manager: DatabaseManager) -> None:
    service = QmtPositionService(db_manager)
    service.report_positions(account="testS", positions=_positions())
    service.report_positions(
        account="testS",
        positions=_positions(volume=800, can_use_volume=800, float_profit=99.9),
    )

    listed = service.list_positions(account="testS")
    assert len(listed["items"]) == 1
    assert listed["items"][0]["volume"] == 800
    assert listed["items"][0]["float_profit"] == 99.9


def test_report_positions_full_replace(db_manager: DatabaseManager) -> None:
    service = QmtPositionService(db_manager)
    service.report_positions(
        account="testS",
        positions=[
            {"symbol": "000858", "volume": 900, "can_use_volume": 900},
            {"symbol": "113002", "volume": 10, "can_use_volume": 10},
        ],
    )

    # 第二次只上报 000858，113002 应被清理
    service.report_positions(
        account="testS",
        positions=[{"symbol": "000858", "volume": 800, "can_use_volume": 800}],
    )

    listed = service.list_positions(account="testS")
    assert len(listed["items"]) == 1
    assert listed["items"][0]["symbol"] == "000858"


def test_report_positions_empty_clears_account(db_manager: DatabaseManager) -> None:
    service = QmtPositionService(db_manager)
    service.report_positions(account="testS", positions=_positions())

    # 空列表 = 清仓，删除该账户全部持仓
    result = service.report_positions(account="testS", positions=[])
    assert result == {"account": "testS", "reported": 0}

    listed = service.list_positions(account="testS")
    assert listed["items"] == []


def test_report_positions_empty_account(db_manager: DatabaseManager) -> None:
    service = QmtPositionService(db_manager)
    with pytest.raises(ValueError, match="account"):
        service.report_positions(account="  ", positions=_positions())


def test_report_positions_invalid_symbol(db_manager: DatabaseManager) -> None:
    service = QmtPositionService(db_manager)
    with pytest.raises(ValueError, match="6-digit"):
        service.report_positions(account="testS", positions=_positions(symbol="abc"))


def test_report_positions_non_list(db_manager: DatabaseManager) -> None:
    service = QmtPositionService(db_manager)
    with pytest.raises(ValueError, match="list"):
        service.report_positions(account="testS", positions="not-a-list")  # type: ignore[arg-type]
