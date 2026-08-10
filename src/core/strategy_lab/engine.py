# -*- coding: utf-8 -*-
"""Engine interface for Strategy Lab."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from src.core.strategy_lab.models import StrategyLabRunConfig, StrategyLabRunResult


class StrategyLabEngine(ABC):
    """Execute one Strategy Lab run."""

    name = "abstract"

    @abstractmethod
    def run(self, config: StrategyLabRunConfig) -> StrategyLabRunResult:
        """Execute a strategy and return a structured result."""


def list_builtin_strategies() -> List[dict]:
    """Return strategies exposed by the Strategy Lab backend."""

    return [
        {
            "strategy_id": "double-low",
            "name": "Double Low Rotation",
            "instrument_types": ["convertible_bond"],
            "markets": ["cn"],
            "description": "Select instruments by close price plus convertible-bond premium rate.",
        },
        {
            "strategy_id": "low-premium",
            "name": "Low Premium Rotation",
            "instrument_types": ["convertible_bond"],
            "markets": ["cn"],
            "description": "Select convertible bonds by the lowest conversion premium rate.",
        },
        {
            "strategy_id": "ma-crossover",
            "name": "Moving Average Crossover",
            "instrument_types": ["convertible_bond"],
            "markets": ["cn"],
            "description": "Buy on a fast/slow moving-average golden cross and exit on a death cross or event block.",
        },
    ]
