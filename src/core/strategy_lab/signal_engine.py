# -*- coding: utf-8 -*-
"""Signal-style backtest engine for Strategy Lab (time-series entry/exit rules).

纯 Python 逐标的状态机（不运行 backtrader；指标组装复用
:mod:`src.core.strategy_lab.backtest.analyzers` 纯函数，与轮动引擎同口径）：

- 每个标的独立运行「空仓 → 全部买入规则成立 → 持有 → 任一卖出规则成立
  → 全仓卖出」的状态机，标的之间共享现金池但互不干扰；
- 规则引用的因子值缺失（均线预热期 / 数据未同步如股息率）当日不触发；
- 信号在 T 日收盘确认并按 T 日收盘价成交（cheat-on-close，与轮动引擎
  一致）；双边佣金、整手取整、sizing 预留佣金余量；
- 买入预算 = 当日总权益 / 标的数 × 单标的仓位上限（%），存量持仓不修剪；
- 期末不强平：持仓按最后市值计入 final_equity（与轮动引擎一致），
  胜率只统计已完成的卖出回合。

数据行为 ``strategy_lab_etf_daily`` 装载（含预热段），引擎只在
``[start_date, end_date]`` 窗口内产生信号与快照。
"""

from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from src.core.strategy_lab.backtest.analyzers import (
    compute_series_metrics,
    compute_turnover_series,
    compute_win_rate_pct,
    daily_returns,
)
from src.core.strategy_lab.backtest.params import BENCHMARK_EQUAL_WEIGHT, BENCHMARK_INDEX_ALIASES
from src.core.strategy_lab.backtest.strategy import ExecutionRecord
from src.core.strategy_lab.engine import StrategyLabEngine
from src.core.strategy_lab.factors import COMPARATORS
from src.core.strategy_lab.models import (
    StrategyLabEquityPoint,
    StrategyLabMetric,
    StrategyLabRunConfig,
    StrategyLabRunResult,
    StrategyLabTradeResult,
)
from src.core.strategy_lab.signal_params import SignalParams, SignalRule
from src.core.strategy_lab.series_factors import get_series_factor


