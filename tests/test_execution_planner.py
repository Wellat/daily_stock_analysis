from datetime import date, datetime

from src.core.strategies import Bar, MarketContext, PositionSnapshot, StrategyDecision
from src.core.strategies.execution_planner import ExecutionPlanner


def _context(price=100, quantity=0, available=None):
    return MarketContext(
        datetime.now(), "cn", "convertible_bond",
        bars={"113001": [Bar(date.today(), close=price)]},
        positions={"113001": PositionSnapshot(quantity, available)},
    )


def test_planner_converts_target_amount_and_emits_delta():
    plan = ExecutionPlanner().plan(
        [StrategyDecision("buy", symbol="113001", target_amount=2000)],
        _context(price=100, quantity=3), lot_size=10,
    )
    assert [(o.side, o.quantity) for o in plan.orders] == [("buy", 10)]


def test_planner_clamps_sell_to_available_lots():
    plan = ExecutionPlanner().plan(
        [StrategyDecision("exit", symbol="113001", suggested_quantity=27)],
        _context(quantity=27, available=17), lot_size=10,
    )
    assert [(o.side, o.quantity) for o in plan.orders] == [("sell", 10)]
