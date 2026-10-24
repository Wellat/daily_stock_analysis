# -*- coding: utf-8 -*-
"""周度持仓复盘（weekly review）服务与 API 测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
from src.services.portfolio_trend_service import PortfolioTrendService
from src.services.portfolio_weekly_review_service import (
    PortfolioWeeklyReviewService,
    resolve_week,
)
from src.services.runtime_scheduler import build_weekly_review_background_tasks
from src.storage import DatabaseManager


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


class WeeklyReviewTestCase(unittest.TestCase):
    """Weekly review API and service tests."""

    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.env_path = self.data_dir / ".env"
        self.db_path = self.data_dir / "weekly_review_test.db"
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
        self.db.save_daily_data(df, code=symbol, data_source="weekly-review-test")

    def _create_account(self, name: str) -> int:
        resp = self.client.post(
            "/api/v1/portfolio/accounts",
            json={"name": name, "broker": "Demo", "market": "cn", "base_currency": "CNY"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["id"]

    def _add_trade(
        self,
        account_id: int,
        symbol: str,
        trade_date: str,
        side: str,
        quantity: float,
        price: float,
        note: str = "",
    ) -> None:
        resp = self.client.post(
            "/api/v1/portfolio/trades",
            json={
                "account_id": account_id,
                "symbol": symbol,
                "trade_date": trade_date,
                "side": side,
                "quantity": quantity,
                "price": price,
                "fee": 0,
                "tax": 0,
                "market": "cn",
                "currency": "CNY",
                "note": note or None,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def _snapshot_days(self, days) -> None:
        trend = PortfolioTrendService()
        for day in days:
            trend.run_daily_snapshot(trade_date=date.fromisoformat(day))

    def _seed_two_accounts_week(self):
        """账户 A 持仓跨周 + 周内加减仓；账户 B 周内清仓。

        周期：2026-09-14（周一）~ 2026-09-18（周五）；期初快照 2026-09-11。
        账户 A（零现金起步）：
          09-10 买 600519 10股@100；09-11 快照（收盘 100）→ equity 0
          09-15 买 5股@120；09-17 卖 4股@150（note=qmt_deal → 自动策略）
          09-18 收盘 140 → 期末持仓 11 股，现金 -1000-600+600+500+300
        账户 B：
          09-10 买 000002 10股@10；09-16 全部卖出@12 → 周内清仓，已实现 +20
        现金流：09-15 外部转入 500；09-16 qmt_deal_cash 结算流水 300（不计外部流，
        但会计入回放现金余额 → equity_end 含这 300）
        期望：A 周收益 840（= equity_end 1340 − equity_start 0 − 500）
              总周收益 860；A 已实现 +200（自动）；B 已实现 +20（手动）
              600519 归因 540（= 1540 − 1000 − 600 + 600，与现金流水无关）
        """
        account_a = self._create_account("AccountA")
        account_b = self._create_account("AccountB")
        self._add_trade(account_a, "600519", "2026-09-10", "buy", 10, 100)
        self._add_trade(account_a, "600519", "2026-09-15", "buy", 5, 120)
        self._add_trade(account_a, "600519", "2026-09-17", "sell", 4, 150, note="qmt_deal:TEST")
        self._add_trade(account_b, "000002", "2026-09-10", "buy", 10, 10)
        self._add_trade(account_b, "000002", "2026-09-16", "sell", 10, 12)

        for day, close in (
            ("2026-09-10", 100.0), ("2026-09-11", 100.0),
            ("2026-09-14", 110.0), ("2026-09-15", 120.0), ("2026-09-16", 130.0),
            ("2026-09-17", 150.0), ("2026-09-18", 140.0),
        ):
            self._save_close("600519", date.fromisoformat(day), close)
        for day, close in (
            ("2026-09-10", 10.0), ("2026-09-11", 10.0),
            ("2026-09-14", 10.5), ("2026-09-15", 11.0), ("2026-09-16", 12.0),
            ("2026-09-17", 12.0), ("2026-09-18", 12.0),
        ):
            self._save_close("000002", date.fromisoformat(day), close)

        cash_resp = self.client.post(
            "/api/v1/portfolio/cash-ledger",
            json={
                "account_id": account_a,
                "event_date": "2026-09-15",
                "direction": "in",
                "amount": 500,
                "currency": "CNY",
                "note": "外部转入",
            },
        )
        self.assertEqual(cash_resp.status_code, 200, cash_resp.text)
        cash_resp = self.client.post(
            "/api/v1/portfolio/cash-ledger",
            json={
                "account_id": account_a,
                "event_date": "2026-09-16",
                "direction": "in",
                "amount": 300,
                "currency": "CNY",
                "note": "qmt_deal_cash:qmt_deal:TEST:001",
            },
        )
        self.assertEqual(cash_resp.status_code, 200, cash_resp.text)

        self._snapshot_days([
            "2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18",
        ])
        return account_a, account_b

    # ------------------------------------------------------------------
    # 服务与周期
    # ------------------------------------------------------------------

    def test_resolve_week_anchor_and_default(self) -> None:
        self.assertEqual(resolve_week(date(2026, 10, 24)), (date(2026, 10, 19), date(2026, 10, 23)))
        self.assertEqual(resolve_week(date(2026, 9, 16)), (date(2026, 9, 14), date(2026, 9, 18)))
        with patch(
            "src.services.portfolio_weekly_review_service.date"
        ) as fake_date:
            fake_date.today.return_value = date(2026, 10, 24)
            fake_date.side_effect = lambda *args, **kwargs: date(*args, **kwargs)
            self.assertEqual(resolve_week(None), (date(2026, 10, 12), date(2026, 10, 16)))

    def test_generate_weekly_metrics_and_cash_flow_adjustment(self) -> None:
        account_a, account_b = self._seed_two_accounts_week()

        resp = self.client.post(
            "/api/v1/portfolio/weekly-reviews/generate",
            json={"period_start": "2026-09-14", "push": False},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIsNone(body.get("skipped"))
        self.assertEqual(body["period_start"], "2026-09-14")
        self.assertEqual(body["period_end"], "2026-09-18")

        detail = self.client.get("/api/v1/portfolio/weekly-reviews/2026-09-14").json()
        payload = detail["payload"]
        self.assertEqual(payload["period"]["trading_days"][0], "2026-09-14")

        # 总览：周收益 = 期末权益 − 期初权益 − 外部净流入（qmt_deal_cash 不计）
        total = payload["overview"]["total"]
        self.assertAlmostEqual(total["net_flow_in"], 500.0, places=2)
        self.assertAlmostEqual(total["weekly_return_amount"], 860.0, places=2)
        accounts = {item["account_name"]: item for item in payload["overview"]["accounts"]}
        self.assertAlmostEqual(accounts["AccountA"]["weekly_return_amount"], 840.0, places=2)
        self.assertAlmostEqual(accounts["AccountB"]["weekly_return_amount"], 20.0, places=2)

        # 已实现：A 卖出 4@150 成本 100 → +200（note=qmt_deal → 自动）；B +20（手动）
        realized = payload["realized"]
        self.assertAlmostEqual(realized["week_realized_total"], 220.0, places=2)
        self.assertAlmostEqual(realized["auto_realized"], 200.0, places=2)
        self.assertAlmostEqual(realized["manual_realized"], 20.0, places=2)

        # 归因：600519 = (1540 − 1000) − 600 + 600 = 540；000002 清仓 = −100 + 120 = 20
        by_symbol = {item["symbol"]: item for item in payload["attribution"]["items"]}
        self.assertAlmostEqual(by_symbol["600519"]["pnl"], 540.0, places=2)
        self.assertAlmostEqual(by_symbol["600519"]["weekly_price_change_pct"], 40.0, places=2)
        self.assertAlmostEqual(by_symbol["000002"]["pnl"], 20.0, places=2)
        self.assertTrue(by_symbol["000002"]["exited_position"])

        # 结构：期末仅 600519 持仓；未填买入逻辑应被列出
        self.assertEqual(payload["structure"]["position_count"], 1)
        self.assertEqual(
            [item["symbol"] for item in payload["structure"]["missing_buy_logic"]], ["600519"]
        )

        # 分层与风险
        self.assertEqual(payload["position_tiers"]["items"][0]["symbol"], "600519")
        risk_types = {item["type"] for item in payload["risk_list"]["items"]}
        self.assertIn("missing_buy_logic", risk_types)

        # 列表接口带 summary
        listing = self.client.get("/api/v1/portfolio/weekly-reviews").json()
        self.assertEqual(listing["total"], 1)
        self.assertAlmostEqual(
            listing["items"][0]["summary"]["weekly_return_amount"], 860.0, places=2
        )
        self.assertEqual(detail["status"], "completed")
        self.assertFalse(detail["feishu_sent"])

    def test_regenerate_preserves_manual_area(self) -> None:
        self._seed_two_accounts_week()
        generate = self.client.post(
            "/api/v1/portfolio/weekly-reviews/generate",
            json={"period_start": "2026-09-14", "push": False},
        )
        review_id = generate.json()["review_id"]

        note_resp = self.client.put(
            "/api/v1/portfolio/weekly-reviews/2026-09-14/note",
            json={"weekly_note": "本周追高了，下周等回踩"},
        )
        self.assertEqual(note_resp.status_code, 200, note_resp.text)
        plan_resp = self.client.post(
            "/api/v1/portfolio/weekly-reviews/2026-09-14/plans",
            json={"action": "reduce", "symbol": "600519", "content": "跌破 120 减半仓"},
        )
        self.assertEqual(plan_resp.status_code, 200, plan_resp.text)
        plan_id = plan_resp.json()["plan_id"]

        again = self.client.post(
            "/api/v1/portfolio/weekly-reviews/generate",
            json={"period_start": "2026-09-14", "push": False},
        )
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["review_id"], review_id)

        detail = self.client.get("/api/v1/portfolio/weekly-reviews/2026-09-14").json()
        self.assertEqual(detail["weekly_note"], "本周追高了，下周等回踩")
        self.assertEqual(len(detail["plans"]), 1)
        self.assertEqual(detail["plans"][0]["plan_id"], plan_id)
        self.assertEqual(detail["plans"][0]["status"], "pending")

        # 计划状态流转 + 删除
        update = self.client.put(
            f"/api/v1/portfolio/weekly-reviews/2026-09-14/plans/{plan_id}",
            json={"status": "done"},
        )
        self.assertEqual(update.status_code, 200, update.text)
        self.assertEqual(update.json()["status"], "done")
        delete = self.client.delete(
            f"/api/v1/portfolio/weekly-reviews/2026-09-14/plans/{plan_id}"
        )
        self.assertEqual(delete.status_code, 200, delete.text)
        detail = self.client.get("/api/v1/portfolio/weekly-reviews/2026-09-14").json()
        self.assertEqual(detail["plans"], [])

    def test_plan_validation_and_missing_review(self) -> None:
        self._seed_two_accounts_week()
        bad_action = self.client.post(
            "/api/v1/portfolio/weekly-reviews/2026-09-14/plans",
            json={"action": "hold", "content": "x"},
        )
        self.assertEqual(bad_action.status_code, 400, bad_action.text)
        missing = self.client.get("/api/v1/portfolio/weekly-reviews/2026-01-05")
        self.assertEqual(missing.status_code, 404, missing.text)
        missing_generate = self.client.post(
            "/api/v1/portfolio/weekly-reviews/generate",
            json={"period_start": "not-a-date"},
        )
        self.assertEqual(missing_generate.status_code, 400, missing_generate.text)

    def test_prev_week_plans_surface_in_detail(self) -> None:
        self._seed_two_accounts_week()
        self.client.post(
            "/api/v1/portfolio/weekly-reviews/generate",
            json={"period_start": "2026-09-07", "push": False},
        )
        self.client.post(
            "/api/v1/portfolio/weekly-reviews/generate",
            json={"period_start": "2026-09-14", "push": False},
        )
        plan = self.client.post(
            "/api/v1/portfolio/weekly-reviews/2026-09-07/plans",
            json={"action": "watch", "content": "关注 600519 突破"},
        )
        self.assertEqual(plan.status_code, 200, plan.text)

        detail = self.client.get("/api/v1/portfolio/weekly-reviews/2026-09-14").json()
        self.assertEqual(detail["prev_period"]["period_start"], "2026-09-07")
        self.assertEqual(len(detail["prev_plans"]), 1)
        self.assertEqual(detail["prev_plans"][0]["content"], "关注 600519 突破")

    def test_no_trading_days_returns_skipped(self) -> None:
        self._seed_two_accounts_week()
        with patch(
            "src.services.portfolio_weekly_review_service.cn_trading_days_between",
            return_value=[],
        ):
            service = PortfolioWeeklyReviewService()
            result = service.run(period_end=date(2026, 9, 18), push=False)
        self.assertEqual(result.get("skipped"), "no_trading_days")
        listing = self.client.get("/api/v1/portfolio/weekly-reviews").json()
        self.assertEqual(listing["total"], 0)

    def test_feishu_failure_still_persists(self) -> None:
        self._seed_two_accounts_week()
        service = PortfolioWeeklyReviewService()
        with patch.object(
            PortfolioWeeklyReviewService, "_send_feishu", return_value=False
        ):
            result = service.run(period_end=date(2026, 9, 18), push=True)
        self.assertFalse(result["feishu_sent"])
        detail = self.client.get("/api/v1/portfolio/weekly-reviews/2026-09-14").json()
        self.assertEqual(detail["status"], "completed")
        self.assertFalse(detail["feishu_sent"])

    def test_scheduler_builder_gating(self) -> None:
        disabled = build_weekly_review_background_tasks(
            SimpleNamespace(weekly_review_enabled=False)
        )
        self.assertEqual(disabled, [])
        enabled = build_weekly_review_background_tasks(
            SimpleNamespace(weekly_review_enabled=True, weekly_review_time="01:00")
        )
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0]["name"], "weekly_portfolio_review")
        self.assertGreaterEqual(enabled[0]["interval_seconds"], 30)


if __name__ == "__main__":
    unittest.main()
