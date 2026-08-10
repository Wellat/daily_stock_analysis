# -*- coding: utf-8 -*-
"""Strategy Lab API contract tests."""

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


class StrategyLabApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "strategy_lab_api.db"
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

    def test_strategy_lab_run_lifecycle(self) -> None:
        strategies = self.client.get("/api/v1/strategy-lab/strategies")
        self.assertEqual(strategies.status_code, 200, strategies.text)
        self.assertEqual(strategies.json()["items"][0]["strategy_id"], "double-low")

        response = self.client.post(
            "/api/v1/strategy-lab/runs",
            json={
                "strategy_id": "double-low",
                "market": "cn",
                "instrument_type": "convertible_bond",
                "start_date": "2024-01-02",
                "end_date": "2024-01-04",
                "initial_cash": 100000,
                "benchmark_symbol": "113001",
                "parameters": {"max_positions": 2},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["metrics"]["trade_count"], 4)
        self.assertEqual(len(payload["equity_curve"]), 3)

        run_id = payload["id"]
        get_response = self.client.get(f"/api/v1/strategy-lab/runs/{run_id}")
        self.assertEqual(get_response.status_code, 200, get_response.text)
        self.assertEqual(get_response.json()["id"], run_id)

        trades = self.client.get(f"/api/v1/strategy-lab/runs/{run_id}/trades")
        self.assertEqual(trades.status_code, 200, trades.text)
        self.assertEqual(len(trades.json()["items"]), 4)

        runs = self.client.get("/api/v1/strategy-lab/runs")
        self.assertEqual(runs.status_code, 200, runs.text)
        self.assertEqual(runs.json()["total"], 1)

    def test_strategy_lab_returns_400_for_invalid_portfolio_account(self) -> None:
        response = self.client.post(
            "/api/v1/strategy-lab/runs",
            json={
                "strategy_id": "double-low",
                "market": "cn",
                "instrument_type": "convertible_bond",
                "start_date": "2024-01-02",
                "end_date": "2024-01-04",
                "initial_cash": 100000,
                "portfolio_account_id": 999,
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"], "invalid_params")
