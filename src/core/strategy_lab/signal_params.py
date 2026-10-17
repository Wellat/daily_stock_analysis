# -*- coding: utf-8 -*-
"""Parameter contract for the signal backtest engine.

参数以平铺 dict 进入（API ``parameters`` 字段），归一化为
:class:`SignalParams`。规则双轨（沿用 ``score_preset`` vs ``score_factors``
先例）：显式 ``entry_rules`` / ``exit_rules`` 列表优先；缺失时由扁平标量
（``entry_gap_max`` / ``dividend_yield_min`` / ``exit_gap_min``）构建默认
规则——扁平键同时是批量参数网格的寻优轴。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.core.strategy_lab.factors import COMPARATORS
from src.core.strategy_lab.series_factors import get_series_factor


@dataclass(frozen=True)
class SignalRule:
    """One entry/exit condition: ``factor_value op threshold``."""

    factor: str
    op: str
    value: float

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SignalRule":
        factor = str(data.get("factor", "")).strip()
        if not factor:
            raise ValueError("signal rule entry requires 'factor'")
        get_series_factor(factor)  # 未知因子 fail-fast
        op = str(data.get("op", "")).strip()
        if op not in COMPARATORS:
            raise ValueError(f"Unsupported comparator: {op}")
        try:
            value = float(data.get("value"))
        except (TypeError, ValueError) as exc:
            raise ValueError("signal rule value must be a number") from exc
        if not math.isfinite(value):
            raise ValueError("signal rule value must be finite")
        return cls(factor=factor, op=op, value=value)

    def to_dict(self) -> Dict[str, Any]:
        return {"factor": self.factor, "op": self.op, "value": self.value}


@dataclass
class SignalParams:
    """Normalized signal backtest parameters."""

    ma_window: int = 182
    roc_window: int = 20
    entry_rules: List[SignalRule] = field(default_factory=list)
    exit_rules: List[SignalRule] = field(default_factory=list)
    max_position_pct: float = 100.0
    commission: float = 0.0002
    lot_size: int = 100

    @classmethod
    def from_parameters(cls, parameters: Dict[str, Any]) -> "SignalParams":
        parameters = parameters or {}

        def _int(key: str, default: int, *, minimum: int) -> int:
            try:
                value = int(parameters.get(key, default))
            except (TypeError, ValueError):
                raise ValueError(f"parameter {key} must be an integer")
            if value < minimum:
                raise ValueError(f"parameter {key} must be >= {minimum}")
            return value

        def _float(key: str, default: float, *, minimum: float = 0.0, maximum: float = 100.0) -> float:
            try:
                value = float(parameters.get(key, default))
            except (TypeError, ValueError):
                raise ValueError(f"parameter {key} must be a number")
            if not math.isfinite(value) or value < minimum or value > maximum:
                raise ValueError(f"parameter {key} must be in [{minimum}, {maximum}]")
            return value

        ma_window = _int("ma_window", 182, minimum=2)
        params = cls(
            ma_window=ma_window,
            roc_window=_int("roc_window", 20, minimum=1),
            entry_rules=cls._resolve_rules(
                parameters, "entry_rules", cls._default_entry_rules(parameters)
            ),
            exit_rules=cls._resolve_rules(
                parameters, "exit_rules", [SignalRule("ma_gap_pct", ">", _float("exit_gap_min", 7.0, minimum=-100.0))]
            ),
            max_position_pct=_float("max_position_pct", 100.0),
            commission=_float("commission", 0.0002, minimum=0.0, maximum=0.01),
            lot_size=_int("lot_size", 100, minimum=1),
        )
        if not params.entry_rules:
            raise ValueError("entry_rules must not be empty")
        if not params.exit_rules:
            raise ValueError("exit_rules must not be empty")
        return params

    @staticmethod
    def _default_entry_rules(parameters: Dict[str, Any]) -> List[SignalRule]:
        """扁平标量 → 默认买入规则：乖离率低于阈值，且（可选）股息率高于阈值。"""
        rules = [SignalRule(
            "ma_gap_pct", "<", SignalParams._scalar(parameters, "entry_gap_max", 0.0, minimum=-100.0)
        )]
        dividend_min = SignalParams._scalar(parameters, "dividend_yield_min", None, minimum=-100.0)
        if dividend_min is not None:
            rules.append(SignalRule("dividend_yield", ">", dividend_min))
        return rules

    @staticmethod
    def _scalar(parameters: Dict[str, Any], key: str, default: Any, *, minimum: float = -1e9) -> Any:
        raw = parameters.get(key)
        if raw is None or raw == "":
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"parameter {key} must be a number")
        if not math.isfinite(value) or value < minimum:
            raise ValueError(f"parameter {key} must be >= {minimum}")
        return value

    @staticmethod
    def _resolve_rules(
        parameters: Dict[str, Any], key: str, defaults: List[SignalRule]
    ) -> List[SignalRule]:
        raw = parameters.get(key)
        if not raw:
            return defaults
        if not isinstance(raw, (list, tuple)):
            raise ValueError(f"{key} must be a list")
        return [SignalRule.from_dict(item) for item in raw]

    def window_params(self) -> Dict[str, int]:
        """时序因子计算所需的窗口标量（键名与因子 window_params 对齐）。"""
        return {"ma_window": self.ma_window, "roc_window": self.roc_window}

    def summary(self) -> Dict[str, Any]:
        return {
            "ma_window": self.ma_window,
            "roc_window": self.roc_window,
            "entry_rules": [rule.to_dict() for rule in self.entry_rules],
            "exit_rules": [rule.to_dict() for rule in self.exit_rules],
            "max_position_pct": self.max_position_pct,
            "commission": self.commission,
            "lot_size": self.lot_size,
        }
