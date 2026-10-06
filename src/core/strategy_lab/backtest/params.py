# -*- coding: utf-8 -*-
"""Parameter contract for the rotation backtest engine.

参数以平铺 dict 进入（API ``parameters`` 字段），此处归一化为
:class:`RotationParams`；旧键（``max_positions`` / ``min_remaining_size`` /
``max_abs_premium``）做兼容映射，等价表达为排除规则，避免双轨语义。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

from src.core.strategy_lab.factors import (
    DIRECTION_ASC,
    ExclusionRule,
    ScoreWeight,
    resolve_exclusion_rules,
    resolve_score_weights,
)

REBALANCE_UNITS = ("trading_day", "week", "month")
BENCHMARK_INDEX_ALIASES = {"000300", "hs300", "csi300", "sh000300", "1.000300"}
BENCHMARK_EQUAL_WEIGHT = "equal_weight_pool"


@dataclass
class RotationParams:
    """Normalized rotation backtest parameters."""

    rebalance_unit: str = "trading_day"
    rebalance_interval: int = 1
    min_positions: int = 1
    max_positions: int = 5
    weight_mode: str = "equal_cash"
    max_position_pct: float = 100.0
    score_weights: List[ScoreWeight] = field(default_factory=list)
    exclusion_rules: List[ExclusionRule] = field(default_factory=list)
    score_missing: str = "skip"
    exclude_new_bond_days: int = 0
    exclude_last_trading_days: int = 0
    excluded_symbols: Set[str] = field(default_factory=set)
    exclude_event_blocked: bool = True
    rebalance_weights: bool = False
    commission: float = 0.0002
    lot_size: int = 10

    @classmethod
    def from_parameters(cls, parameters: Dict[str, Any]) -> "RotationParams":
        parameters = parameters or {}

        def _int(key: str, default: int, *, minimum: int = 0) -> int:
            try:
                value = int(parameters.get(key, default))
            except (TypeError, ValueError):
                raise ValueError(f"parameter {key} must be an integer")
            return max(minimum, value)

        def _float(key: str, default: float, *, minimum: float = 0.0) -> float:
            try:
                value = float(parameters.get(key, default))
            except (TypeError, ValueError):
                raise ValueError(f"parameter {key} must be a number")
            if not math.isfinite(value) or value < minimum:
                raise ValueError(f"parameter {key} must be >= {minimum}")
            return value

        rebalance_unit = str(parameters.get("rebalance_unit") or "trading_day")
        if rebalance_unit not in REBALANCE_UNITS:
            raise ValueError(f"Unsupported rebalance_unit: {rebalance_unit}")

        score_missing = str(parameters.get("score_missing") or "skip")
        if score_missing not in ("skip", "neutral"):
            raise ValueError(f"Unsupported score_missing: {score_missing}")

        params = cls(
            rebalance_unit=rebalance_unit,
            rebalance_interval=max(1, _int("rebalance_interval", 1, minimum=1)),
            min_positions=max(1, _int("min_positions", 1, minimum=1)),
            max_positions=max(1, _int("max_positions", 5, minimum=1)),
            weight_mode=str(parameters.get("weight_mode") or "equal_cash"),
            max_position_pct=min(100.0, _float("max_position_pct", 100.0)),
            score_weights=resolve_score_weights(parameters),
            exclusion_rules=resolve_exclusion_rules(parameters),
            score_missing=score_missing,
            exclude_new_bond_days=_int("exclude_new_bond_days", 0),
            exclude_last_trading_days=_int("exclude_last_trading_days", 0),
            excluded_symbols={
                str(symbol).strip().lower().split(".")[-1]
                for symbol in (parameters.get("excluded_symbols") or [])
                if str(symbol).strip()
            },
            exclude_event_blocked=bool(parameters.get("exclude_event_blocked", True)),
            rebalance_weights=bool(parameters.get("rebalance_weights", False)),
            commission=_float("commission", 0.0002),
            lot_size=max(1, _int("lot_size", 10, minimum=1)),
        )
        if params.min_positions > params.max_positions:
            raise ValueError("min_positions cannot exceed max_positions")
        if params.weight_mode != "equal_cash":
            raise ValueError(f"Unsupported weight_mode: {params.weight_mode}")
        params._apply_legacy_filters(parameters)
        return params

    def _apply_legacy_filters(self, parameters: Dict[str, Any]) -> None:
        """把旧排除参数（min_remaining_size / max_abs_premium）映射为排除规则。"""
        try:
            min_remaining_size = float(parameters.get("min_remaining_size", 0.0) or 0.0)
            max_abs_premium = float(parameters.get("max_abs_premium", 200.0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid legacy filter parameter: {exc}")
        if min_remaining_size > 0:
            self.exclusion_rules.append(
                ExclusionRule(factor="remaining_size", op="<", value=min_remaining_size)
            )
        if math.isfinite(max_abs_premium) and max_abs_premium < float("inf"):
            self.exclusion_rules.append(
                ExclusionRule(factor="premium_rate", op=">", value=max_abs_premium)
            )
            self.exclusion_rules.append(
                ExclusionRule(factor="premium_rate", op="<", value=-max_abs_premium)
            )

    def summary(self) -> Dict[str, Any]:
        return {
            "rebalance_unit": self.rebalance_unit,
            "rebalance_interval": self.rebalance_interval,
            "min_positions": self.min_positions,
            "max_positions": self.max_positions,
            "weight_mode": self.weight_mode,
            "max_position_pct": self.max_position_pct,
            "score_factors": [w.to_dict() for w in self.score_weights],
            "exclusion_factors": [r.to_dict() for r in self.exclusion_rules],
            "score_missing": self.score_missing,
            "exclude_new_bond_days": self.exclude_new_bond_days,
            "exclude_last_trading_days": self.exclude_last_trading_days,
            "excluded_symbols": sorted(self.excluded_symbols),
            "exclude_event_blocked": self.exclude_event_blocked,
            "rebalance_weights": self.rebalance_weights,
            "commission": self.commission,
            "lot_size": self.lot_size,
        }
