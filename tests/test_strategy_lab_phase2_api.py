# -*- coding: utf-8 -*-
"""Strategy Lab phase 2 API tests."""

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


class StrategyLabPhase2ApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "strategy_lab_phase2_api.db"
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

    def test_data_sync_and_batch_contract(self) -> None:
        sync_resp = self.client.post("/api/v1/strategy-lab/data-sync", json={"market": "cn", "source": "fixture"})
        self.assertEqual(sync_resp.status_code, 200, sync_resp.text)
        self.assertEqual(sync_resp.json()["cb_basic_upserted"], 3)

        batch_resp = self.client.post(
            "/api/v1/strategy-lab/batches",
            json={
                "strategy_id": "double-low",
                "market": "cn",
                "instrument_type": "convertible_bond",
                "base_config": {
                    "strategy_id": "double-low",
                    "market": "cn",
                    "instrument_type": "convertible_bond",
                    "start_date": "2024-01-02",
                    "end_date": "2024-01-04",
                    "initial_cash": 100000,
                },
                "parameter_grid": {"max_positions": [1, 2]},
            },
        )
        self.assertEqual(batch_resp.status_code, 200, batch_resp.text)
        self.assertEqual(batch_resp.json()["total_tasks"], 2)
        batch_id = batch_resp.json()["id"]

        detail_resp = self.client.get(f"/api/v1/strategy-lab/batches/{batch_id}")
        self.assertEqual(detail_resp.status_code, 200, detail_resp.text)
        self.assertEqual(len(detail_resp.json()["items"]), 2)

        resume_resp = self.client.post(f"/api/v1/strategy-lab/batches/{batch_id}/resume")
        self.assertEqual(resume_resp.status_code, 200, resume_resp.text)
        self.assertEqual(resume_resp.json()["status"], "completed")

        stream_resp = self.client.get(f"/api/v1/strategy-lab/batches/{batch_id}/stream")
        self.assertEqual(stream_resp.status_code, 200, stream_resp.text)
        self.assertIn("batch_done", stream_resp.text)

        delete_resp = self.client.delete(f"/api/v1/strategy-lab/batches/{batch_id}")
        self.assertEqual(delete_resp.status_code, 200, delete_resp.text)
        self.assertEqual(delete_resp.json(), {"deleted": True})

    def test_payload_data_sync_contract(self) -> None:
        sync_resp = self.client.post(
            "/api/v1/strategy-lab/data-sync",
            json={
                "market": "cn",
                "source": "manual",
                "cb_basic": [
                    {
                        "bond_code": "123001",
                        "bond_name": "测试转债",
                        "stock_code": "600001",
                        "stock_name": "测试正股",
                        "list_date": "2024-01-02",
                        "maturity_date": "2028-01-02",
                    }
                ],
                "cb_daily_factors": [
                    {
                        "bond_code": "123001",
                        "trade_date": "2024-01-03",
                        "close": 101.2,
                        "premium_rate": 17.8,
                    }
                ],
            },
        )

        self.assertEqual(sync_resp.status_code, 200, sync_resp.text)
        self.assertEqual(sync_resp.json()["cb_basic_upserted"], 1)
        self.assertEqual(sync_resp.json()["cb_factor_upserted"], 1)

    def test_event_study_contract(self) -> None:
        self.client.post(
            "/api/v1/strategy-lab/data-sync",
            json={
                "source": "manual",
                "cb_basic": [{"bond_code": "123001", "bond_name": "测试转债", "stock_code": "600001"}],
                "cb_daily_factors": [
                    {"bond_code": "123001", "trade_date": "2024-01-02", "close": 100},
                    {"bond_code": "123001", "trade_date": "2024-01-03", "close": 105},
                ],
                "cb_events": [{"bond_code": "123001", "event_date": "2024-01-02", "event_type": "strong_redeem"}],
            },
        )
        response = self.client.post("/api/v1/strategy-lab/studies/events", json={"event_type": "strong_redeem", "offsets": [1]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"][0]["returns_pct"]["1"], 5.0)
