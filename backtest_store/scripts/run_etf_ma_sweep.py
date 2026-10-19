# -*- coding: utf-8 -*-
"""ETF 信号策略·均线窗口批量寻优（510880）。

基于策略实验室批量框架（StrategyLabBatchService）执行：
- 网格：ma_window ∈ [20, 25, ..., 260]，共 49 组
- 其余参数固定为策略定义：入场 ma_gap_pct<0，出场 ma_gap_pct>7，
  佣金 0.0002，整手 100，基准 buy_hold（长持 510880）
- 双区间：A=2018-01-02~2026-10-16（全区间稳健性）、B=2024-01-02~2026-10-16（近三年）

用法：.venv/bin/python backtest_store/scripts/run_etf_ma_sweep.py
产物：backtest_store/raw/etf_ma_sweep_batches.json（batch id，供报告脚本复用）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.services.strategy_lab.batch_service import StrategyLabBatchService  # noqa: E402

SYMBOLS = ["510880"]
MA_WINDOWS = list(range(20, 261, 5))  # 20..260 step 5 -> 49 组
WINDOWS = {
    "A_full": {"start": "2018-01-02", "end": "2026-10-16", "label": "全区间 2018-2026"},
    "B_recent": {"start": "2024-01-02", "end": "2026-10-16", "label": "近三年 2024-2026"},
}
INITIAL_CASH = 100000.0
BENCHMARK = "buy_hold"


def run_batch(service: StrategyLabBatchService, win: dict, symbols: list | None = None) -> dict:
    base_config = {
        "strategy_id": "signal",
        "market": "cn",
        "instrument_type": "etf",
        "start_date": win["start"],
        "end_date": win["end"],
        "initial_cash": INITIAL_CASH,
        "benchmark_symbol": BENCHMARK,
        "symbols": symbols or SYMBOLS,
        "parameters": {},  # 缺省构建 入<0/出>7，网格只覆盖 ma_window
    }
    payload = service.create_batch(
        strategy_id="signal",
        market="cn",
        instrument_type="etf",
        base_config=base_config,
        parameter_grid={"ma_window": MA_WINDOWS},
        run_async=False,
    )
    return payload


def print_summary(service: StrategyLabBatchService, batch_id: int, label: str) -> None:
    batch = service.get_batch(batch_id) or {}
    print(f"\n=== 批次 #{batch_id} {label} ===")
    print(
        f"status={batch.get('status')} "
        f"tasks={batch.get('completed_tasks')}/{batch.get('total_tasks')} "
        f"success={batch.get('success_tasks')} failed={batch.get('failed_tasks')}"
    )
    header = (
        f"{'MA':>4} {'总收益%':>9} {'年化%':>8} {'回撤%':>8} {'夏普':>6} "
        f"{'交易':>4} {'胜率%':>6} {'基准%':>8} {'超额%':>8}"
    )
    print(header)
    for item in batch.get("items", []):
        params = json.loads(item["parameters"]) if isinstance(item.get("parameters"), str) else (item.get("parameters") or {})
        ma = (params.get("parameters") or {}).get("ma_window", "?")
        if item.get("status") != "completed":
            print(f"{ma!s:>4} FAILED: {item.get('error_message')}")
            continue
        run = item_run_metrics(item.get("run_id"))
        if run is None:
            print(f"{ma!s:>4} run {item.get('run_id')} 无指标")
            continue
        print(
            f"{ma:>4} {run['total_return_pct']:>9.2f} {run['annualized_return_pct'] or 0:>8.2f} "
            f"{run['max_drawdown_pct']:>8.2f} {run['sharpe_ratio'] or 0:>6.2f} "
            f"{run['trade_count']:>4} {run['win_rate_pct'] or 0:>6.1f} "
            f"{run['benchmark_return_pct']:>8.2f} "
            f"{(run['total_return_pct'] - run['benchmark_return_pct']):>8.2f}"
        )


def item_run_metrics(run_id):
    import sqlite3

    conn = sqlite3.connect(REPO_ROOT / "data" / "stock_analysis.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT m.total_return_pct, m.annualized_return_pct, m.max_drawdown_pct,
               m.sharpe_ratio, m.win_rate_pct, m.trade_count,
               r.benchmark_return_pct
        FROM strategy_lab_run_metrics m
        JOIN strategy_lab_runs r ON r.id = m.run_id
        WHERE m.run_id = ?
        """,
        (run_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def main() -> None:
    service = StrategyLabBatchService()
    out_path = REPO_ROOT / "backtest_store" / "raw" / "etf_ma_sweep_batches.json"
    batch_ids = {}
    for key, win in WINDOWS.items():
        print(f"启动批次：{win['label']}（{win['start']} ~ {win['end']}），{len(MA_WINDOWS)} 组均线窗口 ...")
        payload = run_batch(service, win)
        batch_id = int(payload["id"])
        batch_ids[key] = {
            "batch_id": batch_id,
            "start": win["start"],
            "end": win["end"],
            "label": win["label"],
        }
        print_summary(service, batch_id, win["label"])
    out_path.write_text(
        json.dumps(
            {
                "symbol": SYMBOLS[0],
                "ma_windows": MA_WINDOWS,
                "initial_cash": INITIAL_CASH,
                "benchmark": BENCHMARK,
                "batches": batch_ids,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nbatch ids 已写入 {out_path}")


if __name__ == "__main__":
    main()
