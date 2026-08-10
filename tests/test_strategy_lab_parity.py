# -*- coding: utf-8 -*-
"""Cross-project parity contract for the migrated double-low score semantics."""

from __future__ import annotations

from datetime import date

from src.core.strategy_lab.fixture_engine import FixtureDoubleLowEngine
from src.core.strategy_lab.models import StrategyLabBar, StrategyLabDataSet, StrategyLabInstrument, StrategyLabRunConfig


def test_same_normalized_snapshot_keeps_legacy_double_low_selection_and_accounting() -> None:
    """The shared snapshot fixes the comparison inputs and expected legacy rule output."""
    instruments = [
        StrategyLabInstrument(f"cn.convertible_bond.{code}", code, "cn", "convertible_bond", code)
        for code in ("123001", "123002", "123003")
    ]
    bars = {
        instrument.canonical_id: [StrategyLabBar(date(2024, 1, 2), close, premium)]
        for instrument, close, premium in zip(
            instruments, (102.0, 96.0, 118.0), (18.0, 22.0, 9.0), strict=True
        )
    }
    result = FixtureDoubleLowEngine(StrategyLabDataSet(instruments, bars)).run(
        StrategyLabRunConfig(
            strategy_id="double-low",
            market="cn",
            instrument_type="convertible_bond",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 2),
            initial_cash=100000,
            parameters={"max_positions": 2, "commission": 0.0},
        )
    )

    # Legacy double-low = close + premium, lower is better. This deterministic
    # snapshot is the Phase 6 cross-project comparison input.
    assert result.metrics.diagnostics["score"] == {"123002": 118.0, "123001": 120.0}
    assert result.metrics.diagnostics["selected_symbols"] == ["123002", "123001"]
    assert result.final_equity == 100000.0
