# -*- coding: utf-8 -*-
"""Generic cross-sectional factor framework for Strategy Lab backtests.

框架与品种无关：因子只是「横截面快照 dict 的某列 + 可选横截面变换」。
新品种/新因子 = 向 :data:`FACTOR_REGISTRY` 注册一条 :class:`FactorSpec`，
引擎与前端不需要任何改动（前端因子下拉由 ``list_factor_metadata`` 驱动）。

横截面约定：
- 每行是一个 ``dict``，必须含 ``symbol`` 键，其余键为因子数据列；
- 因子取值用原始列值（排除规则比较原始值）；
- 打分时按 ``transform`` 做横截面变换（percentile/zscore/minmax），
  再按 ``direction`` 与 ``weight`` 加权合成，**分数越小越优先**。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

TRANSFORM_RAW = "raw"
TRANSFORM_PERCENTILE = "percentile"
TRANSFORM_ZSCORE = "zscore"
TRANSFORM_MINMAX = "minmax"

MISSING_SKIP = "skip"
MISSING_NEUTRAL = "neutral"

DIRECTION_ASC = "asc"   # 数值越小越优
DIRECTION_DESC = "desc"  # 数值越大越优

_COMPARATORS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


@dataclass(frozen=True)
class FactorSpec:
    """One factor definition in the registry.

    简单因子从单列取值（``column``）；组合因子声明 ``columns`` 数据依赖并
    由 ``compute`` 从整行派生取值（如双低 = 价格 + 溢价率×100%）。
    """

    factor_id: str
    label: str
    column: Optional[str] = None
    transform: str = TRANSFORM_RAW
    default_direction: str = DIRECTION_ASC
    description: str = ""
    unit: Optional[str] = None
    columns: Tuple[str, ...] = ()
    compute: Optional[Callable[[Mapping[str, Any]], Optional[float]]] = None

    @property
    def is_composite(self) -> bool:
        return self.compute is not None

    def data_columns(self) -> Tuple[str, ...]:
        if self.columns:
            return self.columns
        return (self.column,) if self.column else ()

    def metadata(self) -> Dict[str, Any]:
        return {
            "factor": self.factor_id,
            "label": self.label,
            "kind": "composite" if self.is_composite else "simple",
            "column": self.column,
            "columns": list(self.data_columns()),
            "transform": self.transform,
            "default_direction": self.default_direction,
            "description": self.description,
            "unit": self.unit,
        }


# 内置因子以可转债数据列为基础；框架本身不感知品种语义。
FACTOR_REGISTRY: Dict[str, FactorSpec] = {}


def register_factor(spec: FactorSpec) -> FactorSpec:
    if spec.transform not in (TRANSFORM_RAW, TRANSFORM_PERCENTILE, TRANSFORM_ZSCORE, TRANSFORM_MINMAX):
        raise ValueError(f"Unsupported transform: {spec.transform}")
    if spec.default_direction not in (DIRECTION_ASC, DIRECTION_DESC):
        raise ValueError(f"Unsupported direction: {spec.default_direction}")
    if spec.is_composite:
        if not spec.columns:
            raise ValueError(f"Composite factor requires 'columns': {spec.factor_id}")
    elif not spec.column:
        raise ValueError(f"Simple factor requires 'column': {spec.factor_id}")
    existing = FACTOR_REGISTRY.get(spec.factor_id)
    if existing is not None and existing != spec:
        raise ValueError(f"Factor already registered: {spec.factor_id}")
    FACTOR_REGISTRY[spec.factor_id] = spec
    return spec


def get_factor(factor_id: str) -> FactorSpec:
    spec = FACTOR_REGISTRY.get(factor_id)
    if spec is None:
        raise ValueError(f"Unknown factor: {factor_id}")
    return spec


def list_factor_metadata() -> List[Dict[str, Any]]:
    return [spec.metadata() for spec in FACTOR_REGISTRY.values()]


# ---------------------------------------------------------------------------
# 横截面变换
# ---------------------------------------------------------------------------

def _finite(value: Optional[float]) -> bool:
    return value is not None and isinstance(value, (int, float)) and math.isfinite(float(value))


def percentile_ranks(values: Sequence[Optional[float]]) -> List[Optional[float]]:
    """横截面百分位排名（0-100，并列取平均名次），缺失保持 None。

    与旧 ``fixture_engine._percentile_ranks`` 语义一致，泛化为可缺失输入。
    """
    length = len(values)
    ranks: List[Optional[float]] = [None] * length
    indexed = sorted(
        ((float(v), i) for i, v in enumerate(values) if _finite(v)),
        key=lambda pair: pair[0],
    )
    if not indexed:
        return ranks
    denominator = max(1, len(indexed) - 1)
    cursor = 0
    while cursor < len(indexed):
        end = cursor
        while end + 1 < len(indexed) and indexed[end + 1][0] == indexed[cursor][0]:
            end += 1
        rank = ((cursor + end) / 2) / denominator * 100
        for _, original_index in indexed[cursor:end + 1]:
            ranks[original_index] = rank
        cursor = end + 1
    return ranks


def zscore_values(values: Sequence[Optional[float]]) -> List[Optional[float]]:
    finite = [float(v) for v in values if _finite(v)]
    ranks: List[Optional[float]] = [None] * len(values)
    if len(finite) < 2:
        return ranks
    mean = sum(finite) / len(finite)
    variance = sum((v - mean) ** 2 for v in finite) / len(finite)
    std = math.sqrt(variance)
    if std == 0:
        return [0.0 if _finite(v) else None for v in values]
    for i, v in enumerate(values):
        if _finite(v):
            ranks[i] = (float(v) - mean) / std
    return ranks


def minmax_values(values: Sequence[Optional[float]]) -> List[Optional[float]]:
    finite = [float(v) for v in values if _finite(v)]
    ranks: List[Optional[float]] = [None] * len(values)
    if not finite:
        return ranks
    low, high = min(finite), max(finite)
    span = high - low
    for i, v in enumerate(values):
        if _finite(v):
            ranks[i] = 0.0 if span == 0 else (float(v) - low) / span
    return ranks


_TRANSFORM_FUNCS = {
    TRANSFORM_RAW: lambda values: [float(v) if _finite(v) else None for v in values],
    TRANSFORM_PERCENTILE: percentile_ranks,
    TRANSFORM_ZSCORE: zscore_values,
    TRANSFORM_MINMAX: minmax_values,
}


def transform_values(values: Sequence[Optional[float]], transform: str) -> List[Optional[float]]:
    func = _TRANSFORM_FUNCS.get(transform)
    if func is None:
        raise ValueError(f"Unsupported transform: {transform}")
    return func(values)


# ---------------------------------------------------------------------------
# 打分合成
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScoreWeight:
    """One weighted factor in a composite score."""

    factor: str
    direction: str = DIRECTION_ASC
    weight: float = 1.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScoreWeight":
        factor = str(data.get("factor", "")).strip()
        if not factor:
            raise ValueError("score factor entry requires 'factor'")
        direction = str(data.get("direction") or get_factor(factor).default_direction)
        if direction not in (DIRECTION_ASC, DIRECTION_DESC):
            raise ValueError(f"Unsupported direction: {direction}")
        weight = float(data.get("weight", 1.0))
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"Factor weight must be a non-negative number: {weight}")
        return cls(factor=factor, direction=direction, weight=weight)

    def to_dict(self) -> Dict[str, Any]:
        return {"factor": self.factor, "direction": self.direction, "weight": self.weight}


@dataclass(frozen=True)
class ExclusionRule:
    """One exclusion rule: drop the instrument when ``raw_value op value`` holds."""

    factor: str
    op: str
    value: float

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExclusionRule":
        factor = str(data.get("factor", "")).strip()
        if not factor:
            raise ValueError("exclusion rule entry requires 'factor'")
        op = str(data.get("op", "")).strip()
        if op not in _COMPARATORS:
            raise ValueError(f"Unsupported comparator: {op}")
        value = float(data.get("value"))
        if not math.isfinite(value):
            raise ValueError("exclusion rule value must be finite")
        return cls(factor=factor, op=op, value=value)

    def to_dict(self) -> Dict[str, Any]:
        return {"factor": self.factor, "op": self.op, "value": self.value}


def extract_value(row: Mapping[str, Any], spec: FactorSpec) -> Optional[float]:
    """从横截面行取因子的原始值：组合因子走 compute，简单因子读单列。"""
    if spec.is_composite:
        value = spec.compute(row) if spec.compute is not None else None
        return float(value) if _finite(value) else None
    value = row.get(spec.column) if spec.column else None
    return float(value) if _finite(value) else None


def raw_value(row: Dict[str, Any], factor_id: str) -> Optional[float]:
    """Extract the raw (pre-transform) factor value from a cross-section row."""
    return extract_value(row, get_factor(factor_id))


def passes_exclusions(row: Dict[str, Any], rules: Iterable[ExclusionRule]) -> bool:
    """True 当该行通过所有排除规则；规则命中的因子缺失时视为不通过。"""
    for rule in rules:
        value = raw_value(row, rule.factor)
        if value is None:
            return False
        if _COMPARATORS[rule.op](value, rule.value):
            return False
    return True


def compose_scores(
    cross_section: Sequence[Dict[str, Any]],
    weights: Sequence[ScoreWeight],
    *,
    missing_policy: str = MISSING_SKIP,
) -> Dict[str, Any]:
    """加权合成横截面打分，返回 ``{symbol: score}``（分数越小越优先）。

    ``missing_policy``:
    - ``skip``: 任一因子缺失的标的整行跳过（不出现在结果里）；
    - ``neutral``: 缺失因子按 0 计（对 asc/desc 对称中性）。
    """
    if missing_policy not in (MISSING_SKIP, MISSING_NEUTRAL):
        raise ValueError(f"Unsupported missing policy: {missing_policy}")
    if not weights:
        raise ValueError("score_factors must not be empty")
    if not cross_section:
        return {}

    columns: Dict[str, List[Optional[float]]] = {}
    for weight in weights:
        spec = get_factor(weight.factor)
        columns[weight.factor] = [extract_value(row, spec) for row in cross_section]
    transformed: Dict[str, List[Optional[float]]] = {
        factor_id: transform_values(values, get_factor(factor_id).transform)
        for factor_id, values in columns.items()
    }

    scores: Dict[str, Any] = {}
    for index, row in enumerate(cross_section):
        total = 0.0
        usable = True
        for weight in weights:
            value = transformed[weight.factor][index]
            if value is None:
                if missing_policy == MISSING_SKIP:
                    usable = False
                    break
                value = 0.0
            signed = value if weight.direction == DIRECTION_ASC else -value
            total += signed * weight.weight
        if usable:
            scores[str(row.get("symbol"))] = total
    return scores


# ---------------------------------------------------------------------------
# 预设（预设 = 命名的因子权重配置，不再有独立代码分支）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScorePreset:
    preset_id: str
    label: str
    weights: List[ScoreWeight] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "preset": self.preset_id,
            "label": self.label,
            "factors": [weight.to_dict() for weight in self.weights],
        }


def _builtin_presets() -> List[ScorePreset]:
    return [
        ScorePreset(
            "double_low",
            "双低",
            [ScoreWeight("price"), ScoreWeight("premium_rate")],
        ),
        ScorePreset(
            "low_premium",
            "低溢价",
            [ScoreWeight("premium_rate")],
        ),
        ScorePreset(
            "weighted_double_low",
            "加权双低",
            [ScoreWeight("price", weight=1.0), ScoreWeight("premium_rate", weight=1.0)],
        ),
        ScorePreset(
            "triple_low",
            "三低",
            [ScoreWeight("price"), ScoreWeight("premium_rate"), ScoreWeight("remaining_size_percentile")],
        ),
    ]


SCORE_PRESETS: Dict[str, ScorePreset] = {p.preset_id: p for p in _builtin_presets()}


def resolve_score_weights(parameters: Dict[str, Any]) -> List[ScoreWeight]:
    """从策略参数解析打分权重：``score_preset`` 或显式 ``score_factors``。"""
    factors = parameters.get("score_factors")
    if factors:
        if not isinstance(factors, (list, tuple)) or not factors:
            raise ValueError("score_factors must be a non-empty list")
        return [ScoreWeight.from_dict(item) for item in factors]
    preset_id = str(parameters.get("score_preset") or "double_low")
    preset = SCORE_PRESETS.get(preset_id)
    if preset is None:
        raise ValueError(f"Unknown score preset: {preset_id}")
    return list(preset.weights)


def resolve_exclusion_rules(parameters: Dict[str, Any]) -> List[ExclusionRule]:
    """从策略参数解析排除因子表（禄得式 factor/op/value）。"""
    factors = parameters.get("exclusion_factors")
    if not factors:
        return []
    if not isinstance(factors, (list, tuple)):
        raise ValueError("exclusion_factors must be a list")
    return [ExclusionRule.from_dict(item) for item in factors]


def _register_builtin_factors() -> None:
    def _double_low(row: Mapping[str, Any]) -> Optional[float]:
        # 溢价率按百分数落库（18.5 表示 18.5%），故 价格+溢价率 即经典双低，
        # 等价于 价格 + 溢价率(小数)×100
        close, premium = row.get("close"), row.get("premium_rate")
        if not _finite(close) or not _finite(premium):
            return None
        return float(close) + float(premium)

    register_factor(FactorSpec(
        factor_id="price",
        label="转债价格",
        column="close",
        default_direction=DIRECTION_ASC,
        description="转债收盘价",
        unit="元",
    ))
    register_factor(FactorSpec(
        factor_id="premium_rate",
        label="转股溢价率",
        column="premium_rate",
        default_direction=DIRECTION_ASC,
        description="转股溢价率（百分数）",
        unit="%",
    ))
    register_factor(FactorSpec(
        factor_id="double_low",
        label="双低",
        columns=("close", "premium_rate"),
        compute=_double_low,
        default_direction=DIRECTION_ASC,
        description="转债价格 + 转股溢价率×100%（价格+溢价率，经典双低值）",
        unit="",
    ))
    register_factor(FactorSpec(
        factor_id="remaining_size",
        label="剩余规模",
        column="remaining_size",
        default_direction=DIRECTION_ASC,
        description="剩余未转股规模（亿元）",
        unit="亿元",
    ))
    register_factor(FactorSpec(
        factor_id="remaining_size_percentile",
        label="剩余规模百分位",
        column="remaining_size",
        transform=TRANSFORM_PERCENTILE,
        default_direction=DIRECTION_ASC,
        description="剩余规模的横截面百分位排名（0-100）",
        unit="%",
    ))
    register_factor(FactorSpec(
        factor_id="price_percentile",
        label="价格百分位",
        column="close",
        transform=TRANSFORM_PERCENTILE,
        default_direction=DIRECTION_ASC,
        description="收盘价的横截面百分位排名（0-100）",
        unit="%",
    ))
    register_factor(FactorSpec(
        factor_id="premium_rate_percentile",
        label="溢价率百分位",
        column="premium_rate",
        transform=TRANSFORM_PERCENTILE,
        default_direction=DIRECTION_ASC,
        description="转股溢价率的横截面百分位排名（0-100）",
        unit="%",
    ))


_register_builtin_factors()
