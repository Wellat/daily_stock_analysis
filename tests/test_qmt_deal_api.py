# -*- coding: utf-8 -*-
"""QMT 当日成交上报 API 与自动入账测试。"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.deps import get_database_manager
from src.repositories.portfolio_repo import PortfolioRepository
from src.services.portfolio_service import PortfolioService
from src.services.qmt_deal_service import QmtDealService, parse_trade_time
from src.storage import DatabaseManager


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


@pytest.fixture()
def client(db_manager: DatabaseManager) -> TestClient:
    app = create_app(static_dir=Path(tempfile.mkdtemp()))
    app.dependency_overrides[get_database_manager] = lambda: db_manager
    return TestClient(app)


def _deal(
    trade_id: str = "1000123",
    symbol: str = "113002",
    side: str = "buy",
    price: float = 120.4,
    volume: float = 10,
    fee: float = 0.0,
    trade_time: str = "2026-09-12 14:45:03",
    **overrides,
) -> dict:
    payload = {
        "account": "testS",
        "symbol": symbol,
        "name": "工行转债",
        "side": side,
        "price": price,
        "volume": volume,
        "amount": round(price * volume, 2),
        "fee": fee,
        "trade_id": trade_id,
        "order_sys_id": "202609120001",
        "trade_time": trade_time,
        "xt_trade": "1",
    }
    payload.update(overrides)
    return payload


def _report(db: DatabaseManager, account: str, deals: list[dict]) -> dict:
    return QmtDealService(db).report_deals(account=account, deals=deals)


def _snapshot(db: DatabaseManager, account_id: int) -> dict:
    return PortfolioService(repo=PortfolioRepository(db)).get_portfolio_snapshot(
        account_id=account_id, cost_method="fifo", include_realtime=False
    )["accounts"][0]


def test_report_deals_creates_account_and_position(db_manager: DatabaseManager, client: TestClient) -> None:
    resp = client.post(
        "/api/v1/trading/qmt/deals",
        json={"account": "testS", "deals": [_deal()]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["account"] == "testS"
    assert body["account_created"] is True
    assert body["account_name"] == "testS"
    assert body["received"] == 1
    assert body["inserted_count"] == 1
    assert body["cash_entries"] == 1
    assert body["items"][0]["status"] == "inserted"
    assert body["items"][0]["trade_time_parsed"] is True

    trades = PortfolioRepository(db_manager).list_trades(body["account_id"], as_of=date(2026, 9, 12))
    assert len(trades) == 1
    trade = trades[0]
    assert trade.symbol == "113002"
    assert trade.side == "buy"
    assert trade.quantity == pytest.approx(10.0)
    assert trade.price == pytest.approx(120.4)
    assert trade.market == "cn"
    assert trade.note == "qmt_deal:testS"
    assert trade.trade_uid == "qmt_deal:testS:1000123"

    snapshot = _snapshot(db_manager, body["account_id"])
    assert snapshot["total_cash"] == pytest.approx(0.0)
    position = next(p for p in snapshot["positions"] if p["symbol"] == "113002")
    assert position["quantity"] == pytest.approx(10.0)
    assert position["avg_cost"] == pytest.approx(120.4)


def test_repost_same_batch_is_idempotent(db_manager: DatabaseManager) -> None:
    first = _report(db_manager, "testS", [_deal()])
    assert first["inserted_count"] == 1

    second = _report(db_manager, "testS", [_deal()])
    assert second["account_created"] is False
    assert second["inserted_count"] == 0
    assert second["duplicate_count"] == 1
    assert second["cash_entries"] == 0
    assert second["items"][0]["status"] == "duplicate"

    trades = PortfolioRepository(db_manager).list_trades(first["account_id"], as_of=date(2026, 9, 12))
    assert len(trades) == 1
    assert _snapshot(db_manager, first["account_id"])["total_cash"] == pytest.approx(0.0)


def test_sell_after_buy_records_realized_pnl(db_manager: DatabaseManager) -> None:
    result = _report(db_manager, "testS", [
        _deal(trade_id="B1", side="buy", price=100.0, volume=10, trade_time="2026-09-12 09:35:00"),
        _deal(trade_id="S1", side="sell", price=110.0, volume=10, fee=2.0, trade_time="2026-09-12 14:45:03"),
    ])
    assert result["inserted_count"] == 2
    assert result["failed_count"] == 0

    snapshot = _snapshot(db_manager, result["account_id"])
    # 已实现盈亏 = (110*10-2) - 100*10 = 98
    assert snapshot["realized_pnl"] == pytest.approx(98.0)
    assert snapshot["total_cash"] == pytest.approx(0.0)
    quantities = [p["quantity"] for p in snapshot["positions"]]
    assert all(abs(q) < 1e-9 for q in quantities)


def test_unknown_side_skipped_without_creating_account(db_manager: DatabaseManager) -> None:
    result = _report(db_manager, "testS", [_deal(side="unknown", trade_id="U1")])
    assert result["skipped_count"] == 1
    assert result["inserted_count"] == 0
    assert result["account_id"] is None
    assert result["account_created"] is False
    assert result["items"][0]["status"] == "skipped"
    assert PortfolioRepository(db_manager).list_accounts() == []


def test_oversell_failed_without_blocking_other_deals(db_manager: DatabaseManager) -> None:
    result = _report(db_manager, "testS", [
        _deal(trade_id="S1", symbol="110077", side="sell", price=130.0, volume=10),
        _deal(trade_id="B1", symbol="113002", side="buy", price=120.4, volume=10),
    ])
    assert result["inserted_count"] == 1
    assert result["failed_count"] == 1
    statuses = {item["trade_id"]: item["status"] for item in result["items"]}
    assert statuses["S1"] == "failed"
    assert statuses["B1"] == "inserted"
    failed_item = next(i for i in result["items"] if i["trade_id"] == "S1")
    assert failed_item["error"]


def test_batch_reordered_by_trade_time(db_manager: DatabaseManager) -> None:
    # 请求里卖出排在买入前，但成交时间买入更早 → 按时间排序后先买后卖，均可入账
    result = _report(db_manager, "testS", [
        _deal(trade_id="S1", side="sell", price=110.0, volume=10, trade_time="2026-09-12 14:45:03"),
        _deal(trade_id="B1", side="buy", price=100.0, volume=10, trade_time="2026-09-12 09:35:00"),
    ])
    assert result["failed_count"] == 0
    assert result["inserted_count"] == 2
    assert [item["trade_id"] for item in result["items"]] == ["B1", "S1"]
    assert _snapshot(db_manager, result["account_id"])["realized_pnl"] == pytest.approx(100.0)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-09-12 14:45:03", date(2026, 9, 12)),
        ("20260912 14:45:03", date(2026, 9, 12)),
        ("20260912144503", date(2026, 9, 12)),
        ("20260912 14:45", date(2026, 9, 12)),
        ("2026-09-12", date(2026, 9, 12)),
        ("20260912144503.500", date(2026, 9, 12)),
    ],
)
def test_parse_trade_time_supported_formats(raw: str, expected: date) -> None:
    trade_date, _, parsed = parse_trade_time(raw)
    assert trade_date == expected
    assert parsed is True


def test_parse_trade_time_fallback_to_today() -> None:
    trade_date, _, parsed = parse_trade_time("not-a-time")
    assert trade_date == date.today()
    assert parsed is False


def test_deal_token_required(db_manager: DatabaseManager, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QMT_API_TOKEN", "secret")
    payload = {"account": "testS", "deals": [_deal()]}

    denied = client.post("/api/v1/trading/qmt/deals", json=payload)
    assert denied.status_code == 401
    assert denied.json()["error"] == "unauthorized"

    allowed = client.post("/api/v1/trading/qmt/deals", json=payload, headers={"X-QMT-Token": "secret"})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["inserted_count"] == 1


def test_invalid_params_return_400(db_manager: DatabaseManager, client: TestClient) -> None:
    bad_symbol = client.post(
        "/api/v1/trading/qmt/deals",
        json={"account": "testS", "deals": [_deal(symbol="600519.SH")]},
    )
    assert bad_symbol.status_code == 400
    assert bad_symbol.json()["error"] == "invalid_params"
    assert "symbol" in bad_symbol.json()["message"]

    empty_account = client.post(
        "/api/v1/trading/qmt/deals",
        json={"account": "  ", "deals": [_deal()]},
    )
    assert empty_account.status_code == 400