class SignalEngine(StrategyLabEngine):
    """Per-symbol entry/exit signal backtest over daily bar rows."""

    name = "signal_v1"

    def __init__(self, rows: Optional[Sequence[Dict[str, Any]]] = None, *, benchmark_rows=None):
        self.rows: List[Dict[str, Any]] = list(rows or [])
        self.benchmark_rows: List[Tuple[date, float]] = list(benchmark_rows or [])

    def run(self, config: StrategyLabRunConfig) -> StrategyLabRunResult:
        if config.start_date > config.end_date:
            raise ValueError("start_date cannot be after end_date")
        if config.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        params = SignalParams.from_parameters(config.parameters)

        symbols_filter = {
            str(symbol).strip().lower().split(".")[-1]
            for symbol in config.symbols
            if str(symbol).strip()
        }
        per_symbol: Dict[str, List[Dict[str, Any]]] = {}
        for row in self.rows:
            symbol = str(row["symbol"])
            if row.get("close") is None:
                continue
            if symbols_filter and symbol.lower() not in symbols_filter:
                continue
            per_symbol.setdefault(symbol, []).append(row)
        for rows in per_symbol.values():
            rows.sort(key=lambda row: row["trade_date"])
        if not per_symbol:
            raise ValueError(
                "回测区间内没有已同步的 ETF 日线数据，请先在「行情数据-数据同步」完成 etf_daily 同步"
            )

        needed_factors: Set[str] = {rule.factor for rule in params.entry_rules + params.exit_rules}
        factor_series: Dict[str, Dict[str, List[Optional[float]]]] = {}
        for symbol, rows in per_symbol.items():
            window_params = params.window_params()
            factor_series[symbol] = {
                factor: get_series_factor(factor).compute(rows, window_params)
                for factor in sorted(needed_factors)
            }

        dates = sorted({
            row["trade_date"] for rows in per_symbol.values() for row in rows
            if config.start_date <= row["trade_date"] <= config.end_date
        })
        if not dates:
            raise ValueError("回测窗口内没有 ETF 日线数据，请检查区间或先完成同步")

        cash = float(config.initial_cash)
        positions: Dict[str, float] = {}
        trades: List[StrategyLabTradeResult] = []
        executions: List[ExecutionRecord] = []
        amounts_by_date: Dict[date, Dict[str, float]] = {}
        equity_curve: List[StrategyLabEquityPoint] = []
        entry_signals = 0
        exit_signals = 0
        factor_available_cells = {factor: 0 for factor in needed_factors}
        universe = sorted(per_symbol)

        for current_date in dates:
            # 估值用「该标的当日或最近一根」收盘价
            closes_today = {
                symbol: self._close_at_or_before(per_symbol, symbol, current_date)
                for symbol in universe
            }
            equity_before = cash + sum(
                qty * closes_today[symbol] for symbol, qty in positions.items()
            )
            budget = equity_before / len(universe) * (params.max_position_pct / 100.0)

            for symbol in universe:
                index = self._index_at_or_before(per_symbol, symbol, current_date)
                if index is None:
                    continue
                close = closes_today[symbol]
                if close is None or close <= 0:
                    continue
                values = {
                    factor: factor_series[symbol][factor][index]
                    for factor in needed_factors
                }
                for factor, value in values.items():
                    if value is not None:
                        factor_available_cells[factor] += 1
                quantity = positions.get(symbol, 0.0)
                if quantity > 0 and self._any_rule_holds(params.exit_rules, values):
                    exit_signals += 1
                    amount = quantity * close
                    fee = amount * params.commission
                    cash += amount - fee
                    positions.pop(symbol, None)
                    self._record(
                        trades, executions, amounts_by_date, config, current_date,
                        symbol, "sell", quantity, close, fee, "signal_exit",
                    )
                elif quantity <= 0 and self._all_rules_hold(params.entry_rules, values):
                    lot = params.lot_size
                    buy_quantity = math.floor(budget / (close * (1.0 + params.commission)) / lot) * lot
                    buy_amount = buy_quantity * close
                    if buy_quantity > 0 and buy_amount * (1.0 + params.commission) <= cash:
                        fee = buy_amount * params.commission
                        cash -= buy_amount + fee
                        positions[symbol] = float(buy_quantity)
                        entry_signals += 1
                        self._record(
                            trades, executions, amounts_by_date, config, current_date,
                            symbol, "buy", buy_quantity, close, fee, "signal_entry",
                        )

            positions_value = sum(
                qty * closes_today[symbol] for symbol, qty in positions.items()
            )
            equity_curve.append(StrategyLabEquityPoint(
                trade_date=current_date,
                equity=round(cash + positions_value, 4),
                cash=round(cash, 4),
                positions_value=round(positions_value, 4),
                holdings=sorted(positions),
                holdings_count=len(positions),
            ))

        equity = [point.equity for point in equity_curve]
        initial_cash = float(config.initial_cash)
        mode, benchmark_series = self._benchmark_series(
            config, per_symbol, dates, initial_cash
        )
        drawdowns = self._drawdown_series(equity)
        returns = daily_returns(equity, initial_cash)
        benchmark_returns = daily_returns(benchmark_series, initial_cash)
        excess_equity: List[float] = []
        excess_value = initial_cash
        for excess in [r - b for r, b in zip(returns, benchmark_returns)]:
            excess_value *= 1.0 + excess
            excess_equity.append(excess_value)
        turnover_series, turnover_avg = compute_turnover_series(
            dates=dates, equity=equity, amounts_by_date=amounts_by_date, initial_cash=initial_cash
        )
        strategy_metrics = compute_series_metrics(dates=dates, equity=equity, initial_cash=initial_cash)
        benchmark_metrics = compute_series_metrics(dates=dates, equity=benchmark_series, initial_cash=initial_cash)
        excess_metrics = compute_series_metrics(dates=dates, equity=excess_equity, initial_cash=initial_cash)
        benchmark_metrics["relative_excess"] = excess_metrics
        benchmark_metrics["mode"] = mode

        total_cells = len(dates) * len(universe)
        diagnostics = {
            "engine": self.name,
            **params.summary(),
            "benchmark_mode": mode,
            "symbols_count": len(universe),
            "calendar_days": len(dates),
            "warmup_skip_days": sum(
                1 for point in equity_curve if point.holdings_count == 0
            ),
            "entry_signals": entry_signals,
            "exit_signals": exit_signals,
            "factor_coverage_pct": {
                factor: round(available / total_cells * 100, 2) if total_cells else None
                for factor, available in factor_available_cells.items()
            },
            "final_holdings": sorted(positions),
        }
        metrics = StrategyLabMetric(
            total_return_pct=strategy_metrics["total_return_pct"] or 0.0,
            annualized_return_pct=strategy_metrics["annualized_return_pct"],
            max_drawdown_pct=strategy_metrics["max_drawdown_pct"] or 0.0,
            sharpe_ratio=strategy_metrics["sharpe_ratio"],
            sortino_ratio=strategy_metrics["sortino_ratio"],
            calmar_ratio=strategy_metrics["calmar_ratio"],
            win_rate_pct=compute_win_rate_pct(executions),
            trade_count=len(trades),
            exposure_days=(dates[-1] - dates[0]).days if len(dates) > 1 else 0,
            diagnostics=diagnostics,
            turnover_avg_pct=turnover_avg,
            period_count=strategy_metrics["period_count"],
            profit_periods=strategy_metrics["profit_periods"],
            loss_periods=strategy_metrics["loss_periods"],
            benchmark_metrics=benchmark_metrics,
        )
        return StrategyLabRunResult(
            final_equity=round(equity[-1], 4),
            benchmark_return_pct=benchmark_metrics.get("total_return_pct"),
            metrics=metrics,
            trades=trades,
            equity_curve=[
                StrategyLabEquityPoint(
                    trade_date=point.trade_date,
                    equity=point.equity,
                    cash=point.cash,
                    positions_value=point.positions_value,
                    benchmark_equity=round(benchmark_series[index], 4) if index < len(benchmark_series) else None,
                    drawdown_pct=drawdowns[index],
                    daily_return_pct=round(returns[index] * 100, 4) if index < len(returns) else None,
                    turnover_pct=turnover_series[index],
                    holdings=list(point.holdings),
                    holdings_count=point.holdings_count,
                )
                for index, point in enumerate(equity_curve)
            ],
        )

    # ------------------------------------------------------------------
    # 规则求值
    # ------------------------------------------------------------------

    @staticmethod
    def _all_rules_hold(rules: List[SignalRule], values: Dict[str, Optional[float]]) -> bool:
        """全部规则成立才买入；任一因子缺失即整体不成立。"""
        for rule in rules:
            value = values.get(rule.factor)
            if value is None:
                return False
            if not COMPARATORS[rule.op](value, rule.value):
                return False
        return True

    @staticmethod
    def _any_rule_holds(rules: List[SignalRule], values: Dict[str, Optional[float]]) -> bool:
        """任一规则成立即卖出（缺失因子不参与判断）。"""
        for rule in rules:
            value = values.get(rule.factor)
            if value is None:
                continue
            if COMPARATORS[rule.op](value, rule.value):
                return True
        return False

    # ------------------------------------------------------------------
    # 数据访问
    # ------------------------------------------------------------------

    @staticmethod
    def _index_at_or_before(
        per_symbol: Dict[str, List[Dict[str, Any]]], symbol: str, current_date: date
    ) -> Optional[int]:
        rows = per_symbol.get(symbol) or []
        keys = [row["trade_date"] for row in rows]
        index = bisect_right(keys, current_date) - 1
        return index if index >= 0 else None

    @classmethod
    def _close_at_or_before(
        cls, per_symbol: Dict[str, List[Dict[str, Any]]], symbol: str, current_date: date
    ) -> Optional[float]:
        index = cls._index_at_or_before(per_symbol, symbol, current_date)
        if index is None:
            return None
        close = per_symbol[symbol][index].get("close")
        return float(close) if close is not None else None

    # ------------------------------------------------------------------
    # 基准
    # ------------------------------------------------------------------

    def _benchmark_series(
        self,
        config: StrategyLabRunConfig,
        per_symbol: Dict[str, List[Dict[str, Any]]],
        dates: Sequence[date],
        initial_cash: float,
    ) -> Tuple[str, List[float]]:
        requested = (config.benchmark_symbol or "").strip().lower()
        universe = sorted(per_symbol)
        if requested in {"buy_hold", "hold"}:
            # 长持：回测首日等权买入 universe 并持有到期末（期初固定份额，
            # 区别于等权池的每日再平衡；理想化对比线：无费用、不取整手）
            return "buy_hold", self._buy_hold_series(per_symbol, dates, initial_cash)
        if requested and requested not in BENCHMARK_INDEX_ALIASES:
            normalized = requested.split(".")[-1]
            if normalized in {symbol.lower() for symbol in universe}:
                return f"symbol:{normalized}", self._normalized_closes(
                    per_symbol[normalized], dates, initial_cash
                )
        if requested in BENCHMARK_INDEX_ALIASES:
            return "index:000300", self._normalized_series(
                dict(self.benchmark_rows), dates, initial_cash
            )
        # 等权池：universe 每日等权收益累计（停牌日沿用最近收盘）
        closes_by_symbol = {
            symbol: self._ffill_closes(per_symbol[symbol], dates) for symbol in universe
        }
        equity = [initial_cash]
        value = initial_cash
        for index in range(1, len(dates)):
            daily_returns_pool: List[float] = []
            for series in closes_by_symbol.values():
                previous, current = series[index - 1], series[index]
                if previous is not None and current is not None and previous > 0:
                    daily_returns_pool.append(current / previous - 1.0)
            value *= 1.0 + (sum(daily_returns_pool) / len(daily_returns_pool) if daily_returns_pool else 0.0)
            equity.append(value)
        return BENCHMARK_EQUAL_WEIGHT, equity

    @staticmethod
    def _buy_hold_series(
        per_symbol: Dict[str, List[Dict[str, Any]]], dates: Sequence[date], initial_cash: float
    ) -> List[float]:
        """期初等权买入持有：首日按收盘买入固定份额，此后按最近收盘估值。

        某标的在首日（含预热）都没有收盘价时份额记 0（该份资金闲置），
        不向其余标的重分配——ETF 连续数据下为罕见边界。
        """
        universe = sorted(per_symbol)
        budget = initial_cash / len(universe) if universe else 0.0
        shares: Dict[str, float] = {}
        for symbol in universe:
            first_close = SignalEngine._close_at_or_before(per_symbol, symbol, dates[0])
            shares[symbol] = budget / first_close if first_close and first_close > 0 else 0.0
        series: List[float] = []
        for current_date in dates:
            value = sum(
                share * (SignalEngine._close_at_or_before(per_symbol, symbol, current_date) or 0.0)
                for symbol, share in shares.items()
            )
            series.append(round(value, 4))
        return series

    @staticmethod
    def _ffill_closes(
        rows: List[Dict[str, Any]], dates: Sequence[date]
    ) -> List[Optional[float]]:
        series: List[Optional[float]] = []
        last: Optional[float] = None
        index = 0
        for current_date in dates:
            while index < len(rows) and rows[index]["trade_date"] <= current_date:
                close = rows[index].get("close")
                if close is not None:
                    last = float(close)
                index += 1
            series.append(last)
        return series

    @classmethod
    def _normalized_closes(
        cls, rows: List[Dict[str, Any]], dates: Sequence[date], initial_cash: float
    ) -> List[float]:
        closes = cls._ffill_closes(rows, dates)
        return cls._normalized_values(closes, initial_cash)

    @classmethod
    def _normalized_series(
        cls, closes_by_date: Dict[date, float], dates: Sequence[date], initial_cash: float
    ) -> List[float]:
        series: List[Optional[float]] = []
        last: Optional[float] = None
        for current_date in dates:
            if current_date in closes_by_date:
                last = float(closes_by_date[current_date])
            series.append(last)
        return cls._normalized_values(series, initial_cash)

    @staticmethod
    def _normalized_values(closes: List[Optional[float]], initial_cash: float) -> List[float]:
        equity: List[float] = []
        base: Optional[float] = None
        for close in closes:
            if close is None:
                equity.append(initial_cash)
                continue
            if base is None:
                base = close
            equity.append(initial_cash * close / base)
        return equity

    @staticmethod
    def _drawdown_series(equity: Sequence[float]) -> List[Optional[float]]:
        peak: Optional[float] = None
        drawdowns: List[Optional[float]] = []
        for value in equity:
            peak = value if peak is None else max(peak, value)
            drawdowns.append(round((value / peak - 1.0) * 100, 4) if peak else None)
        return drawdowns

    # ------------------------------------------------------------------
    # 成交记录
    # ------------------------------------------------------------------

    @staticmethod
    def _record(
        trades: List[StrategyLabTradeResult],
        executions: List[ExecutionRecord],
        amounts_by_date: Dict[date, Dict[str, float]],
        config: StrategyLabRunConfig,
        trade_date: date,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        fee: float,
        reason: str,
    ) -> None:
        trades.append(StrategyLabTradeResult(
            trade_date=trade_date,
            canonical_id=f"{config.market}.etf.{symbol}",
            symbol=symbol,
            market=config.market,
            instrument_type="etf",
            side=side,
            quantity=quantity,
            price=price,
            amount=round(quantity * price, 4),
            fee=round(fee, 4),
            reason=reason,
        ))
        executions.append(ExecutionRecord(
            trade_date=trade_date,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            amount=quantity * price,
            fee=fee,
            reason=reason,
        ))
        slot = amounts_by_date.setdefault(trade_date, {"buy": 0.0, "sell": 0.0})
        slot[side] += quantity * price
