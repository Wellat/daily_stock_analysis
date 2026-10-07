from datetime import date, datetime

from src.core.strategies import Bar, FactorSnapshot, InstrumentSnapshot, LowPremiumStrategy, MarketContext, MarketEvent, PositionSnapshot


def context(rows, positions=None, events=None):
    return MarketContext(datetime.now(), "cn", "convertible_bond",
        [InstrumentSnapshot(r[0], r[1]) for r in rows],
        {r[0]: [Bar(date.today(), r[2])] for r in rows},
        {r[0]: FactorSnapshot(r[3]) for r in rows}, events or {}, positions or {})


def test_low_premium_sorts_and_limits_positions():
    c = context([("A", "A", 100, 8), ("B", "B", 100, 2), ("C", "C", 100, 5)])
    decisions = LowPremiumStrategy().evaluate(c, parameters={"max_positions": 2})
    assert [d.symbol for d in decisions] == ["B", "C"]


def test_low_premium_fixed_quantity_overrides_target_amount():
    """per_position_quantity>0 时发固定张数（suggested_quantity），否则发目标金额。"""
    c = context([("A", "A", 100, 8), ("B", "B", 100, 2)])
    decisions = LowPremiumStrategy().evaluate(
        c, parameters={"max_positions": 1, "per_position_quantity": 45, "lot_size": 10}
    )
    assert [(d.symbol, d.suggested_quantity, d.target_amount) for d in decisions] == [("B", 40, None)]

    decisions = LowPremiumStrategy().evaluate(
        c, parameters={"max_positions": 1, "per_position_quantity": 0}
    )
    assert [(d.symbol, d.suggested_quantity, d.target_amount) for d in decisions] == [("B", None, 10000)]


def test_event_check_only_exits_blocked_current_position():
    c = context([("A", "A", 100, 8), ("B", "B", 100, 2)],
        {"A": PositionSnapshot(20, 10)}, {"A": [MarketEvent("redemption")]})
    decisions = LowPremiumStrategy().evaluate(c, mode="event_check")
    assert [(d.action, d.symbol, d.suggested_quantity) for d in decisions] == [("exit", "A", 10)]


def test_event_check_holds_blocked_position_when_exclusion_disabled():
    """exclude_event_blocked=False 时强赎/下修提醒不再触发持仓退出。"""
    c = context([("A", "A", 100, 8)], {"A": PositionSnapshot(20, 10)}, {"A": [MarketEvent("redemption")]})
    decisions = LowPremiumStrategy().evaluate(c, mode="event_check", parameters={"exclude_event_blocked": False})
    assert [(d.action, d.reason) for d in decisions] == [("hold", "event_exit_disabled")]


def test_low_premium_excludes_st_underlying_stock():
    """exclude_st=True 时正股名称带 ST（含 *ST）的候选不入选。"""
    c = context([("A", "A", 100, 1), ("B", "B", 100, 2), ("C", "C", 100, 3)])
    c = MarketContext(c.as_of, "cn", "convertible_bond", c.instruments, c.bars,
        {"A": FactorSnapshot(1, values={"stock_name": "*ST测试"}),
         "B": FactorSnapshot(2, values={"stock_name": "ST测试"}),
         "C": FactorSnapshot(3, values={"stock_name": "正常股份"})}, {}, {})
    decisions = LowPremiumStrategy().evaluate(c, parameters={"max_positions": 3, "exclude_st": True})
    assert [d.symbol for d in decisions] == ["C"]

    decisions = LowPremiumStrategy().evaluate(c, parameters={"max_positions": 3})
    assert [d.symbol for d in decisions] == ["A", "B", "C"]


def test_low_premium_caps_target_amount_by_position_weight():
    """account_capital×max_position_pct 折算单只金额上限，取与目标资金的较小值。"""
    c = context([("B", "B", 100, 2)])
    decisions = LowPremiumStrategy().evaluate(c, parameters={
        "max_positions": 1, "per_position_cash": 9000,
        "account_capital": 50000, "max_position_pct": 10,
    })
    assert [(d.symbol, d.target_amount) for d in decisions] == [("B", 5000)]

    # 上限高于目标资金时不放大仓位
    decisions = LowPremiumStrategy().evaluate(c, parameters={
        "max_positions": 1, "per_position_cash": 4000,
        "account_capital": 50000, "max_position_pct": 10,
    })
    assert decisions[0].target_amount == 4000

    # 未启用（account_capital=0）时不设上限
    decisions = LowPremiumStrategy().evaluate(c, parameters={
        "max_positions": 1, "per_position_cash": 9000, "account_capital": 0, "max_position_pct": 10,
    })
    assert decisions[0].target_amount == 9000


def test_low_premium_caps_fixed_quantity_by_position_weight():
    """固定张数路径同样受权重上限约束，按手数向下取整。"""
    c = context([("B", "B", 100, 2)])
    decisions = LowPremiumStrategy().evaluate(c, parameters={
        "max_positions": 1, "per_position_quantity": 100, "lot_size": 10,
        "account_capital": 50000, "max_position_pct": 10,
    })
    assert decisions[0].suggested_quantity == 50

    # 上限金额不足一手时跳过该候选
    decisions = LowPremiumStrategy().evaluate(c, parameters={
        "max_positions": 1, "per_position_quantity": 100, "lot_size": 10,
        "account_capital": 5000, "max_position_pct": 10,
    })
    assert decisions == []


def test_low_premium_scales_amount_by_price_tiers():
    """默认四档价格分档：≤165 全额、(185,220] 60%、>250 20%。"""
    c = context([("A", "A", 100, 1), ("B", "B", 190, 2), ("C", "C", 260, 3)])
    decisions = LowPremiumStrategy().evaluate(c, parameters={"max_positions": 3, "per_position_cash": 10000})
    assert {d.symbol: d.target_amount for d in decisions} == {"A": 10000, "B": 6000, "C": 2000}
    assert {d.symbol: d.decision_data["price_ratio"] for d in decisions} == {"A": 1.0, "B": 0.6, "C": 0.2}


def test_low_premium_price_tier_scales_before_weight_cap():
    """先按价格分档缩放，再被单只权重上限封顶。"""
    c = context([("B", "B", 190, 2)])
    # 9000×0.6=5400 > cap 5000 → 封顶生效
    decisions = LowPremiumStrategy().evaluate(c, parameters={
        "max_positions": 1, "per_position_cash": 9000,
        "account_capital": 50000, "max_position_pct": 10,
    })
    assert decisions[0].target_amount == 5000
    # 8000×0.6=4800 < cap 5000 → 分档结果不被封顶
    decisions = LowPremiumStrategy().evaluate(c, parameters={
        "max_positions": 1, "per_position_cash": 8000,
        "account_capital": 50000, "max_position_pct": 10,
    })
    assert decisions[0].target_amount == 4800


def test_low_premium_price_tiers_disabled_by_zero_tier1():
    """一档上限填 0 关闭分档，高价债也按全额目标资金。"""
    c = context([("A", "A", 260, 3)])
    decisions = LowPremiumStrategy().evaluate(c, parameters={
        "max_positions": 1, "per_position_cash": 9000, "price_tier1_max": 0,
    })
    assert decisions[0].target_amount == 9000


def test_low_premium_fixed_quantity_scaled_by_price_tiers():
    """固定张数路径同样按分档比例缩放（整手取整）。"""
    c = context([("B", "B", 190, 2)])
    decisions = LowPremiumStrategy().evaluate(c, parameters={
        "max_positions": 1, "per_position_quantity": 100, "lot_size": 10,
    })
    assert decisions[0].suggested_quantity == 60
