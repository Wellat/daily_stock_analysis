# -*- coding: utf-8 -*-
"""Backtest report export (Markdown / CSV) for Strategy Lab runs.

报告与页面共用同一份持久化结果（equity_curve / metrics / trades），
口径天然一致——汇总表、走势、回报分布都从同一条曲线派生。
"""

from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Optional


def _monthly_returns(equity_curve: List[Dict[str, Any]]) -> Dict[str, float]:
    """按月聚合日收益率（复利）：{YYYY-MM: 月收益率%}。"""
    monthly: Dict[str, float] = {}
    for point in equity_curve:
        daily = point.get("daily_return_pct")
        if daily is None:
            continue
        month_key = str(point.get("trade_date", ""))[:7]
        monthly.setdefault(month_key, 0.0)
        monthly[month_key] = (1.0 + monthly[month_key]) * (1.0 + float(daily) / 100.0) - 1.0
    return {key: round(value * 100, 4) for key, value in sorted(monthly.items())}


def _fmt_optional(value: Any, suffix: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}{suffix}"
    return f"{value}{suffix}"


def _summary_metric_rows(metrics: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    benchmark = (metrics or {}).get("benchmark_metrics") or {}
    excess = benchmark.get("relative_excess") or {}

    def _row(label: str, source: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "行": label,
            "总收益率%": _fmt_optional(source.get("total_return_pct")),
            "年化收益率%": _fmt_optional(source.get("annualized_return_pct")),
            "最大回撤%": _fmt_optional(source.get("max_drawdown_pct")),
            "夏普": _fmt_optional(source.get("sharpe_ratio")),
            "索提诺": _fmt_optional(source.get("sortino_ratio")),
            "卡玛": _fmt_optional(source.get("calmar_ratio")),
            "交易周期": _fmt_optional(source.get("period_count")),
            "盈利周期": _fmt_optional(source.get("profit_periods")),
            "亏损周期": _fmt_optional(source.get("loss_periods")),
        }

    rows = [_row("当前策略", metrics or {})]
    if benchmark:
        rows.append(_row("基准策略", benchmark))
    if excess:
        rows.append(_row("相对超额", excess))
    return rows


def _markdown_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return ""
    headers = list(rows[0].keys())
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(h, "-")) for h in headers) + " |")
    return "\n".join(lines)


