# -*- coding: utf-8 -*-
"""价格分档仓位共享函数测试（实盘与回测共用 src.core.strategies.sizing）。"""

import pytest

from src.core.strategies.sizing import (
    price_size_ratio,
    price_tiers_from_scalars,
    validate_price_tiers,
)

BOUNDS = (165.0, 185.0, 220.0, 250.0)
RATIOS = (1.0, 0.8, 0.6, 0.4, 0.2)


def _scalars(**overrides):
    values = {"tier1_max": 165, "tier2_max": 185, "tier3_max": 220, "tier4_max": 250,
              "tier2_pct": 80, "tier3_pct": 60, "tier4_pct": 40, "tier5_pct": 20}
    values.update(overrides)
    return values


def test_ratio_by_price_band_with_boundaries():
    """价格恰为上界归入该档内：165→全额、185→80%、220→60%、250→40%。"""
    cases = [
        (100.0, 1.0), (165.0, 1.0),
        (165.01, 0.8), (185.0, 0.8),
        (185.01, 0.6), (220.0, 0.6),
        (220.01, 0.4), (250.0, 0.4),
        (250.01, 0.2), (300.0, 0.2),
    ]
    for price, expected in cases:
        assert price_size_ratio(price, BOUNDS, RATIOS) == expected


def test_empty_bounds_disables_tiering():
    assert price_size_ratio(300.0, (), (1.0,)) == 1.0


def test_validate_rejects_bad_tier_configs():
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_price_tiers((185.0, 165.0), (1.0, 0.8, 0.5))
    with pytest.raises(ValueError, match="positive"):
        validate_price_tiers((0.0, 165.0), (1.0, 0.8, 0.5))
    with pytest.raises(ValueError, match="ratio"):
        validate_price_tiers((165.0,), (1.0, 0.0))
    with pytest.raises(ValueError, match="ratios"):
        validate_price_tiers((165.0,), (1.0, 0.8, 0.5))


def test_scalars_default_four_tiers():
    bounds, ratios = price_tiers_from_scalars(**_scalars())
    assert bounds == (165.0, 185.0, 220.0, 250.0)
    assert ratios == (1.0, 0.8, 0.6, 0.4, 0.2)


def test_scalars_tier1_zero_disables_all_tiers():
    bounds, ratios = price_tiers_from_scalars(**_scalars(tier1_max=0))
    assert bounds == ()
    assert ratios == (1.0,)


def test_scalars_zero_bound_truncates_remaining_tiers():
    """中间档上界填 0：该档及之后档位不启用（前缀截断，不跳档）。"""
    bounds, ratios = price_tiers_from_scalars(**_scalars(tier3_max=0))
    assert bounds == (165.0, 185.0)
    assert ratios == (1.0, 0.8, 0.6)

    bounds, ratios = price_tiers_from_scalars(**_scalars(tier2_max=0))
    assert bounds == (165.0,)
    assert ratios == (1.0, 0.8)


def test_scalars_reject_non_ascending_bounds():
    with pytest.raises(ValueError, match="strictly increasing"):
        price_tiers_from_scalars(**_scalars(tier2_max=160))
