# -*- coding: utf-8 -*-
"""Convertible-bond data providers for Strategy Lab."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import json
import os
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
        if market != "cn":
            raise ValueError("AkShare convertible-bond provider supports cn market only")

        import akshare as ak

        symbol_filter = {str(symbol).strip().lower() for symbol in symbols or [] if str(symbol).strip()}
        basic_df = _akshare_call_with_timeout(
            ak.bond_zh_cov,
            timeout=self.timeout,
            call_name="strategy_lab_cb_basic",
        )
        basics = [
            row
            for row in (self._basic_row(record) for record in _records(basic_df))
            if row and self._matches_symbol(row["bond_code"], symbol_filter)
        ]
        terms = [
            terms_row
            for record in _records(basic_df)
            for terms_row in [self._terms_row(record)]
            if terms_row and self._matches_symbol(terms_row["bond_code"], symbol_filter)
        ]

        event_rows: List[Dict[str, Any]] = []
        try:
            redeem_df = _akshare_call_with_timeout(
                ak.bond_cb_redeem_jsl,
                timeout=self.timeout,
                call_name="strategy_lab_cb_redeem_events",
            )
            event_rows = [
                event
                for event in (self._redeem_event_row(record) for record in _records(redeem_df))
                if event and self._matches_symbol(event["bond_code"], symbol_filter)
            ]
        except Exception:
            event_rows = []

        factor_rows: List[Dict[str, Any]] = []
        for basic in basics:
            factor_rows.extend(self._fetch_daily_factors(ak, basic["bond_code"], symbol_filter))

        return ConvertibleBondSyncPayload(
            cb_basic=basics,
            cb_terms=terms,
            cb_daily_factors=factor_rows,
            cb_events=event_rows,
        )

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
        bond_code = _first_value(record, "债券代码", "代码", "bond_code", "转债代码")
        if not bond_code:
            return None
        return {
            "bond_code": _strip_code(bond_code),
            "bond_name": _first_value(record, "债券简称", "转债名称", "名称", "bond_name") or str(bond_code),
            "stock_code": _strip_code(_first_value(record, "正股代码", "stock_code") or ""),
            "stock_name": _first_value(record, "正股简称", "正股名称", "stock_name"),
            "market": "cn",
            "list_date": _parse_date(_first_value(record, "上市时间", "上市日期", "list_date")),
            "maturity_date": _parse_date(_first_value(record, "到期时间", "到期日期", "maturity_date")),
            "remaining_size": _parse_float(_first_value(record, "剩余规模", "余额", "remaining_size")),
            "current_premium_rate": _parse_float(_first_value(record, "转股溢价率", "溢价率", "current_premium_rate")),
            "convert_price": _parse_float(_first_value(record, "转股价", "convert_price")),
            "terms": {"provider": "akshare"},
        }

    @staticmethod
    def _terms_row(record: Dict[str, Any]) -> Dict[str, Any] | None:
        bond_code = _first_value(record, "债券代码", "代码", "bond_code", "转债代码")
        if not bond_code:
            return None
        values = {
            "bond_code": _strip_code(bond_code),
            "redeem_clause": _first_value(record, "强赎条款", "强赎条件", "redeem_clause"),
            "down_revise_clause": _first_value(record, "下修条款", "下修条件", "down_revise_clause"),
            "put_clause": _first_value(record, "回售条款", "回售条件", "put_clause"),
            "redeem_trigger_price": _parse_float(_first_value(record, "强赎触发价", "redeem_trigger_price")),
            "down_revise_trigger_price": _parse_float(_first_value(record, "下修触发价", "down_revise_trigger_price")),
            "put_trigger_price": _parse_float(_first_value(record, "回售触发价", "put_trigger_price")),
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