def build_markdown_report(run: Dict[str, Any], trades: List[Dict[str, Any]]) -> str:
    metrics = run.get("metrics") or {}
    diagnostics = metrics.get("diagnostics") or {}
    benchmark_metrics = metrics.get("benchmark_metrics") or {}
    equity_curve = run.get("equity_curve") or []
    parameters = run.get("parameters") or {}

    lines: List[str] = []
    lines.append(f"# 策略实验室回测报告 #{run.get('id')}")
    lines.append("")
    lines.append(f"- 策略：{run.get('strategy_name')}（{run.get('strategy_id')}）")
    lines.append(f"- 引擎：{run.get('engine_name')}")
    lines.append(f"- 回测区间：{run.get('start_date')} ~ {run.get('end_date')}")
    lines.append(f"- 初始资金：{run.get('initial_cash')}")
    lines.append(f"- 期末资产：{_fmt_optional(run.get('final_equity'))}")
    benchmark_mode = benchmark_metrics.get("mode") or diagnostics.get("benchmark_mode") or "-"
    lines.append(f"- 基准：{benchmark_mode}（区间收益 {_fmt_optional(run.get('benchmark_return_pct'))}%）")
    lines.append(f"- 生成时间：{run.get('completed_at') or '-'}")
    lines.append("")

    lines.append("## 参数配置")
    lines.append("")
    simple_params = {k: v for k, v in parameters.items() if not isinstance(v, (list, dict))}
    if simple_params:
        lines.extend(f"- **{key}**: {value}" for key, value in sorted(simple_params.items()))
    score_factors = parameters.get("score_factors")
    if isinstance(score_factors, list) and score_factors:
        lines.append("")
        lines.append("### 打分因子")
        lines.append("")
        rows = [
            {"因子": item.get("factor"), "方向": item.get("direction"), "权重": item.get("weight")}
            for item in score_factors
        ]
        lines.append(_markdown_table(rows))
    exclusion_factors = parameters.get("exclusion_factors")
    if isinstance(exclusion_factors, list) and exclusion_factors:
        lines.append("")
        lines.append("### 排除因子")
        lines.append("")
        rows = [
            {"因子": item.get("factor"), "比较符": item.get("op"), "值": item.get("value")}
            for item in exclusion_factors
        ]
        lines.append(_markdown_table(rows))
    lines.append("")

    lines.append("## 绩效汇总")
    lines.append("")
    lines.append(_markdown_table(_summary_metric_rows(metrics)))
    lines.append("")
    if metrics.get("turnover_avg_pct") is not None:
        lines.append(f"日均换手：{metrics['turnover_avg_pct']}%　胜率：{_fmt_optional(metrics.get('win_rate_pct'))}%　成交笔数：{metrics.get('trade_count')}")
        lines.append("")

    monthly = _monthly_returns(equity_curve)
    if monthly:
        lines.append("## 月度回报")
        lines.append("")
        rows = [{"月份": key, "收益率%": round(value, 2)} for key, value in monthly.items()]
        lines.append(_markdown_table(rows))
        lines.append("")

    if equity_curve:
        last_point = equity_curve[-1]
        holdings = last_point.get("holdings") or []
        if holdings:
            lines.append("## 期末持仓")
            lines.append("")
            lines.append(f"共 {len(holdings)} 只：" + "、".join(str(code) for code in holdings))
            lines.append("")

    if trades:
        lines.append("## 成交明细")
        lines.append("")
        rows = [
            {
                "日期": trade.get("trade_date"),
                "方向": trade.get("side"),
                "标的": f"{trade.get('symbol_name') or ''}({trade.get('symbol')})",
                "数量": trade.get("quantity"),
                "价格": trade.get("price"),
                "金额": trade.get("amount"),
                "费用": trade.get("fee"),
                "依据": trade.get("reason"),
            }
            for trade in trades
        ]
        lines.append(_markdown_table(rows))
        lines.append("")

    lines.append("---")
    lines.append("> 本报告由策略实验室按用户配置自动生成，历史模拟结果不代表未来表现，不构成投资建议。")
    lines.append("")
    return "\n".join(lines)


def build_holdings_csv(run: Dict[str, Any]) -> str:
    equity_curve = run.get("equity_curve") or []
    initial_cash = float(run.get("initial_cash") or 0.0)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["trade_date", "holdings", "holdings_count", "turnover_pct", "daily_return_pct", "cumulative_return_pct", "equity", "cash", "positions_value", "benchmark_equity"])
    for point in equity_curve:
        holdings = point.get("holdings") or []
        equity = float(point.get("equity") or 0.0)
        cumulative = (equity / initial_cash - 1.0) * 100 if initial_cash else None
        writer.writerow(
            [
                point.get("trade_date"),
                ";".join(str(code) for code in holdings),
                point.get("holdings_count", len(holdings)),
                point.get("turnover_pct"),
                point.get("daily_return_pct"),
                round(cumulative, 4) if cumulative is not None else None,
                point.get("equity"),
                point.get("cash"),
                point.get("positions_value"),
                point.get("benchmark_equity"),
            ]
        )
    return output.getvalue()


def build_trades_csv(trades: List[Dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["trade_date", "side", "symbol", "symbol_name", "quantity", "price", "amount", "fee", "reason"])
    for trade in trades:
        writer.writerow(
            [
                trade.get("trade_date"),
                trade.get("side"),
                trade.get("symbol"),
                trade.get("symbol_name") or "",
                trade.get("quantity"),
                trade.get("price"),
                trade.get("amount"),
                trade.get("fee"),
                trade.get("reason") or "",
            ]
        )
    return output.getvalue()
