# -*- coding: utf-8 -*-
"""持仓展示元数据（行业/分类/买入逻辑/备注）服务与 API 测试。"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.app import create_app
from api.deps import get_database_manager
from src.repositories.portfolio_repo import PortfolioRepository
from src.services.portfolio_service import PortfolioService
from src.storage import DatabaseManager, StrategyLabCbBasic


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


def _record_buy(portfolio: PortfolioService, account_id: int, symbol: str, *, trade_uid: str = None) -> None:
    portfolio.record_trade(
        account_id=account_id,
        symbol=symbol,
        trade_date=date(2026, 9, 12),
        side="buy",
        quantity=10,
        price=100,
        market="cn",
        currency="CNY",
        trade_uid=trade_uid,
    )


def _add_cb_basic(db_manager: DatabaseManager, bond_code: str, stock_code: str) -> None:
    with db_manager.get_session() as session:
        session.add(
            StrategyLabCbBasic(
                bond_code=bond_code,
                bond_name=f"{bond_code}转债",
                stock_code=stock_code,
                stock_name="测试正股",
                market="cn",
                source="unit-test",
            )
        )
        session.commit()


def test_snapshot_positions_carry_meta_defaults(db_manager: DatabaseManager) -> None:
    portfolio = PortfolioService(repo=PortfolioRepository(db_manager))
    manual = portfolio.create_account(name="Manual", broker="Demo", market="cn", base_currency="CNY")
    qmt = portfolio.create_account(name="Qmt", broker="Demo", market="cn", base_currency="CNY")
    for symbol in ("600519", "513010", "021583.OF", "110077"):
        _record_buy(portfolio, int(manual["id"]), symbol)
    _record_buy(portfolio, int(qmt["id"]), "113050", trade_uid="qmt:TEST:113050:20260912:1")
    _add_cb_basic(db_manager, "110077", "600061")

    snapshot = portfolio.get_portfolio_snapshot(include_realtime=False)
    by_account = {a["account_name"]: a for a in snapshot["accounts"]}

    manual_positions = {p["symbol"]: p for p in by_account["Manual"]["positions"]}
    assert manual_positions["600519"]["category"] == "stock"
    assert manual_positions["513010"]["category"] == "etf"
    assert manual_positions["021583.OF"]["category"] == "fund"
    assert manual_positions["110077"]["category"] == "convertible_bond"
    assert all(p["buy_logic"] == "undefined" for p in manual_positions.values())
    assert all(p["industry"] is None for p in manual_positions.values())

    qmt_positions = {p["symbol"]: p for p in by_account["Qmt"]["positions"]}
    _add_cb_basic(db_manager, "113050", "601985")
    snapshot = portfolio.get_portfolio_snapshot(include_realtime=False)
    by_account = {a["account_name"]: a for a in snapshot["accounts"]}
    qmt_positions = {p["symbol"]: p for p in by_account["Qmt"]["positions"]}
    assert qmt_positions["113050"]["category"] == "convertible_bond"
    assert qmt_positions["113050"]["buy_logic"] == "automated"


def test_update_position_meta_upserts_and_propagates_industry(db_manager: DatabaseManager) -> None:
    portfolio = PortfolioService(repo=PortfolioRepository(db_manager))
    account_a = portfolio.create_account(name="A", broker="Demo", market="cn", base_currency="CNY")
    account_b = portfolio.create_account(name="B", broker="Demo", market="cn", base_currency="CNY")
    _record_buy(portfolio, int(account_a["id"]), "600519")
    _record_buy(portfolio, int(account_b["id"]), "600519")

    row = portfolio.update_position_meta(
        account_id=int(account_a["id"]),
        symbol="600519",
        updates={"industry": "食品饮料", "buy_logic": "dividend", "note": "核心持仓"},
    )
    assert row["industry"] == "食品饮料"
    assert row["buy_logic"] == "dividend"

    repo = PortfolioRepository(db_manager)
    meta_a = repo.get_position_meta_map(int(account_a["id"]))
    meta_b = repo.get_position_meta_map(int(account_b["id"]))
    assert meta_a["600519"].industry == "食品饮料"
    assert meta_b["600519"].industry == "食品饮料"  # 行业按代码全局共享
    assert meta_b["600519"].buy_logic is None  # 其余字段不跨账户传播

    # 清除字段 + 非法枚举校验
    portfolio.update_position_meta(
        account_id=int(account_a["id"]), symbol="600519", updates={"note": ""}
    )
    assert repo.get_position_meta_map(int(account_a["id"]))["600519"].note is None
    row = portfolio.update_position_meta(
        account_id=int(account_a["id"]), symbol="600519", updates={"buy_logic": "value"}
    )
    assert row["buy_logic"] == "value"  # 「价值」枚举可用
    with pytest.raises(ValueError):
        portfolio.update_position_meta(
            account_id=int(account_a["id"]), symbol="600519", updates={"category": "warrant"}
        )
    with pytest.raises(ValueError):
        portfolio.update_position_meta(
            account_id=int(account_a["id"]), symbol="600519", updates={"buy_logic": "yolo"}
        )


def test_autofill_fills_industry_for_stocks_and_cb_underlying(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.services.portfolio_risk_service as risk_module

    industries = {"600519": "食品饮料", "600061": "建材"}

    def fake_fetch(symbol, market, *, data_manager=None):
        return industries.get(symbol)

    monkeypatch.setattr(risk_module, "fetch_industry_name", fake_fetch)

    portfolio = PortfolioService(repo=PortfolioRepository(db_manager))
    account = portfolio.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
    for symbol in ("600519", "110077", "021583.OF", "513010"):
        _record_buy(portfolio, int(account["id"]), symbol)
    _add_cb_basic(db_manager, "110077", "600061")
    # 513010 预置人工行业，autofill 不应覆盖
    portfolio.update_position_meta(
        account_id=int(account["id"]), symbol="513010", updates={"industry": "人工指定"}
    )

    result = portfolio.autofill_position_meta()

    assert result["positions_scanned"] == 4
    assert result["industry_filled"] == 2  # 股票 + 转债（按正股），基金/ETF 跳过
    meta = PortfolioRepository(db_manager).get_position_meta_map(int(account["id"]))
    assert meta["600519"].industry == "食品饮料"
    assert meta["110077"].industry == "建材"  # 转债按正股行业
    assert "021583.of" not in meta or meta["021583.of"].industry is None  # 基金不建行
    assert meta["513010"].industry == "人工指定"  # 人工值不被覆盖


def test_position_meta_api_roundtrip(db_manager: DatabaseManager, client: TestClient) -> None:
    portfolio = PortfolioService(repo=PortfolioRepository(db_manager))
    account = portfolio.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
    _record_buy(portfolio, int(account["id"]), "600519")

    response = client.put(
        "/api/v1/portfolio/position-meta",
        json={
            "account_id": int(account["id"]),
            "symbol": "600519",
            "industry": "食品饮料",
            "buy_logic": "dividend",
            "note": "打新底仓",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["industry"] == "食品饮料"
    assert body["buy_logic"] == "dividend"

    snapshot = client.get(
        "/api/v1/portfolio/snapshot", params={"include_realtime": "false"}
    ).json()
    position = next(
        p
        for a in snapshot["accounts"]
        for p in a["positions"]
        if p["symbol"] == "600519"
    )
    assert position["industry"] == "食品饮料"
    assert position["category"] == "stock"
    assert position["buy_logic"] == "dividend"
    assert position["note"] == "打新底仓"

    bad = client.put(
        "/api/v1/portfolio/position-meta",
        json={"account_id": int(account["id"]), "symbol": "600519", "category": "warrant"},
    )
    assert bad.status_code == 400
