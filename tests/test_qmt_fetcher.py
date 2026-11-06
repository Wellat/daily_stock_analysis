# -*- coding: utf-8 -*-
"""Tests for the QMT local quotes-bridge fetcher (Redis read side)."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from data_provider.qmt_fetcher import (
    QmtFetcher,
    QmtRedisClient,
    _parse_qmt_timetag,
    _to_qmt_symbol,
    get_qmt_client,
    reset_qmt_client_cache,
)


class FakeRedisConnection:
    """最小 Redis 命令面（EXISTS/GET/HGET/HSET），供客户端注入。"""

    def __init__(self, store=None, hash_store=None):
        self.store = store or {}
        self.hash_store = hash_store or {}
        self.fail = False

    def exists(self, key):
        if self.fail:
            raise ConnectionError("redis unreachable")
        return 1 if key in self.store else 0

    def get(self, key):
        if self.fail:
            raise ConnectionError("redis unreachable")
        return self.store.get(key)

    def hget(self, key, field):
        if self.fail:
            raise ConnectionError("redis unreachable")
        return self.hash_store.get(f"{key}|{field}")

    def hset(self, key, field, value):
        if self.fail:
            raise ConnectionError("redis unreachable")
        self.hash_store[f"{key}|{field}"] = value
        return 1


def _make_client(store=None, hash_store=None):
    connection = FakeRedisConnection(store=store, hash_store=hash_store)
    client = QmtRedisClient(redis_url="redis://fake", connection=connection)
    return client, connection


def _daily_payload(bars, code="600519.SH"):
    return {
        "code": code,
        "dividend_type": "none",
        "updated_at": "2026-10-31 22:43:21",
        "bars": bars,
    }


def _bars(n, start="2026-10-01", base_close=10.0):
    dates = pd.bdate_range(start, periods=n)
    return [
        {
            "date": day.strftime("%Y%m%d"),
            "open": round(base_close + i, 2),
            "high": round(base_close + i + 0.5, 2),
            "low": round(base_close + i - 0.5, 2),
            "close": round(base_close + i, 2),
            "volume": 1000 + i,
            "amount": 20000.0 + i,
        }
        for i, day in enumerate(dates)
    ]


class QmtSymbolConversionTestCase(unittest.TestCase):
    def test_qmt_symbol_conversion_supports_cn_markets(self) -> None:
        # 沪深股票
        self.assertEqual(_to_qmt_symbol("600519"), "600519.SH")
        self.assertEqual(_to_qmt_symbol("600519.SH"), "600519.SH")
        self.assertEqual(_to_qmt_symbol("SH600519"), "600519.SH")
        self.assertEqual(_to_qmt_symbol("688001"), "688001.SH")
        self.assertEqual(_to_qmt_symbol("000001"), "000001.SZ")
        self.assertEqual(_to_qmt_symbol("300750.SZ"), "300750.SZ")
        # ETF：51/52/56/58 沪，15/16/18 深
        self.assertEqual(_to_qmt_symbol("510050"), "510050.SH")
        self.assertEqual(_to_qmt_symbol("588000"), "588000.SH")
        self.assertEqual(_to_qmt_symbol("159915"), "159915.SZ")
        # 可转债：11x 沪、12x 深
        self.assertEqual(_to_qmt_symbol("113050"), "113050.SH")
        self.assertEqual(_to_qmt_symbol("123270"), "123270.SZ")

    def test_qmt_symbol_conversion_rejects_unsupported_markets(self) -> None:
        self.assertEqual(_to_qmt_symbol("920748"), "")  # 北交所
        self.assertEqual(_to_qmt_symbol("HK01810"), "")  # 港股
        self.assertEqual(_to_qmt_symbol("AAPL"), "")  # 美股
        self.assertEqual(_to_qmt_symbol(""), "")
        self.assertEqual(_to_qmt_symbol("6005"), "")


class QmtRedisClientTestCase(unittest.TestCase):
    def setUp(self) -> None:
        reset_qmt_client_cache()

    def tearDown(self) -> None:
        reset_qmt_client_cache()

    def test_alive_heartbeat_and_quote_read(self) -> None:
        quote = {
            "code": "600519.SH",
            "last": 1392.05,
            "lastClose": 1354.0,
            "timetag": datetime.now().strftime("%Y%m%d %H:%M:%S"),
            "pvolume": 5992729,
        }
        client, _conn = _make_client(
            store={
                "qmt:hb": "2026-10-31 22:43:21|cb_quotes_bridge",
                "qmt:quote:600519.SH": json.dumps(quote),
            }
        )
        self.assertTrue(client.redis_lib_available)
        self.assertTrue(client.is_alive())
        payload = client.get_quote("600519.SH")
        self.assertEqual(payload["last"], 1392.05)
        self.assertIsNone(client.get_quote("510050.SH"))

    def test_subscription_roundtrip_validates_and_sorts(self) -> None:
        client, connection = _make_client()
        self.assertTrue(client.set_subscription(["510050.SH", "600519.SH", "bad", "600519.SH"]))
        written = json.loads(connection.hash_store["qmt:cfg:symbols|symbols"])
        self.assertEqual(written, ["510050.SH", "600519.SH"])

        connection.hash_store["qmt:cfg:symbols|symbols"] = json.dumps(["600519.SH", "oops"])
        self.assertEqual(client.get_subscription(), ["600519.SH"])

    def test_connection_failure_marks_client_down(self) -> None:
        client, connection = _make_client(store={"qmt:hb": "alive"})
        connection.fail = True
        self.assertFalse(client.is_alive())
        self.assertIsNone(client.get_quote("600519.SH"))
        # 退避期内不再触达连接（恢复连接也不读，直到退避窗口结束）
        connection.fail = False
        self.assertFalse(client.is_alive())
        self.assertEqual(client.get_subscription(), [])
        # 清除退避后恢复
        client.reset_backoff()
        self.assertTrue(client.is_alive())

    def test_redis_lib_missing_means_unavailable(self) -> None:
        client = QmtRedisClient(redis_url="redis://fake")
        with patch("data_provider.qmt_fetcher.redis_lib", None):
            self.assertFalse(client.redis_lib_available)
            self.assertFalse(client.is_alive())

    def test_client_cache_keyed_by_url(self) -> None:
        first = get_qmt_client("redis://a:6379/0")
        second = get_qmt_client("redis://a:6379/0")
        other = get_qmt_client("redis://b:6379/0")
        self.assertIs(first, second)
        self.assertIsNot(first, other)


class QmtFetcherDailyTestCase(unittest.TestCase):
    def _fetcher(self, store=None, hash_store=None):
        client, connection = _make_client(store=store, hash_store=hash_store)
        return QmtFetcher(client=client), connection

    def test_daily_parses_bars_and_converts_volume(self) -> None:
        bars = _bars(10, start="2026-10-16")
        store = {
            "qmt:hb": "alive",
            "qmt:daily:600519.SH": json.dumps(_daily_payload(bars)),
        }
        fetcher, _conn = self._fetcher(store)
        df = fetcher.get_daily_data("600519", start_date="2026-10-16", end_date="2026-12-31")
        self.assertEqual(list(df.columns)[:8], ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"])
        self.assertEqual(len(df), 10)
        self.assertEqual(pd.Timestamp(df.iloc[0]["date"]).strftime("%Y-%m-%d"), "2026-10-16")
        # 股票成交量：手 ×100
        self.assertEqual(float(df.iloc[0]["volume"]), 1000 * 100)
        # 尾部百分比首行补 0
        self.assertEqual(float(df.iloc[0]["pct_chg"]), 0.0)

    def test_daily_convertible_bond_volume_uses_lot_ten(self) -> None:
        bars = _bars(5, start="2026-10-26")
        store = {
            "qmt:hb": "alive",
            "qmt:daily:123270.SZ": json.dumps(_daily_payload(bars, code="123270.SZ")),
        }
        fetcher, _conn = self._fetcher(store)
        df = fetcher.get_daily_data("123270", start_date="2026-10-26", end_date="2026-12-31")
        self.assertEqual(len(df), 5)
        # 转债成交量：手 ×10
        self.assertEqual(float(df.iloc[0]["volume"]), 1000 * 10)

    def test_daily_coverage_guard_returns_empty_when_window_too_old(self) -> None:
        bars = _bars(30, start="2026-09-18")  # 最早 2026-09-18
        store = {
            "qmt:hb": "alive",
            "qmt:daily:600519.SH": json.dumps(_daily_payload(bars)),
        }
        fetcher, _conn = self._fetcher(store)
        # 请求窗口早于最早 bar → 覆盖不住 → 空帧滑落（allow_empty_daily_data）
        df = fetcher.get_daily_data("600519", start_date="2026-01-01", end_date="2026-12-31")
        self.assertTrue(df.empty)

    def test_daily_empty_without_heartbeat_or_support(self) -> None:
        bars = _bars(5, start="2026-10-26")
        # 有数据但心跳缺失 → 不可用
        store = {"qmt:daily:600519.SH": json.dumps(_daily_payload(bars))}
        fetcher, _conn = self._fetcher(store)
        self.assertTrue(fetcher.get_daily_data("600519", days=5).empty)
        # 心跳在但代码不支持（北交所/港股）
        fetcher2, _conn2 = self._fetcher({"qmt:hb": "alive"})
        self.assertTrue(fetcher2.get_daily_data("920748", days=5).empty)
        self.assertTrue(fetcher2.get_daily_data("HK01810", days=5).empty)

    def test_is_available_for_request_requires_heartbeat(self) -> None:
        fetcher_alive, _ = self._fetcher({"qmt:hb": "alive"})
        self.assertTrue(fetcher_alive.is_available_for_request("daily_data"))
        fetcher_dead, _ = self._fetcher({})
        self.assertFalse(fetcher_dead.is_available_for_request("realtime_quote"))
        # 未知 capability 不拦截其他探测
        self.assertTrue(fetcher_dead.is_available_for_request("something_else"))


class QmtFetcherRealtimeTestCase(unittest.TestCase):
    def _quote_payload(self, *, timetag=None, last=1392.05, last_close=1354.0):
        return {
            "code": "600519.SH",
            "last": last,
            "open": 1363.0,
            "high": 1393.37,
            "low": 1352.98,
            "lastClose": last_close,
            "volume": 59927,
            "pvolume": 5992729,
            "amount": 8257381800.0,
            "timetag": timetag or datetime.now().strftime("%Y%m%d %H:%M:%S"),
            "written_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _fetcher_with_quote(self, payload):
        store = {
            "qmt:hb": "alive",
            "qmt:quote:600519.SH": json.dumps(payload),
        }
        client, _conn = _make_client(store)
        return QmtFetcher(client=client)

    def test_realtime_quote_field_mapping(self) -> None:
        fetcher = self._fetcher_with_quote(self._quote_payload())
        quote = fetcher.get_realtime_quote("600519.SH")
        self.assertIsNotNone(quote)
        self.assertEqual(quote.source.value, "qmt")
        self.assertEqual(quote.price, 1392.05)
        self.assertEqual(quote.pre_close, 1354.0)
        self.assertAlmostEqual(quote.change_pct, (1392.05 - 1354.0) / 1354.0 * 100, places=6)
        # pvolume（股）而非 volume（手）
        self.assertEqual(quote.volume, 5992729)
        self.assertEqual(quote.amount, 8257381800.0)
        self.assertEqual(quote.market, "cn")
        self.assertTrue(quote.has_basic_data())
        self.assertIsNotNone(quote.provider_timestamp)

    def test_parse_qmt_timetag(self) -> None:
        parsed = _parse_qmt_timetag("20261030 15:00:01")
        self.assertEqual(parsed, datetime(2026, 10, 30, 15, 0, 1))
        self.assertIsNone(_parse_qmt_timetag("not-a-time"))
        self.assertIsNone(_parse_qmt_timetag(""))

    def test_realtime_stale_in_session_degrades_to_none(self) -> None:
        stale = (datetime.now() - timedelta(seconds=600)).strftime("%Y%m%d %H:%M:%S")
        fetcher = self._fetcher_with_quote(self._quote_payload(timetag=stale))
        with patch("data_provider.qmt_fetcher._cn_market_in_session", return_value=True):
            self.assertIsNone(fetcher.get_realtime_quote("600519.SH"))

    def test_realtime_stale_outside_session_returns_frozen_quote(self) -> None:
        stale = (datetime.now() - timedelta(seconds=3600)).strftime("%Y%m%d %H:%M:%S")
        fetcher = self._fetcher_with_quote(self._quote_payload(timetag=stale))
        with patch("data_provider.qmt_fetcher._cn_market_in_session", return_value=False):
            quote = fetcher.get_realtime_quote("600519.SH")
        self.assertIsNotNone(quote)
        self.assertEqual(quote.price, 1392.05)

    def test_realtime_fresh_in_session_returned(self) -> None:
        fetcher = self._fetcher_with_quote(self._quote_payload())
        with patch("data_provider.qmt_fetcher._cn_market_in_session", return_value=True):
            self.assertIsNotNone(fetcher.get_realtime_quote("600519.SH"))

    def test_realtime_missing_or_invalid_returns_none(self) -> None:
        store = {"qmt:hb": "alive"}  # 无 quote key
        client, _conn = _make_client(store)
        fetcher = QmtFetcher(client=client)
        self.assertIsNone(fetcher.get_realtime_quote("600519.SH"))
        self.assertIsNone(fetcher.get_realtime_quote("920748"))


class QmtRoutingTestCase(unittest.TestCase):
    def test_daily_routing_cn_only(self) -> None:
        from data_provider.base import DataFetcherManager

        fetchers = DataFetcherManager()._get_fetchers_snapshot()
        kept_cn = DataFetcherManager._filter_daily_fetchers_for_market(fetchers, "cn")
        kept_hk = DataFetcherManager._filter_daily_fetchers_for_market(fetchers, "hk")
        self.assertTrue(any(f.name == "QmtFetcher" for f in kept_cn))
        self.assertFalse(any(f.name == "QmtFetcher" for f in kept_hk))

    def test_realtime_qmt_token_branch_serves_local_quote(self) -> None:
        from data_provider.base import DataFetcherManager

        store = {
            "qmt:hb": "alive",
            "qmt:quote:600519.SH": json.dumps(
                {
                    "code": "600519.SH",
                    "last": 1392.05,
                    "lastClose": 1354.0,
                    "pvolume": 5992729,
                    "timetag": datetime.now().strftime("%Y%m%d %H:%M:%S"),
                }
            ),
        }
        client, _conn = _make_client(store)
        manager = DataFetcherManager(fetchers=[QmtFetcher(client=client)])
        fake_config = SimpleNamespace(
            enable_realtime_quote=True,
            realtime_source_priority="qmt",
            realtime_cache_ttl=600,
        )
        with patch("src.config.get_config", return_value=fake_config):
            quote = manager.get_realtime_quote("600519.SH", log_final_failure=False)
        self.assertIsNotNone(quote)
        self.assertEqual(quote.source.value, "qmt")
        self.assertEqual(quote.price, 1392.05)

    def test_realtime_priority_auto_prepends_qmt(self) -> None:
        import os

        from src.config import Config

        saved = {key: os.environ.get(key) for key in ("REALTIME_SOURCE_PRIORITY", "TUSHARE_TOKEN", "QMT_QUOTES_ENABLED")}
        try:
            os.environ.pop("REALTIME_SOURCE_PRIORITY", None)
            os.environ.pop("TUSHARE_TOKEN", None)
            os.environ["QMT_QUOTES_ENABLED"] = "true"
            self.assertTrue(Config._resolve_realtime_source_priority().startswith("qmt,"))

            os.environ["QMT_QUOTES_ENABLED"] = "false"
            self.assertFalse(Config._resolve_realtime_source_priority().startswith("qmt,"))

            # 显式配置优先级时原样尊重，不注入
            os.environ["REALTIME_SOURCE_PRIORITY"] = "tencent"
            self.assertEqual(Config._resolve_realtime_source_priority(), "tencent")
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
