"""Execution adapters for plans produced by :mod:`execution_planner`."""
from dataclasses import dataclass
from datetime import date
from typing import Any
from .execution_planner import ExecutionPlan

@dataclass(frozen=True)
class BacktestFill:
    trade_date: date
    symbol: str
    side: str
    quantity: float
    price: float
    amount: float
    decision_uid: str | None = None

class BacktestExecutor:
    def execute(self, plan: ExecutionPlan, *, trade_date: date, prices: dict[str, float]) -> list[BacktestFill]:
        fills=[]
        for order in plan.orders:
            price=order.price or prices.get(order.symbol)
            if price is None: continue
            fills.append(BacktestFill(trade_date, order.symbol, order.side, order.quantity,
                float(price), float(price)*order.quantity,
                order.decision.decision_uid if order.decision else None))
        return fills

class LiveExecutor:
    def __init__(self, order_service): self.order_service = order_service
    def execute(self, plan: ExecutionPlan, *, run_id=None, batch_id=None, symbol_names=None) -> list[dict[str, Any]]:
        result=[]
        for order in plan.orders:
            d=order.decision
            result.append(self.order_service.create_order(symbol=order.symbol, side=order.side,
                quantity=order.quantity, order_type="market", limit_price=None,
                source="live_strategy", reason=d.reason if d else None,
                symbol_name=(symbol_names or {}).get(order.symbol), live_run_id=run_id,
                rebalance_batch_id=batch_id))
        return result
