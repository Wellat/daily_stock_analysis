# -*- coding: utf-8 -*-
"""可转债实盘交易指令 Repository 测试。"""

from __future__ import annotations

import pytest

from src.repositories.trading_order_repo import TradingOrderRepository
from src.storage import DatabaseManager


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def _fields(**overrides):
    fields = {
        "order_uid": "qmt_test1",
        "symbol": "113002",
        "market": "cn",
        "instrument_type": "convertible_bond",
        "side": "buy",
        "quantity": 10.0,
        "order_type": "limit",
        "limit_price": 120.5,
        "status": "pending",
        "source": "api",
    }
    fields.update(overrides)
    return fields


def test_create_and_get(db_manager: DatabaseManager) -> None:
    repo = TradingOrderRepository(db_manager)
    row = repo.create(**_fields())
    assert row.id is not None

    got = repo.get(row.id)
    assert got is not None
    assert got.order_uid == "qmt_test1"
    assert got.status == "pending"
    assert got.symbol == "113002"


def test_get_by_uid(db_manager: DatabaseManager) -> None:
    repo = TradingOrderRepository(db_manager)
    repo.create(**_fields())
    row = repo.get_by_uid("qmt_test1")
    assert row is not None
    assert row.symbol == "113002"
    assert repo.get_by_uid("missing") is None


def test_list_and_pending(db_manager: DatabaseManager) -> None:
    repo = TradingOrderRepository(db_manager)
    repo.create(**_fields(order_uid="qmt_a", status="pending"))
    repo.create(**_fields(order_uid="qmt_b", status="filled"))

    result = repo.list(status=None, limit=10, offset=0)
    assert result["total"] == 2

    pending = repo.list_pending()
    assert len(pending) == 1
    assert pending[0].order_uid == "qmt_a"


def test_update(db_manager: DatabaseManager) -> None:
    repo = TradingOrderRepository(db_manager)
    row = repo.create(**_fields())
    updated = repo.update(row.id, status="filled", filled_price=120.4)
    assert updated.status == "filled"
    assert updated.filled_price == 120.4
