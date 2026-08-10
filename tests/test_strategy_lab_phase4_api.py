# -*- coding: utf-8 -*-
"""Strategy Lab phase 4 API tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

import src.auth as auth
from api.app import create_app
from src.config import Config
from src.storage import DatabaseManager


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


class StrategyLabPhase4ApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "strategy_lab_phase4_api.db"
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

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def test_signal_contract(self) -> None:
        account_resp = self.client.post(
            "/api/v1/portfolio/accounts",
            json={
                "name": "Strategy Lab",
                "broker": "paper",
                "market": "cn",
                "base_currency": "CNY",
            },
        )
        self.assertEqual(account_resp.status_code, 200, account_resp.text)
        account_id = account_resp.json()["id"]

        run_resp = self.client.post(
            "/api/v1/strategy-lab/runs",
            json={
                "strategy_id": "double-low",
                "market": "cn",
                "instrument_type": "convertible_bond",
                "start_date": "2024-01-02",
                "end_date": "2024-01-04",
                "initial_cash": 100000,
                "parameters": {"max_positions": 2},
            },
        )
        self.assertEqual(run_resp.status_code, 200, run_resp.text)
        run_id = run_resp.json()["id"]

        signal_resp = self.client.post(
            "/api/v1/strategy-lab/signals",
            json={"run_id": run_id, "suggested_action": "buy", "confidence": 0.88},
        )
        self.assertEqual(signal_resp.status_code, 200, signal_resp.text)
        self.assertEqual(signal_resp.json()["suggested_action"], "buy")
        self.assertEqual(signal_resp.json()["symbol"], "113002")
        signal_id = signal_resp.json()["id"]

        confirm_resp = self.client.post(
            f"/api/v1/strategy-lab/signals/{signal_id}/confirm",
            json={
                "portfolio_account_id": account_id,
                "trade_date": "2024-01-05",
                "quantity": 10,
                "price": 100,
            },
        )
        self.assertEqual(confirm_resp.status_code, 200, confirm_resp.text)
        self.assertEqual(confirm_resp.json()["status"], "confirmed")
        self.assertIsNotNone(confirm_resp.json()["portfolio_trade_id"])

        trades_resp = self.client.get(f"/api/v1/portfolio/trades?account_id={account_id}")
        self.assertEqual(trades_resp.status_code, 200, trades_resp.text)
        self.assertEqual(trades_resp.json()["total"], 1)
