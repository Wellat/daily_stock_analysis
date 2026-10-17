# -*- coding: utf-8 -*-
"""Time-series factor framework for signal-style Strategy Lab backtests.

与 :mod:`src.core.strategy_lab.factors` 的横截面框架并列：这里的因子是
「单标的日线行序列 → 等长值序列」的逐日变换（如均线乖离率），值与标的
自身历史比较，不做跨标的排名。进出场规则复用同一 ``{factor, op, value}``
形态与比较器语义（``factors.COMPARATORS``）。

序列约定：
- 输入为单标的按交易日升序的行（含预热段），每行至少含 ``close``，
  可选 ``dividend_yield`` 等慢变列；
- 输出与输入等长，未就绪的位置为 None（滚动窗口预热 / 数据缺失）；
- 引擎只在回测窗口内消费输出，预热段仅参与滚动计算。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

SERIES_TRANSFORM = "series"

Row = Mapping[str, Any]


@dataclass(frozen=True)
class SeriesFactorSpec:
    """One per-symbol time-series factor definition."""

    factor_id: str
    label: str
    compute: Callable[[Sequence[Row], Dict[str, Any]], List[Optional[float]]]
    window_params: tuple = ()  # 依赖的标量参数名（如 ("ma_window",)），metadata 透出
    description: str = ""
    unit: Optional[str] = None

    def metadata(self) -> Dict[str, Any]:
        """与横截面 FactorSpec.metadata 形状兼容（前端因子下拉共用）。"""
        return {
            "factor": self.factor_id,
            "label": self.label,
            "kind": "series",
            "column": None,
            "transform": SERIES_TRANSFORM,
            "default_direction": "asc",
            "description": self.description,
            "unit": self.unit,
            "window_params": list(self.window_params),
        }


SERIES_FACTOR_REGISTRY: Dict[str, SeriesFactorSpec] = {}


def register_series_factor(spec: SeriesFactorSpec) -> SeriesFactorSpec:
    existing = SERIES_FACTOR_REGISTRY.get(spec.factor_id)
    if existing is not None and existing != spec:
        raise ValueError(f"Series factor already registered: {spec.factor_id}")
    SERIES_FACTOR_REGISTRY[spec.factor_id] = spec
    return spec


def get_series_factor(factor_id: str) -> SeriesFactorSpec:
    spec = SERIES_FACTOR_REGISTRY.get(factor_id)
    if spec is None:
        raise ValueError(f"Unknown series factor: {factor_id}")
    return spec


def list_series_factor_metadata() -> List[Dict[str, Any]]:
    return [spec.metadata() for spec in SERIES_FACTOR_REGISTRY.values()]


def _sma_series(closes: Sequence[float], window: int) -> List[Optional[float]]:
    values: List[Optional[float]] = []
    running = 0.0
    for index, close in enumerate(closes):
        running += float(close)
        if index >= window:
            running -= float(closes[index - window])
        if index + 1 >= window:
            values.append(running / window)
        else:
            values.append(None)
    return values


def _ema_series(closes: Sequence[float], period: int) -> List[float]:
    """标准 EMA：以首值为种子，k = 2/(period+1)。"""
    k = 2.0 / (period + 1.0)
    values: List[float] = []
    last = float(closes[0]) if closes else 0.0
    for close in closes:
        last = last + k * (float(close) - last)
        values.append(last)
    return values


def _macd_series(
    closes: Sequence[float], fast: int, slow: int, signal: int
) -> tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    """返回 (DIF, DEA, 柱, 交叉事件)；前 slow+signal 根为种子预热期，输出 None。

    事件序列：上穿（金叉）当日 +1，下穿（死叉）当日 -1，其余 0；预热期 None。
    """
    ema_fast = _ema_series(closes, fast)
    ema_slow = _ema_series(closes, slow)
    # DEA：对 DIF 再做 EMA；DIF 的种子即首日 DIF
    dif_raw = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea_raw = _ema_series(dif_raw, signal)
    hist_raw = [d - e for d, e in zip(dif_raw, dea_raw)]
    warm = slow + signal
    dif = [None] * min(warm, len(dif_raw)) + dif_raw[warm:]
    dea = [None] * min(warm, len(dea_raw)) + dea_raw[warm:]
    hist = [None] * min(warm, len(hist_raw)) + hist_raw[warm:]
    cross: List[Optional[float]] = [None] * len(closes)
    for i in range(len(closes)):
        if dif[i] is None:
            continue
        if i == 0 or dif[i - 1] is None:
            cross[i] = 0.0  # 首个就绪日无前值可比，视为无事件
            continue
        if dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]:
            cross[i] = 1.0
        elif dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]:
            cross[i] = -1.0
        else:
            cross[i] = 0.0
    return dif, dea, hist, cross


def _ffill(values: Sequence[Optional[float]]) -> List[Optional[float]]:
    filled: List[Optional[float]] = []
    last: Optional[float] = None
    for value in values:
        if value is not None:
            last = float(value)
        filled.append(last)
    return filled


def _register_builtin_series_factors() -> None:
    def _close(rows: Sequence[Row], params: Dict[str, Any]) -> List[Optional[float]]:
        return [float(row["close"]) for row in rows]

    def _ma_gap_pct(rows: Sequence[Row], params: Dict[str, Any]) -> List[Optional[float]]:
        window = max(2, int(params.get("ma_window", 182)))
        closes = [float(row["close"]) for row in rows]
        mas = _sma_series(closes, window)
        return [
            (close / ma - 1.0) * 100.0 if ma is not None and ma > 0 else None
            for close, ma in zip(closes, mas)
        ]

    def _roc_pct(rows: Sequence[Row], params: Dict[str, Any]) -> List[Optional[float]]:
        window = max(1, int(params.get("roc_window", 20)))
        closes = [float(row["close"]) for row in rows]
        return [
            (closes[index] / closes[index - window] - 1.0) * 100.0
            if index >= window and closes[index - window] > 0
            else None
            for index in range(len(closes))
        ]

    def _dividend_yield(rows: Sequence[Row], params: Dict[str, Any]) -> List[Optional[float]]:
        # 慢变列：按最近可得值前向填充；当前无同步来源时恒为 None，
        # 数据落库后规则即刻生效（引擎无需改动）。
        return _ffill([row.get("dividend_yield") for row in rows])

    def _macd(rows: Sequence[Row], params: Dict[str, Any]) -> tuple:
        fast = max(2, int(params.get("macd_fast", 12)))
        slow = max(3, int(params.get("macd_slow", 26)))
        signal = max(2, int(params.get("macd_signal", 9)))
        closes = [float(row["close"]) for row in rows]
        return _macd_series(closes, fast, slow, signal)

    def _macd_dif(rows: Sequence[Row], params: Dict[str, Any]) -> List[Optional[float]]:
        return _macd(rows, params)[0]

    def _macd_dea(rows: Sequence[Row], params: Dict[str, Any]) -> List[Optional[float]]:
        return _macd(rows, params)[1]

    def _macd_hist(rows: Sequence[Row], params: Dict[str, Any]) -> List[Optional[float]]:
        return _macd(rows, params)[2]

    def _macd_cross(rows: Sequence[Row], params: Dict[str, Any]) -> List[Optional[float]]:
        return _macd(rows, params)[3]

    register_series_factor(SeriesFactorSpec(
        factor_id="close",
        label="收盘价",
        compute=_close,
        description="（后复权）收盘价",
        unit="元",
    ))
    register_series_factor(SeriesFactorSpec(
        factor_id="ma_gap_pct",
        label="均线乖离率",
        compute=_ma_gap_pct,
        window_params=("ma_window",),
        description="(收盘价 / N日均线 - 1) × 100%，N 由「均线窗口」参数控制；低于 0 即价格位于均线下方",
        unit="%",
    ))
    register_series_factor(SeriesFactorSpec(
        factor_id="roc_pct",
        label="区间涨跌幅",
        compute=_roc_pct,
        window_params=("roc_window",),
        description="(收盘价 / N日前收盘 - 1) × 100%，N 由「涨跌幅窗口」参数控制",
        unit="%",
    ))
    register_series_factor(SeriesFactorSpec(
        factor_id="dividend_yield",
        label="股息率",
        compute=_dividend_yield,
        description="股息率（%）。当前版本未同步该数据，恒为缺失——引用该因子的规则不会触发",
        unit="%",
    ))
    macd_window_params = ("macd_fast", "macd_slow", "macd_signal")
    register_series_factor(SeriesFactorSpec(
        factor_id="macd_dif",
        label="MACD DIF",
        compute=_macd_dif,
        window_params=macd_window_params,
        description="MACD 快线 DIF = EMA(fast) - EMA(slow)；可用于零轴过滤等规则（如 DIF > 0）",
        unit="元",
    ))
    register_series_factor(SeriesFactorSpec(
        factor_id="macd_dea",
        label="MACD DEA",
        compute=_macd_dea,
        window_params=macd_window_params,
        description="MACD 信号线 DEA = DIF 的 EMA(signal)",
        unit="元",
    ))
    register_series_factor(SeriesFactorSpec(
        factor_id="macd_hist",
        label="MACD 柱",
        compute=_macd_hist,
        window_params=macd_window_params,
        description="MACD 柱 = DIF - DEA（未乘 2，国内软件柱高为 2 倍；符号与软件一致，"
                    "用于「柱转正买入 / 柱转负卖出」类规则）",
        unit="元",
    ))
    register_series_factor(SeriesFactorSpec(
        factor_id="macd_cross",
        label="MACD 交叉事件",
        compute=_macd_cross,
        window_params=macd_window_params,
        description="事件因子：DIF 上穿 DEA（金叉）当日 = +1，下穿（死叉）当日 = -1，其余交易日 = 0。"
                    "典型规则：买入 macd_cross > 0，卖出 macd_cross < 0",
    ))


_register_builtin_series_factors()
