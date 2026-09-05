#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证可转债盘中实时快照：日线接口当天 close 是否等于实时最新价。

背景：
    ``sync_cb_ohlc`` 走东财日线接口（``push2his`` + ``klt=101``），东财失败时
    自动兜底腾讯日线接口（``web.ifzq.gtimg.cn`` + ``day``）。两者盘中拉当天
    那根日线时，其 ``close`` 字段语义上都应是"当前最新成交价"。

    本脚本在盘中分别对东财、腾讯两条链路做对比：
      - 东财：实时快照 ``push2.eastmoney.com/api/qt/stock/get``（f43=最新价）
              vs 日线 ``push2his.eastmoney.com/api/qt/stock/kline/get``（当天 close）
      - 腾讯：实时快照 ``qt.gtimg.cn/q=``（字段3=最新价）
              vs 日线 ``web.ifzq.gtimg.cn/appstock/app/fqkline/get``（当天 close）

用法：
    python scripts/verify_cb_realtime_snapshot.py --bond 128138
    python scripts/verify_cb_realtime_snapshot.py --bond 128138 --rounds 3 --interval 5
    python scripts/verify_cb_realtime_snapshot.py --bond 113001,128138

说明：
    - 建议在 A 股交易时段（9:30-11:30 / 13:00-15:00）运行，否则当天日线
      已定型为收盘价，实时快照与日线 close 应完全一致。
    - 盘中连续多轮采样，若 close 随行情变化且与实时最新价一致，即证明是实时最新价。
    - 东财接口在部分网络环境（如沙箱）会被拒，此时东财列为「请求失败」，
      腾讯列仍可正常对比，不影响验证腾讯兜底链路。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.strategy_lab.cb_providers import _DEFAULT_UA_HEADERS  # noqa: E402

_EM_REALTIME_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_TX_REALTIME_URL = "https://qt.gtimg.cn/q="
_TX_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _strip_code(value: str) -> str:
    return value.strip().lower().split(".")[-1]


def _em_secid(code: str) -> str:
    # 可转债代码前缀：11xxxx -> 沪市(secid=1.*)，12xxxx -> 深市(secid=0.*)
    return f"{'1' if code.startswith('11') else '0'}.{code}"


def _tx_symbol(code: str) -> str:
    # 腾讯代码前缀：11xxxx -> sh，12xxxx -> sz
    return f"{'sh' if code.startswith('11') else 'sz'}{code}"


# ---------------------------------------------------------------------------
# 东财
# ---------------------------------------------------------------------------

