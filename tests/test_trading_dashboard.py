# -*- coding: utf-8 -*-
"""策略看板（trading dashboard）FIFO 配对与聚合测试。"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.deps import get_database_manager
from src.repositories.trading_order_repo import TradingOrderRepository
from src.services.trading_order_service import TradingOrderService
from src.storage import DatabaseManager


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def _fill(
    service: TradingOrderService,
    repo: TradingOrderRepository,
    *,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    completed_at: datetime,
    symbol_name: str = "样例转债",
) -> None:
    order = service.create_order(
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type="market",
        limit_price=None,
        symbol_name=symbol_name,
    )
    service.apply_callback(
        order_id=order["id"],
        status="filled",
        filled_quantity=quantity,
        filled_price=price,
    )
    repo.update(order["id"], completed_at=completed_at)


def test_fifo_matching_and_summary(db_manager: DatabaseManager) -> None:
    service = TradingOrderService(db_manager)
    repo = TradingOrderRepository(db_manager)
    _fill(service, repo, symbol="113001", side="buy", quantity=10, price=100.0,
          completed_at=datetime(2026, 1, 5, 10, 0))
    _fill(service, repo, symbol="113001", side="buy", quantity=10, price=110.0,
          completed_at=datetime(2026, 1, 6, 10, 0))
    _fill(service, repo, symbol="113001", side="sell", quantity=15, price=120.0,
          completed_at=datetime(2026, 1, 7, 10, 0))

    result = service.get_dashboard()
    assert result["summary"]["total_count"] == 3
    assert result["summary"]["buy_count"] == 2
    assert result["summary"]["sell_count"] == 1
    assert result["summary"]["buy_amount"] == pytest.approx(2100.0)
    assert result["summary"]["sell_amount"] == pytest.approx(1800.0)
    # FIFO：(120-100)*10 + (120-110)*5 = 250
    assert result["summary"]["realized_pnl"] == pytest.approx(250.0)
    assert result["summary"]["win_count"] == 1
    assert result["summary"]["loss_count"] == 0
    assert result["summary"]["win_rate"] == pytest.approx(1.0)
    assert result["summary"]["unmatched_sell_quantity"] == pytest.approx(0.0)

    stat = result["symbols"][0]
    assert stat["symbol"] == "113001"
    assert stat["symbol_name"] == "样例转债"
    assert stat["open_quantity"] == pytest.approx(5.0)
    assert stat["open_cost"] == pytest.approx(550.0)

    assert len(result["curve"]) == 1
    assert result["curve"][0]["date"] == "2026-01-07"
    assert result["curve"][0]["daily_pnl"] == pytest.approx(250.0)
    assert result["curve"][0]["cumulative_pnl"] == pytest.approx(250.0)


def test_unmatched_sell_excluded_from_pnl(db_manager: DatabaseManager) -> None:
    """初始持仓直接卖出（无买入记录）不计盈亏，单独统计。"""
    service = TradingOrderService(db_manager)
    repo = TradingOrderRepository(db_manager)
    _fill(service, repo, symbol="110077", side="sell", quantity=20, price=105.0,
          completed_at=datetime(2026, 1, 5, 10, 0))

    result = service.get_dashboard()
    assert result["summary"]["sell_count"] == 1
    assert result["summary"]["realized_pnl"] == pytest.approx(0.0)
    assert result["summary"]["win_count"] == 0
    assert result["summary"]["loss_count"] == 0
    assert result["summary"]["win_rate"] is None
    assert result["summary"]["unmatched_sell_quantity"] == pytest.approx(20.0)
    assert result["curve"] == []
    assert result["symbols"][0]["unmatched_sell_quantity"] == pytest.approx(20.0)


def test_loss_counting_and_daily_curve(db_manager: DatabaseManager) -> None:
    service = TradingOrderService(db_manager)
    repo = TradingOrderRepository(db_manager)
    _fill(service, repo, symbol="113001", side="buy", quantity=10, price=100.0,
          completed_at=datetime(2026, 1, 5, 10, 0))
    _fill(service, repo, symbol="113001", side="sell", quantity=5, price=95.0,
          completed_at=datetime(2026, 1, 6, 10, 0))
    _fill(service, repo, symbol="113001", side="sell", quantity=5, price=102.0,
          completed_at=datetime(2026, 1, 8, 10, 0))

    result = service.get_dashboard()
    # (95-100)*5 = -25；(102-100)*5 = +10
    assert result["summary"]["realized_pnl"] == pytest.approx(-15.0)
    assert result["summary"]["win_count"] == 1
    assert result["summary"]["loss_count"] == 1
    assert result["summary"]["win_rate"] == pytest.approx(0.5)

    assert [point["date"] for point in result["curve"]] == ["2026-01-06", "2026-01-08"]
    assert result["curve"][0]["cumulative_pnl"] == pytest.approx(-25.0)
    assert result["curve"][1]["cumulative_pnl"] == pytest.approx(-15.0)


def test_time_range_filter(db_manager: DatabaseManager) -> None:
    from datetime import date

    service = TradingOrderService(db_manager)
    repo = TradingOrderRepository(db_manager)
    _fill(service, repo, symbol="113001", side="buy", quantity=10, price=100.0,
          completed_at=datetime(2026, 1, 5, 10, 0))
    _fill(service, repo, symbol="113001", side="buy", quantity=10, price=120.0,
          completed_at=datetime(2026, 2, 10, 10, 0))

    result = service.get_dashboard(start=date(2026, 1, 1), end=date(2026, 1, 31))
    assert result["start"] == "2026-01-01"
    assert result["end"] == "2026-01-31"
    assert result["summary"]["total_count"] == 1
    assert result["summary"]["buy_amount"] == pytest.approx(1000.0)

    # 范围内只有第一笔买入：该笔卖出全部为无配对
    _fill(service, repo, symbol="113001", side="sell", quantity=5, price=130.0,
          completed_at=datetime(2026, 1, 20, 10, 0))
    result = service.get_dashboard(start=date(2026, 1, 1), end=date(2026, 1, 31))
    assert result["summary"]["realized_pnl"] == pytest.approx((130.0 - 100.0) * 5)
    assert result["summary"]["unmatched_sell_quantity"] == pytest.approx(0.0)


def test_empty_dashboard(db_manager: DatabaseManager) -> None:
    service = TradingOrderService(db_manager)
    result = service.get_dashboard()
    assert result["summary"]["total_count"] == 0
    assert result["summary"]["win_rate"] is None
    assert result["curve"] == []
    assert result["symbols"] == []


def test_symbols_sorted_by_realized_pnl(db_manager: DatabaseManager) -> None:
    service = TradingOrderService(db_manager)
    repo = TradingOrderRepository(db_manager)
    _fill(service, repo, symbol="113001", side="buy", quantity=10, price=100.0,
          completed_at=datetime(2026, 1, 5, 10, 0), symbol_name="低溢价")
    _fill(service, repo, symbol="113001", side="sell", quantity=10, price=90.0,
          completed_at=datetime(2026, 1, 6, 10, 0), symbol_name="低溢价")
    _fill(service, repo, symbol="110077", side="buy", quantity=10, price=100.0,
          completed_at=datetime(2026, 1, 5, 11, 0), symbol_name="高溢价")
    _fill(service, repo, symbol="110077", side="sell", quantity=10, price=150.0,
          completed_at=datetime(2026, 1, 6, 11, 0), symbol_name="高溢价")

    result = service.get_dashboard()
    assert [item["symbol"] for item in result["symbols"]] == ["110077", "113001"]
    assert result["symbols"][0]["symbol_name"] == "高溢价"


def test_dashboard_endpoint(client: TestClient) -> None:
    created = client.post("/api/v1/trading/orders", json={
        "symbol": "113001", "side": "buy", "quantity": 10,
        "order_type": "market",
    })
    assert created.status_code == 200, created.text
    order_id = created.json()["id"]
    callback = client.post(f"/api/v1/trading/qmt/orders/{order_id}/callback", json={
        "status": "filled", "filled_quantity": 10, "filled_price": 101.0,
    })
    assert callback.status_code == 200, callback.text

    resp = client.get("/api/v1/trading/dashboard")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["summary"]["total_count"] == 1
    assert payload["summary"]["buy_amount"] == pytest.approx(1010.0)
    assert payload["symbols"][0]["open_quantity"] == pytest.approx(10.0)

    ranged = client.get("/api/v1/trading/dashboard", params={
        "start": "2020-01-01", "end": "2020-12-31",
    })
    assert ranged.status_code == 200, ranged.text
    assert ranged.json()["summary"]["total_count"] == 0


@pytest.fixture()
def client():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    app = create_app(static_dir=Path(tempfile.mkdtemp()))
    app.dependency_overrides[get_database_manager] = lambda: db
    try:
        yield TestClient(app)
    finally:
        DatabaseManager.reset_instance()
