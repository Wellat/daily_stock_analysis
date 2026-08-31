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
    # The new strategy registry is the source of truth for migrated strategies;
    # retain legacy entries until they are migrated.
    from src.core.strategies.registry import list_builtin_strategies as registry_list
    migrated = {item["strategy_id"]: item for item in registry_list()}
    legacy = [
        {
            "strategy_id": "double-low",
            "name": "Double Low Rotation",
            "instrument_types": ["convertible_bond"],
            "markets": ["cn"],
            "description": "Select instruments by close price plus convertible-bond premium rate.",
            "parameters": [
                {"key": "max_positions", "label": "最大持仓数", "type": "integer", "default": 2, "min": 1, "max": 50},
                {"key": "per_position_cash", "label": "单债目标资金", "type": "number", "default": 10000, "min": 100},
                {"key": "lot_size", "label": "最小交易单位", "type": "integer", "default": 10, "min": 1},
                {"key": "max_abs_premium", "label": "最大溢价率", "type": "number", "default": 200, "min": 0},
                {"key": "exclude_event_blocked", "label": "排除风险事件", "type": "boolean", "default": True},
            ],
        },
        {
            "strategy_id": "low-premium",
            "name": "Low Premium Rotation",
            "instrument_types": ["convertible_bond"],
            "markets": ["cn"],
            "description": "Select convertible bonds by the lowest conversion premium rate.",
            "parameters": [
                {"key": "max_positions", "label": "最大持仓数", "type": "integer", "default": 2, "min": 1, "max": 50},
                {"key": "per_position_cash", "label": "单债目标资金", "type": "number", "default": 10000, "min": 100},
                {"key": "lot_size", "label": "最小交易单位", "type": "integer", "default": 10, "min": 1},
                {"key": "max_abs_premium", "label": "最大溢价率", "type": "number", "default": 200, "min": 0},
                {"key": "exclude_event_blocked", "label": "排除风险事件", "type": "boolean", "default": True},
            ],
        },
        {
            "strategy_id": "ma-crossover",
            "name": "Moving Average Crossover",
            "instrument_types": ["convertible_bond"],
            "markets": ["cn"],
            "description": "Buy on a fast/slow moving-average golden cross and exit on a death cross or event block.",
            "parameters": [
                {"key": "fast_period", "label": "快速均线周期", "type": "integer", "default": 5, "min": 1},
                {"key": "slow_period", "label": "慢速均线周期", "type": "integer", "default": 20, "min": 2},
                {"key": "premium_rate_threshold", "label": "溢价率上限", "type": "number", "default": 30, "min": 0},
                {"key": "position_pct", "label": "仓位比例", "type": "number", "default": 0.95, "min": 0, "max": 1},
                {"key": "lot_size", "label": "最小交易单位", "type": "integer", "default": 10, "min": 1},
            ],
        },
    ]
    result = []
    for item in legacy:
        result.append(migrated.get(item["strategy_id"], item))
    for key, item in migrated.items():
        if key not in {x["strategy_id"] for x in legacy}: result.append(item)
    return result
