# -*- coding: utf-8 -*-
"""持仓技术指标（indicators）与止损位（stop_loss_price）测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
from fastapi.testclient import TestClient

# Keep this test runnable when optional LLM runtime deps are not installed.
try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

import src.auth as auth
from api.app import create_app
from src.config import Config
from src.services.portfolio_indicator_service import PortfolioIndicatorService
from src.services.portfolio_trend_service import PortfolioTrendService
from src.services.portfolio_weekly_review_service import PortfolioWeeklyReviewService
from src.storage import DatabaseManager, StrategyLabCbBasic, StrategyLabCbDailyFactor


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


class PositionIndicatorsTestCase(unittest.TestCase):
    """Indicator endpoint / service / stop-loss meta tests."""

    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.env_path = self.data_dir / ".env"
        self.db_path = self.data_dir / "indicators_test.db"
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

    # ------------------------------------------------------------------
    # 夹具
    # ------------------------------------------------------------------

    def _save_bars(
        self, symbol: str, closes: list, volumes: list | None = None, start: str = "2026-09-07"
    ) -> None:
        volumes = volumes if volumes is not None else [100.0] * len(closes)
        rows = []
        for index, close in enumerate(closes):
            on_date = date.fromisoformat(start) + pd.Timedelta(days=index).to_pytimedelta()
            rows.append(
                {
                    "date": on_date,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": volumes[index],
                    "amount": close,
                    "pct_chg": 0.0,
                }
            )
        df = pd.DataFrame(rows)
        self.db.save_daily_data(df, code=symbol, data_source="indicators-test")

    def _create_account_with_buy(
        self, name: str, symbol: str, trade_date: str, quantity: float = 10.0, price: float = 100.0
    ) -> int:
        account_id = self.client.post(
            "/api/v1/portfolio/accounts",
            json={"name": name, "broker": "Demo", "market": "cn", "base_currency": "CNY"},
        ).json()["id"]
        resp = self.client.post(
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
        self.assertEqual(resp.status_code, 200, resp.text)
        return account_id

    def _refresh_positions(self, as_of: str) -> None:
        """生成当日快照并刷新 portfolio_positions/lots 最新缓存（indicators 数据源）。"""
        PortfolioTrendService().run_daily_snapshot(trade_date=date.fromisoformat(as_of))
        resp = self.client.get(
            "/api/v1/portfolio/snapshot", params={"include_realtime": "false"}
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    # ------------------------------------------------------------------
    # 指标计算
    # ------------------------------------------------------------------

    def test_indicators_computed_from_closes(self) -> None:
        account_id = self._create_account_with_buy("Main", "600519", "2026-09-07")
        # 25 个交易日单调上涨（覆盖 MA20 窗口）；量比 = 300 / 100 = 3.0
        closes = [100 + i for i in range(24)] + [124]
        self._save_bars("600519", closes, volumes=[100.0] * 24 + [300.0])
        self._refresh_positions("2026-10-11")

        resp = self.client.get("/api/v1/portfolio/positions/indicators")
        self.assertEqual(resp.status_code, 200, resp.text)
        items = resp.json()["items"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["symbol"], "600519")
        self.assertIsNotNone(item["last_bar_date"])
        # 均线与形态：单调上涨 → 多头排列
        ma5 = (120 + 121 + 122 + 123 + 124) / 5
        self.assertAlmostEqual(item["ma5"], ma5, places=4)
        self.assertEqual(item["trend_pattern"], "bull")
        # 25 根收盘 24 次上涨 → 连涨 24 天
        self.assertEqual(item["streak_days"], 24)
        # 量比 = 最新 300 / 前 5 日均量 100
        self.assertAlmostEqual(item["volume_ratio"], 3.0, places=2)
        # 52 周位置：收在区间最高点 → 100
        self.assertAlmostEqual(item["pos_52w_pct"], 100.0, places=1)
        # 周涨跌 = 124 / closes[-6](119) - 1
        self.assertAlmostEqual(item["week_change_pct"], round((124 / 119 - 1) * 100, 2), places=2)
        # 乖离为最近收盘对均线
        self.assertAlmostEqual(item["bias_ma5_pct"], round((124 / ma5 - 1) * 100, 2), places=2)
        # 持有天数随快照 lot 生成
        self.assertIn(str(account_id), item["holding_days_by_account"])
        # 迷你曲线
        self.assertEqual(len(item["sparkline"]), 25)

    def test_insufficient_bars_and_fund_volume_ratio(self) -> None:
        self._create_account_with_buy("Fund", "161725.OF", "2026-09-07", quantity=1000, price=1.0)
        # 场外基金只有 3 根净值（不足 MA5 窗口）
        self._save_bars("161725.OF", [1.0, 1.01, 1.02], volumes=[500.0, 500.0, 500.0])
        self._refresh_positions("2026-09-18")

        item = self.client.get("/api/v1/portfolio/positions/indicators").json()["items"][0]
        self.assertEqual(item["symbol"], "161725.OF")
        self.assertIsNone(item["ma5"])
        self.assertIsNone(item["bias_ma5_pct"])
        self.assertEqual(item["trend_pattern"], "unknown")
        self.assertEqual(item["streak_days"], 2)
        # 场外基金无量比
        self.assertIsNone(item["volume_ratio"])

    def test_bear_streak_and_account_filter(self) -> None:
        self._create_account_with_buy("A", "600519", "2026-09-07")
        self._create_account_with_buy("B", "000002", "2026-09-07")
        self._save_bars("600519", [100 + i for i in range(24)] + [124])
        self._save_bars("000002", [20 - 0.5 * i for i in range(25)])
        self._refresh_positions("2026-10-11")

        all_items = {
            item["symbol"]: item
            for item in self.client.get("/api/v1/portfolio/positions/indicators").json()["items"]
        }
        self.assertEqual(set(all_items), {"600519", "000002"})
        self.assertEqual(all_items["000002"]["trend_pattern"], "bear")
        self.assertEqual(all_items["000002"]["streak_days"], -24)
        self.assertAlmostEqual(all_items["000002"]["pos_52w_pct"], 0.0, places=1)

        accounts = self.client.get("/api/v1/portfolio/accounts").json()["accounts"]
        target = next(a for a in accounts if a["name"] == "B")
        filtered = self.client.get(
            "/api/v1/portfolio/positions/indicators", params={"account_id": target["id"]}
        ).json()["items"]
        self.assertEqual([item["symbol"] for item in filtered], ["000002"])

    def test_cb_extra_fields(self) -> None:
        self._create_account_with_buy("CB", "113042", "2026-09-07", quantity=80, price=115.0)
        self._save_bars("113042", [114] * 6 + [115, 116, 117, 118])
        with self.db.get_session() as session:
            session.add(
                StrategyLabCbBasic(
                    bond_code="113042",
                    bond_name="上银转债",
                    stock_code="601229",
                    maturity_date=date(2027, 1, 25),
                    remaining_size=190.74,
                    terms_json='{"force_redeem_countdown": "0/15 | 30"}',
                )
            )
            session.add(
                StrategyLabCbDailyFactor(
                    bond_code="113042",
                    trade_date=date(2026, 9, 18),
                    close=118.0,
                    premium_rate=2.73,
                )
            )
            session.commit()
        self._refresh_positions("2026-09-18")

        item = self.client.get("/api/v1/portfolio/positions/indicators").json()["items"][0]
        cb = item["cb"]
        self.assertIsNotNone(cb)
        self.assertAlmostEqual(cb["premium_rate"], 2.73, places=2)
        self.assertEqual(cb["price_band"], "110-130")
        self.assertIsNotNone(cb["days_to_maturity"])
        self.assertEqual(cb["force_redeem_countdown"], "0/15 | 30")

    # ------------------------------------------------------------------
    # stop_loss_price meta 链路
    # ------------------------------------------------------------------

    def test_stop_loss_price_roundtrip_and_validation(self) -> None:
        account_id = self._create_account_with_buy("Main", "600519", "2026-09-07")
        set_resp = self.client.put(
            "/api/v1/portfolio/position-meta",
            json={"account_id": account_id, "symbol": "600519", "stop_loss_price": 95.5},
        )
        self.assertEqual(set_resp.status_code, 200, set_resp.text)
        self.assertAlmostEqual(set_resp.json()["stop_loss_price"], 95.5, places=4)

        # 快照 position 行透出
        self._save_bars("600519", [100, 101, 102, 103, 104])
        self._refresh_positions("2026-09-11")
        snapshot = self.client.get("/api/v1/portfolio/snapshot").json()
        position = next(
            p for acc in snapshot["accounts"] for p in acc["positions"] if p["symbol"] == "600519"
        )
        self.assertAlmostEqual(position["stop_loss_price"], 95.5, places=4)

        # 清除
        clear_resp = self.client.put(
            "/api/v1/portfolio/position-meta",
            json={"account_id": account_id, "symbol": "600519", "stop_loss_price": None},
        )
        self.assertEqual(clear_resp.status_code, 200, clear_resp.text)
        self.assertIsNone(clear_resp.json()["stop_loss_price"])

        # 非法值：0 / 负数 → 请求体校验 422（pydantic gt=0）
        for bad in (0, -1):
            resp = self.client.put(
                "/api/v1/portfolio/position-meta",
                json={"account_id": account_id, "symbol": "600519", "stop_loss_price": bad},
            )
            self.assertEqual(resp.status_code, 422, resp.text)

    def test_weekly_review_lists_near_stop_loss_risk(self) -> None:
        self._create_account_with_buy("Main", "600519", "2026-09-10")
        for day, close in (
            ("2026-09-10", 100.0), ("2026-09-11", 100.0),
            ("2026-09-14", 101.0), ("2026-09-15", 101.5), ("2026-09-16", 102.0),
            ("2026-09-17", 102.5), ("2026-09-18", 103.0),
        ):
            self._save_bars("600519", [close], start=day)
        self._refresh_positions("2026-09-18")

        # 止损价 101（现价 103，距离 1.98% ≤ 5%）→ 风险清单应包含 stop_loss_near
        account_id = self.client.get("/api/v1/portfolio/accounts").json()["accounts"][0]["id"]
        self.client.put(
            "/api/v1/portfolio/position-meta",
            json={"account_id": account_id, "symbol": "600519", "stop_loss_price": 101.0},
        )
        result = PortfolioWeeklyReviewService().run(period_end=date(2026, 9, 18), push=False)
        near = [
            item for item in result["risk_list"]["items"] if item["type"] == "stop_loss_near"
        ]
        self.assertEqual(len(near), 1)
        self.assertIn("600519", near[0]["message"])

        # 止损价远离（80，距离 >5%）→ 不产生条目
        self.client.put(
            "/api/v1/portfolio/position-meta",
            json={"account_id": account_id, "symbol": "600519", "stop_loss_price": 80.0},
        )
        result = PortfolioWeeklyReviewService().run(period_end=date(2026, 9, 18), push=False)
        near = [
            item for item in result["risk_list"]["items"] if item["type"] == "stop_loss_near"
        ]
        self.assertEqual(near, [])


if __name__ == "__main__":
    unittest.main()
