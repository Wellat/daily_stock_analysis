# -*- coding: utf-8 -*-
"""实盘可转债持仓聚合（GET /api/v1/trading/positions/cb）测试。"""

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.deps import get_database_manager
from src.services.qmt_cb_holdings_service import QmtCbHoldingsService
from src.services.qmt_position_service import QmtPositionService
from src.storage import (
    DatabaseManager,
    LiveStrategyConfig,
    StrategyLabCbBasic,
    StrategyLabCbDailyFactor,
)


def _seed_universe(db: DatabaseManager, trade_date: date) -> None:
    """两只转债（其一临近最后交易日、已公告强赎）+ 最新因子行。"""
    with db.get_session() as session:
        session.add(StrategyLabCbBasic(
            bond_code="113042", bond_name="上银转债", stock_code="601229", stock_name="上海银行",
            market="cn", status="active", maturity_date=date(2029, 6, 1), remaining_size=192.0,
            terms_json=json.dumps({"force_redeem_countdown": "10/30", "down_revise_countdown": "5/30",
                                   "put_countdown": None, "bond_rating": "AAA"}),
        ))
        session.add(StrategyLabCbBasic(
            bond_code="118027", bond_name="宏图转债", stock_code="688066", stock_name="*ST航图",
            market="cn", status="active", maturity_date=date(2027, 3, 1), remaining_size=2.5,
            terms_json=json.dumps({"force_redeem_countdown": "已公告强赎",
                                   "last_trading_date": (date.today() + timedelta(days=4)).isoformat()}),
        ))
        session.add(LiveStrategyConfig(name="default", qmt_account="135129739", enabled=True))
        session.commit()
        for code, close, premium in (("113042", 117.891, 0.24), ("118027", 106.247, 0.62)):
            session.add(StrategyLabCbDailyFactor(bond_code=code, trade_date=trade_date,
                                                 close=close, premium_rate=premium, remaining_size=5.0))
        session.commit()


def _report_positions(db: DatabaseManager) -> None:
    QmtPositionService(db).report_positions(account="135129739", positions=[
        {"symbol": "113042", "name": "上银转债", "volume": 60, "can_use_volume": 60, "open_price": 110.5},
        {"symbol": "118027", "name": "宏图转债", "volume": 40, "can_use_volume": 40, "open_price": 120.0},
        # 非转债持仓：应被过滤并计数
        {"symbol": "600519", "name": "贵州茅台", "volume": 100, "can_use_volume": 100, "open_price": 1500.0},
    ])
    QmtPositionService(db).report_positions(account="testS", positions=[
        {"symbol": "113042", "name": "上银转债", "volume": 10, "can_use_volume": 10, "open_price": 111.0},
    ])


@pytest.fixture()
def service():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    _seed_universe(db, date.today())
    _report_positions(db)
    try:
        yield QmtCbHoldingsService(db)
    finally:
        DatabaseManager.reset_instance()


def test_holdings_only_cb_of_account_with_enrichment(service: QmtCbHoldingsService):
    result = service.list_holdings()  # 缺省账户 → 实盘配置 135129739
    assert result["account"] == "135129739"
    assert result["excluded_count"] == 1  # 贵州茅台
    assert [item["symbol"] for item in result["items"]] == ["113042", "118027"]

    top = result["items"][0]  # 市值降序：60×117.891 > 40×106.247
    assert top["bond_name"] == "上银转债"
    assert top["stock_name"] == "上海银行"
    assert top["latest_close"] == pytest.approx(117.891)
    assert top["latest_premium_rate"] == pytest.approx(0.24)
    assert top["market_value"] == pytest.approx(60 * 117.891)
    assert top["pnl"] == pytest.approx((117.891 - 110.5) * 60)
    assert top["pnl_pct"] == pytest.approx((117.891 / 110.5 - 1) * 100)
    assert top["force_redeem_countdown"] == "10/30"
    assert top["down_revise_countdown"] == "5/30"
    assert top["bond_rating"] == "AAA"
    assert top["remaining_years"] == pytest.approx((date(2029, 6, 1) - date.today()).days / 365.0, abs=0.01)
    assert top["remaining_trading_days"] is None  # 无最后交易日 → 无倒计

    near = result["items"][1]
    assert near["force_redeem_countdown"] == "已公告强赎"
    assert near["last_trading_date"] == (date.today() + timedelta(days=4)).isoformat()
    assert near["remaining_trading_days"] is not None and 0 < near["remaining_trading_days"] <= 5

    # 汇总：持仓数量、市值、盈亏（按成本合计折算）
    summary = result["summary"]
    assert summary["count"] == 2
    assert summary["total_volume"] == pytest.approx(100.0)
    assert summary["total_market_value"] == pytest.approx(60 * 117.891 + 40 * 106.247, abs=0.01)
    expected_pnl = (117.891 - 110.5) * 60 + (106.247 - 120.0) * 40
    assert summary["total_pnl"] == pytest.approx(expected_pnl, abs=0.01)
    assert result["as_of_trade_date"] == date.today().isoformat()
    assert result["positions_updated_at"]


def test_holdings_weight_pct_and_explicit_account(service: QmtCbHoldingsService):
    result = service.list_holdings(account="testS")
    assert result["account"] == "testS"
    assert result["excluded_count"] == 0
    assert len(result["items"]) == 1
    assert result["items"][0]["weight_pct"] == pytest.approx(100.0)

    main = service.list_holdings(account="135129739")
    weights = {item["symbol"]: item["weight_pct"] for item in main["items"]}
    assert sum(weights.values()) == pytest.approx(100.0, abs=0.01)


def test_holdings_rejects_unknown_account(service: QmtCbHoldingsService):
    with pytest.raises(ValueError, match="account must be one of"):
        service.list_holdings(account="888888")


def test_cb_positions_endpoint(client_like=None) -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    _seed_universe(db, date.today())
    _report_positions(db)
    app = create_app(static_dir=Path(tempfile.mkdtemp()))
    app.dependency_overrides[get_database_manager] = lambda: db
    try:
        response = TestClient(app).get("/api/v1/trading/positions/cb")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["account"] == "135129739"
        assert payload["excluded_count"] == 1
        assert {item["symbol"] for item in payload["items"]} == {"113042", "118027"}
        assert payload["items"][0]["force_redeem_countdown"] == "10/30"

        bad = TestClient(app).get("/api/v1/trading/positions/cb", params={"account": "888888"})
        assert bad.status_code == 400
        assert "account must be one of" in bad.json()["message"]
    finally:
        DatabaseManager.reset_instance()
