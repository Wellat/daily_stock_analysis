# -*- coding: utf-8 -*-
"""Domain models for Strategy Lab."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class StrategyLabInstrument:
    canonical_id: str
    symbol: str
    market: str
    instrument_type: str
    name: Optional[str] = None


@dataclass(frozen=True)
class StrategyLabBar:
    trade_date: date
    close: float
    cb_premium_rate: Optional[float] = None
    remaining_size: Optional[float] = None
    event_blocked: bool = False


@dataclass(frozen=True)
class StrategyLabDataSet:
    instruments: List[StrategyLabInstrument]
    bars: Dict[str, List[StrategyLabBar]]


@dataclass(frozen=True)
class StrategyLabRunConfig:
    strategy_id: str
    market: str
    instrument_type: str
    start_date: date
    end_date: date
    initial_cash: float
    benchmark_symbol: Optional[str] = None
    symbols: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    portfolio_account_id: Optional[int] = None


@dataclass(frozen=True)
class StrategyLabMetric:
    total_return_pct: float
    annualized_return_pct: Optional[float]
    max_drawdown_pct: float
    sharpe_ratio: Optional[float]
    sortino_ratio: Optional[float]
    calmar_ratio: Optional[float]
    win_rate_pct: Optional[float]
    trade_count: int
    exposure_days: int
    diagnostics: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyLabTradeResult:
    trade_date: date
    canonical_id: str
    symbol: str
    market: str
    instrument_type: str
    side: str
    quantity: float
    price: float
    amount: float
    fee: float = 0.0
    reason: Optional[str] = None
    portfolio_trade_id: Optional[int] = None


@dataclass(frozen=True)
class StrategyLabEquityPoint:
    trade_date: date
    equity: float
    cash: float
    positions_value: float


@dataclass(frozen=True)
class StrategyLabRunResult:
    final_equity: float
    benchmark_return_pct: Optional[float]
    metrics: StrategyLabMetric
    trades: List[StrategyLabTradeResult]
    equity_curve: List[StrategyLabEquityPoint]
