# -*- coding: utf-8 -*-
"""Deterministic Phase 1 Strategy Lab engine.

This adapter keeps Phase 1 dependency-light while preserving the generic engine
contract that a backtrader adapter can implement later.
"""

from __future__ import annotations

import math
from datetime import date
from statistics import mean, pstdev
from typing import Dict, List

from src.core.strategy_lab.engine import StrategyLabEngine
from src.core.strategy_lab.models import (
    StrategyLabBar,
    StrategyLabDataSet,
    StrategyLabEquityPoint,
    StrategyLabInstrument,
    StrategyLabMetric,
    StrategyLabRunConfig,
    StrategyLabRunResult,
    StrategyLabTradeResult,
)


class FixtureDoubleLowEngine(StrategyLabEngine):
    """Run a small deterministic double-low strategy sample."""

    name = "fixture_double_low_v1"

    def __init__(self, dataset: StrategyLabDataSet | None = None, *, name: str | None = None):
        self.dataset = dataset or build_default_fixture_dataset()
        if name:
            self.name = name

    def run(self, config: StrategyLabRunConfig) -> StrategyLabRunResult:
        if config.strategy_id not in {"double-low", "low-premium"}:
            raise ValueError(f"Unsupported strategy_id: {config.strategy_id}")
        if config.instrument_type != "convertible_bond":
            raise ValueError("Phase 1 double-low fixture supports convertible_bond only")
        if config.start_date > config.end_date:
            raise ValueError("start_date cannot be after end_date")
        if config.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")

        candidates = self._candidate_instruments(config)
        if not candidates:
            raise ValueError("No instruments available for requested run")

        first_bars = {item.canonical_id: self._first_bar(item.canonical_id, config) for item in candidates}
        last_bars = {item.canonical_id: self._last_bar(item.canonical_id, config) for item in candidates}
        eligible = [
            item for item in candidates
            if first_bars[item.canonical_id] is not None
            and last_bars[item.canonical_id] is not None
            and self._passes_filters(first_bars[item.canonical_id], config)
        ]
        default_score_mode = "low_premium" if config.strategy_id == "low-premium" else "double_low"
        score_mode = str(config.parameters.get("score_mode", default_score_mode))
        if score_mode not in {"double_low", "low_premium", "weighted_double_low", "triple_low"}:
            raise ValueError(f"Unsupported score_mode: {score_mode}")
        percentiles = self._percentile_ranks([
            float(first_bars[item.canonical_id].remaining_size)
            for item in eligible
            if first_bars[item.canonical_id].remaining_size is not None
        ]) if score_mode == "triple_low" else []
        percentile_by_symbol = {}
        if score_mode == "triple_low":
            percentile_index = 0
            for item in eligible:
                if first_bars[item.canonical_id].remaining_size is not None:
                    percentile_by_symbol[item.symbol] = percentiles[percentile_index]
                    percentile_index += 1
        scored = sorted(
            (
                (self._score(first_bars[item.canonical_id], config, percentile_by_symbol.get(item.symbol)), item)
                for item in eligible
                if self._score(first_bars[item.canonical_id], config, percentile_by_symbol.get(item.symbol)) is not None
            ),
            key=lambda pair: pair[0],
        )
        max_positions = int(config.parameters.get("max_positions", 2) or 2)
        max_positions = max(1, min(max_positions, len(scored)))
        selected = [item for _, item in scored[:max_positions]]
        if not selected:
            raise ValueError("No instruments have enough fixture bars for requested date range")

        buy_date = min(first_bars[item.canonical_id].trade_date for item in selected)  # type: ignore[union-attr]
        sell_date = max(last_bars[item.canonical_id].trade_date for item in selected)  # type: ignore[union-attr]
        target_exposure = min(max(float(config.parameters.get("target_exposure", 1.0) or 1.0), 0.0), 1.0)
        commission = max(float(config.parameters.get("commission", 0.0002) or 0.0), 0.0)
        cash_per_position = config.initial_cash * target_exposure / len(selected)
        uninvested_cash = config.initial_cash - cash_per_position * len(selected)
        lot_size = max(int(config.parameters.get("lot_size", 10) or 10), 1)
        trades: List[StrategyLabTradeResult] = []
        final_equity = 0.0
        winning_positions = 0

        for item in selected:
            first = first_bars[item.canonical_id]
            last = last_bars[item.canonical_id]
            if first is None or last is None:
                continue
            quantity = math.floor((cash_per_position / first.close) / lot_size) * lot_size
            buy_amount = quantity * first.close
            sell_amount = quantity * last.close
            fee = (buy_amount + sell_amount) * commission
            final_equity += sell_amount + (cash_per_position - buy_amount) - fee
            if sell_amount > buy_amount:
                winning_positions += 1
            trades.append(
                StrategyLabTradeResult(
                    trade_date=buy_date,
                    canonical_id=item.canonical_id,
                    symbol=item.symbol,
                    market=item.market,
                    instrument_type=item.instrument_type,
                    side="buy",
                    quantity=quantity,
                    price=first.close,
                    amount=buy_amount,
                    fee=round(buy_amount * commission, 4),
                    reason=f"{score_mode}_entry",
                )
            )
            trades.append(
                StrategyLabTradeResult(
                    trade_date=sell_date,
                    canonical_id=item.canonical_id,
                    symbol=item.symbol,
                    market=item.market,
                    instrument_type=item.instrument_type,
                    side="sell",
                    quantity=quantity,
                    price=last.close,
                    amount=sell_amount,
                    fee=round(sell_amount * commission, 4),
                    reason="window_exit",
                )
            )

        final_equity += uninvested_cash
        equity_curve = self._build_equity_curve(config, selected, cash_per_position, uninvested_cash, lot_size)
        total_return_pct = (final_equity / config.initial_cash - 1.0) * 100
        max_drawdown_pct = _max_drawdown_pct([point.equity for point in equity_curve])
        annualized_return_pct = _annualized_return_pct(total_return_pct, buy_date, sell_date)
        daily_returns = _daily_returns([point.equity for point in equity_curve])
        sharpe_ratio = _sharpe_ratio(daily_returns)
        calmar_ratio = (
            annualized_return_pct / abs(max_drawdown_pct)
            if annualized_return_pct is not None and max_drawdown_pct < 0
            else None
        )
        benchmark_return_pct = self._benchmark_return_pct(config)

        metrics = StrategyLabMetric(
            total_return_pct=round(total_return_pct, 4),
            annualized_return_pct=round(annualized_return_pct, 4) if annualized_return_pct is not None else None,
            max_drawdown_pct=round(max_drawdown_pct, 4),
            sharpe_ratio=round(sharpe_ratio, 4) if sharpe_ratio is not None else None,
            sortino_ratio=None,
            calmar_ratio=round(calmar_ratio, 4) if calmar_ratio is not None else None,
            win_rate_pct=round((winning_positions / len(selected)) * 100, 4),
            trade_count=len(trades),
            exposure_days=max(0, (sell_date - buy_date).days),
            diagnostics={
                "engine": self.name,
                "score_mode": score_mode,
                "commission": commission,
                "target_exposure": target_exposure,
                "selected_symbols": [item.symbol for item in selected],
                "score": {
                    item.symbol: round(
                        self._score(
                            first_bars[item.canonical_id],
                            config,
                            percentile_by_symbol.get(item.symbol),
                        ),  # type: ignore[arg-type]
                        4,
                    )
                    for item in selected
                },
            },
        )
        return StrategyLabRunResult(
            final_equity=round(final_equity, 4),
            benchmark_return_pct=benchmark_return_pct,
            metrics=metrics,
            trades=trades,
            equity_curve=equity_curve,
        )

    def _candidate_instruments(self, config: StrategyLabRunConfig) -> List[StrategyLabInstrument]:
        symbols = {symbol.lower() for symbol in config.symbols}
        return [
            item
            for item in self.dataset.instruments
            if item.market == config.market
            and item.instrument_type == config.instrument_type
            and (not symbols or item.symbol.lower() in symbols or item.canonical_id.lower() in symbols)
        ]

    def _first_bar(self, canonical_id: str, config: StrategyLabRunConfig) -> StrategyLabBar | None:
        bars = [
            bar
            for bar in self.dataset.bars.get(canonical_id, [])
            if config.start_date <= bar.trade_date <= config.end_date
        ]
        return bars[0] if bars else None

    def _last_bar(self, canonical_id: str, config: StrategyLabRunConfig) -> StrategyLabBar | None:
        bars = [
            bar
            for bar in self.dataset.bars.get(canonical_id, [])
            if config.start_date <= bar.trade_date <= config.end_date
        ]
        return bars[-1] if bars else None

    @staticmethod
    def _double_low_score(bar: StrategyLabBar) -> float:
        return bar.close + (bar.cb_premium_rate or 0.0)

    @classmethod
    def _score(cls, bar: StrategyLabBar, config: StrategyLabRunConfig, percentile: float | None) -> float | None:
        premium = bar.cb_premium_rate
        if premium is None:
            return None
        default_score_mode = "low_premium" if config.strategy_id == "low-premium" else "double_low"
        mode = str(config.parameters.get("score_mode", default_score_mode))
        if mode == "low_premium":
            return premium
        if mode == "weighted_double_low":
            return bar.close * float(config.parameters.get("price_weight", 1.0)) + premium * float(config.parameters.get("premium_weight", 1.0))
        if mode == "triple_low":
            if percentile is None:
                return None
            return bar.close + premium + percentile * float(config.parameters.get("remain_size_weight", 1.0))
        return cls._double_low_score(bar)

    @staticmethod
    def _passes_filters(bar: StrategyLabBar, config: StrategyLabRunConfig) -> bool:
        min_size = float(config.parameters.get("min_remaining_size", 0.0) or 0.0)
        max_premium = float(config.parameters.get("max_abs_premium", 200.0) or 200.0)
        return (
            (bar.remaining_size is None or bar.remaining_size >= min_size)
            and bar.cb_premium_rate is not None
            and abs(bar.cb_premium_rate) <= max_premium
            and (not bool(config.parameters.get("exclude_event_blocked", True)) or not bar.event_blocked)
        )

    @staticmethod
    def _percentile_ranks(values: List[float]) -> List[float]:
        if not values:
            return []
        if len(values) == 1:
            return [0.0]
        sorted_values = sorted((value, index) for index, value in enumerate(values))
        ranks = [0.0] * len(values)
        denominator = len(values) - 1
        cursor = 0
        while cursor < len(sorted_values):
            end = cursor
            while end + 1 < len(sorted_values) and sorted_values[end + 1][0] == sorted_values[cursor][0]:
                end += 1
            rank = ((cursor + end) / 2) / denominator * 100
            for _, original_index in sorted_values[cursor:end + 1]:
                ranks[original_index] = rank
            cursor = end + 1
        return ranks

    def _build_equity_curve(
        self,
        config: StrategyLabRunConfig,
        selected: List[StrategyLabInstrument],
        cash_per_position: float,
        uninvested_cash: float = 0.0,
        lot_size: int = 10,
    ) -> List[StrategyLabEquityPoint]:
        trade_dates = sorted({
            bar.trade_date
            for item in selected
            for bar in self.dataset.bars.get(item.canonical_id, [])
            if config.start_date <= bar.trade_date <= config.end_date
        })
        first_bars = {item.canonical_id: self._first_bar(item.canonical_id, config) for item in selected}
        points: List[StrategyLabEquityPoint] = []
        for current_date in trade_dates:
            positions_value = 0.0
            idle_cash = 0.0
            for item in selected:
                first = first_bars[item.canonical_id]
                if first is None:
                    continue
                quantity = math.floor((cash_per_position / first.close) / lot_size) * lot_size
                idle_cash += cash_per_position - quantity * first.close
                current_bar = self._bar_at_or_before(item.canonical_id, current_date)
                positions_value += quantity * (current_bar.close if current_bar else first.close)
            points.append(
                StrategyLabEquityPoint(
                    trade_date=current_date,
                    equity=round(uninvested_cash + idle_cash + positions_value, 4),
                    cash=round(idle_cash, 4),
                    positions_value=round(positions_value, 4),
                )
            )
        return points

    def _bar_at_or_before(self, canonical_id: str, current_date: date) -> StrategyLabBar | None:
        bars = [bar for bar in self.dataset.bars.get(canonical_id, []) if bar.trade_date <= current_date]
        return bars[-1] if bars else None

    def _benchmark_return_pct(self, config: StrategyLabRunConfig) -> float | None:
        if not config.benchmark_symbol:
            return None
        symbol = config.benchmark_symbol.lower()
        for item in self.dataset.instruments:
            if item.symbol.lower() == symbol or item.canonical_id.lower() == symbol:
                first = self._first_bar(item.canonical_id, config)
                last = self._last_bar(item.canonical_id, config)
                if first is not None and last is not None:
                    return round((last.close / first.close - 1.0) * 100, 4)
        return None


