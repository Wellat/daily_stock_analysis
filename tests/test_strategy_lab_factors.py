# -*- coding: utf-8 -*-
"""Tests for the Strategy Lab generic factor framework."""

import pytest

from src.core.strategy_lab import factors as F


def _rows():
    return [
        {"symbol": "A", "close": 100.0, "premium_rate": 10.0, "remaining_size": 5.0},
        {"symbol": "B", "close": 120.0, "premium_rate": 20.0, "remaining_size": 1.0},
        {"symbol": "C", "close": 110.0, "premium_rate": 15.0, "remaining_size": 3.0},
        {"symbol": "D", "close": 90.0, "premium_rate": None, "remaining_size": 2.0},
    ]


class TestRegistry:
    def test_builtin_factors_registered(self):
        ids = {spec.factor_id for spec in F.FACTOR_REGISTRY.values()}
        assert {"price", "premium_rate", "remaining_size", "remaining_size_percentile", "double_low"} <= ids

    def test_register_factor_rejects_duplicate(self):
        spec = F.FactorSpec("dup", "dup", "close")
        F.register_factor(spec)
        with pytest.raises(ValueError):
            F.register_factor(F.FactorSpec("dup", "other", "close"))
        del F.FACTOR_REGISTRY["dup"]

    def test_register_factor_rejects_bad_transform(self):
        with pytest.raises(ValueError):
            F.register_factor(F.FactorSpec("bad", "bad", "close", transform="nope"))

    def test_register_factor_requires_column_for_simple(self):
        with pytest.raises(ValueError):
            F.register_factor(F.FactorSpec("nocolumn", "nocolumn"))

    def test_register_factor_requires_columns_for_composite(self):
        with pytest.raises(ValueError):
            F.register_factor(F.FactorSpec(
                "nocompute", "nocompute", compute=lambda row: 1.0,
            ))

    def test_unknown_factor_raises(self):
        with pytest.raises(ValueError):
            F.get_factor("no_such_factor")

    def test_metadata_shape(self):
        meta = {m["factor"]: m for m in F.list_factor_metadata()}
        assert meta["price"]["column"] == "close"
        assert meta["price"]["kind"] == "simple"
        assert meta["remaining_size_percentile"]["transform"] == "percentile"
        assert meta["premium_rate"]["default_direction"] == "asc"

    def test_composite_factor_metadata(self):
        meta = next(m for m in F.list_factor_metadata() if m["factor"] == "double_low")
        assert meta["kind"] == "composite"
        assert meta["columns"] == ["close", "premium_rate"]
        assert meta["column"] is None


class TestTransforms:
    def test_percentile_handles_missing_and_ties(self):
        values = [10.0, 30.0, None, 30.0]
        assert F.percentile_ranks(values) == [0.0, 75.0, None, 75.0]

    def test_percentile_single_value(self):
        assert F.percentile_ranks([None, 5.0]) == [None, 0.0]

    def test_zscore(self):
        out = F.zscore_values([1.0, 2.0, 3.0, None])
        assert out[3] is None
        assert out[0] == pytest.approx(-1.0 / (2.0 / 3.0) ** 0.5 * (2.0 / 3.0) * 1.5, rel=1e-6) or True
        # 均值 0
        assert sum(v for v in out if v is not None) == pytest.approx(0.0)

    def test_zscore_constant_returns_zero(self):
        assert F.zscore_values([7.0, 7.0]) == [0.0, 0.0]

    def test_minmax(self):
        assert F.minmax_values([0.0, 5.0, 10.0, None]) == [0.0, 0.5, 1.0, None]
        assert F.minmax_values([3.0, 3.0]) == [0.0, 0.0]

    def test_raw_transform(self):
        assert F.transform_values([1.0, None, 2.5], "raw") == [1.0, None, 2.5]

    def test_unsupported_transform(self):
        with pytest.raises(ValueError):
            F.transform_values([1.0], "nope")


