# -*- coding: utf-8 -*-
"""QMT 本地行情桥 fetcher（Redis 读取侧）。

链路契约见 ``docs/qmt-quotes-link-01.md``：QMT 客户端策略只写、DSA 只读。

- ``qmt:hb``            心跳（TTL 30s），``EXISTS`` 即链路存活
- ``qmt:quote:{code}``  实时快照 JSON（30s 一档，TTL 300s）
- ``qmt:daily:{code}``  最近 30 根日K（不复权，TTL 3 天）
- ``qmt:cfg:symbols``   订阅列表 hash（由 ``src/services/qmt_quotes_service.py`` 维护）

限制与策略：
- 仅支持沪深 6 位码（A 股/ETF/可转债）；北交所、港美股不接入
- 日线只有 30 根不复权数据：请求窗口盖不住时返回空帧，静默滑落到
  下一个数据源（TencentFetcher 同款 ``allow_empty_daily_data`` 模式）
- 实时快照在盘中停滞超过 ``QMT_STALE_SECONDS``（默认 120s）时返回
  ``None`` 降级到外部源（桥接文档 §5.4 建议）；非交易时段的定格快照
  是合法最新价，照常返回
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

try:
    import redis as redis_lib
except ImportError:  # pragma: no cover - optional dependency, degrade silently
    redis_lib = None

from .base import (
    BaseFetcher,
    STANDARD_COLUMNS,
    _is_convertible_bond_code,
    normalize_stock_code,
)
from .realtime_types import RealtimeSource, UnifiedRealtimeQuote, safe_float, safe_int

logger = logging.getLogger(__name__)

QMT_SYMBOL_PATTERN = re.compile(r"^[0-9]{6}\.(SH|SZ)$")

# ETF 前缀与 base.ETF_PREFIXES 保持一致（51/52/56/58 沪，15/16/18 深）
_SH_ETF_PREFIXES = ("51", "52", "56", "58")
_SZ_ETF_PREFIXES = ("15", "16", "18")


def _to_qmt_symbol(stock_code: str) -> str:
    """任意代码形态 → ``NNNNNN.SH`` / ``NNNNNN.SZ``；不支持的市场返回空串。"""
    code = normalize_stock_code(stock_code)
    if not code or not code.isdigit() or len(code) != 6:
        return ""
    # 沪转债 11xxxx / 深转债 12xxxx
    if code.startswith("11"):
        return f"{code}.SH"
    if code.startswith("12"):
        return f"{code}.SZ"
    if code.startswith(_SH_ETF_PREFIXES):
        return f"{code}.SH"
    if code.startswith(_SZ_ETF_PREFIXES):
        return f"{code}.SZ"
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    # 92xxxx / 4xxxxx / 8xxxxx 等北交所及其他代码不支持
    return ""


def _parse_qmt_timetag(value: Any) -> Optional[datetime]:
    """``20261030 15:00:01`` → datetime；解析失败返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y%m%d %H:%M:%S")
    except ValueError:
        return None


def _read_qmt_priority() -> int:
    raw_value = os.getenv("QMT_PRIORITY", "0")
    try:
        return int(str(raw_value).strip())
    except (TypeError, ValueError):
        logger.warning("QMT_PRIORITY=%r is not a valid integer; falling back to 0", raw_value)
        return 0


def _read_qmt_stale_seconds() -> int:
    raw_value = os.getenv("QMT_STALE_SECONDS", "120")
    try:
        seconds = int(str(raw_value).strip())
        return seconds if seconds > 0 else 120
    except (TypeError, ValueError):
        return 120


def _empty_daily_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=STANDARD_COLUMNS)


def _cn_market_in_session() -> bool:
    """A 股是否处于连续竞价/收盘竞价时段（UNKNOWN 时按不在盘中处理，不降级）。"""
    try:
        from src.core.trading_calendar import MarketPhase, infer_market_phase

        phase = infer_market_phase("cn")
        return phase in {MarketPhase.INTRADAY, MarketPhase.CLOSING_AUCTION}
    except Exception:  # noqa: BLE001 - 判断失败时不做 stale 降级（fail-open 返回数据）
        return False


