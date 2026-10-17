# -*- coding: utf-8 -*-
"""ETF 信号策略·均线窗口批量寻优（多标的：510880 / 159307 / 513630）。

- 510880：复用 etf_ma_sweep_batches.json 中已有的三批次（当日刚跑，数据未变）。
- 159307 / 513630：先确认库内数据范围，按「全区间 + 前半段/后半段对半切」
  生成三个窗口（年轻 ETF 无 2018 历史可用，对半切保证两段互不重叠），
  网格与其他参数同 run_etf_ma_sweep.py（MA 20~260 步长 5，入<0 出>7，
  佣金 0.02%，整手 100，基准 buy_hold 长持）。

用法：.venv/bin/python backtest_store/scripts/run_etf_ma_sweep_multi.py
产物：backtest_store/raw/etf_ma_sweep_multi.json（供报告脚本使用）
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backtest_store" / "scripts"))

from run_etf_ma_sweep import MA_WINDOWS, print_summary, run_batch  # noqa: E402
from src.services.strategy_lab.batch_service import StrategyLabBatchService  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "stock_analysis.db"
MANIFEST_OUT = REPO_ROOT / "backtest_store" / "raw" / "etf_ma_sweep_multi.json"
NEW_SYMBOLS = ["159307", "513630"]


def trading_dates(symbol: str) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    dates = [r[0] for r in conn.execute(
        "SELECT trade_date FROM strategy_lab_etf_daily WHERE symbol=? ORDER BY trade_date", (symbol,)
    )]
    conn.close()
    return dates


def derive_windows(symbol: str) -> dict:
    dates = trading_dates(symbol)
    if len(dates) < 120:
        raise SystemExit(f"{symbol} 数据不足（{len(dates)} 个交易日），无法对半切窗口")
    h = len(dates) // 2
    return {
        "data_range": [dates[0], dates[-1]],
        "n_days": len(dates),
        "windows": {
            "A_full": {"start": dates[0], "end": dates[-1], "label": f"全区间 {dates[0][:4]}~{dates[-1][:4]}"},
            "C_early": {"start": dates[0], "end": dates[h - 1], "label": "前半段（早期）"},
            "B_recent": {"start": dates[h], "end": dates[-1], "label": "后半段（近期）"},
        },
    }


def count_days(symbol: str, start: str, end: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    n = conn.execute(
        "SELECT count(*) FROM strategy_lab_etf_daily WHERE symbol=? AND trade_date BETWEEN ? AND ?",
        (symbol, start, end),
    ).fetchone()[0]
    conn.close()
    return n


def main() -> None:
    service = StrategyLabBatchService()
    manifest = {
        "generated": f"{date.today():%Y-%m-%d}",
        "grid": {"ma_windows": MA_WINDOWS, "initial_cash": 100000.0, "benchmark": "buy_hold"},
        "symbols": {},
    }

    # 510880：复用已有三批次
    old = json.loads((REPO_ROOT / "backtest_store" / "raw" / "etf_ma_sweep_batches.json").read_text(encoding="utf-8"))
    dates = trading_dates("510880")
    reused = {"reused": True, "data_range": [dates[0], dates[-1]], "n_days": len(dates), "windows": {}}
    for key, meta in old["batches"].items():
        reused["windows"][key] = {
            "batch_id": meta["batch_id"], "start": meta["start"], "end": meta["end"], "label": meta["label"],
            "n_days": count_days("510880", meta["start"], meta["end"]),
        }
    if old.get("extra_runs_ma182"):
        reused["extra_runs_ma182"] = old["extra_runs_ma182"]
    manifest["symbols"]["510880"] = reused
    print("510880：复用已有批次", {k: v["batch_id"] for k, v in reused["windows"].items()})

    for symbol in NEW_SYMBOLS:
        info = derive_windows(symbol)
        print(f"\n===== {symbol} 数据 {info['data_range'][0]} ~ {info['data_range'][1]}（{info['n_days']} 个交易日）=====")
        for key in ["A_full", "C_early", "B_recent"]:
            win = info["windows"][key]
            print(f"启动批次：{symbol} {win['label']}（{win['start']} ~ {win['end']}），{len(MA_WINDOWS)} 组 ...")
            payload = run_batch(service, win)
            win["batch_id"] = int(payload["id"])
            win["n_days"] = count_days(symbol, win["start"], win["end"])
            print_summary(service, win["batch_id"], f"{symbol} {win['label']}")
            info["windows"][key] = win
        manifest["symbols"][symbol] = info

    MANIFEST_OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n多标的清单已写入 {MANIFEST_OUT}")


if __name__ == "__main__":
    main()
