# -*- coding: utf-8 -*-
"""Strategy Lab engine tests."""

from __future__ import annotations

from datetime import date

import pytest

from src.core.strategy_lab.fixture_engine import FixtureDoubleLowEngine
from src.core.strategy_lab.ma_engine import MovingAverageCrossoverEngine
from src.core.strategy_lab.models import (
    StrategyLabBar,
    StrategyLabDataSet,
    StrategyLabInstrument,
    StrategyLabRunConfig,
)


def test_double_low_fixture_engine_returns_structured_result() -> None:
    engine = FixtureDoubleLowEngine()
    result = engine.run(
        StrategyLabRunConfig(
            strategy_id="double-low",
            market="cn",
            instrument_type="convertible_bond",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            initial_cash=100000,
            parameters={"max_positions": 2},
        )
    )

    assert result.final_equity > 0
    assert result.metrics.trade_count == 4
    assert result.metrics.total_return_pct is not None
    assert result.metrics.diagnostics["selected_symbols"] == ["113002", "113001"]
    assert [trade.side for trade in result.trades] == ["buy", "sell", "buy", "sell"]
    assert len(result.equity_curve) == 3


def test_double_low_fixture_engine_rejects_unknown_strategy() -> None:
    engine = FixtureDoubleLowEngine()

    with pytest.raises(ValueError, match="Unsupported strategy_id"):
        engine.run(
            StrategyLabRunConfig(
                strategy_id="unknown",
                market="cn",
                instrument_type="convertible_bond",
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 4),
                initial_cash=100000,
            )
        )


def test_double_low_engine_supports_migrated_score_modes() -> None:
    bar = StrategyLabBar(date(2024, 1, 2), 110.0, 20.0, remaining_size=5.0)
    assert FixtureDoubleLowEngine._score(
        bar,
        StrategyLabRunConfig("double-low", "cn", "convertible_bond", date(2024, 1, 2), date(2024, 1, 2), 1000, parameters={"score_mode": "low_premium"}),
        None,
    ) == 20.0
    assert FixtureDoubleLowEngine._score(
        bar,
        StrategyLabRunConfig("double-low", "cn", "convertible_bond", date(2024, 1, 2), date(2024, 1, 2), 1000, parameters={"score_mode": "weighted_double_low", "price_weight": 0.3, "premium_weight": 0.7}),
        None,
    ) == 47.0


def test_low_premium_strategy_alias_defaults_to_premium_score() -> None:
    result = FixtureDoubleLowEngine().run(
        StrategyLabRunConfig(
            strategy_id="low-premium",
            market="cn",
            instrument_type="convertible_bond",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            initial_cash=100000,
            parameters={"max_positions": 1},
        )
    )
    assert result.metrics.diagnostics["score_mode"] == "low_premium"


def test_moving_average_engine_migrates_golden_and_death_cross_rules() -> None:
    instrument = StrategyLabInstrument(
        "cn.convertible_bond.123001", "123001", "cn", "convertible_bond", "测试转债"
    )
    closes = [10.0, 9.0, 8.0, 10.0, 12.0, 8.0, 6.0]
    bars = [StrategyLabBar(date(2024, 1, 2 + index), close, 10.0) for index, close in enumerate(closes)]
    engine = MovingAverageCrossoverEngine(
        StrategyLabDataSet([instrument], {instrument.canonical_id: bars})
    )

    result = engine.run(
        StrategyLabRunConfig(
            strategy_id="ma-crossover",
            market="cn",
            instrument_type="convertible_bond",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 8),
            initial_cash=100000,
            parameters={"fast_period": 2, "slow_period": 3, "position_pct": 0.95},
        )
    )

    assert [trade.reason for trade in result.trades] == ["golden_cross", "death_cross"]
    assert result.metrics.diagnostics["fast_period"] == 2
    assert result.final_equity > 0
