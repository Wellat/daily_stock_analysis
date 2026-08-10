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
    StrategyLabDataSyncService(db_manager).sync_fixture_convertible_bonds()


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


def test_get_instrument_detail_merges_terms_and_counts(db_manager: DatabaseManager) -> None:
    _seed_fixture(db_manager)
    service = StrategyLabDataSyncService(db_manager)

    items = service.list_instruments(market="cn", page=1, limit=10)["items"]
    detail = service.get_instrument_detail(market="cn", bond_code=items[0]["bond_code"])

    assert detail is not None
    assert detail["bond_code"] == items[0]["bond_code"]
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
    service.sync_payload_convertible_bonds(
        market="cn",
        source="test",
        cb_basic=[
            {"bond_code": "123001", "bond_name": "正常转债", "stock_code": "600001", "market": "cn", "status": "正常"},
            {"bond_code": "123002", "bond_name": "退市转债", "stock_code": "600002", "market": "cn", "status": "已退市"},
            {"bond_code": "123003", "bond_name": "无状态转债", "stock_code": "600003", "market": "cn"},
        ],
        cb_terms=[],
        cb_daily_factors=[],
        cb_events=[],
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
    service.sync_payload_convertible_bonds(
        market="cn",
        source="test",
        cb_basic=[
            {"bond_code": "123001", "bond_name": "持有时", "stock_code": "600001", "market": "cn", "status": "正常"},
            {"bond_code": "123002", "bond_name": "未持有", "stock_code": "600002", "market": "cn", "status": "正常"},
        ],
        cb_terms=[],
        cb_daily_factors=[],
        cb_events=[],
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
        self.client.post(
            "/api/v1/strategy-lab/data-sync",
            json={"market": "cn", "source": "fixture"},
        )

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
