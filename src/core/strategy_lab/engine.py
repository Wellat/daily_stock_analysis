# -*- coding: utf-8 -*-
"""Engine interface for Strategy Lab."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from src.core.strategy_lab.models import StrategyLabRunConfig, StrategyLabRunResult


class StrategyLabEngine(ABC):
    """Execute one Strategy Lab run."""

    name = "abstract"

    @abstractmethod
    def run(self, config: StrategyLabRunConfig) -> StrategyLabRunResult:
        """Execute a strategy and return a structured result."""


def _rotation_strategy_entry() -> Dict[str, Any]:
    """backtrader 轮动策略的注册条目（含参数与因子元数据，供前端动态渲染）。"""
    from src.core.strategy_lab import factors as F

    parameters = [
        {"key": "rebalance_unit", "label": "换仓频率类型", "type": "enum", "default": "trading_day", "options": ["trading_day", "week", "month"]},
        {"key": "rebalance_interval", "label": "换仓频率", "type": "integer", "default": 1, "min": 1},
        {"key": "min_positions", "label": "最小持有数量", "type": "integer", "default": 1, "min": 1},
        {"key": "max_positions", "label": "最大持有数量", "type": "integer", "default": 5, "min": 1, "max": 50},
        {"key": "weight_mode", "label": "持有权重", "type": "enum", "default": "equal_cash", "options": ["equal_cash"]},
        {"key": "max_position_pct", "label": "单标的最大仓位(%)", "type": "number", "default": 100, "min": 1, "max": 100},
        {"key": "score_preset", "label": "打分预设", "type": "enum", "default": "double_low", "options": sorted(F.SCORE_PRESETS.keys())},
        {"key": "score_missing", "label": "因子缺失处理", "type": "enum", "default": "skip", "options": ["skip", "neutral"]},
        {"key": "exclude_new_bond_days", "label": "排除新债天数", "type": "integer", "default": 0, "min": 0},
        {"key": "exclude_last_trading_days", "label": "排除临近到期天数", "type": "integer", "default": 0, "min": 0},
        {"key": "exclude_event_blocked", "label": "排除风险事件", "type": "boolean", "default": True},
        {"key": "rebalance_weights", "label": "换仓日再平衡", "type": "boolean", "default": False},
        {"key": "commission", "label": "单边手续费率", "type": "number", "default": 0.0002, "min": 0},
        {"key": "lot_size", "label": "最小交易单位(张)", "type": "integer", "default": 10, "min": 1},
    ]
    return {
        "strategy_id": "rotation",
        "name": "Convertible Bond Rotation",
        "instrument_types": ["convertible_bond"],
        "markets": ["cn"],
        "description": "Periodic convertible-bond rotation with cross-sectional factor scoring (backtrader).",
        "parameters": parameters,
        "factors": [spec.metadata() for spec in F.FACTOR_REGISTRY.values()],
        "score_presets": [preset.to_dict() for preset in F.SCORE_PRESETS.values()],
    }


def _signal_strategy_entry() -> Dict[str, Any]:
    """ETF 信号策略的注册条目（时序进出场规则，含因子元数据供前端动态渲染）。"""
    from src.core.strategy_lab import series_factors as SF

    parameters = [
        {"key": "ma_window", "label": "均线窗口(日)", "type": "integer", "default": 182, "min": 2},
        {"key": "roc_window", "label": "涨跌幅窗口(日)", "type": "integer", "default": 20, "min": 1},
        {"key": "entry_gap_max", "label": "买入乖离率上限(%)", "type": "number", "default": 0, "min": -100},
        {"key": "dividend_yield_min", "label": "买入股息率下限(%)", "type": "number", "default": 0, "min": 0},
        {"key": "exit_gap_min", "label": "卖出乖离率下限(%)", "type": "number", "default": 7, "min": -100},
        {"key": "max_position_pct", "label": "单标的最大仓位(%)", "type": "number", "default": 100, "min": 1, "max": 100},
        {"key": "commission", "label": "单边手续费率", "type": "number", "default": 0.0002, "min": 0},
        {"key": "lot_size", "label": "最小交易单位(股)", "type": "integer", "default": 100, "min": 1},
    ]
    return {
        "strategy_id": "signal",
        "name": "ETF Signal",
        "instrument_types": ["etf"],
        "markets": ["cn"],
        "description": "Per-symbol time-series entry/exit rules on daily bars (e.g. MA deviation with optional dividend-yield filter).",
        "parameters": parameters,
        "factors": SF.list_series_factor_metadata(),
    }


def _signal_macd_strategy_entry() -> Dict[str, Any]:
    """ETF 信号·MACD 策略注册条目（同一 signal_v1 引擎，默认金叉买入/死叉卖出）。"""
    from src.core.strategy_lab import series_factors as SF

    parameters = [
        {"key": "macd_fast", "label": "MACD 快线EMA(日)", "type": "integer", "default": 12, "min": 2},
        {"key": "macd_slow", "label": "MACD 慢线EMA(日)", "type": "integer", "default": 26, "min": 3},
        {"key": "macd_signal", "label": "MACD 信号线EMA(日)", "type": "integer", "default": 9, "min": 2},
        {"key": "max_position_pct", "label": "单标的最大仓位(%)", "type": "number", "default": 100, "min": 1, "max": 100},
        {"key": "commission", "label": "单边手续费率", "type": "number", "default": 0.0002, "min": 0},
        {"key": "lot_size", "label": "最小交易单位(股)", "type": "integer", "default": 100, "min": 1},
    ]
    macd_only = [
        meta for meta in SF.list_series_factor_metadata()
        if meta["factor"].startswith("macd_") or meta["factor"] == "close"
    ]
    return {
        "strategy_id": "signal_macd",
        "name": "ETF Signal · MACD",
        "instrument_types": ["etf"],
        "markets": ["cn"],
        "description": "MACD 金叉买入 / 死叉卖出（DIF 上穿/下穿 DEA，收盘确认与成交）；"
                       "规则可组合零轴过滤（macd_dif）与柱状线（macd_hist）等因子。",
        "parameters": parameters,
        "factors": macd_only,
    }


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
    rotation = _rotation_strategy_entry()
    if rotation["strategy_id"] not in {x["strategy_id"] for x in result}:
        result.insert(0, rotation)  # rotation 为推荐的默认策略，置顶展示
    signal = _signal_strategy_entry()
    if signal["strategy_id"] not in {x["strategy_id"] for x in result}:
        result.insert(1, signal)  # ETF 信号策略紧随其后
    signal_macd = _signal_macd_strategy_entry()
    if signal_macd["strategy_id"] not in {x["strategy_id"] for x in result}:
        result.insert(2, signal_macd)  # MACD 变体紧随信号策略
    return result
