"""按价格分档缩放仓位额度，实盘与策略实验室回测共用同一语义。

分档由一组严格递增的价格上界与对应比例构成：bounds=(165, 185, 220, 250)、
ratios=(1.0, 0.8, 0.6, 0.4, 0.2) 表示 ≤165 全额、(165,185] 80%、(185,220] 60%、
(220,250] 40%、>250 20%。价格恰好等于上界时归入该上界区间内。
"""

from typing import Sequence

PRICE_TIER_BOUNDS_DEFAULT = (165.0, 185.0, 220.0, 250.0)
PRICE_TIER_RATIOS_DEFAULT = (1.0, 0.8, 0.6, 0.4, 0.2)


def validate_price_tiers(bounds: Sequence[float], ratios: Sequence[float]) -> None:
    """校验分档配置：上界严格递增且为正，比例个数=上界数+1 且落在 (0, 1]。"""
    if not bounds:
        if len(ratios) != 1:
            raise ValueError("price tiers: ratios must contain exactly one entry when bounds are empty")
        return
    if len(ratios) != len(bounds) + 1:
        raise ValueError(
            f"price tiers: need {len(bounds) + 1} ratios for {len(bounds)} bounds, got {len(ratios)}"
        )
    for i, upper in enumerate(bounds):
        if not (upper > 0):
            raise ValueError(f"price tiers: bound #{i + 1} must be positive, got {upper}")
        if i > 0 and upper <= bounds[i - 1]:
            raise ValueError(
                f"price tiers: bounds must be strictly increasing, bound #{i + 1} ({upper}) <= #{i} ({bounds[i - 1]})"
            )
    for i, ratio in enumerate(ratios):
        if not (0.0 < ratio <= 1.0):
            raise ValueError(f"price tiers: ratio #{i + 1} must be in (0, 1], got {ratio}")


def price_size_ratio(price: float, bounds: Sequence[float], ratios: Sequence[float]) -> float:
    """返回价格所在档位的额度比例；bounds 为空表示未启用分档，恒返回 1.0。"""
    if not bounds:
        return 1.0
    validate_price_tiers(bounds, ratios)
    for upper, ratio in zip(bounds, ratios):
        if price <= upper:
            return ratio
    return ratios[-1]


def price_tiers_from_scalars(
    *,
    tier1_max: float,
    tier2_max: float,
    tier3_max: float,
    tier4_max: float,
    tier2_pct: float,
    tier3_pct: float,
    tier4_pct: float,
    tier5_pct: float,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """8 个固定档标量参数 → (bounds, ratios)。

    tier1_max<=0 表示整体关闭，返回空 bounds（调用方按未启用分档处理）。
    一档（≤tier1_max）比例固定为 1.0，五档（>tier4_max）用 tier5_pct 兜底。
    上界按前缀截断：中间某档填 0 即该档及之后档位不启用（避免跳档歧义）。
    """
    raw_bounds = (tier1_max, tier2_max, tier3_max, tier4_max)
    bounds: list[float] = []
    for value in raw_bounds:
        if value is None or float(value) <= 0:
            break
        bounds.append(float(value))
    ratios = [1.0] + [float(x) / 100.0 for x in (tier2_pct, tier3_pct, tier4_pct, tier5_pct)]
    ratios = ratios[: len(bounds) + 1]
    validate_price_tiers(bounds, ratios)
    return tuple(bounds), tuple(ratios)
