# -*- coding: utf-8 -*-
"""Tests for the QMT subscription management service."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.config import Config
from src.services.qmt_quotes_service import (
    QmtQuotesService,
    QmtQuotesServiceError,
    sync_subscription_safely,
)
from src.services.scheduled_task_registry import _is_failed_marker
from src.storage import (
    AlertRuleRecord,
    DatabaseManager,
    PortfolioAccount,
    PortfolioPosition,
)

from data_provider.qmt_fetcher import QmtRedisClient


class FakeRedisConnection:
    def __init__(self, hash_store=None, fail_write=False):
        self.store = {"qmt:hb": "alive"}
        self.hash_store = hash_store if hash_store is not None else {}
        self.fail_write = fail_write

    def exists(self, key):
        return 1 if key in self.store else 0

    def get(self, key):
        return self.store.get(key)

    def hget(self, key, field):
        return self.hash_store.get(f"{key}|{field}")

    def hset(self, key, field, value):
        if self.fail_write:
            raise ConnectionError("redis unreachable")
        self.hash_store[f"{key}|{field}"] = value
        return 1


def _fake_config(**overrides):
    base = {
        "qmt_quotes_enabled": True,
        "qmt_redis_url": "redis://localhost:6379/0",
        "qmt_auto_include_holdings": True,
        "qmt_include_alert_symbols": True,
        "qmt_manual_symbols": "",
        "stock_list": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class QmtQuotesServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "qmt_service_test.db"
        os.environ["ENV_FILE"] = str(self.temp_dir.name + "/.env")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

        self.connection = FakeRedisConnection()
        self.client = QmtRedisClient(redis_url="redis://fake", connection=self.connection)
        self.service = QmtQuotesService(db_manager=self.db, client=self.client)
        self._config = _fake_config()
        self._config_patch = patch(
            "src.services.qmt_quotes_service.get_config", return_value=self._config
        )
        self._config_patch.start()

    def tearDown(self) -> None:
        self._config_patch.stop()
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    # ------------------------------------------------------------------
    def _seed_holdings(self) -> None:
        with self.db.get_session() as session:
            active = PortfolioAccount(owner_id="u1", name="QMT-A", broker="qmt", market="cn")
            inactive = PortfolioAccount(owner_id="u1", name="QMT-B", broker="qmt", market="cn", is_active=False)
            session.add(active)
            session.add(inactive)
            session.flush()
            rows = [
                (active.id, "600519.SH", "cn", 100),
                (active.id, "510310", "cn", 1000),
                (active.id, "123270.SZ", "cn", 10),
                (active.id, "HK01810", "hk", 200),  # 港股：不支持
                (active.id, "920748", "cn", 300),  # 北交所：不支持
                (active.id, "510310.OF", "cn", 500),  # 场外基金：不支持
                (inactive.id, "000001.SZ", "cn", 100),  # 停用账户：不计
                (active.id, "300750.SZ", "cn", 0),  # 清仓：不计
            ]
            for account_id, symbol, market, quantity in rows:
                session.add(
                    PortfolioPosition(
                        account_id=account_id,
                        symbol=symbol,
                        market=market,
                        quantity=quantity,
                    )
                )
            session.commit()

    def _seed_alert_rules(self) -> None:
        with self.db.get_session() as session:
            session.add(
                AlertRuleRecord(
                    name="r1",
                    target_scope="single_symbol",
                    target="600519.SH",
                    alert_type="price_cross",
                    enabled=True,
                )
            )
            session.add(
                AlertRuleRecord(
                    name="r2",
                    target_scope="watchlist",
                    target="default",
                    alert_type="macd_cross",
                    enabled=True,
                )
            )
            session.add(
                AlertRuleRecord(
                    name="r3-disabled",
                    target_scope="single_symbol",
                    target="000001.SZ",
                    alert_type="price_cross",
                    enabled=False,
                )
            )
            session.add(
                AlertRuleRecord(
                    name="r4-market",
                    target_scope="market",
                    target="cn",
                    alert_type="market_light",
                    enabled=True,
                )
            )
            session.add(
                AlertRuleRecord(
                    name="r5-hk",
                    target_scope="single_symbol",
                    target="HK01810",
                    alert_type="price_cross",
                    enabled=True,
                )
            )
            session.commit()

    # ------------------------------------------------------------------
    def test_holdings_symbols_filter_cn_sh_sz_only(self) -> None:
        self._seed_holdings()
        self.assertEqual(
            self.service.list_holdings_symbols(),
            ["123270.SZ", "510310.SH", "600519.SH"],
        )

    def test_alert_symbols_from_single_and_watchlist(self) -> None:
        self._seed_alert_rules()
        self._config.stock_list = ["510050.SH", "HK01810", "159915"]
        self.assertEqual(
            self.service.list_alert_symbols(),
            ["159915.SZ", "510050.SH", "600519.SH"],
        )

    def test_manual_symbols_accept_mixed_code_forms(self) -> None:
        self._config.qmt_manual_symbols = "600519, 510050.SH, bad, ,920748"
        self.assertEqual(
            QmtQuotesService.list_manual_symbols(),
            ["510050.SH", "600519.SH"],
        )

    def test_sync_merges_sources_and_writes_whole_list(self) -> None:
        self._seed_holdings()
        self._seed_alert_rules()
        self._config.stock_list = ["510050.SH"]
        self._config.qmt_manual_symbols = "113050.SH"

        result = self.service.sync_subscription()
        self.assertTrue(result["changed"])
        self.assertEqual(
            result["added"],
            ["113050.SH", "123270.SZ", "510050.SH", "510310.SH", "600519.SH"],
        )
        written = json.loads(self.connection.hash_store["qmt:cfg:symbols|symbols"])
        self.assertEqual(written, result["added"])
        # 紧凑 JSON（无空格分隔符）
        self.assertNotIn(" ", self.connection.hash_store["qmt:cfg:symbols|symbols"])

        # 再同步一次：无变化不写
        result_again = self.service.sync_subscription()
        self.assertFalse(result_again["changed"])
        self.assertEqual(result_again["total"], len(written))

    def test_sync_respects_toggles(self) -> None:
        self._seed_holdings()
        self._seed_alert_rules()
        self._config.stock_list = ["510050.SH"]
        self._config.qmt_auto_include_holdings = False
        self._config.qmt_include_alert_symbols = False
        self._config.qmt_manual_symbols = "113050.SH"

        result = self.service.sync_subscription()
        self.assertEqual(result["added"], ["113050.SH"])

    def test_sync_write_failure_raises(self) -> None:
        self._config.qmt_manual_symbols = "113050.SH"
        self.connection.fail_write = True
        with self.assertRaises(QmtQuotesServiceError):
            self.service.sync_subscription()

    def test_subscription_detail_tags_origins(self) -> None:
        self._seed_holdings()
        # Redis 里现存的残留标的（不在任何来源）
        self.connection.hash_store["qmt:cfg:symbols|symbols"] = json.dumps(
            ["600519.SH", "123270.SZ", "588000.SH"]
        )
        detail = self.service.get_subscription_detail()
        origins = {item["symbol"]: item["origins"] for item in detail["symbols"]}
        self.assertEqual(origins["600519.SH"], ["holdings"])
        self.assertEqual(origins["588000.SH"], ["stale"])

    def test_subscription_detail_resolves_names_from_local_tables(self) -> None:
        """名称优先取 qmt_positions 快照，其次 cb_basic，无名称留空（不查实时行情）。"""
        with self.db.get_session() as session:
            from src.storage import QmtPosition, StrategyLabCbBasic

            session.add(QmtPosition(account="QMT-A", symbol="600519", name="贵 州 茅 台", volume=100, can_use_volume=100))
            session.add(QmtPosition(account="QMT-A", symbol="510310", name="沪深300ETF", volume=100, can_use_volume=100))
            session.add(StrategyLabCbBasic(bond_code="113050", bond_name="皓元转债", stock_code="688111", market="cn"))
            session.commit()

        self.connection.hash_store["qmt:cfg:symbols|symbols"] = json.dumps(
            ["600519.SH", "510310.SH", "113050.SH", "588000.SH"]
        )
        detail = self.service.get_subscription_detail()
        names = {item["symbol"]: item["name"] for item in detail["symbols"]}
        # qmt_positions（名称内空格去除）
        self.assertEqual(names["600519.SH"], "贵州茅台")
        self.assertEqual(names["510310.SH"], "沪深300ETF")
        # cb_basic 兜底
        self.assertEqual(names["113050.SH"], "皓元转债")
        # 无任何本地来源 → 留空（前端显示 --）
        self.assertEqual(names["588000.SH"], "")

    def test_set_manual_symbols_normalizes_and_rejects_invalid(self) -> None:
        recorded = {}
        with patch.object(
            QmtQuotesService, "_write_env_items", lambda updates: recorded.update(updates)
        ):
            result = QmtQuotesService.set_manual_symbols(["600519", "510050.SH", "600519"])
            self.assertEqual(result["symbols"], ["510050.SH", "600519.SH"])
            self.assertEqual(recorded["QMT_MANUAL_SYMBOLS"], "510050.SH,600519.SH")

            with self.assertRaises(QmtQuotesServiceError):
                QmtQuotesService.set_manual_symbols(["600519", "AAPL"])

    def test_update_settings_writes_env_keys(self) -> None:
        recorded = {}
        with patch.object(
            QmtQuotesService, "_write_env_items", lambda updates: recorded.update(updates)
        ):
            QmtQuotesService.update_settings(enabled=False, include_alert_symbols=True)
        self.assertEqual(
            recorded,
            {
                "QMT_QUOTES_ENABLED": "false",
                "QMT_INCLUDE_ALERT_SYMBOLS": "true",
            },
        )

    def test_status_reflects_client_and_config(self) -> None:
        self._seed_holdings()
        status = self.service.get_status()
        self.assertTrue(status["enabled"])
        self.assertTrue(status["alive"])
        self.assertEqual(status["holdings_count"], 3)
        self.assertEqual(status["subscribed_count"], 0)

    def test_sync_subscription_safely_conventions(self) -> None:
        # 未启用 → 空跳（None，不记心跳）
        self._config.qmt_quotes_enabled = False
        self.assertIsNone(sync_subscription_safely())

        # 正常同步 → 真值（心跳 success）
        self._config.qmt_quotes_enabled = True
        self._config.qmt_manual_symbols = "113050.SH"
        outcome = sync_subscription_safely(service=self.service)
        self.assertTrue(outcome)
        self.assertEqual(outcome["status"], "ok")
        self.assertTrue(outcome["changed"])

        # 写失败 → task_failed 标记
        self.connection.fail_write = True
        self._config.qmt_manual_symbols = "600519.SH"
        failed = sync_subscription_safely(service=self.service)
        self.assertTrue(_is_failed_marker(failed))


if __name__ == "__main__":
    unittest.main()
