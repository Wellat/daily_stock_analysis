# -*- coding: utf-8 -*-
"""Metric computation for rotation backtests.

口径说明（全部从同一条权益曲线派生，汇总表 / 走势图 / 回报分布共用，
不会出现禄得网汇总表与分布图差 2bp 的口径漂移）：

- 日收益率首日相对初始资金（首日建仓的盈亏计入第一根 bar）；
- 年化采用自然日折算 ``((1+r) ** (365/days) - 1)``，与旧引擎一致；
- 索提诺用下行半标准差（MAR=0）；
- 单日换手 = 当日成交金额（买+卖）/ 2 / 前一日总资产，日均对全部交易日取均值；
- 胜率按 FIFO 配对的多头回合（卖出事件）计算，无平仓时为 None。
"""

from __future__ import annotations

import math
from datetime import date
from statistics import mean, pstdev
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import backtrader as bt

from src.core.strategy_lab.backtest.strategy import ExecutionRecord

TRADING_DAYS_PER_YEAR = 252


def daily_returns(equity: Sequence[float], initial_cash: float) -> List[float]:
    if not equity:
        return []
    returns: List[float] = []
    previous = float(initial_cash) if initial_cash else equity[0]
    for value in equity:
        value = float(value)
        if previous:
            returns.append(value / previous - 1.0)
        previous = value
    return returns[1:] if not initial_cash else returns


def cumulative_max_drawdown_pct(equity: Sequence[float]) -> float:
    if not equity:
        return 0.0
    peak = float(equity[0])
    max_drawdown = 0.0
    for value in equity:
        value = float(value)
        peak = max(peak, value)
        if peak:
            max_drawdown = min(max_drawdown, value / peak - 1.0)
    return max_drawdown * 100


def annualized_return_pct(total_return_pct: float, start_date: date, end_date: date) -> Optional[float]:
    if end_date <= start_date:
        return None
    days = max(1, (end_date - start_date).days)
    return ((1 + total_return_pct / 100) ** (365 / days) - 1) * 100


def sharpe_ratio(returns: Sequence[float]) -> Optional[float]:
    if len(returns) < 2:
        return None
    volatility = pstdev(returns)
    if volatility == 0:
        return None
    return (mean(returns) / volatility) * math.sqrt(TRADING_DAYS_PER_YEAR)


def sortino_ratio(returns: Sequence[float]) -> Optional[float]:
    if len(returns) < 2:
        return None
    downside = [min(r, 0.0) for r in returns]
    downside_dev = math.sqrt(sum(r * r for r in downside) / len(downside))
    if downside_dev == 0:
        return None
    return (mean(returns) / downside_dev) * math.sqrt(TRADING_DAYS_PER_YEAR)


def compute_series_metrics(
    *,
    dates: Sequence[date],
    equity: Sequence[float],
    initial_cash: float,
) -> Dict[str, Optional[float]]:
    """对一条权益曲线计算整套指标（策略与基准共用同一口径）。"""
    if not equity or not dates:
        return {
            "total_return_pct": None,
            "final_equity": None,
            "annualized_return_pct": None,
            "max_drawdown_pct": None,
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "calmar_ratio": None,
            "period_count": 0,
            "profit_periods": 0,
            "loss_periods": 0,
        }
    final_equity = float(equity[-1])
    total_return_pct = (final_equity / initial_cash - 1.0) * 100 if initial_cash else 0.0
    annualized = annualized_return_pct(total_return_pct, dates[0], dates[-1])
    max_drawdown = cumulative_max_drawdown_pct(equity)
    returns = daily_returns(equity, initial_cash)
    sharpe = sharpe_ratio(returns)
    sortino = sortino_ratio(returns)
    calmar = annualized / abs(max_drawdown) if annualized is not None and max_drawdown < 0 else None
    return {
        "total_return_pct": round(total_return_pct, 4),
        "final_equity": round(final_equity, 4),
        "annualized_return_pct": round(annualized, 4) if annualized is not None else None,
        "max_drawdown_pct": round(max_drawdown, 4),
        "sharpe_ratio": round(sharpe, 4) if sharpe is not None else None,
        "sortino_ratio": round(sortino, 4) if sortino is not None else None,
        "calmar_ratio": round(calmar, 4) if calmar is not None else None,
        "period_count": len(equity),
        "profit_periods": sum(1 for r in returns if r > 0),
        "loss_periods": sum(1 for r in returns if r < 0),
    }


def compute_turnover_series(
    *,
    dates: Sequence[date],
    equity: Sequence[float],
    amounts_by_date: Dict[date, Dict[str, float]],
    initial_cash: float,
) -> Tuple[List[Optional[float]], Optional[float]]:
    """逐日换手率（%）与日均换手。首日基数取初始资金。"""
    series: List[Optional[float]] = []
    for index, trade_date in enumerate(dates):
        amounts = amounts_by_date.get(trade_date)
        base = float(equity[index - 1]) if index > 0 else float(initial_cash)
        if not amounts or base <= 0:
            series.append(None)
            continue
        traded = amounts.get("buy", 0.0) + amounts.get("sell", 0.0)
        series.append(round(traded / 2.0 / base * 100, 4))
    values = [v for v in series if v is not None]
    average = round(mean(values), 4) if values else None
    return series, average


def compute_win_rate_pct(executions: Iterable[ExecutionRecord]) -> Optional[float]:
    """FIFO 配对的多头回合胜率：盈利卖出事件 / 总卖出事件。"""
    lots: Dict[str, List[Tuple[float, float, float]]] = {}
    total_sells = 0
    winning_sells = 0
    for record in sorted(executions, key=lambda r: (r.trade_date, r.symbol)):
        queue = lots.setdefault(record.symbol, [])
        if record.side == "buy":
            queue.append((record.quantity, record.price, record.fee))
            continue
        total_sells += 1
        remaining = record.quantity
        cost = 0.0
        buy_fees = 0.0
        while remaining > 1e-9 and queue:
            qty, price, fee = queue[0]
            take = min(qty, remaining)
            cost += take * price
            buy_fees += fee * (take / qty) if qty else 0.0
            remaining -= take
            if take >= qty - 1e-9:
                queue.pop(0)
            else:
                queue[0] = (qty - take, price, fee)
        proceeds = record.quantity * record.price - record.fee - cost - buy_fees
        if proceeds > 0:
            winning_sells += 1
    if total_sells == 0:
        return None
    return round(winning_sells / total_sells * 100, 4)


class MetricsAnalyzer(bt.Analyzer):
    """Expose strategy-collected series through the analyzer interface.

    计算本身是纯函数（便于单测），分析器只做 cerebro 集成的薄封装。
    """

    def get_analysis(self) -> Dict[str, List]:
        strategy = self.strategy
        return {
            "records": list(strategy.records),
            "executions": list(strategy.executions),
            "rebalance_dates": list(strategy.rebalance_dates),
        }
