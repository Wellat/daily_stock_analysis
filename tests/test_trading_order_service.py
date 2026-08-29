# -*- coding: utf-8 -*-
"""可转债实盘交易指令 Service 测试。"""

from __future__ import annotations

import pytest

from src.services.trading_order_service import (
    TradingOrderNotFoundError,
    TradingOrderService,
)
from src.storage import DatabaseManager


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def _create(service: TradingOrderService, **overrides) -> dict:
    payload = {
        "symbol": "113002",
        "side": "buy",
        "quantity": 10,
        "order_type": "limit",
        "limit_price": 120.5,
    }
    payload.update(overrides)
    return service.create_order(**payload)


def test_create_order_requires_valid_symbol(db_manager: DatabaseManager) -> None:
    service = TradingOrderService(db_manager)
    with pytest.raises(ValueError, match="6-digit"):
        _create(service, symbol="abc")
    with pytest.raises(ValueError, match="6-digit"):
        _create(service, symbol="11300")


def test_create_order_limit_requires_price(db_manager: DatabaseManager) -> None:
    service = TradingOrderService(db_manager)
    with pytest.raises(ValueError, match="limit_price"):
        _create(service, limit_price=None)


def test_create_order_success(db_manager: DatabaseManager) -> None:
    service = TradingOrderService(db_manager)
    order = _create(service)
    assert order["status"] == "pending"
    assert order["order_uid"].startswith("qmt_")
    assert order["instrument_type"] == "convertible_bond"


def test_list_pending(db_manager: DatabaseManager) -> None:
    service = TradingOrderService(db_manager)
    _create(service)
    pending = service.list_pending()
    assert len(pending["items"]) == 1
    assert pending["items"][0]["symbol"] == "113002"
    assert pending["items"][0]["side"] == "buy"


def test_cancel_only_pending(db_manager: DatabaseManager) -> None:
    service = TradingOrderService(db_manager)
    order = _create(service)
    cancelled = service.cancel_order(order["id"])
    assert cancelled["status"] == "cancelled"

    with pytest.raises(ValueError, match="only pending"):
        service.cancel_order(order["id"])


def test_cancel_missing_order(db_manager: DatabaseManager) -> None:
    service = TradingOrderService(db_manager)
    with pytest.raises(TradingOrderNotFoundError):
        service.cancel_order(9999)


def test_callback_filled_and_idempotent(db_manager: DatabaseManager) -> None:
    service = TradingOrderService(db_manager)
    order = _create(service)

    filled = service.apply_callback(
        order_id=order["id"],
        status="filled",
        qmt_order_id="q123",
        filled_quantity=10,
        filled_price=120.4,
    )
    assert filled["status"] == "filled"
    assert filled["qmt_order_id"] == "q123"
    assert filled["filled_price"] == 120.4

    # 终态幂等：重复回调不改写结果
    again = service.apply_callback(
        order_id=order["id"],
        status="rejected",
        error_message="should not overwrite",
    )
    assert again["status"] == "filled"
    assert again["error_message"] is None


def test_callback_submitted_then_filled(db_manager: DatabaseManager) -> None:
    service = TradingOrderService(db_manager)
    order = _create(service)

    submitted = service.apply_callback(order_id=order["id"], status="submitted")
    assert submitted["status"] == "submitted"

    filled = service.apply_callback(
        order_id=order["id"],
        status="filled",
        qmt_order_id="q456",
        filled_quantity=10,
        filled_price=121.0,
    )
    assert filled["status"] == "filled"
    assert filled["qmt_order_id"] == "q456"


def test_callback_missing_order(db_manager: DatabaseManager) -> None:
    service = TradingOrderService(db_manager)
    with pytest.raises(TradingOrderNotFoundError):
        service.apply_callback(order_id=9999, status="filled")