def fetch_em_realtime(code: str, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """东财实时快照，返回 {price, high, low, open, time} 或 None。"""
    params = {
        "secid": _em_secid(code),
        "fields": "f43,f44,f45,f46,f60,f86",
        "_": str(int(time.time() * 1000)),
    }
    resp = requests.get(_EM_REALTIME_URL, params=params, headers=_DEFAULT_UA_HEADERS, timeout=timeout)
    resp.raise_for_status()
    data = (resp.json() or {}).get("data") or {}
    if not data:
        return None

    def _price(key: str) -> Optional[float]:
        raw = data.get(key)
        if raw in (None, "-", ""):
            return None
        try:
            return float(raw) / 100
        except (TypeError, ValueError):
            return None

    return {
        "price": _price("f43"),
        "high": _price("f44"),
        "low": _price("f45"),
        "open": _price("f46"),
        "time": data.get("f86"),
    }


def fetch_em_daily_today(code: str, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """东财日线当天那根，返回 {date, open, close, high, low} 或 None。"""
    today = date.today()
    params = {
        "secid": _em_secid(code),
        "klt": "101",
        "fqt": "0",
        "beg": today.strftime("%Y%m%d"),
        "end": today.strftime("%Y%m%d"),
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "_": str(int(time.time() * 1000)),
    }
    resp = requests.get(_EM_KLINE_URL, params=params, headers=_DEFAULT_UA_HEADERS, timeout=timeout)
    resp.raise_for_status()
    data = (resp.json() or {}).get("data") or {}
    klines = data.get("klines") or []
    if not klines:
        return None
    parts = str(klines[-1]).split(",")
    if len(parts) < 6:
        return None
    return {
        "date": parts[0],
        "open": parts[1],
        "close": parts[2],
        "high": parts[3],
        "low": parts[4],
    }


# ---------------------------------------------------------------------------
# 腾讯
# ---------------------------------------------------------------------------

def fetch_tx_realtime(code: str, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """腾讯实时快照，返回 {price, high, low, open, time} 或 None。

    返回文本形如 ``v_sz128138="51~侨银转债~128138~131.099~131.080~..."``，
    字段按 ``~`` 分隔：3=最新价 4=昨收 5=今开 33=最高 34=最低 30=时间。
    """
    symbol = _tx_symbol(code)
    resp = requests.get(_TX_REALTIME_URL + symbol, headers=_DEFAULT_UA_HEADERS, timeout=timeout)
    resp.raise_for_status()
    text = resp.text.strip()
    if "=" not in text:
        return None
    payload = text.split("=", 1)[1].strip().strip('";')
    parts = payload.split("~")
    if len(parts) < 35:
        return None

    def _f(idx: int) -> Optional[float]:
        raw = parts[idx] if idx < len(parts) else ""
        if raw in (None, "", "-"):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    return {
        "price": _f(3),
        "high": _f(33),
        "low": _f(34),
        "open": _f(5),
        "time": parts[30] if len(parts) > 30 else None,
    }


def fetch_tx_daily_today(code: str, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """腾讯日线当天那根，返回 {date, open, close, high, low} 或 None。"""
    symbol = _tx_symbol(code)
    param = f"{symbol},day,,,5,qfq"
    resp = requests.get(_TX_KLINE_URL, params={"param": param}, headers=_DEFAULT_UA_HEADERS, timeout=timeout)
    resp.raise_for_status()
    data = (resp.json() or {}).get("data") or {}
    item = data.get(symbol) if isinstance(data, dict) else None
    if not isinstance(item, dict):
        return None
    rows = item.get("qfqday") or item.get("day") or []
    if not rows:
        return None
    row = rows[-1]
    if not isinstance(row, list) or len(row) < 6:
        return None
    return {
        "date": str(row[0]),
        "open": row[1],
        "close": row[2],
        "high": row[3],
        "low": row[4],
    }


def _fmt(value: Optional[float]) -> str:
    return "--" if value is None else f"{value:.3f}"


def _to_float(value: Any) -> Optional[float]:
    if value in (None, "-", ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _print_block(title: str, rt: Optional[Dict[str, Any]], daily: Optional[Dict[str, Any]], rnd: int) -> None:
    if rt is None or daily is None:
        print(f"{rnd:>3} | 无数据（rt={rt is not None} daily={daily is not None}）")
        return
    price = rt["price"]
    close = _to_float(daily["close"])
    diff = None if price is None or close is None else round(price - close, 3)
    print(
        f"{rnd:>3} | {_fmt(price):>10} | {_fmt(close):>10} | {_fmt(diff):>8} | "
        f"{_fmt(_to_float(daily['high'])):>10} | {_fmt(_to_float(daily['low'])):>10} | "
        f"{str(rt['time']):>19}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="验证可转债盘中实时快照与日线当天 close 是否一致（东财 + 腾讯）")
    parser.add_argument("--bond", default="128138", help="可转债代码，逗号分隔多只（默认 128138）")
    parser.add_argument("--rounds", type=int, default=3, help="采样轮数（默认 3）")
    parser.add_argument("--interval", type=float, default=5.0, help="每轮间隔秒数（默认 5）")
    args = parser.parse_args()

    codes = [_strip_code(c) for c in args.bond.split(",") if c.strip()]
    if not codes:
        print("未提供有效可转债代码")
        sys.exit(1)

    header = f"{'轮':>3} | {'实时最新价':>10} | {'日线close':>10} | {'差值':>8} | {'日线high':>10} | {'日线low':>10} | {'快照时间':>19}"

    print(f"验证标的: {', '.join(codes)}  轮数={args.rounds}  间隔={args.interval}s")
    print("=" * 90)

    for code in codes:
        print(f"\n【{code}】")

        print("\n--- 东财链路 ---")
        print(header)
        print("-" * 90)
        for rnd in range(1, args.rounds + 1):
            try:
                rt = fetch_em_realtime(code)
                daily = fetch_em_daily_today(code)
            except Exception as exc:  # noqa: BLE001 - 单轮失败不中断
                print(f"{rnd:>3} | 请求失败: {exc}")
            else:
                _print_block("东财", rt, daily, rnd)
            if rnd < args.rounds:
                time.sleep(args.interval)

        print("\n--- 腾讯链路 ---")
        print(header)
        print("-" * 90)
        for rnd in range(1, args.rounds + 1):
            try:
                rt = fetch_tx_realtime(code)
                daily = fetch_tx_daily_today(code)
            except Exception as exc:  # noqa: BLE001 - 单轮失败不中断
                print(f"{rnd:>3} | 请求失败: {exc}")
            else:
                _print_block("腾讯", rt, daily, rnd)
            if rnd < args.rounds:
                time.sleep(args.interval)

    print("\n" + "=" * 90)
    print("判定：盘中若「实时最新价」与「日线close」一致（差值≈0），即证明该链路日线当天 close 是实时最新价。")
    print("      东财被拒时看腾讯链路即可，腾讯兜底同样能拿到盘中最新价。")


if __name__ == "__main__":
    main()
