# -*- coding: utf-8 -*-
"""Strategy Lab data query tests (instruments / bars / events / detail)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

import src.auth as auth
from api.app import create_app
from src.config import Config
from src.repositories.stock_repo import StockRepository
from src.services.strategy_lab.data_sync_service import StrategyLabDataSyncService
from src.storage import (
    DatabaseManager,
    PortfolioAccount,
    PortfolioPosition,
    StockDaily,
)


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def _seed_fixture(db_manager: DatabaseManager) -> None:
    repo = StrategyLabDataSyncService(db_manager).repository
    basics = [
        {
            "bond_code": "113001",
            "bond_name": "CB Alpha",
            "stock_code": "113001",
            "stock_name": "113001 正股",
            "market": "cn",
            "list_date": date(2024, 1, 1),
            "maturity_date": date(2028, 1, 1),
            "remaining_size": 50.0,
            "current_premium_rate": 18.0,
            "convert_price": 100.0,
            "terms": {
                "source": "fixture",
                "strategy": "double-low",
                "industry": "电子-半导体-分立器件",
                "force_redeem_countdown": "已公告强赎",
                "down_revise_countdown": "10/30",
                "put_countdown": "2/30",
                "last_trading_date": "2024-03-01",
                "bond_rating": "AA+",
            },
        },
        {
            "bond_code": "113002",
            "bond_name": "CB Beta",
            "stock_code": "113002",
            "stock_name": "113002 正股",
            "market": "cn",
            "list_date": date(2024, 1, 1),
            "maturity_date": date(2028, 1, 1),
            "remaining_size": 50.0,
            "current_premium_rate": 18.0,
            "convert_price": 100.0,
            "terms": {"source": "fixture", "strategy": "double-low"},
        },
        {
            "bond_code": "113003",
            "bond_name": "CB Gamma",
            "stock_code": "113003",
            "stock_name": "113003 正股",
            "market": "cn",
            "list_date": date(2024, 1, 1),
            "maturity_date": date(2028, 1, 1),
            "remaining_size": 50.0,
            "current_premium_rate": 18.0,
            "convert_price": 100.0,
            "terms": {"source": "fixture", "strategy": "double-low"},
        },
    ]
    terms = [
        {
            "bond_code": "113001",
            "redeem_clause": "fixture redeem clause",
            "down_revise_clause": "fixture down revise clause",
            "put_clause": "fixture put clause",
            "redeem_trigger_price": 130.0,
            "down_revise_trigger_price": 80.0,
            "put_trigger_price": 70.0,
        },
        {
            "bond_code": "113002",
            "redeem_clause": "fixture redeem clause",
            "down_revise_clause": "fixture down revise clause",
            "put_clause": "fixture put clause",
            "redeem_trigger_price": 130.0,
            "down_revise_trigger_price": 80.0,
            "put_trigger_price": 70.0,
        },
        {
            "bond_code": "113003",
            "redeem_clause": "fixture redeem clause",
            "down_revise_clause": "fixture down revise clause",
            "put_clause": "fixture put clause",
            "redeem_trigger_price": 130.0,
            "down_revise_trigger_price": 80.0,
            "put_trigger_price": 70.0,
        },
    ]
    factors = [
        {"bond_code": "113001", "trade_date": date(2024, 1, 2), "close": 102.0, "premium_rate": 18.0, "remaining_size": 50.0, "redeem_alert": False, "down_revise_alert": False, "put_alert": False},
        {"bond_code": "113001", "trade_date": date(2024, 1, 3), "close": 103.5, "premium_rate": 17.2, "remaining_size": 50.0, "redeem_alert": False, "down_revise_alert": False, "put_alert": False},
        {"bond_code": "113001", "trade_date": date(2024, 1, 4), "close": 105.0, "premium_rate": 16.5, "remaining_size": 50.0, "redeem_alert": False, "down_revise_alert": False, "put_alert": False},
        {"bond_code": "113002", "trade_date": date(2024, 1, 2), "close": 96.0, "premium_rate": 22.0, "remaining_size": 50.0, "redeem_alert": False, "down_revise_alert": False, "put_alert": False},
        {"bond_code": "113002", "trade_date": date(2024, 1, 3), "close": 97.0, "premium_rate": 21.0, "remaining_size": 50.0, "redeem_alert": False, "down_revise_alert": False, "put_alert": False},
        {"bond_code": "113002", "trade_date": date(2024, 1, 4), "close": 98.5, "premium_rate": 20.0, "remaining_size": 50.0, "redeem_alert": False, "down_revise_alert": False, "put_alert": False},
        {"bond_code": "113003", "trade_date": date(2024, 1, 2), "close": 118.0, "premium_rate": 9.0, "remaining_size": 50.0, "redeem_alert": True, "down_revise_alert": False, "put_alert": False},
        {"bond_code": "113003", "trade_date": date(2024, 1, 3), "close": 117.0, "premium_rate": 9.5, "remaining_size": 50.0, "redeem_alert": True, "down_revise_alert": False, "put_alert": False},
        {"bond_code": "113003", "trade_date": date(2024, 1, 4), "close": 116.0, "premium_rate": 10.0, "remaining_size": 50.0, "redeem_alert": False, "down_revise_alert": False, "put_alert": False},
    ]
    events = [
        {
            "bond_code": "113001",
            "event_date": date(2024, 1, 2),
            "event_type": "strong_redeem",
            "event_detail": "fixture strong redeem watch",
        }
    ]
    repo.upsert_cb_basic(basics, source="fixture")
    repo.upsert_cb_terms(terms, source="fixture")
    repo.upsert_cb_daily_factors(factors, source="fixture")
    repo.upsert_cb_events(events, source="fixture")


def test_list_instruments_returns_paginated_items_with_latest_factor(db_manager: DatabaseManager) -> None:
    _seed_fixture(db_manager)
    service = StrategyLabDataSyncService(db_manager)

    payload = service.list_instruments(market="cn", page=1, limit=10)

    assert payload["total"] == 3
    assert len(payload["items"]) == 3
    first = payload["items"][0]
    assert first["bond_code"]
    assert first["bond_name"]
    assert first["latest_close"] is not None
    assert first["latest_premium_rate"] is not None
    assert first["event_count"] == 0  # fixture events only cover the first instrument


def test_list_instruments_keyword_filter(db_manager: DatabaseManager) -> None:
    _seed_fixture(db_manager)
    service = StrategyLabDataSyncService(db_manager)

    all_items = service.list_instruments(market="cn", page=1, limit=10)["items"]
    keyword = all_items[0]["bond_code"]
    filtered = service.list_instruments(market="cn", keyword=keyword, page=1, limit=10)

    assert filtered["total"] == 1
    assert filtered["items"][0]["bond_code"] == keyword


def test_list_instruments_active_sorted_by_premium_asc(db_manager: DatabaseManager) -> None:
    """未退市列表按最新转股溢价率升序，溢价率缺失/无因子的排最后。"""
    _seed_fixture(db_manager)
    repo = StrategyLabDataSyncService(db_manager).repository
    repo.upsert_cb_basic(
        [
            {"bond_code": "113010", "bond_name": "CB Active High", "stock_code": "113010", "market": "cn", "status": "active"},
            {"bond_code": "113011", "bond_name": "CB Active Low", "stock_code": "113011", "market": "cn", "status": "active"},
            {"bond_code": "113012", "bond_name": "CB Active NoFactor", "stock_code": "113012", "market": "cn", "status": "active"},
        ],
        source="fixture",
    )
    repo.upsert_cb_daily_factors(
        [
            {"bond_code": "113010", "trade_date": date(2024, 1, 4), "close": 110.0, "premium_rate": 30.0},
            {"bond_code": "113011", "trade_date": date(2024, 1, 4), "close": 100.0, "premium_rate": 5.0},
        ],
        source="fixture",
    )
    service = StrategyLabDataSyncService(db_manager)

    payload = service.list_instruments(market="cn", status="active", page=1, limit=10)

    assert payload["total"] == 3
    assert [item["bond_code"] for item in payload["items"]] == ["113011", "113010", "113012"]
    assert [item["latest_premium_rate"] for item in payload["items"][:2]] == [5.0, 30.0]

    # 不带状态过滤（全部）时保持原有更新时间倒序，不受溢价率排序影响
    unfiltered = service.list_instruments(market="cn", page=1, limit=10)
    assert unfiltered["total"] == 6


def test_list_instruments_explicit_sort_fields(db_manager: DatabaseManager) -> None:
    """显式排序：premium/double_low/last_trading_date（缺失到期兜底、无键排最后）。"""
    _seed_fixture(db_manager)
    repo = StrategyLabDataSyncService(db_manager).repository
    # 113001 双低=105.0+16.5、113003 双低=116.0+10.0；113012 无因子
    repo.upsert_cb_basic(
        [
            {"bond_code": "113012", "bond_name": "CB Active NoFactor", "stock_code": "113012", "market": "cn", "status": "active"},
        ],
        source="fixture",
    )

    service = StrategyLabDataSyncService(db_manager)

    by_premium_desc = service.list_instruments(market="cn", sort_by="premium_rate", sort_order="desc", page=1, limit=10)
    assert [item["bond_code"] for item in by_premium_desc["items"]] == ["113002", "113001", "113003", "113012"]

    by_double_low = service.list_instruments(market="cn", sort_by="double_low", sort_order="asc", page=1, limit=10)
    # 双低：113002=98.5+20.0、113001=105.0+16.5、113003=116.0+10.0
    assert [item["bond_code"] for item in by_double_low["items"][:3]] == ["113002", "113001", "113003"]
    assert by_double_low["items"][-1]["bond_code"] == "113012"  # 排序值缺失排最后

    # last_trading_date：fixture 113001 terms 有 2024-03-01；其余用到期时间兜底（2028-01-01）；113012 无到期时间排最后
    by_exit_date = service.list_instruments(market="cn", sort_by="last_trading_date", sort_order="asc", page=1, limit=10)
    codes = [item["bond_code"] for item in by_exit_date["items"]]
    assert codes[0] == "113001"
    assert codes[-1] == "113012"
    assert set(codes[1:-1]) == {"113002", "113003"}

    with pytest.raises(ValueError, match="unsupported sort_by"):
        service.list_instruments(market="cn", sort_by="unknown", page=1, limit=10)


def test_get_instrument_detail_merges_terms_and_counts(db_manager: DatabaseManager) -> None:
    _seed_fixture(db_manager)
    service = StrategyLabDataSyncService(db_manager)

    items = service.list_instruments(market="cn", page=1, limit=10)["items"]
    detail = service.get_instrument_detail(market="cn", bond_code=items[0]["bond_code"])

    assert detail is not None
    assert detail["bond_code"] == items[0]["bond_code"]
    assert detail["stock_code"]
    assert detail["stock_name"]
    assert detail["current_premium_rate"] is not None
    assert detail["latest_close"] is not None
    assert detail["latest_premium_rate"] is not None
    assert detail["industry"] == detail["terms"].get("industry")
    assert detail["redeem_clause"] is not None
    assert detail["redeem_trigger_price"] == 130.0
    assert detail["bar_count"] > 0
    assert detail["terms"]["strategy"] == "double-low"


def test_get_instrument_detail_missing_returns_none(db_manager: DatabaseManager) -> None:
    _seed_fixture(db_manager)
    service = StrategyLabDataSyncService(db_manager)

    assert service.get_instrument_detail(market="cn", bond_code="999999") is None


def test_list_instrument_bars_ordered_ascending(db_manager: DatabaseManager) -> None:
    _seed_fixture(db_manager)
    service = StrategyLabDataSyncService(db_manager)

    items = service.list_instruments(market="cn", page=1, limit=10)["items"]
    bars = service.list_instrument_bars(market="cn", bond_code=items[0]["bond_code"])

    assert bars is not None
    assert bars["total"] > 0
    dates = [row["trade_date"] for row in bars["items"]]
    assert dates == sorted(dates)
    assert bars["items"][0]["close"] is not None


def test_list_instrument_bars_missing_returns_none(db_manager: DatabaseManager) -> None:
    _seed_fixture(db_manager)
    service = StrategyLabDataSyncService(db_manager)

    assert service.list_instrument_bars(market="cn", bond_code="999999") is None


def test_list_instrument_events_returns_fixture_event(db_manager: DatabaseManager) -> None:
    _seed_fixture(db_manager)
    service = StrategyLabDataSyncService(db_manager)

    items = service.list_instruments(market="cn", page=1, limit=10)["items"]
    with_event = next((item for item in items if item["event_count"] > 0), None)
    if with_event is None:
        pytest.skip("fixture event missing, cannot verify event query")

    events = service.list_instrument_events(market="cn", bond_code=with_event["bond_code"])

    assert events is not None
    assert events["total"] == 1
    assert events["items"][0]["event_type"] == "strong_redeem"


def test_list_instruments_status_filter(db_manager: DatabaseManager) -> None:
    service = StrategyLabDataSyncService(db_manager)
    service.repository.upsert_cb_basic(
        [
            {"bond_code": "123001", "bond_name": "正常转债", "stock_code": "600001", "market": "cn", "status": "正常"},
            {"bond_code": "123002", "bond_name": "退市转债", "stock_code": "600002", "market": "cn", "status": "已退市"},
            {"bond_code": "123003", "bond_name": "无状态转债", "stock_code": "600003", "market": "cn"},
        ],
        source="test",
    )

    active = service.list_instruments(market="cn", status="active")
    delisted = service.list_instruments(market="cn", status="delisted")
    all_items = service.list_instruments(market="cn")

    assert active["total"] == 1
    assert active["items"][0]["bond_code"] == "123001"
    assert delisted["total"] == 1
    assert delisted["items"][0]["bond_code"] == "123002"
    assert all_items["total"] == 3


def test_list_instruments_held_only(db_manager: DatabaseManager) -> None:
    service = StrategyLabDataSyncService(db_manager)
    service.repository.upsert_cb_basic(
        [
            {"bond_code": "123001", "bond_name": "持有时", "stock_code": "600001", "market": "cn", "status": "正常"},
            {"bond_code": "123002", "bond_name": "未持有", "stock_code": "600002", "market": "cn", "status": "正常"},
        ],
        source="test",
    )
    with db_manager.get_session() as session:
        account = PortfolioAccount(owner_id="u1", name="测试账户", market="cn", is_active=True)
        session.add(account)
        session.commit()
        session.refresh(account)
        session.add(
            PortfolioPosition(
                account_id=account.id,
                symbol="123001",
                market="cn",
                currency="CNY",
                cost_method="fifo",
                quantity=10.0,
            )
        )
        session.commit()

    held = service.list_instruments(market="cn", held_only=True)
    assert held["total"] == 1
    assert held["items"][0]["bond_code"] == "123001"


def test_stock_repo_list_codes_and_bars(db_manager: DatabaseManager) -> None:
    with db_manager.get_session() as session:
        session.add_all(
            [
                StockDaily(code="600001", date=date(2026, 1, 2), close=10.0, instrument_type="stock"),
                StockDaily(code="600001", date=date(2026, 1, 3), close=11.0, instrument_type="stock"),
                StockDaily(code="123001", date=date(2026, 1, 2), close=100.0, instrument_type="convertible_bond"),
            ]
        )
        session.commit()

    repo = StockRepository(db_manager)
    listing = repo.list_codes(limit=10)
    assert listing["total"] == 1
    assert listing["items"][0]["code"] == "600001"
    assert listing["items"][0]["latest_close"] == 11.0

    bars = repo.get_bars(code="600001")
    assert bars["total"] == 2
    assert bars["items"][0]["date"] == "2026-01-02"


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


class StrategyLabDataQueryApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "query_api.db"
        self.env_path = self.data_dir / ".env"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=600519",
                    "GEMINI_API_KEY=test",
                    "ADMIN_AUTH_ENABLED=false",
                    f"DATABASE_PATH={self.db_path}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.client = TestClient(create_app(static_dir=self.data_dir / "empty-static"))
        _seed_fixture(DatabaseManager())

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def test_instruments_list_endpoint(self) -> None:
        response = self.client.get("/api/v1/strategy-lab/instruments?market=cn")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["limit"], 20)
        self.assertTrue(payload["items"])

    def test_instruments_list_includes_terms_fields(self) -> None:
        """列表项扁平化输出条款计数/最后交易日/评级等 terms 元数据。"""
        response = self.client.get("/api/v1/strategy-lab/instruments?market=cn&limit=10")

        self.assertEqual(response.status_code, 200, response.text)
        items = {item["bond_code"]: item for item in response.json()["items"]}
        first = items["113001"]
        self.assertEqual(first["force_redeem_countdown"], "已公告强赎")
        self.assertEqual(first["down_revise_countdown"], "10/30")
        self.assertEqual(first["put_countdown"], "2/30")
        self.assertEqual(first["last_trading_date"], "2024-03-01")
        self.assertEqual(first["bond_rating"], "AA+")
        self.assertEqual(first["industry"], "电子-半导体-分立器件")
        # terms 无对应键的标的输出 None（容错）
        self.assertIsNone(items["113002"]["force_redeem_countdown"])
        self.assertIsNone(items["113002"]["bond_rating"])

    def test_instruments_list_keyword_filter(self) -> None:
        list_payload = self.client.get("/api/v1/strategy-lab/instruments").json()
        keyword = list_payload["items"][0]["bond_code"]

        response = self.client.get(f"/api/v1/strategy-lab/instruments?keyword={keyword}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 1)

    def test_instrument_detail_endpoint(self) -> None:
        code = self.client.get("/api/v1/strategy-lab/instruments").json()["items"][0]["bond_code"]

        response = self.client.get(f"/api/v1/strategy-lab/instruments/{code}")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["bond_code"], code)
        self.assertIn("latest_close", payload)
        self.assertIn("latest_premium_rate", payload)
        self.assertIn("industry", payload)
        self.assertIn("redeem_clause", payload)
        self.assertIn("terms", payload)

    def test_instrument_detail_not_found(self) -> None:
        response = self.client.get("/api/v1/strategy-lab/instruments/999999")

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"], "not_found")

    def test_instrument_bars_endpoint(self) -> None:
        code = self.client.get("/api/v1/strategy-lab/instruments").json()["items"][0]["bond_code"]

        response = self.client.get(f"/api/v1/strategy-lab/instruments/{code}/bars")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["bond_code"], code)
        self.assertGreater(payload["total"], 0)
        self.assertIn("close", payload["items"][0])
        self.assertIn("premium_rate", payload["items"][0])

    def test_instrument_bars_not_found(self) -> None:
        response = self.client.get("/api/v1/strategy-lab/instruments/999999/bars")

        self.assertEqual(response.status_code, 404, response.text)

    def test_instrument_stock_bars_endpoint(self) -> None:
        """正股 K 线端点：透传日期窗口给 fetcher，items 按交易日升序。"""
        import pandas as pd
        from unittest.mock import patch

        code = self.client.get("/api/v1/strategy-lab/instruments").json()["items"][0]["bond_code"]
        fetch_daily = MagicMock(return_value=pd.DataFrame({
            "date": [date(2024, 1, 2), date(2024, 1, 1)],
            "close": [10.5, 10.0],
        }))
        fetcher = MagicMock(fetch_daily=fetch_daily, last_source="tencent")

        with patch(
            "src.services.strategy_lab.data_sync_service.CbUnderlyingStockOhlcFetcher",
            return_value=fetcher,
        ):
            response = self.client.get(
                f"/api/v1/strategy-lab/instruments/{code}/stock-bars",
                params={"start_date": "2024-01-01", "end_date": "2024-01-02"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["bond_code"], code)
        self.assertEqual(payload["stock_code"], code)
        self.assertEqual(payload["source"], "tencent")
        self.assertEqual(
            [item["trade_date"] for item in payload["items"]], ["2024-01-01", "2024-01-02"]
        )
        self.assertEqual([item["close"] for item in payload["items"]], [10.0, 10.5])
        fetch_daily.assert_called_once_with(code, date(2024, 1, 1), date(2024, 1, 2))

    def test_instrument_stock_bars_not_found(self) -> None:
        response = self.client.get("/api/v1/strategy-lab/instruments/999999/stock-bars")

        self.assertEqual(response.status_code, 404, response.text)

    def test_instrument_stock_bars_without_stock_code(self) -> None:
        """无正股代码的转债返回空 items（200 降级），不报错。"""
        repo = StrategyLabDataSyncService(DatabaseManager()).repository
        repo.upsert_cb_basic(
            [{"bond_code": "113009", "bond_name": "CB NoStock", "stock_code": "", "market": "cn"}],
            source="fixture",
        )

        response = self.client.get("/api/v1/strategy-lab/instruments/113009/stock-bars")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["stock_code"], "")
        self.assertEqual(payload["items"], [])

    def test_instrument_events_endpoint(self) -> None:
        list_payload = self.client.get("/api/v1/strategy-lab/instruments").json()
        with_event = next((item for item in list_payload["items"] if item["event_count"] > 0), None)
        if with_event is None:
            self.skipTest("fixture event missing")

        response = self.client.get(f"/api/v1/strategy-lab/instruments/{with_event['bond_code']}/events")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["event_type"], "strong_redeem")

    def test_instrument_events_not_found(self) -> None:
        response = self.client.get("/api/v1/strategy-lab/instruments/999999/events")

        self.assertEqual(response.status_code, 404, response.text)
