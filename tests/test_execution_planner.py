from datetime import date, datetime

from src.core.strategies import Bar, MarketContext, PositionSnapshot, StrategyDecision
from src.core.strategies.execution_planner import ExecutionPlanner


def _context(price=100, quantity=0, available=None):
    return MarketContext(
        datetime.now(), "cn", "convertible_bond",
        bars={"113001": [Bar(date.today(), close=price)]},
        positions={"113001": PositionSnapshot(quantity, available)},
    )


def _multi_context(prices: dict[str, float]):
    return MarketContext(
        datetime.now(), "cn", "convertible_bond",
        bars={s: [Bar(date.today(), close=p)] for s, p in prices.items()},
        positions={},
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


def test_planner_caps_single_buy_amount_to_MAX_BUY_AMOUNT_EACH_SYMBOL():
    # 价格 1000，1 万最多买 10 张（lot_size=10）；目标金额 5 万应被截断到 10 张。
    plan = ExecutionPlanner().plan(
        [StrategyDecision("buy", symbol="113001", target_amount=50000)],
        _context(price=1000), lot_size=10,
    )
    assert [(o.side, o.quantity) for o in plan.orders] == [("buy", 10)]


def test_planner_limits_buy_symbols_to_max_buy_symbols():
    # 12 只各买 1000 元，只保留前 10 只的买入，后 2 只记入 skipped。
    prices = {f"113{i:03d}": 100.0 for i in range(1, 13)}
    decisions = [StrategyDecision("buy", symbol=s, target_amount=1000) for s in prices]
    plan = ExecutionPlanner().plan(decisions, _multi_context(prices), lot_size=10)
    assert len(plan.orders) == 10
    assert all(o.side == "buy" for o in plan.orders)
    assert [s.decision.symbol for s in plan.skipped] == ["113011", "113012"]
    assert all(s.reason == "max_buy_symbols" for s in plan.skipped)


def test_planner_skips_buy_when_cash_is_insufficient_and_records_risk_check():
    plan = ExecutionPlanner().plan(
        [StrategyDecision("buy", symbol="113001", target_amount=1000)],
        _context(price=100), cash=500, lot_size=10,
    )
    assert plan.orders == []
    assert [(item.decision.symbol, item.reason) for item in plan.skipped] == [("113001", "insufficient_cash")]
    assert any(check.name == "cash" and not check.passed for check in plan.risk_checks)


def test_planner_skips_sell_without_available_position():
    plan = ExecutionPlanner().plan(
        [StrategyDecision("sell", symbol="113001", suggested_quantity=10)],
        _context(price=100, quantity=0), lot_size=10,
    )
    assert plan.orders == []
    assert plan.skipped[0].reason == "no_available_quantity"


def test_planner_ignores_unknown_actions_and_empty_symbols():
    plan = ExecutionPlanner().plan(
        [StrategyDecision("hold", symbol="113001"), StrategyDecision("buy", symbol="")],
        _context(),
    )
    assert plan.orders == []
    assert plan.skipped == []
