# -*- coding: utf-8 -*-
"""Moving-average crossover engine for Strategy Lab.

The original project used a Backtrader strategy for this rule.  Strategy Lab
keeps the execution contract independent from Backtrader and applies the same
signal semantics to the normalized domain dataset.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Dict, List

from src.core.strategy_lab.engine import StrategyLabEngine
from src.core.strategy_lab.fixture_engine import (
    _annualized_return_pct,
    _daily_returns,
    _max_drawdown_pct,
    _sharpe_ratio,
)
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


class MovingAverageCrossoverEngine(StrategyLabEngine):
    """Run the migrated fast/slow moving-average strategy."""

    name = "moving_average_crossover_v1"

    def __init__(self, dataset: StrategyLabDataSet):
        self.dataset = dataset

    def run(self, config: StrategyLabRunConfig) -> StrategyLabRunResult:
        if config.strategy_id != "ma-crossover":
            raise ValueError(f"Unsupported strategy_id: {config.strategy_id}")
        if config.instrument_type != "convertible_bond":
            raise ValueError("Moving average crossover currently supports convertible_bond only")
        if config.start_date > config.end_date:
            raise ValueError("start_date cannot be after end_date")
        if config.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")

        fast_period = _positive_int(config.parameters.get("fast_period", 5), "fast_period")
        slow_period = _positive_int(config.parameters.get("slow_period", 20), "slow_period")
        if fast_period >= slow_period:
            raise ValueError("fast_period must be smaller than slow_period")
        premium_threshold = _non_negative_float(
            config.parameters.get("premium_rate_threshold", 30.0),
            "premium_rate_threshold",
        )
        position_pct = min(max(_non_negative_float(config.parameters.get("position_pct", 0.95), "position_pct"), 0.0), 1.0)
        commission = max(_non_negative_float(config.parameters.get("commission", 0.0002), "commission"), 0.0)
        lot_size = max(_positive_int(config.parameters.get("lot_size", 10), "lot_size"), 1)

        candidates = self._candidate_instruments(config)
        if not candidates:
            raise ValueError("No instruments available for requested run")

        dates = sorted({
            bar.trade_date
            for item in candidates
            for bar in self.dataset.bars.get(item.canonical_id, [])
            if config.start_date <= bar.trade_date <= config.end_date
        })
        if not dates:
            raise ValueError("No bars available for requested run")

        cash = float(config.initial_cash)
        positions: Dict[str, float] = {}
        entry_prices: Dict[str, float] = {}
        trades: List[StrategyLabTradeResult] = []
        equity_curve: List[StrategyLabEquityPoint] = []
        selected_symbols: set[str] = set()
        winning_positions = 0
        closed_positions = 0

        for current_date in dates:
            for item in candidates:
                bars = self._bars_to_date(item.canonical_id, current_date)
                if not bars:
                    continue
                current = bars[-1]
                closes = [bar.close for bar in bars]
                fast = _sma(closes, fast_period)
                slow = _sma(closes, slow_period)
                previous_bars = bars[:-1]
                previous_fast = _sma([bar.close for bar in previous_bars], fast_period)
                previous_slow = _sma([bar.close for bar in previous_bars], slow_period)
                code = item.canonical_id
                quantity = positions.get(code, 0.0)
                tradable = current.close > 0 and not current.event_blocked
                premium = current.cb_premium_rate
                golden_cross = (
                    fast is not None and slow is not None and previous_fast is not None and previous_slow is not None
                    and previous_fast <= previous_slow and fast > slow
                )
                death_cross = (
                    fast is not None and slow is not None and previous_fast is not None and previous_slow is not None
                    and previous_fast >= previous_slow and fast < slow
                )

                if quantity > 0 and (death_cross or current.event_blocked):
                    amount = quantity * current.close
                    fee = amount * commission
                    cash += amount - fee
                    entry = entry_prices.pop(code, current.close)
                    if amount > quantity * entry:
                        winning_positions += 1
                    closed_positions += 1
                    positions.pop(code, None)
                    trades.append(self._trade(item, current_date, "sell", quantity, current.close, fee, "event_exit" if current.event_blocked else "death_cross"))
                    quantity = 0.0

                if quantity <= 0 and tradable and golden_cross and (premium is None or premium < premium_threshold):
                    amount = cash * position_pct
                    buy_quantity = math.floor((amount / current.close) / lot_size) * lot_size
                    buy_amount = buy_quantity * current.close
                    if buy_quantity > 0 and buy_amount * (1 + commission) <= cash:
                        fee = buy_amount * commission
                        cash -= buy_amount + fee
                        positions[code] = float(buy_quantity)
                        entry_prices[code] = current.close
                        selected_symbols.add(item.symbol)
                        trades.append(self._trade(item, current_date, "buy", buy_quantity, current.close, fee, "golden_cross"))

            positions_value = sum(
                quantity * (self._bar_at_or_before(code, current_date).close if self._bar_at_or_before(code, current_date) else 0.0)
                for code, quantity in positions.items()
            )
            equity_curve.append(StrategyLabEquityPoint(current_date, round(cash + positions_value, 4), round(cash, 4), round(positions_value, 4)))

        # The Backtrader strategy also liquidates an open position at the end of
        # the run for a comparable realized final value.
        if equity_curve and positions:
            final_date = dates[-1]
            for code, quantity in list(positions.items()):
                bar = self._bar_at_or_before(code, final_date)
                item = next(candidate for candidate in candidates if candidate.canonical_id == code)
                if bar is None:
                    continue
                amount = quantity * bar.close
                fee = amount * commission
                cash += amount - fee
                entry = entry_prices.get(code, bar.close)
                if amount > quantity * entry:
                    winning_positions += 1
                closed_positions += 1
                trades.append(self._trade(item, final_date, "sell", quantity, bar.close, fee, "window_exit"))
            positions.clear()

        final_equity = cash if not positions else equity_curve[-1].equity
        total_return = (final_equity / config.initial_cash - 1.0) * 100
        drawdown = _max_drawdown_pct([point.equity for point in equity_curve])
        annualized = _annualized_return_pct(total_return, dates[0], dates[-1])
        daily_returns = _daily_returns([point.equity for point in equity_curve])
        return StrategyLabRunResult(
            final_equity=round(final_equity, 4),
            benchmark_return_pct=None,
            metrics=StrategyLabMetric(
                total_return_pct=round(total_return, 4),
                annualized_return_pct=round(annualized, 4) if annualized is not None else None,
                max_drawdown_pct=round(drawdown, 4),
                sharpe_ratio=round(_sharpe_ratio(daily_returns), 4) if _sharpe_ratio(daily_returns) is not None else None,
                sortino_ratio=None,
                calmar_ratio=None,
                win_rate_pct=round(winning_positions / closed_positions * 100, 4) if closed_positions else None,
                trade_count=len(trades),
                exposure_days=sum(1 for point in equity_curve if point.positions_value > 0),
                diagnostics={
                    "engine": self.name,
                    "fast_period": fast_period,
                    "slow_period": slow_period,
                    "premium_rate_threshold": premium_threshold,
                    "selected_symbols": sorted(selected_symbols),
                },
            ),
            trades=trades,
            equity_curve=equity_curve,
        )

    def _candidate_instruments(self, config: StrategyLabRunConfig) -> List[StrategyLabInstrument]:
        symbols = {str(symbol).lower() for symbol in config.symbols}
        return [
            item for item in self.dataset.instruments
            if item.market == config.market
            and item.instrument_type == config.instrument_type
            and (not symbols or item.symbol.lower() in symbols or item.canonical_id.lower() in symbols)
        ]

    def _bars_to_date(self, canonical_id: str, current_date: date) -> List[StrategyLabBar]:
        return [
            bar for bar in self.dataset.bars.get(canonical_id, [])
            if bar.trade_date <= current_date
        ]

    def _bar_at_or_before(self, canonical_id: str, current_date: date) -> StrategyLabBar | None:
        bars = self._bars_to_date(canonical_id, current_date)
        return bars[-1] if bars else None

    @staticmethod
    def _trade(item: StrategyLabInstrument, trade_date: date, side: str, quantity: float, price: float, fee: float, reason: str) -> StrategyLabTradeResult:
        return StrategyLabTradeResult(
            trade_date=trade_date,
            canonical_id=item.canonical_id,
            symbol=item.symbol,
            market=item.market,
            instrument_type=item.instrument_type,
            side=side,
            quantity=quantity,
            price=price,
            amount=round(quantity * price, 4),
            fee=round(fee, 4),
            reason=reason,
        )


def _sma(values: List[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _positive_int(value: object, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _non_negative_float(value: object, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return parsed
