# -*- coding: utf-8 -*-
"""Convertible-bond data providers for Strategy Lab."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import json
import os
import requests
import subprocess
from typing import Any, Dict, List, Optional, Protocol

from data_provider.akshare_fetcher import _akshare_call_with_timeout


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
    """Use an installed OpenCLI Jisilu adapter without coupling Strategy Lab to it."""

    name = "opencli"

    def __init__(self, *, timeout: float = 45.0, executable: Optional[str] = None):
        self.timeout = timeout
        self.executable = executable or os.getenv("OPENCLI_BIN", "opencli")

    def fetch(self, *, market: str, symbols: Optional[List[str]] = None) -> ConvertibleBondSyncPayload:
        if market != "cn":
            raise ValueError("OpenCLI Jisilu provider supports cn market only")
        command = [self.executable, "jisilu", "cb", "-f", "json"]
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("OpenCLI returned invalid JSON") from exc
        return _snapshot_payload(_extract_rows(payload), symbols=symbols, source=self.name)


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
