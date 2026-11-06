# -*- coding: utf-8 -*-
"""API tests for QMT quotes-bridge endpoints (service stubbed)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app import create_app
from src.config import Config
from src.services.qmt_quotes_service import QmtQuotesServiceError
from src.storage import DatabaseManager


class _StubQmtQuotesService:
    """端点层的最小服务面：固定返回值 + 记录调用。"""

    settings_calls: list = []
    manual_calls: list = []

    def __init__(self) -> None:
        pass

    def get_status(self) -> dict:
        return {
            "enabled": True,
            "redis_url": "redis://localhost:6379/0",
            "redis_available": True,
            "alive": True,
            "heartbeat": "2026-11-01 22:43:21|cb_quotes_bridge",
            "subscribed_count": 3,
            "auto_include_holdings": True,
            "include_alert_symbols": True,
            "manual_symbols": ["113050.SH"],
            "holdings_count": 2,
            "alert_count": 1,
        }

    def get_subscription_detail(self) -> dict:
        return {
            "symbols": [
                {"symbol": "600519.SH", "origins": ["holdings", "alert"]},
                {"symbol": "113050.SH", "origins": ["manual"]},
                {"symbol": "588000.SH", "origins": ["stale"]},
            ],
            "holdings": ["600519.SH"],
            "alert_symbols": ["600519.SH"],
            "manual_symbols": ["113050.SH"],
        }

    def sync_subscription(self) -> dict:
        return {"changed": True, "added": ["113050.SH"], "removed": [], "total": 2}

    def get_quote_detail(self, symbol: str) -> dict:
        if not symbol.endswith((".SH", ".SZ")):
            raise QmtQuotesServiceError(f"非法标的代码（仅沪深 6 位码）: {symbol}")
        return {
            "symbol": symbol,
            "alive": True,
            "found": True,
            "data": {"code": symbol, "last": 1392.05, "timetag": "20261030 15:00:01"},
        }

    def get_daily_detail(self, symbol: str) -> dict:
        if not symbol.endswith((".SH", ".SZ")):
            raise QmtQuotesServiceError(f"非法标的代码（仅沪深 6 位码）: {symbol}")
        return {
            "symbol": symbol,
            "alive": True,
            "found": True,
            "data": {
                "code": symbol,
                "dividend_type": "none",
                "updated_at": "2026-10-30 15:05:02",
                "bars": [
                    {"date": "20261029", "open": 1349.99, "high": 1356.0, "low": 1330.01, "close": 1354.0, "volume": 42992, "amount": 5797980000.0},
                    {"date": "20261030", "open": 1363.0, "high": 1393.37, "low": 1352.98, "close": 1392.05, "volume": 59927, "amount": 8257381800.0},
                ],
            },
        }

    @staticmethod
    def set_manual_symbols(symbols):
        _StubQmtQuotesService.manual_calls.append(list(symbols))
        if any(str(item).strip().upper() == "BAD" for item in symbols):
            raise QmtQuotesServiceError("不支持的标的代码（仅沪深 6 位码）: BAD")
        return {"symbols": list(symbols), "invalid": []}

    @staticmethod
    def update_settings(**kwargs):
        _StubQmtQuotesService.settings_calls.append(kwargs)


class QmtQuotesApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.env_path = self.data_dir / ".env"
        self.db_path = self.data_dir / "qmt_api_test.db"
        self.env_path.write_text(
            "\n".join(
                [
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
        app = create_app(static_dir=self.data_dir / "empty-static")
        self.client = TestClient(app)
        _StubQmtQuotesService.settings_calls = []
        _StubQmtQuotesService.manual_calls = []
        self.service_patch = patch(
            "api.v1.endpoints.qmt_quotes.QmtQuotesService", _StubQmtQuotesService
        )
        self.service_patch.start()

    def tearDown(self) -> None:
        self.service_patch.stop()
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def test_get_status_and_subscription(self) -> None:
        resp = self.client.get("/api/v1/qmt-quotes/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["enabled"])
        self.assertTrue(body["alive"])
        self.assertEqual(body["subscribed_count"], 3)

        detail = self.client.get("/api/v1/qmt-quotes/subscription").json()
        self.assertEqual(len(detail["symbols"]), 3)
        self.assertEqual(detail["symbols"][0]["origins"], ["holdings", "alert"])

    def test_sync_returns_diff(self) -> None:
        resp = self.client.post("/api/v1/qmt-quotes/subscription/sync")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["changed"])
        self.assertEqual(body["added"], ["113050.SH"])

    def test_manual_symbols_reject_invalid_with_400(self) -> None:
        ok = self.client.put(
            "/api/v1/qmt-quotes/subscription/manual", json={"symbols": ["600519.SH"]}
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["symbols"], ["600519.SH"])

        bad = self.client.put(
            "/api/v1/qmt-quotes/subscription/manual", json={"symbols": ["BAD"]}
        )
        self.assertEqual(bad.status_code, 400)
        # 全局异常处理器把 error/message 提到响应顶层
        self.assertIn("不支持的标的代码", bad.json()["message"])

    def test_raw_data_inspection_endpoints(self) -> None:
        quote = self.client.get("/api/v1/qmt-quotes/data/quote", params={"symbol": "600519.SH"})
        self.assertEqual(quote.status_code, 200)
        quote_body = quote.json()
        self.assertTrue(quote_body["found"])
        self.assertEqual(quote_body["data"]["last"], 1392.05)
        # 原始字段名保留（timetag 不转驼峰）
        self.assertIn("timetag", quote_body["data"])

        daily = self.client.get("/api/v1/qmt-quotes/data/daily", params={"symbol": "600519.SH"})
        self.assertEqual(daily.status_code, 200)
        daily_body = daily.json()
        self.assertTrue(daily_body["found"])
        self.assertEqual(len(daily_body["data"]["bars"]), 2)
        self.assertEqual(daily_body["data"]["bars"][-1]["close"], 1392.05)

    def test_raw_data_rejects_invalid_symbol_with_400(self) -> None:
        for path in ("/data/quote", "/data/daily"):
            bad = self.client.get(f"/api/v1/qmt-quotes{path}", params={"symbol": "BAD;DEL"})
            self.assertEqual(bad.status_code, 400)
            self.assertIn("非法标的代码", bad.json()["message"])

    def test_settings_update_requires_at_least_one_field(self) -> None:
        empty = self.client.put("/api/v1/qmt-quotes/settings", json={})
        self.assertEqual(empty.status_code, 400)

        ok = self.client.put(
            "/api/v1/qmt-quotes/settings", json={"auto_include_holdings": False}
        )
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["enabled"])
        self.assertEqual(
            _StubQmtQuotesService.settings_calls,
            [{"enabled": None, "auto_include_holdings": False, "include_alert_symbols": None}],
        )


if __name__ == "__main__":
    unittest.main()
