"""Pure order planning shared by backtest and live executors."""
from dataclasses import dataclass, field
from typing import Any
from .base import StrategyDecision
from .context import MarketContext

@dataclass(frozen=True)
class PlannedOrder:
    symbol: str; side: str; quantity: float; price: float | None = None
    client_order_key: str = ""; decision: StrategyDecision | None = None

@dataclass(frozen=True)
class SkippedAction:
    decision: StrategyDecision; reason: str

@dataclass(frozen=True)
class RiskCheck:
    name: str; passed: bool; detail: str = ""

@dataclass(frozen=True)
class ExecutionPlan:
    orders: list[PlannedOrder] = field(default_factory=list)
    skipped: list[SkippedAction] = field(default_factory=list)
    risk_checks: list[RiskCheck] = field(default_factory=list)

class ExecutionPlanner:
    def plan(self, decisions: list[StrategyDecision], context: MarketContext, *, cash: float | None = None, lot_size: int = 10) -> ExecutionPlan:
        orders=[]; skipped=[]; checks=[RiskCheck("context", True)]
        available_cash = context.cash if cash is None else cash
        for d in decisions:
            if d.action not in ("buy","sell","exit") or not d.symbol: continue
            pos=context.positions.get(d.symbol)
            qty=d.suggested_quantity or 0
            if d.action == "buy" and not qty and d.target_amount is not None:
                bars = context.bars.get(d.symbol) or []
                price = bars[-1].close if bars else None
                if price is not None and price > 0:
                    qty = int(float(d.target_amount) / price / lot_size) * lot_size
            if d.action == "buy":
                # Strategy quantities represent target holdings; orders carry
                # only the delta from the current position.
                qty = max(0, qty - (pos.quantity if pos else 0))
                qty = int(qty / lot_size) * lot_size
            if d.action in ("sell","exit"):
                qty=min(qty, pos.available if pos else 0); qty=int(qty/lot_size)*lot_size
            if qty<=0: skipped.append(SkippedAction(d,"no_available_quantity")); continue
            if d.action=="buy" and available_cash is not None:
                price=(context.bars.get(d.symbol) or [None])[-1].close if context.bars.get(d.symbol) else None
                if price is None or qty*price>available_cash: skipped.append(SkippedAction(d,"insufficient_cash")); checks.append(RiskCheck("cash",False)); continue
                available_cash-=qty*price
            key=f"{d.decision_uid or d.symbol}:{d.action}:{qty}"
            orders.append(PlannedOrder(d.symbol,"buy" if d.action=="buy" else "sell",qty,client_order_key=key,decision=d))
        return ExecutionPlan(orders,skipped,checks)