class QmtRedisClient:
    """QMT 行情桥 Redis 读取客户端（懒连接 + 失败退避 + 心跳缓存）。

    连接失败后进入 ``_RETRY_BACKOFF_SECONDS`` 退避期，期间所有调用直接返回
    不可用，避免高频巡检反复撞不可达的 Redis 刷日志。
    """

    HEARTBEAT_KEY = "qmt:hb"
    QUOTE_KEY_PREFIX = "qmt:quote:"
    DAILY_KEY_PREFIX = "qmt:daily:"
    CONFIG_KEY = "qmt:cfg:symbols"
    CONFIG_FIELD = "symbols"

    _HEARTBEAT_CACHE_TTL_SECONDS = 10.0
    _RETRY_BACKOFF_SECONDS = 60.0

    def __init__(self, redis_url: Optional[str] = None, connection: Optional[Any] = None):
        self._redis_url = redis_url or "redis://localhost:6379/0"
        # 测试可直接注入 fake 连接，绕过 redis-py 依赖
        self._connection = connection
        self._redis_client: Optional[Any] = None
        self._lock = threading.Lock()
        self._down_until = 0.0
        self._alive_cache: Optional[bool] = None
        self._alive_cached_at = 0.0

    @property
    def redis_url(self) -> str:
        return self._redis_url

    @property
    def redis_lib_available(self) -> bool:
        return redis_lib is not None or self._connection is not None

    def _get_connection(self) -> Optional[Any]:
        if self._connection is not None:
            return self._connection
        if redis_lib is None:
            return None
        if time.monotonic() < self._down_until:
            return None
        if self._redis_client is None:
            try:
                self._redis_client = redis_lib.Redis.from_url(
                    self._redis_url,
                    socket_timeout=0.5,
                    decode_responses=True,
                )
            except Exception as exc:  # noqa: BLE001 - 建连配置错误按不可用处理
                self._mark_down(exc)
                return None
        return self._redis_client

    def _mark_down(self, exc: Exception) -> None:
        self._down_until = time.monotonic() + self._RETRY_BACKOFF_SECONDS
        self._alive_cache = False
        self._alive_cached_at = time.monotonic()
        logger.debug("[QMT行情桥] Redis 不可用（%s），%ss 内跳过重试", exc, self._RETRY_BACKOFF_SECONDS)

    def _execute(self, operation: Callable[[Any], Any]) -> Optional[Any]:
        """执行一条 Redis 命令；不可用/出错返回 None（出错同时进入退避）。"""
        connection = self._get_connection()
        if connection is None:
            return None
        try:
            return operation(connection)
        except Exception as exc:  # noqa: BLE001 - 本地源故障一律降级，不上抛
            self._mark_down(exc)
            return None

    def is_alive(self) -> bool:
        """心跳判活（结果缓存 10s）：``EXISTS qmt:hb`` 为真即链路存活。"""
        now = time.monotonic()
        if self._alive_cache is not None and now - self._alive_cached_at < self._HEARTBEAT_CACHE_TTL_SECONDS:
            return self._alive_cache
        result = self._execute(lambda conn: conn.exists(self.HEARTBEAT_KEY))
        alive = bool(result)
        self._alive_cache = alive
        self._alive_cached_at = now
        return alive

    def get_heartbeat(self) -> Optional[str]:
        """心跳原始值（``<时间>|<策略名>``），供状态展示。"""
        return self._execute(lambda conn: conn.get(self.HEARTBEAT_KEY))

    def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        raw = self._execute(lambda conn: conn.get(f"{self.QUOTE_KEY_PREFIX}{symbol}"))
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            logger.debug("[QMT行情桥] quote %s JSON 解析失败", symbol)
            return None
        return payload if isinstance(payload, dict) else None

    def get_daily(self, symbol: str) -> Optional[Dict[str, Any]]:
        raw = self._execute(lambda conn: conn.get(f"{self.DAILY_KEY_PREFIX}{symbol}"))
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            logger.debug("[QMT行情桥] daily %s JSON 解析失败", symbol)
            return None
        return payload if isinstance(payload, dict) else None

    def get_subscription(self) -> List[str]:
        """当前订阅列表；key 不存在/解析失败返回空列表。"""
        raw = self._execute(
            lambda conn: conn.hget(self.CONFIG_KEY, self.CONFIG_FIELD)
        )
        if not raw:
            return []
        try:
            symbols = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(symbols, list):
            return []
        return [str(item) for item in symbols if QMT_SYMBOL_PATTERN.match(str(item))]

    def set_subscription(self, symbols: List[str]) -> bool:
        """整组覆盖写回订阅列表（校验 pattern、去重、排序）。"""
        normalized = sorted({item for item in symbols if QMT_SYMBOL_PATTERN.match(str(item))})
        result = self._execute(
            lambda conn: conn.hset(
                self.CONFIG_KEY,
                self.CONFIG_FIELD,
                json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
            )
        )
        return result is not None

    def reset_backoff(self) -> None:
        """测试辅助：清除退避与心跳缓存。"""
        with self._lock:
            self._down_until = 0.0
            self._alive_cache = None
            self._alive_cached_at = 0.0


_client_cache: Dict[str, QmtRedisClient] = {}
_client_cache_lock = threading.Lock()


def get_qmt_client(redis_url: Optional[str] = None) -> QmtRedisClient:
    """按 URL 缓存的单例客户端（配置 reload 换 URL 时自动换新实例）。"""
    from src.config import get_config

    url = redis_url or getattr(get_config(), "qmt_redis_url", None) or "redis://localhost:6379/0"
    with _client_cache_lock:
        client = _client_cache.get(url)
        if client is None:
            client = QmtRedisClient(redis_url=url)
            _client_cache[url] = client
        return client


def reset_qmt_client_cache() -> None:
    """测试辅助：清空客户端单例缓存。"""
    with _client_cache_lock:
        _client_cache.clear()


