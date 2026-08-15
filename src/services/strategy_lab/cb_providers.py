# -*- coding: utf-8 -*-
"""Convertible-bond data providers for Strategy Lab."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
import json
import logging
import os
import requests
import subprocess
import time
from typing import Any, Dict, List, Optional, Protocol

import pandas as pd

from data_provider.akshare_fetcher import _akshare_call_with_timeout
from src.config import get_config

try:
    from src.patches.eastmoney_patch import eastmoney_patch
except ImportError:  # pragma: no cover - patch is optional at runtime
    eastmoney_patch = None

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConvertibleBondSyncPayload:
    """Normalized convertible-bond payload ready for Strategy Lab persistence."""

    cb_basic: List[Dict[str, Any]] = field(default_factory=list)
    cb_terms: List[Dict[str, Any]] = field(default_factory=list)
    cb_daily_factors: List[Dict[str, Any]] = field(default_factory=list)
    cb_events: List[Dict[str, Any]] = field(default_factory=list)


class ConvertibleBondDataProvider(Protocol):
    name: str

    def fetch(self, *, market: str, symbols: Optional[List[str]] = None) -> ConvertibleBondSyncPayload:
        """Fetch and normalize convertible-bond data."""


class AkshareConvertibleBondProvider:
    """Fetch convertible-bond master/factor/event data via AkShare."""

    name = "akshare"

    def __init__(self, *, timeout: float = 30.0):
        self.timeout = timeout

    def fetch(self, *, market: str, symbols: Optional[List[str]] = None) -> ConvertibleBondSyncPayload:
        """Fetch the full payload at once (kept for callers that do not batch)."""
        basics, terms = self.fetch_list(market=market, symbols=symbols)
        factor_rows: List[Dict[str, Any]] = []
        for basic in basics:
            factor_rows.extend(self.fetch_factors(basic["bond_code"]))
        return ConvertibleBondSyncPayload(
            cb_basic=basics,
            cb_terms=terms,
            cb_daily_factors=factor_rows,
            cb_events=self.fetch_events(symbols=symbols),
        )

    def fetch_list(self, *, market: str, symbols: Optional[List[str]] = None) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Fetch master + terms rows for the convertible-bond list.

        Returns ``(basics, terms)`` so callers can persist them before pulling
        the (much slower) per-symbol daily bars.
        """
        if market != "cn":
            raise ValueError("AkShare convertible-bond provider supports cn market only")
        symbol_filter = {str(symbol).strip().lower() for symbol in symbols or [] if str(symbol).strip()}
        # akshare 的 bond_zh_cov 硬编码 71 个列名，而东财接口实际返回 72 列，
        # 在 akshare 1.17.x 会直接抛 Length mismatch；这里改为直接请求东财接口按英文
        # 字段自映射，避免依赖 akshare 的列重命名。
        records = self._fetch_cb_list()
        basics = [
            row
            for row in (self._basic_row(record) for record in records)
            if row and self._matches_symbol(row["bond_code"], symbol_filter)
        ]
        terms = [
            terms_row
            for record in records
            for terms_row in [self._terms_row(record)]
            if terms_row and self._matches_symbol(terms_row["bond_code"], symbol_filter)
        ]
        return basics, terms

    def fetch_factors(self, bond_code: str) -> List[Dict[str, Any]]:
        """Fetch daily factor rows for one symbol."""
        import akshare as ak

        return self._fetch_daily_factors(ak, bond_code, set())

    def fetch_events(self, *, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Fetch redeem events, best-effort (returns [] on upstream failure)."""
        import akshare as ak

        symbol_filter = {str(symbol).strip().lower() for symbol in symbols or [] if str(symbol).strip()}
        try:
            redeem_df = _akshare_call_with_timeout(
                ak.bond_cb_redeem_jsl,
                timeout=self.timeout,
                call_name="strategy_lab_cb_redeem_events",
            )
            return [
                event
                for event in (self._redeem_event_row(record) for record in _records(redeem_df))
                if event and self._matches_symbol(event["bond_code"], symbol_filter)
            ]
        except Exception:
            return []

    def _fetch_cb_list(self) -> List[Dict[str, Any]]:
        """Fetch the full convertible-bond list from the Eastmoney datacenter API."""
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        base_params = {
            "sortColumns": "PUBLIC_START_DATE",
            "sortTypes": "-1",
            "pageSize": "500",
            "pageNumber": "1",
            "reportName": "RPT_BOND_CB_LIST",
            "columns": "ALL",
            "quoteColumns": (
                "f2~01~CONVERT_STOCK_CODE~CONVERT_STOCK_PRICE,"
                "f235~10~SECURITY_CODE~TRANSFER_PRICE,"
                "f236~10~SECURITY_CODE~TRANSFER_VALUE,"
                "f2~10~SECURITY_CODE~CURRENT_BOND_PRICE,"
                "f237~10~SECURITY_CODE~TRANSFER_PREMIUM_RATIO,"
                "f239~10~SECURITY_CODE~RESALE_TRIG_PRICE,"
                "f240~10~SECURITY_CODE~REDEEM_TRIG_PRICE,"
                "f23~01~CONVERT_STOCK_CODE~PBV_RATIO"
            ),
            "source": "WEB",
            "client": "WEB",
        }
        records: List[Dict[str, Any]] = []
        page = 1
        while True:
            params = {**base_params, "pageNumber": page}
            resp = requests.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            result = (resp.json() or {}).get("result")
            if not result or not result.get("data"):
                break
            records.extend(result["data"])
            if page >= int(result.get("pages") or 1):
                break
            page += 1
        return records

    @staticmethod
    def _matches_symbol(bond_code: str, symbol_filter: set[str]) -> bool:
        if not symbol_filter:
            return True
        code = str(bond_code).lower()
        return code in symbol_filter or f"cn.convertible_bond.{code}" in symbol_filter

    def _fetch_daily_factors(self, ak: Any, bond_code: str, symbol_filter: set[str]) -> List[Dict[str, Any]]:
        if not self._matches_symbol(bond_code, symbol_filter):
            return []
        prefixed = _akshare_cov_symbol(bond_code)
        try:
            df = _akshare_call_with_timeout(
                ak.bond_zh_hs_cov_daily,
                symbol=prefixed,
                timeout=self.timeout,
                call_name=f"strategy_lab_cb_daily_{bond_code}",
            )
        except Exception:
            return []
        rows: List[Dict[str, Any]] = []
        for record in _records(df):
            trade_date = _first_value(record, "date", "日期", "trade_date")
            close = _first_value(record, "close", "收盘", "收盘价")
            parsed_date = _parse_date(trade_date)
            if parsed_date is None:
                continue
            rows.append(
                {
                    "bond_code": bond_code,
                    "trade_date": parsed_date,
                    "close": _parse_float(close),
                }
            )
        return rows

    @staticmethod
    def _basic_row(record: Dict[str, Any]) -> Dict[str, Any] | None:
        bond_code = _first_value(record, "SECURITY_CODE", "债券代码", "代码", "bond_code", "转债代码")
        if not bond_code:
            return None
        return {
            "bond_code": _strip_code(bond_code),
            "bond_name": _first_value(record, "SECURITY_NAME_ABBR", "债券简称", "转债名称", "名称", "bond_name") or str(bond_code),
            "stock_code": _strip_code(_first_value(record, "CONVERT_STOCK_CODE", "正股代码", "stock_code") or ""),
            "stock_name": _first_value(record, "SECURITY_SHORT_NAME", "正股简称", "正股名称", "stock_name"),
            "market": "cn",
            "list_date": _parse_date(_first_value(record, "LISTING_DATE", "上市时间", "上市日期", "list_date")),
            "maturity_date": _parse_date(_first_value(record, "CEASE_DATE", "EXPIRE_DATE", "到期时间", "到期日期", "maturity_date")),
            "remaining_size": _parse_float(_first_value(record, "剩余规模", "余额", "remaining_size")),
            "current_premium_rate": _parse_float(_first_value(record, "TRANSFER_PREMIUM_RATIO", "转股溢价率", "溢价率", "current_premium_rate")),
            "convert_price": _parse_float(_first_value(record, "TRANSFER_PRICE", "INITIAL_TRANSFER_PRICE", "转股价", "convert_price")),
            "terms": {
                "provider": "akshare",
                "rating": _first_value(record, "RATING", "信用评级"),
                "issue_scale": _parse_float(_first_value(record, "ACTUAL_ISSUE_SCALE", "发行规模")),
                "delist_date": _first_value(record, "DELIST_DATE"),
            },
        }

    @staticmethod
    def _terms_row(record: Dict[str, Any]) -> Dict[str, Any] | None:
        bond_code = _first_value(record, "SECURITY_CODE", "债券代码", "代码", "bond_code", "转债代码")
        if not bond_code:
            return None
        values = {
            "bond_code": _strip_code(bond_code),
            "redeem_clause": _first_value(record, "REDEEM_CLAUSE", "强赎条款", "强赎条件", "redeem_clause"),
            "down_revise_clause": _first_value(record, "DOWN_REVISE_CLAUSE", "下修条款", "下修条件", "down_revise_clause"),
            "put_clause": _first_value(record, "RESALE_CLAUSE", "回售条款", "回售条件", "put_clause"),
            "redeem_trigger_price": _parse_float(_first_value(record, "REDEEM_TRIG_PRICE", "强赎触发价", "redeem_trigger_price")),
            "down_revise_trigger_price": _parse_float(_first_value(record, "下修触发价", "down_revise_trigger_price")),
            "put_trigger_price": _parse_float(_first_value(record, "RESALE_TRIG_PRICE", "回售触发价", "put_trigger_price")),
        }
        return values if any(value is not None for key, value in values.items() if key != "bond_code") else None

    @staticmethod
    def _redeem_event_row(record: Dict[str, Any]) -> Dict[str, Any] | None:
        bond_code = _first_value(record, "代码", "债券代码", "转债代码", "bond_code")
        if not bond_code:
            return None
        event_date = _parse_date(_first_value(record, "公告日期", "日期", "event_date"))
        if event_date is None:
            return None
        return {
            "bond_code": _strip_code(bond_code),
            "event_date": event_date,
            "event_type": "strong_redeem",
            "event_detail": str(_first_value(record, "强赎状态", "状态", "详情", "event_detail") or ""),
        }


class JisiluConvertibleBondProvider:
    """Fetch the current Jisilu convertible-bond snapshot.

    Jisilu is a live valuation source rather than a historical OHLC store.  The
    provider therefore writes a dated factor snapshot and master/terms fields;
    historical backtests should continue to use a provider that supplies daily
    bars (AkShare or a payload import).
    """

    name = "jisilu"
    endpoint = "https://www.jisilu.cn/data/cbnew/"

    def __init__(self, *, timeout: float = 30.0, session: Any = None, cookie: Optional[str] = None):
        self.timeout = timeout
        self.session = session
        self.cookie = cookie or os.getenv("JISILU_COOKIE")

    def fetch(self, *, market: str, symbols: Optional[List[str]] = None) -> ConvertibleBondSyncPayload:
        if market != "cn":
            raise ValueError("Jisilu convertible-bond provider supports cn market only")
        import requests

        client = self.session or requests.Session()
        headers = {
            "User-Agent": "daily-stock-analysis/strategy-lab",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        response = client.get(self.endpoint, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        rows = _extract_rows(payload)
        return _snapshot_payload(rows, symbols=symbols, source=self.name)


class OpencliConvertibleBondProvider:
    """Fetch convertible-bond master data via the local OpenCLI Jisilu adapter.

    Data chain: ``opencli jisilu cb-list``（列表）→ 逐只 ``opencli jisilu
    cb-detail``（详情）。支持活跃 / 已退市两种状态，活跃与退市的处理差异：
    退市列表走 ``--delisted`` 参数，且详情中的退市字段
    （``delist_reason``/``last_trading_date``/``last_conversion_date`` 等）
    一并落入元数据。
    """

    name = "opencli"

    def __init__(self, *, timeout: float = 120.0, executable: Optional[str] = None, workers: int = 3):
        self.timeout = timeout
        self.executable = executable or os.getenv("OPENCLI_BIN", "opencli")
        self.workers = max(1, workers)

    def fetch(
        self,
        *,
        market: str,
        symbols: Optional[List[str]] = None,
        include_delisted: bool = False,
    ) -> ConvertibleBondSyncPayload:
        """Fetch list + per-symbol detail at once (kept for callers that do not batch)."""
        if market != "cn":
            raise ValueError("OpenCLI Jisilu provider supports cn market only")
        symbol_filter = {str(symbol).strip().lower().split(".")[-1] for symbol in symbols or [] if str(symbol).strip()}
        basics: List[Dict[str, Any]] = []
        codes: List[str] = []
        for row in self.fetch_list(include_delisted=include_delisted):
            basic = _cb_list_basic_row(row)
            if not basic:
                continue
            if symbol_filter and basic["bond_code"].lower() not in symbol_filter:
                continue
            basics.append(basic)
            codes.append(basic["bond_code"])
        terms: List[Dict[str, Any]] = []
        events: List[Dict[str, Any]] = []
        detail_map = self.fetch_detail_batch(codes, workers=self.workers)
        for basic in basics:
            detail = detail_map.get(basic["bond_code"])
            if not detail:
                continue
            normalized = _cb_detail_normalize(detail)
            if not normalized:
                continue
            basic.update({key: value for key, value in normalized["basic"].items() if value is not None})
            basic["terms"] = {**(basic.get("terms") or {}), **normalized["meta"]}
            if normalized["status"]:
                basic["status"] = normalized["status"]
            terms.append(normalized["terms"])
            events.extend(normalized["events"])
        return ConvertibleBondSyncPayload(cb_basic=basics, cb_terms=terms, cb_events=events)

    def fetch_list(self, *, include_delisted: bool = False) -> List[Dict[str, Any]]:
        """Fetch the convertible-bond list (active by default, or delisted)."""
        if include_delisted:
            command = [self.executable, "jisilu", "cb-list", "--delisted", "-f", "json"]
        else:
            command = [self.executable, "jisilu", "cb-list", "-f", "json"]
        completed = self._run(command)
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError(f"OpenCLI cb-list returned invalid JSON for {command}") from exc
        return [row for row in _extract_rows(payload) if isinstance(row, dict)]

    def fetch_detail(self, bond_code: str) -> Optional[Dict[str, Any]]:
        """Fetch one convertible-bond detail row, or None when the adapter returns nothing."""
        command = [self.executable, "jisilu", "cb-detail", _strip_code(bond_code), "-f", "json"]
        completed = self._run(command)
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError(f"OpenCLI cb-detail returned invalid JSON for {_strip_code(bond_code)}") from exc
        rows = [row for row in _extract_rows(payload) if isinstance(row, dict)]
        return rows[0] if rows else None

    def fetch_detail_batch(
        self,
        bond_codes: List[str],
        workers: Optional[int] = None,
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """Concurrently fetch details; returns ``{bond_code: detail_or_None}``.

        A single detail failure never aborts the batch (the code maps to None).
        opencli drives a browser per invocation, so keep the default worker
        count low (3) to avoid exhausting the local browser pool.
        """
        pool_workers = max(1, workers or self.workers)
        results: Dict[str, Optional[Dict[str, Any]]] = {}
        if pool_workers <= 1 or len(bond_codes) <= 1:
            for code in bond_codes:
                try:
                    results[str(code)] = self.fetch_detail(str(code))
                except Exception as exc:  # noqa: BLE001 - keep going on single failure
                    logger.warning("cb-detail %s failed: %s", code, exc)
                    results[str(code)] = None
            return results
        with ThreadPoolExecutor(max_workers=pool_workers) as executor:
            future_map = {executor.submit(self.fetch_detail, str(code)): str(code) for code in bond_codes}
            for future in as_completed(future_map):
                code = future_map[future]
                try:
                    results[code] = future.result()
                except Exception as exc:  # noqa: BLE001 - keep going on single failure
                    logger.warning("cb-detail %s failed: %s", code, exc)
                    results[code] = None
        return results

    def _run(self, command: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )

    def normalize_list_row(self, record: Dict[str, Any]) -> Dict[str, Any] | None:
        """Public wrapper around ``_cb_list_basic_row`` for the sync service."""
        return _cb_list_basic_row(record)

    def normalize_detail(self, record: Dict[str, Any]) -> Dict[str, Any] | None:
        """Public wrapper around ``_cb_detail_normalize`` for the sync service."""
        return _cb_detail_normalize(record)


def get_convertible_bond_provider(source: str) -> ConvertibleBondDataProvider:
    source_norm = (source or "").strip().lower()
    if source_norm == "akshare":
        return AkshareConvertibleBondProvider()
    if source_norm == "jisilu":
        return JisiluConvertibleBondProvider()
    if source_norm == "opencli":
        return OpencliConvertibleBondProvider()
    raise ValueError(f"Unsupported convertible-bond provider: {source}")


def _extract_rows(payload: Any) -> List[Dict[str, Any]]:
    """Normalize common Jisilu/OpenCLI response envelopes."""
    if isinstance(payload, dict):
        for key in ("rows", "data", "items", "results"):
            if key in payload:
                return _extract_rows(payload[key])
        cell = payload.get("cell")
        if isinstance(cell, dict):
            return [cell]
        return [payload]
    if isinstance(payload, list):
        rows: List[Dict[str, Any]] = []
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("cell"), dict):
                rows.append(item["cell"])
            elif isinstance(item, dict):
                rows.append(item)
        return rows
    return []


def _snapshot_payload(rows: List[Dict[str, Any]], *, symbols: Optional[List[str]], source: str) -> ConvertibleBondSyncPayload:
    symbol_filter = {str(symbol).strip().lower().split(".")[-1] for symbol in symbols or [] if str(symbol).strip()}
    basics: List[Dict[str, Any]] = []
    terms: List[Dict[str, Any]] = []
    factors: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    snapshot_date = date.today()
    for record in rows:
        bond_code = _first_value(record, "bond_id", "bondId", "bond_code", "代码", "债券代码", "转债代码")
        if not bond_code:
            continue
        code = _strip_code(bond_code)
        if symbol_filter and code.lower() not in symbol_filter:
            continue
        basic = {
            "bond_code": code,
            "bond_name": _first_value(record, "bond_nm", "bondName", "bond_name", "债券简称") or code,
            "stock_code": _strip_code(_first_value(record, "stock_id", "stockId", "stock_code", "正股代码") or ""),
            "stock_name": _first_value(record, "stock_nm", "stockName", "stock_name", "正股简称"),
            "market": "cn",
            "list_date": _parse_date(_first_value(record, "list_dt", "listDate", "上市日期")),
            "maturity_date": _parse_date(_first_value(record, "maturity_dt", "maturityDate", "到期日期")),
            "remaining_size": _parse_float(_first_value(record, "curr_iss_amt", "remain_size", "remainSize", "剩余规模")),
            "current_premium_rate": _parse_float(_first_value(record, "premium_rt", "premiumRate", "转股溢价率")),
            "convert_price": _parse_float(_first_value(record, "convert_price", "convertPrice", "转股价")),
            "terms": {"provider": source, "rating": _first_value(record, "rating_cd", "rating")},
        }
        basics.append(basic)
        terms.append({
            "bond_code": code,
            "redeem_clause": _first_value(record, "redeem_clause", "redeem_trigger", "强赎条款"),
            "down_revise_clause": _first_value(record, "down_revise_clause", "下修条款"),
            "put_clause": _first_value(record, "put_clause", "回售条款"),
        })
        price = _parse_float(_first_value(record, "price", "bond_price", "现价", "价格"))
        premium = basic["current_premium_rate"]
        if price is not None:
            factors.append({
                "bond_code": code,
                "trade_date": snapshot_date,
                "close": price,
                "premium_rate": premium,
                "remaining_size": basic["remaining_size"],
                "redeem_alert": bool(_first_value(record, "redeem_flag", "redeem_status", "强赎状态")),
                "down_revise_alert": False,
                "put_alert": False,
            })
        event_date = _parse_date(_first_value(record, "redeem_dt", "redeemDate", "强赎公告日期"))
        if event_date is not None:
            events.append({
                "bond_code": code,
                "event_date": event_date,
                "event_type": "strong_redeem",
                "event_detail": str(_first_value(record, "redeem_status", "强赎状态") or ""),
            })
    return ConvertibleBondSyncPayload(cb_basic=basics, cb_terms=terms, cb_daily_factors=factors, cb_events=events)


def _records(frame: Any) -> List[Dict[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        return list(frame.to_dict(orient="records"))
    if isinstance(frame, list):
        return [row for row in frame if isinstance(row, dict)]
    return []


def _first_value(record: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def _strip_code(value: Any) -> str:
    return str(value).strip().split(".")[0]


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, float) and value != value:  # NaN
        return None
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text[:10], text.replace("/", "-")[:10], text.replace(".", "-")[:10]):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    text = str(value).strip().replace("%", "").replace(",", "")
    if text in {"-", "--", "nan", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _akshare_cov_symbol(bond_code: str) -> str:
    code = _strip_code(bond_code)
    if code.startswith(("11", "12")):
        return f"sh{code}" if code.startswith("11") else f"sz{code}"
    return code


# ---------------------------------------------------------------------------
# OpenCLI cb-list / cb-detail 解析（opencli 接口文档 .trae/documents/opencli-cb.md）
# ---------------------------------------------------------------------------

# cb-detail 中无独立列的字段，统一收进 strategy_lab_cb_basic.terms_json 元数据
CB_DETAIL_META_FIELDS = [
    "industry",
    "start_date",
    "convert_start_date",
    "put_start_date",
    "put_price",
    "redemption_price",
    "issue_size",
    "bond_rating",
    "force_redeem_countdown",
    "down_revise_countdown",
    "put_countdown",
    "delist_reason",
    "redemption_announcement_date",
    "last_trading_date",
    "last_conversion_date",
]


def _cb_list_basic_row(record: Dict[str, Any]) -> Dict[str, Any] | None:
    """Map one ``cb-list`` row onto a ``strategy_lab_cb_basic`` row stub."""
    bond_code = _first_value(record, "bondId", "bond_id", "bond_code", "债券代码", "代码")
    if not bond_code:
        return None
    code = _strip_code(bond_code)
    status_raw = str(_first_value(record, "status", "状态") or "active").strip().lower()
    status = "已退市" if status_raw in ("delisted", "已退市") else "正常"
    terms: Dict[str, Any] = {"provider": "opencli"}
    last_price = _parse_float(_first_value(record, "lastPrice", "last_price", "最后价格"))
    last_trade_date = _parse_date(_first_value(record, "lastTradeDate", "last_trade_date", "最后交易日"))
    if last_price is not None:
        terms["last_price"] = last_price
    if last_trade_date is not None:
        # terms_json 最终会 json.dumps 落库，date 对象需先转 isoformat 字符串
        terms["last_trade_date"] = last_trade_date.isoformat()
    return {
        "bond_code": code,
        "bond_name": _first_value(record, "bondName", "bond_nm", "bond_name", "债券简称") or code,
        "stock_code": _strip_code(_first_value(record, "stockId", "stock_id", "stock_code", "正股代码") or ""),
        "stock_name": _first_value(record, "stockName", "stock_nm", "stock_name", "正股简称"),
        "market": "cn",
        "status": status,
        "terms": terms,
    }


def _cb_detail_normalize(record: Dict[str, Any]) -> Dict[str, Any] | None:
    """Map one ``cb-detail`` row onto basic/meta/terms/events rows."""
    bond_code = _first_value(record, "bond_code", "bondCode", "代码", "债券代码")
    if not bond_code:
        return None
    code = _strip_code(bond_code)
    meta: Dict[str, Any] = {}
    for key in CB_DETAIL_META_FIELDS:
        value = record.get(key)
        if value not in (None, "", "-"):
            meta[key] = value
    delisted = record.get("delisted")
    if delisted in (True, "true", "True", "1", 1):
        meta["delisted"] = True
        status = "已退市"
    elif delisted in (False, "false", "False", "0", 0):
        status = "正常"
    else:
        status = None
    stock_code_value = _first_value(
        record,
        "stock_code",
        "stockCode",
        "stock_id",
        "stockId",
        "convert_stock_code",
        "convertStockCode",
        "正股代码",
    )
    stock_name_value = _first_value(
        record,
        "stock_name",
        "stockName",
        "stock_nm",
        "正股简称",
        "正股名称",
    )
    basic = {
        "bond_code": code,
        # 详情可能不返回 bond_name（如 EB 债），保持 None 以保留列表提供的名称
        "bond_name": _first_value(record, "bond_name", "bondName"),
        "stock_code": _strip_code(stock_code_value) if stock_code_value else None,
        "stock_name": stock_name_value,
        "list_date": _parse_date(_first_value(record, "list_date", "listDate", "上市日期")),
        "maturity_date": _parse_date(_first_value(record, "maturity_date", "maturityDate", "到期日期")),
        "remaining_size": _parse_float(_first_value(record, "remaining_size", "remain_size", "剩余规模")),
        "convert_price": _parse_float(_first_value(record, "convert_price", "convertPrice", "转股价")),
    }
    terms = {
        "bond_code": code,
        "redeem_trigger_price": _parse_float(_first_value(record, "force_redemption_trigger_price")),
        "down_revise_trigger_price": _parse_float(_first_value(record, "adjust_trigger_price")),
        "put_trigger_price": _parse_float(_first_value(record, "put_trigger_price")),
    }
    events: List[Dict[str, Any]] = []
    for event in record.get("cb_event_list") or []:
        if not isinstance(event, dict):
            continue
        event_date = _parse_date(_first_value(event, "event_time", "event_date", "date"))
        event_type = str(_first_value(event, "event_type", "type") or "").strip()
        if event_date is None or not event_type:
            continue
        detail_text = str(event.get("detail") or "")
        if event_type == "bond_rating_change":
            rating_from, rating_to = event.get("rating_from"), event.get("rating_to")
            if rating_from is not None or rating_to is not None:
                rating_text = f"{rating_from or '-'} -> {rating_to or '-'}"
                detail_text = f"{detail_text}；{rating_text}" if detail_text else rating_text
        events.append(
            {
                "bond_code": code,
                "event_date": event_date,
                "event_type": event_type,
                "event_detail": detail_text or None,
            }
        )
    return {"basic": basic, "meta": meta, "status": status, "terms": terms, "events": events}


# ---------------------------------------------------------------------------
# 可转债 OHLC 行情（东财优先，腾讯兜底）
# ---------------------------------------------------------------------------

_DEFAULT_UA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

_OHLC_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]


class ConvertibleBondOhlcFetcher:
    """Fetch convertible-bond daily OHLC bars (Eastmoney first, Tencent fallback).

    The returned frame uses standardized columns
    ``date/open/high/low/close/volume/amount`` and can be persisted straight
    into ``stock_daily`` with ``instrument_type='convertible_bond'``.
    Code prefix mapping: ``11xxxx -> sh / secid=1.*``, ``12xxxx -> sz / secid=0.*``.
    """

    name = "cb_ohlc"
    _EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    _TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

    def __init__(self, *, timeout: float = 15.0):
        self.timeout = timeout
        self.last_source: Optional[str] = None
        if get_config().enable_eastmoney_patch and eastmoney_patch is not None:
            eastmoney_patch()

    def fetch_daily(self, bond_code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Fetch daily OHLC within ``[start_date, end_date]``; empty frame on total failure.

        ``self.last_source`` reports which upstream served the last frame
        ("eastmoney" / "tencent" / None when empty).
        """
        code = _strip_code(bond_code)
        self.last_source = None
        if not code.isdigit() or len(code) != 6:
            return _empty_ohlc_frame()
        try:
            frame = self._fetch_eastmoney(code, start_date, end_date)
            if not frame.empty:
                self.last_source = "eastmoney"
                return frame
            logger.info("Eastmoney CB daily empty for %s %s~%s", code, start_date, end_date)
        except Exception as exc:  # noqa: BLE001 - fall through to Tencent
            logger.warning("Eastmoney CB daily failed for %s: %s", code, exc)
        try:
            frame = self._fetch_tencent(code, start_date, end_date)
            if not frame.empty:
                self.last_source = "tencent"
            return frame
        except Exception as exc:  # noqa: BLE001 - single symbol failure never aborts a batch
            logger.warning("Tencent CB daily failed for %s: %s", code, exc)
            return _empty_ohlc_frame()

    def _fetch_eastmoney(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        secid = f"{'1' if code.startswith('11') else '0'}.{code}"
        params = {
            "secid": secid,
            "klt": "101",
            "fqt": "0",
            "beg": start_date.strftime("%Y%m%d"),
            "end": end_date.strftime("%Y%m%d"),
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "_": str(int(time.time() * 1000)),
        }
        response = requests.get(
            self._EASTMONEY_KLINE_URL,
            params=params,
            headers=_DEFAULT_UA_HEADERS,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = (response.json() or {}).get("data") or {}
        rows: List[Dict[str, Any]] = []
        for line in data.get("klines") or []:
            parts = str(line).split(",")
            if len(parts) < 6:
                continue
            rows.append(
                {
                    "date": parts[0],
                    "open": parts[1],
                    "close": parts[2],
                    "high": parts[3],
                    "low": parts[4],
                    "volume": parts[5],
                    "amount": parts[6] if len(parts) > 6 else None,
                }
            )
        if not rows:
            return _empty_ohlc_frame()
        return _normalize_ohlc_frame(pd.DataFrame(rows))

    def _fetch_tencent(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        symbol = f"{'sh' if code.startswith('11') else 'sz'}{code}"
        # 腾讯对可转债不支持显式日期窗口（带 start/end 一律返回空），只能按
        # count 拉最近 N 根再在本地按 [start, end] 过滤；count 上限约 800。
        count = min(800, max(30, int((end_date - start_date).days * 1.8) + 40))
        param = f"{symbol},day,,,{count},qfq"
        response = requests.get(
            self._TENCENT_KLINE_URL,
            params={"param": param},
            headers=_DEFAULT_UA_HEADERS,
            timeout=self.timeout,
        )
        response.raise_for_status()
        rows = _extract_tencent_kline_rows(response.json(), symbol=symbol)
        if not rows:
            return _empty_ohlc_frame()
        frame = _normalize_ohlc_frame(pd.DataFrame(rows))
        if frame.empty:
            return frame
        return frame[(frame["date"] >= start_date) & (frame["date"] <= end_date)].reset_index(drop=True)


def _extract_tencent_kline_rows(payload: Any, *, symbol: str) -> List[Dict[str, Any]]:
    """Parse Tencent kline rows, keeping the raw volume unit (手).

    ``tencent_fetcher._extract_kline_rows`` multiplies volume by 100 for A
    shares; for convertible bonds we keep the source unit so the value is
    comparable with the Eastmoney kline volume.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    item = data.get(symbol) if isinstance(data, dict) else None
    if not isinstance(item, dict):
        return []
    rows = item.get("qfqday") or item.get("day") or []
    result: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        result.append(
            {
                "date": str(row[0]),
                "open": row[1],
                "close": row[2],
                "high": row[3],
                "low": row[4],
                "volume": row[5],
                "amount": row[6] if len(row) > 6 else None,
            }
        )
    return result


def _normalize_ohlc_frame(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    for column in ("open", "high", "low", "close", "volume", "amount"):
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized = normalized.dropna(subset=["date"])
    normalized["date"] = normalized["date"].dt.date
    normalized = normalized[normalized["close"].notna()]
    normalized = normalized.sort_values("date").reset_index(drop=True)
    return normalized[_OHLC_COLUMNS]


def _empty_ohlc_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_OHLC_COLUMNS)
