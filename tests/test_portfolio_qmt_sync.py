# -*- coding: utf-8 -*-
"""QMT 持仓快照 → Portfolio 账本同步测试。"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest

from src.repositories.portfolio_repo import PortfolioRepository
from src.repositories.qmt_position_repo import QmtPositionRepository
from src.services.portfolio_qmt_sync import PortfolioQmtSyncService
from src.services.portfolio_service import PortfolioService
from src.storage import DatabaseManager


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def _report(db: DatabaseManager, account: str, positions: list[dict]) -> None:
    QmtPositionRepository(db).replace(account=account, positions=positions)


def _pos(symbol: str, volume: float, open_price: float = 100.0,
         float_profit: float | None = None, name: str = "样例转债") -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "volume": volume,
        "can_use_volume": volume,
        "open_price": open_price,
        "float_profit": float_profit,
    }


def _account_id(db: DatabaseManager, name: str) -> int | None:
    for account in PortfolioRepository(db).list_accounts():
        if account.name == name:
            return account.id
    return None


def _sync_trades(db: DatabaseManager, account_id: int) -> list:
    return [t for t in PortfolioRepository(db).list_trades(account_id, as_of=date.today())
            if (t.note or "").startswith("qmt_sync:")]


def _cash_notes(db: DatabaseManager, account_id: int) -> set[str]:
    ledger = PortfolioRepository(db).list_cash_ledger(account_id, as_of=date.today())
    return {entry.note for entry in ledger if entry.note}


def test_first_sync_creates_account_and_buys_with_cash_balancing(db_manager: DatabaseManager) -> None:
    _report(db_manager, "135129739", [_pos("113001", 20, 100.0), _pos("110077", 10, 120.0, name="洪城转债")])

    result = PortfolioQmtSyncService(db_manager).sync()

    assert len(result["accounts"]) == 1
    acct = result["accounts"][0]
    assert acct["qmt_account"] == "135129739"
    assert acct["account_created"] is True
    assert acct["account_name"] == "135129739"
    assert acct["inserted_count"] == 2
    assert acct["failed_count"] == 0
    assert {e["side"] for e in acct["events"]} == {"buy"}

    trades = _sync_trades(db_manager, acct["account_id"])
    assert len(trades) == 2
    by_symbol = {t.symbol: t for t in trades}
    assert by_symbol["113001"].quantity == pytest.approx(20.0)
    assert by_symbol["113001"].price == pytest.approx(100.0)
    assert by_symbol["113001"].market == "cn"

    # 每笔 buy 配平等额现金 in，现金余额恒 0
    assert len(_cash_notes(db_manager, acct["account_id"])) == 2
    snapshot = PortfolioService(repo=PortfolioRepository(db_manager)).get_portfolio_snapshot(
        account_id=acct["account_id"], cost_method="fifo"
    )
    cash = snapshot.get("cash_balances") or snapshot.get("cash")
    total_cash = sum(float(v) for v in cash.values()) if isinstance(cash, dict) else 0.0
    assert total_cash == pytest.approx(0.0)


def test_incremental_sync_and_clear(db_manager: DatabaseManager) -> None:
    service = PortfolioQmtSyncService(db_manager)
    _report(db_manager, "testS", [_pos("113001", 20, 100.0), _pos("110077", 10, 120.0)])
    first = service.sync()["accounts"][0]
    account_id = first["account_id"]

    # 加仓 10 张、减仓 5 张（浮盈 50 → 现价近似 120+50/5=130）
    _report(db_manager, "testS", [
        _pos("113001", 30, 100.0),
        _pos("110077", 5, 120.0, float_profit=50.0),
    ])
    second = service.sync()["accounts"][0]
    events = {(e["side"], e["symbol"]): e for e in second["events"]}
    assert events[("buy", "113001")]["quantity"] == pytest.approx(10.0)
    assert events[("sell", "110077")]["price"] == pytest.approx(130.0)
    assert second["inserted_count"] == 2

    # 标的全部消失 → 全量清仓（按最近同步价）
    _report(db_manager, "testS", [])
    third = service.sync()["accounts"][0]
    sells = [e for e in third["events"] if e["side"] == "sell"]
    assert {e["symbol"] for e in sells} == {"113001", "110077"}
    assert sells[0]["status"] == "inserted"

    # 清仓后账本净量归零
    net: dict[str, float] = {}
    for trade in _sync_trades(db_manager, account_id):
        net[trade.symbol] = net.get(trade.symbol, 0.0) + (
            trade.quantity if trade.side == "buy" else -trade.quantity
        )
    assert all(abs(qty) < 1e-6 for qty in net.values())


def test_repeated_sync_is_idempotent(db_manager: DatabaseManager) -> None:
    service = PortfolioQmtSyncService(db_manager)
    _report(db_manager, "testS", [_pos("113001", 20, 100.0)])
    first = service.sync()["accounts"][0]
    assert first["inserted_count"] == 1

    again = service.sync()["accounts"][0]
    assert again["inserted_count"] == 0
    assert again["events"] == []
    assert again["account_created"] is False  # 复用已建账户
    assert len(_sync_trades(db_manager, first["account_id"])) == 1


def test_manual_trades_never_touched_by_diff(db_manager: DatabaseManager) -> None:
    db = db_manager
    _report(db, "testS", [_pos("113001", 20, 100.0)])
    portfolio = PortfolioService(repo=PortfolioRepository(db))
    manual = portfolio.create_account(name="手工账户", broker=None, market="cn", base_currency="CNY")
    manual_id = int(manual["id"])
    portfolio.record_trade(
        account_id=manual_id, symbol="600519", trade_date=date.today(),
        side="buy", quantity=100, price=50.0,
    )

    # QMT 账户自动创建，与手工账户互不影响；快照里没有 600519，diff 不生成卖出
    result = PortfolioQmtSyncService(db).sync()["accounts"][0]
    assert result["account_id"] != manual_id
    symbols = {e["symbol"] for e in result["events"]}
    assert symbols == {"113001"}

    manual_trades = PortfolioRepository(db).list_trades(manual_id, as_of=date.today())
    assert len(manual_trades) == 1
    assert manual_trades[0].symbol == "600519"

    # 同步账户内手工录入的同标的交易也不会被清：QMT 快照清空只清同步净量
    sync_account_id = result["account_id"]
    portfolio.record_trade(
        account_id=sync_account_id, symbol="113001", trade_date=date.today(),
        side="buy", quantity=10, price=99.0,
    )
    _report(db, "testS", [])
    cleared = PortfolioQmtSyncService(db).sync()["accounts"][0]
    sell = next(e for e in cleared["events"] if e["side"] == "sell")
    assert sell["quantity"] == pytest.approx(20.0)  # 只清同步净量，不含手工 10 张


def test_explicit_account_id_targeting_and_missing_account(db_manager: DatabaseManager) -> None:
    db = db_manager
    _report(db, "testS", [_pos("113001", 20, 100.0)])
    portfolio = PortfolioService(repo=PortfolioRepository(db))
    existing = portfolio.create_account(name="已有账户", broker=None, market="cn", base_currency="CNY")
    existing_id = int(existing["id"])

    result = PortfolioQmtSyncService(db).sync(account_id=existing_id)
    acct = result["accounts"][0]
    assert acct["account_id"] == existing_id
    assert acct["account_created"] is False
    assert acct["inserted_count"] == 1

    missing = PortfolioQmtSyncService(db).sync(account_id=99999)
    assert missing["accounts"][0]["error"] is not None
    assert missing["accounts"][0]["failed_count"] == 1


def test_dry_run_does_not_persist(db_manager: DatabaseManager) -> None:
    db = db_manager
    _report(db, "testS", [_pos("113001", 20, 100.0)])

    result = PortfolioQmtSyncService(db).sync(dry_run=True)

    acct = result["accounts"][0]
    assert result["dry_run"] is True
    assert acct["account_id"] is None
    assert acct["account_created"] is True  # 标记同步时将创建
    assert acct["inserted_count"] == 1
    assert acct["events"][0]["status"] == "dry_run"
    assert PortfolioRepository(db).list_accounts() == []
    assert PortfolioRepository(db).list_trades(1, as_of=date.today()) == []


def test_missing_price_reported_as_failed(db_manager: DatabaseManager) -> None:
    _report(db_manager, "testS", [{
        "symbol": "113001", "name": "样例转债", "volume": 20,
        "can_use_volume": 20, "open_price": None, "float_profit": None,
    }])

    result = PortfolioQmtSyncService(db_manager).sync()["accounts"][0]

    assert result["inserted_count"] == 0
    assert result["failed_count"] == 1
    assert "open_price" in result["events"][0]["error"]


def test_sync_endpoint_contract(db_manager: DatabaseManager) -> None:
    from fastapi.testclient import TestClient

    from api.app import create_app

    # 端点内 PortfolioQmtSyncService() 走 DatabaseManager.get_instance()，
    # 先建好内存库单例即可被端点复用
    app = create_app(static_dir=Path(tempfile.mkdtemp()))
    client = TestClient(app)

    _report(db_manager, "testS", [_pos("113001", 20, 100.0)])
    resp = client.post("/api/v1/portfolio/imports/qmt/sync", json={"dry_run": True})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["dry_run"] is True
    assert payload["accounts"][0]["events"][0]["status"] == "dry_run"

    committed = client.post("/api/v1/portfolio/imports/qmt/sync", json={})
    assert committed.status_code == 200, committed.text
    body = committed.json()
    assert body["dry_run"] is False
    assert body["accounts"][0]["inserted_count"] == 1
    assert body["accounts"][0]["cash_entries"] == 1
