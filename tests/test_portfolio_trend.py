# -*- coding: utf-8 -*-
"""Portfolio 每日快照趋势（trend）测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import select

# Keep this test runnable when optional LLM runtime deps are not installed.
try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

import src.auth as auth
from api.app import create_app
from src.config import Config
from src.core.trading_calendar import cn_trading_days_between
from src.repositories.portfolio_repo import PortfolioRepository
from src.services.portfolio_trend_service import PortfolioTrendService
from src.storage import DatabaseManager, PortfolioDailySnapshot


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


class PortfolioTrendTestCase(unittest.TestCase):
    """Portfolio trend API and service tests."""

    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.env_path = self.data_dir / ".env"
        self.db_path = self.data_dir / "portfolio_trend_test.db"
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
        app = create_app(static_dir=self.data_dir / "empty-static")
        self.client = TestClient(app)
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def _save_close(self, symbol: str, on_date: date, close: float) -> None:
        df = pd.DataFrame(
            [
                {
                    "date": on_date,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1.0,
                    "amount": close,
                    "pct_chg": 0.0,
                }
            ]
        )
        self.db.save_daily_data(df, code=symbol, data_source="portfolio-trend-test")

    def _create_account_with_buy(
        self, *, name: str, symbol: str, trade_date: str, quantity: float = 10.0, price: float = 100.0
    ) -> int:
        create_resp = self.client.post(
            "/api/v1/portfolio/accounts",
            json={"name": name, "broker": "Demo", "market": "cn", "base_currency": "CNY"},
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        account_id = create_resp.json()["id"]
        trade_resp = self.client.post(
            "/api/v1/portfolio/trades",
            json={
                "account_id": account_id,
                "symbol": symbol,
                "trade_date": trade_date,
                "side": "buy",
                "quantity": quantity,
                "price": price,
                "fee": 0,
                "tax": 0,
                "market": "cn",
                "currency": "CNY",
            },
        )
        self.assertEqual(trade_resp.status_code, 200, trade_resp.text)
        return account_id

    def test_trend_backfills_trading_days_then_idempotent(self) -> None:
        # 8/31（周一）~9/11（周五）：9/5、9/6 与 9/12 为周末，不在序列中
        account_id = self._create_account_with_buy(
            name="Main", symbol="600519", trade_date="2026-08-28"
        )
        for day, close in (
            ("2026-08-31", 100.0), ("2026-09-01", 102.0), ("2026-09-02", 104.0),
            ("2026-09-03", 106.0), ("2026-09-04", 108.0),
            ("2026-09-07", 110.0), ("2026-09-08", 112.0), ("2026-09-09", 114.0),
            ("2026-09-10", 116.0), ("2026-09-11", 118.0),
        ):
            self._save_close("600519", date.fromisoformat(day), close)

        resp = self.client.get(
            "/api/v1/portfolio/trend",
            params={"account_id": account_id, "start": "2026-08-31", "end": "2026-09-11"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["backfilled"] > 0)
        dates = [item["date"] for item in body["items"]]
        self.assertEqual(dates[0], "2026-08-31")
        self.assertEqual(dates[-1], "2026-09-11")
        self.assertNotIn("2026-09-05", dates)  # 周六
        self.assertNotIn("2026-09-06", dates)  # 周日
        # 市值随收盘价变化：8/31 → 10×100；9/11 → 10×118
        by_date = {item["date"]: item for item in body["items"]}
        self.assertAlmostEqual(by_date["2026-08-31"]["total_market_value"], 1000.0, places=4)
        self.assertAlmostEqual(by_date["2026-09-11"]["total_market_value"], 1180.0, places=4)
        # 总收益 = 已实现 + 浮动（无卖出时即浮动盈亏：市值 - 成本）
        self.assertAlmostEqual(by_date["2026-09-11"]["total_pnl"], 180.0, places=4)
        self.assertAlmostEqual(
            by_date["2026-09-11"]["total_pnl"],
            by_date["2026-09-11"]["realized_pnl"] + by_date["2026-09-11"]["unrealized_pnl"],
            places=6,
        )

        # 二次调用：快照已存在，不再回补，结果一致
        again = self.client.get(
            "/api/v1/portfolio/trend",
            params={"account_id": account_id, "start": "2026-08-31", "end": "2026-09-11"},
        ).json()
        self.assertEqual(again["backfilled"], 0)
        self.assertEqual(again["items"], body["items"])

    def test_trend_aggregate_sums_accounts_and_filter(self) -> None:
        first = self._create_account_with_buy(name="First", symbol="600519", trade_date="2026-09-01")
        second = self._create_account_with_buy(
            name="Second", symbol="600519", trade_date="2026-09-01", quantity=20.0
        )
        self._save_close("600519", date(2026, 9, 2), 100.0)

        aggregate = self.client.get(
            "/api/v1/portfolio/trend",
            params={"start": "2026-09-01", "end": "2026-09-02"},
        ).json()
        point = next(item for item in aggregate["items"] if item["date"] == "2026-09-02")
        # 两账户合计 30 股 × 100
        self.assertAlmostEqual(point["total_market_value"], 3000.0, places=4)
        self.assertIsNone(aggregate["account_id"])

        single = self.client.get(
            "/api/v1/portfolio/trend",
            params={"account_id": second, "start": "2026-09-01", "end": "2026-09-02"},
        ).json()
        single_point = next(item for item in single["items"] if item["date"] == "2026-09-02")
        self.assertAlmostEqual(single_point["total_market_value"], 2000.0, places=4)

        missing = self.client.get(
            "/api/v1/portfolio/trend", params={"account_id": 99999}
        )
        self.assertEqual(missing.status_code, 400)

    def test_run_daily_snapshot_writes_both_cost_methods(self) -> None:
        self._create_account_with_buy(name="Main", symbol="600519", trade_date="2026-09-10")
        self._save_close("600519", date(2026, 9, 11), 100.0)

        result = PortfolioTrendService(self.db).run_daily_snapshot(trade_date=date(2026, 9, 11))

        self.assertEqual(result["snapshots"], 2)  # 1 账户 × fifo/avg
        with self.db.get_session() as session:
            rows = session.execute(
                select(PortfolioDailySnapshot).where(
                    PortfolioDailySnapshot.snapshot_date == date(2026, 9, 11)
                )
            ).scalars().all()
        self.assertEqual({row.cost_method for row in rows}, {"fifo", "avg"})
        for row in rows:
            self.assertAlmostEqual(row.total_market_value, 1000.0, places=4)

    def test_replay_daily_snapshot_keeps_latest_position_cache(self) -> None:
        account_id = self._create_account_with_buy(name="Main", symbol="600519", trade_date="2026-09-01")
        self._save_close("600519", date(2026, 9, 2), 100.0)
        # 追加买入，使 9/4 之后持仓变为 20 股
        add_resp = self.client.post(
            "/api/v1/portfolio/trades",
            json={
                "account_id": account_id, "symbol": "600519", "trade_date": "2026-09-04",
                "side": "buy", "quantity": 10, "price": 100, "fee": 0, "tax": 0,
                "market": "cn", "currency": "CNY",
            },
        )
        self.assertEqual(add_resp.status_code, 200, add_resp.text)
        self._save_close("600519", date(2026, 9, 5), 100.0)

        # 当前（9/5）快照：持仓缓存应为 20 股
        snapshot = self.client.get(
            "/api/v1/portfolio/snapshot", params={"as_of": "2026-09-05", "include_realtime": "false"}
        ).json()
        quantity_now = snapshot["accounts"][0]["positions"][0]["quantity"]
        self.assertAlmostEqual(quantity_now, 20.0, places=4)

        # 回补 9/2（当时仅 10 股）不应覆盖最新持仓缓存
        repo = PortfolioRepository(self.db)
        account = repo.get_account(account_id)
        PortfolioTrendService(self.db).portfolio_service.replay_daily_snapshot(
            account=account, as_of=date(2026, 9, 2), cost_method="fifo"
        )
        with self.db.get_session() as session:
            from src.storage import PortfolioPosition

            rows = session.execute(
                select(PortfolioPosition).where(PortfolioPosition.account_id == account_id)
            ).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].quantity, 20.0, places=4)  # 缓存仍是最新状态


class CnTradingDaysTestCase(unittest.TestCase):
    def test_cn_trading_days_between_skips_weekend(self) -> None:
        days = cn_trading_days_between(date(2026, 9, 4), date(2026, 9, 8))
        self.assertEqual([d.isoformat() for d in days], ["2026-09-04", "2026-09-07", "2026-09-08"])

    def test_cn_trading_days_between_empty_range(self) -> None:
        self.assertEqual(cn_trading_days_between(date(2026, 9, 8), date(2026, 9, 1)), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