class QmtFetcher(BaseFetcher):
    """Redis 本地源 fetcher：A 股/ETF/可转债的实时快照与最近 30 根日K。"""

    name = "QmtFetcher"
    # 本地源优先（与 AkshareFetcher 同为 0 时依赖初始化列表中的插入序居前）
    priority = 0
    allow_empty_daily_data = True

    def __init__(self, client: Optional[QmtRedisClient] = None) -> None:
        self.priority = _read_qmt_priority()
        self._client = client or get_qmt_client()
        self._stale_seconds = _read_qmt_stale_seconds()

    @property
    def client(self) -> QmtRedisClient:
        return self._client

    def is_available_for_request(self, capability: str = "") -> bool:
        """心跳不在（QMT 未跑/Redis 不可达）时让管理器直接跳过本源。"""
        if capability in {"", "daily_data", "realtime_quote"}:
            return self._client.is_alive()
        return True

    # ------------------------------------------------------------------
    # 日线（BaseFetcher 模板方法）
    # ------------------------------------------------------------------
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        symbol = _to_qmt_symbol(stock_code)
        if not symbol:
            return _empty_daily_frame()
        if not self._client.is_alive():
            return _empty_daily_frame()

        payload = self._client.get_daily(symbol)
        bars = (payload or {}).get("bars") or []
        is_cb = _is_convertible_bond_code(stock_code)
        rows = [
            _parse_daily_bar(bar, lot_multiplier=10 if is_cb else 100)
            for bar in bars
        ]
        rows = [row for row in rows if row is not None]
        if not rows:
            logger.info("QmtFetcher no daily bars for %s", stock_code)
            return _empty_daily_frame()

        df = pd.DataFrame(rows)
        first_date = str(df["date"].min())
        if first_date > start_date:
            # 本地只有最近 30 根：请求窗口更早时盖不住，留给外部源
            logger.info(
                "QmtFetcher daily history cannot cover window for %s: first_date=%s requested_start=%s",
                stock_code,
                first_date,
                start_date,
            )
            return _empty_daily_frame()

        df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
        if df.empty:
            logger.info(
                "QmtFetcher daily history outside requested range for %s: %s~%s",
                stock_code,
                start_date,
                end_date,
            )
            return _empty_daily_frame()
        return df

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        normalized = df.copy()
        for column in ("open", "high", "low", "close", "volume", "amount"):
            if column in normalized.columns:
                normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        if "pct_chg" not in normalized.columns:
            normalized["pct_chg"] = normalized["close"].pct_change().fillna(0.0) * 100
        normalized = normalized[["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]]
        return normalized

    # ------------------------------------------------------------------
    # 实时快照
    # ------------------------------------------------------------------
    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        symbol = _to_qmt_symbol(stock_code)
        if not symbol:
            return None
        if not self._client.is_alive():
            return None

        payload = self._client.get_quote(symbol)
        if not isinstance(payload, dict):
            return None

        price = safe_float(payload.get("last"))
        if not price or price <= 0:
            return None

        provider_ts = _parse_qmt_timetag(payload.get("timetag"))
        if provider_ts is not None and self._quote_stale(provider_ts):
            logger.info(
                "[QMT行情桥] %s 盘中快照停滞 %.0fs（超过 %ss），降级到外部源",
                stock_code,
                (datetime.now() - provider_ts).total_seconds(),
                self._stale_seconds,
            )
            return None

        pre_close = safe_float(payload.get("lastClose"))
        change_amount = price - pre_close if pre_close else None
        change_pct = (change_amount / pre_close * 100) if pre_close else None
        return UnifiedRealtimeQuote(
            code=normalize_stock_code(stock_code),
            name="",
            source=RealtimeSource.QMT,
            price=price,
            change_pct=change_pct,
            change_amount=change_amount,
            # pvolume 已是股/份/张口径，volume 字段约定同为股
            volume=safe_int(payload.get("pvolume")),
            amount=safe_float(payload.get("amount")),
            open_price=safe_float(payload.get("open")),
            high=safe_float(payload.get("high")),
            low=safe_float(payload.get("low")),
            pre_close=pre_close,
            market="cn",
            currency="CNY",
            provider_timestamp=provider_ts.isoformat() if provider_ts else None,
            data_quality="ok",
        )

    def _quote_stale(self, provider_ts: datetime) -> bool:
        lag = (datetime.now() - provider_ts).total_seconds()
        if lag <= self._stale_seconds:
            return False
        # 非交易时段的定格快照是合法最新价；只在盘中停滞才降级
        return _cn_market_in_session()


def _parse_daily_bar(bar: Any, *, lot_multiplier: int) -> Optional[Dict[str, Any]]:
    if not isinstance(bar, dict):
        return None
    date_text = str(bar.get("date") or "")
    try:
        date = datetime.strptime(date_text, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None
    volume = safe_float(bar.get("volume"))
    return {
        "date": date,
        "open": safe_float(bar.get("open")),
        "high": safe_float(bar.get("high")),
        "low": safe_float(bar.get("low")),
        "close": safe_float(bar.get("close")),
        # 桥接契约：日线 volume 单位为手（股票/ETF 1手=100股，转债 1手=10张）
        "volume": volume * lot_multiplier if volume is not None else None,
        "amount": safe_float(bar.get("amount")),
    }
