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
    def execute(self, plan: ExecutionPlan, *, run_id=None, batch_id=None, symbol_names=None, decision_ids=None) -> list[dict[str, Any]]:
        result=[]
        for order in plan.orders:
            d=order.decision
            # 幂等键加 run 作用域：同 run 重试仍去重，跨日/跨 run 轮动生成同
            # symbol:action:qty 时不再误命中旧单（旧单可能已 rejected，静默
            # 复用会导致当日订单丢失且无告警）
            client_key = (f"{run_id}:{order.client_order_key}"
                          if run_id is not None and order.client_order_key else order.client_order_key)
            result.append(self.order_service.create_order(symbol=order.symbol, side=order.side,
                quantity=order.quantity, order_type="market", limit_price=None,
                source="live_strategy", reason=d.reason if d else None,
                symbol_name=(symbol_names or {}).get(order.symbol), live_run_id=run_id,
                rebalance_batch_id=batch_id,
                decision_id=(decision_ids or {}).get(order.decision.symbol) if order.decision else None,
                client_order_key=client_key))
        return result
