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
        strategy_ids = [item["strategy_id"] for item in strategies.json()["items"]]
        self.assertIn("double-low", strategy_ids)
        self.assertIn("rotation", strategy_ids)
        self.assertEqual(strategy_ids[0], "rotation")  # rotation 为推荐的默认策略，置顶展示

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

        listed = self.client.get("/api/v1/strategy-lab/runs")
        self.assertEqual(listed.status_code, 200, listed.text)
        items = listed.json()["items"]
        self.assertEqual(listed.json()["total"], 1)
        # 列表摘要带紧凑指标与参数快照（additive）
        summary = next(item for item in items if item["id"] == run_id)
        self.assertIsNotNone(summary["metrics"]["total_return_pct"])
        self.assertIsNotNone(summary["metrics"]["max_drawdown_pct"])
        self.assertEqual(summary["parameters"]["max_positions"], 2)

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

    def test_rotation_strategy_exposes_factor_metadata(self) -> None:
        response = self.client.get("/api/v1/strategy-lab/strategies")
        self.assertEqual(response.status_code, 200, response.text)
        rotation = next(
            item for item in response.json()["items"] if item["strategy_id"] == "rotation"
        )
        factor_ids = {factor["factor"] for factor in rotation["factors"]}
        self.assertIn("price", factor_ids)
        self.assertIn("premium_rate", factor_ids)
        preset_ids = {preset["preset"] for preset in rotation["score_presets"]}
        self.assertIn("double_low", preset_ids)
        param_keys = {param["key"] for param in rotation["parameters"]}
        self.assertIn("rebalance_unit", param_keys)
        self.assertIn("max_positions", param_keys)

    def test_configs_crud_lifecycle(self) -> None:
        created = self.client.post(
            "/api/v1/strategy-lab/configs",
            json={
                "name": "双低月轮动",
                "description": "月度换仓双低",
                "strategy_id": "rotation",
                "parameters": {
                    "score_preset": "double_low",
                    "rebalance_unit": "month",
                    "max_positions": 5,
                    "score_factors": [
                        {"factor": "price", "direction": "asc", "weight": 1.0},
                        {"factor": "premium_rate", "direction": "asc", "weight": 1.0},
                    ],
                },
                "symbols": ["111AAA"],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        config_id = created.json()["id"]
        self.assertEqual(created.json()["parameters"]["rebalance_unit"], "month")

        listed = self.client.get("/api/v1/strategy-lab/configs")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["total"], 1)

        updated = self.client.put(
            f"/api/v1/strategy-lab/configs/{config_id}",
            json={"name": "双低月轮动V2", "parameters": {"score_preset": "triple_low"}},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["name"], "双低月轮动V2")
        self.assertEqual(updated.json()["parameters"]["score_preset"], "triple_low")

        deleted = self.client.delete(f"/api/v1/strategy-lab/configs/{config_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        missing = self.client.delete(f"/api/v1/strategy-lab/configs/{config_id}")
        self.assertEqual(missing.status_code, 404, missing.text)

    def test_run_report_export_formats(self) -> None:
        run = self.client.post(
            "/api/v1/strategy-lab/runs",
            json={
                "strategy_id": "double-low",
                "market": "cn",
                "instrument_type": "convertible_bond",
                "start_date": "2024-01-02",
                "end_date": "2024-01-04",
                "initial_cash": 100000,
            },
        )
        self.assertEqual(run.status_code, 200, run.text)
        run_id = run.json()["id"]

        md = self.client.get(f"/api/v1/strategy-lab/runs/{run_id}/report?format=md")
        self.assertEqual(md.status_code, 200, md.text)
        self.assertIn("text/markdown", md.headers["content-type"])
        self.assertIn("策略实验室回测报告", md.text)
        self.assertIn("绩效汇总", md.text)

        holdings = self.client.get(f"/api/v1/strategy-lab/runs/{run_id}/report?format=holdings-csv")
        self.assertEqual(holdings.status_code, 200, holdings.text)
        self.assertIn("text/csv", holdings.headers["content-type"])
        self.assertIn("trade_date,holdings", holdings.text)

        trades = self.client.get(f"/api/v1/strategy-lab/runs/{run_id}/report?format=trades-csv")
        self.assertEqual(trades.status_code, 200, trades.text)
        self.assertIn("trade_date,side,symbol", trades.text)

        missing = self.client.get("/api/v1/strategy-lab/runs/999999/report?format=md")
        self.assertEqual(missing.status_code, 404, missing.text)
        bad = self.client.get(f"/api/v1/strategy-lab/runs/{run_id}/report?format=pdf")
        self.assertEqual(bad.status_code, 422, bad.text)
