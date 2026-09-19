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