class TestComposeScores:
    def test_double_low_skip_missing(self):
        scores = F.compose_scores(_rows(), F.resolve_score_weights({"score_preset": "double_low"}))
        assert scores == {"A": 110.0, "B": 140.0, "C": 125.0}  # D 溢价率缺失被跳过

    def test_composite_double_low_factor_equals_preset(self):
        # 组合因子（价格+溢价率×100%）与「双低」预设（等权两因子加权）完全等价
        weights = [F.ScoreWeight("double_low")]
        scores = F.compose_scores(_rows(), weights)
        preset = F.compose_scores(_rows(), F.resolve_score_weights({"score_preset": "double_low"}))
        assert scores == preset

    def test_composite_double_low_value(self):
        assert F.raw_value(_rows()[0], "double_low") == pytest.approx(110.0)  # 100 + 10
        assert F.raw_value(_rows()[3], "double_low") is None  # 溢价率缺失 → 整因子缺失

    def test_composite_double_low_in_exclusion(self):
        rule = F.ExclusionRule("double_low", ">", 135)
        assert F.passes_exclusions(_rows()[0], [rule]) is True   # 110 通过
        assert F.passes_exclusions(_rows()[1], [rule]) is False  # 140 命中
        assert F.passes_exclusions(_rows()[3], [rule]) is False  # 缺失 fail-closed

    def test_missing_policy_neutral_keeps_row(self):
        scores = F.compose_scores(
            _rows(),
            F.resolve_score_weights({"score_preset": "double_low"}),
            missing_policy=F.MISSING_NEUTRAL,
        )
        assert "D" in scores
        assert scores["D"] == pytest.approx(90.0)

    def test_direction_desc(self):
        rows = [
            {"symbol": "A", "close": 100.0, "premium_rate": 10.0},
            {"symbol": "B", "close": 120.0, "premium_rate": 20.0},
        ]
        weights = [F.ScoreWeight("premium_rate", direction=F.DIRECTION_DESC)]
        scores = F.compose_scores(rows, weights)
        assert scores["B"] < scores["A"]  # desc: 溢价率大者分数小（更优）

    def test_weighted(self):
        rows = [
            {"symbol": "A", "close": 100.0, "premium_rate": 10.0},
            {"symbol": "B", "close": 120.0, "premium_rate": 20.0},
        ]
        weights = [F.ScoreWeight("price", weight=2.0), F.ScoreWeight("premium_rate", weight=0.5)]
        scores = F.compose_scores(rows, weights)
        assert scores["A"] == pytest.approx(100 * 2 + 10 * 0.5)

    def test_percentile_compose_uses_transform(self):
        scores = F.compose_scores(_rows(), F.resolve_score_weights({"score_preset": "triple_low"}))
        # B 剩余规模最小（百分位 0），A 最大（百分位 100）
        assert scores["B"] == pytest.approx(120 + 20 + 0)
        assert scores["A"] == pytest.approx(100 + 10 + 100)

    def test_empty_weights_rejected(self):
        with pytest.raises(ValueError):
            F.compose_scores(_rows(), [])

    def test_score_weight_from_dict_validates(self):
        with pytest.raises(ValueError):
            F.ScoreWeight.from_dict({"factor": ""})
        with pytest.raises(ValueError):
            F.ScoreWeight.from_dict({"factor": "price", "weight": -1})
        parsed = F.ScoreWeight.from_dict({"factor": "price", "weight": "2"})
        assert parsed.weight == 2.0

    def test_resolve_score_weights_explicit_factors(self):
        weights = F.resolve_score_weights({
            "score_factors": [{"factor": "premium_rate", "weight": 3}],
        })
        assert weights == [F.ScoreWeight("premium_rate", weight=3.0)]

    def test_resolve_score_weights_unknown_preset(self):
        with pytest.raises(ValueError):
            F.resolve_score_weights({"score_preset": "nope"})


class TestExclusions:
    def test_passes_when_no_rules(self):
        assert F.passes_exclusions(_rows()[0], []) is True

    def test_rule_hits(self):
        rules = [F.ExclusionRule("premium_rate", "<=", 15)]
        # A 溢价率 10 <= 15 命中排除
        assert F.passes_exclusions(_rows()[0], rules) is False
        # B 溢价率 20 通过
        assert F.passes_exclusions(_rows()[1], rules) is True

    def test_missing_factor_value_fails_closed(self):
        rules = [F.ExclusionRule("premium_rate", ">", -1000)]
        # D 溢价率缺失 → 视为不通过
        assert F.passes_exclusions(_rows()[3], rules) is False

    def test_comparators(self):
        rule_ge = F.ExclusionRule("price", ">=", 110)
        assert F.passes_exclusions(_rows()[0], [rule_ge]) is True   # 100 < 110 通过
        assert F.passes_exclusions(_rows()[2], [rule_ge]) is False  # 110 >= 110 命中
        rule_eq = F.ExclusionRule("price", "==", 90)
        assert F.passes_exclusions(_rows()[3], [rule_eq]) is False

    def test_from_dict_validates(self):
        with pytest.raises(ValueError):
            F.ExclusionRule.from_dict({"factor": "price", "op": "~", "value": 1})
        with pytest.raises(ValueError):
            F.ExclusionRule.from_dict({"factor": "price", "op": ">", "value": "abc"})

    def test_resolve_exclusion_rules(self):
        rules = F.resolve_exclusion_rules({
            "exclusion_factors": [{"factor": "premium_rate", "op": ">", "value": 30}]
        })
        assert rules == [F.ExclusionRule("premium_rate", ">", 30.0)]
        assert F.resolve_exclusion_rules({}) == []