def build_default_fixture_dataset() -> StrategyLabDataSet:
    instruments = [
        StrategyLabInstrument("cn.convertible_bond.113001", "113001", "cn", "convertible_bond", "CB Alpha"),
        StrategyLabInstrument("cn.convertible_bond.113002", "113002", "cn", "convertible_bond", "CB Beta"),
        StrategyLabInstrument("cn.convertible_bond.113003", "113003", "cn", "convertible_bond", "CB Gamma"),
    ]
    bars: Dict[str, List[StrategyLabBar]] = {
        "cn.convertible_bond.113001": [
            StrategyLabBar(date(2024, 1, 2), 102.0, 18.0),
            StrategyLabBar(date(2024, 1, 3), 103.5, 17.2),
            StrategyLabBar(date(2024, 1, 4), 105.0, 16.5),
        ],
        "cn.convertible_bond.113002": [
            StrategyLabBar(date(2024, 1, 2), 96.0, 22.0),
            StrategyLabBar(date(2024, 1, 3), 97.0, 21.0),
            StrategyLabBar(date(2024, 1, 4), 98.5, 20.0),
        ],
        "cn.convertible_bond.113003": [
            StrategyLabBar(date(2024, 1, 2), 118.0, 9.0),
            StrategyLabBar(date(2024, 1, 3), 117.0, 9.5),
            StrategyLabBar(date(2024, 1, 4), 116.0, 10.0),
        ],
    }
    return StrategyLabDataSet(instruments=instruments, bars=bars)


def _daily_returns(equity_values: List[float]) -> List[float]:
    returns: List[float] = []
    for previous, current in zip(equity_values, equity_values[1:]):
        if previous:
            returns.append(current / previous - 1.0)
    return returns


def _max_drawdown_pct(equity_values: List[float]) -> float:
    if not equity_values:
        return 0.0
    peak = equity_values[0]
    max_drawdown = 0.0
    for value in equity_values:
        peak = max(peak, value)
        if peak:
            max_drawdown = min(max_drawdown, value / peak - 1.0)
    return max_drawdown * 100


def _annualized_return_pct(total_return_pct: float, start_date: date, end_date: date) -> float | None:
    days = max(1, (end_date - start_date).days)
    return ((1 + total_return_pct / 100) ** (365 / days) - 1) * 100


def _sharpe_ratio(daily_returns: List[float]) -> float | None:
    if len(daily_returns) < 2:
        return None
    volatility = pstdev(daily_returns)
    if volatility == 0:
        return None
    return (mean(daily_returns) / volatility) * math.sqrt(252)
