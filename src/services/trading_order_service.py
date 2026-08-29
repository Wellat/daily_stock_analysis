# -*- coding: utf-8 -*-
"""可转债实盘交易指令业务层。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from src.repositories.trading_order_repo import TradingOrderRepository
from src.storage import DatabaseManager

_TERMINAL_STATUSES = frozenset({"filled", "rejected", "cancelled"})
_CALLBACK_STATUSES = frozenset({"submitted", "filled", "rejected"})
_SIDES = frozenset({"buy", "sell"})
_ORDER_TYPES = frozenset({"limit", "market"})


class TradingOrderNotFoundError(ValueError):
    """交易指令不存在。"""


class TradingOrderService:
    """Create and manage convertible-bond trading order instructions."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.repository = TradingOrderRepository(db_manager)

    def create_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        limit_price: Optional[float],
        source: str = "api",
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        symbol = (symbol or "").strip()
        if not symbol.isdigit() or len(symbol) != 6:
            raise ValueError("symbol must be a 6-digit convertible-bond code")
        if side not in _SIDES:
            raise ValueError(f"side must be one of {sorted(_SIDES)}")
        if order_type not in _ORDER_TYPES:
            raise ValueError(f"order_type must be one of {sorted(_ORDER_TYPES)}")
        if quantity is None or float(quantity) <= 0:
            raise ValueError("quantity must be positive")
        if order_type == "limit" and (limit_price is None or float(limit_price) <= 0):
            raise ValueError("limit order requires a positive limit_price")

        row = self.repository.create(
            order_uid=f"qmt_{uuid4().hex}",
            symbol=symbol,
            market="cn",
            instrument_type="convertible_bond",
            side=side,
            quantity=float(quantity),
            order_type=order_type,
            limit_price=float(limit_price) if limit_price is not None else None,
            status="pending",
            source=source or "api",
            reason=reason,
        )
        return self.repository._payload(row)

    def list_orders(
        self, *, page: int, limit: int, status: Optional[str] = None
    ) -> Dict[str, Any]:
        offset = (page - 1) * limit
        payload = self.repository.list(status=status, limit=limit, offset=offset)
        return {"page": page, "limit": limit, **payload}

    def list_pending(self) -> Dict[str, Any]:
        rows = self.repository.list_pending()
        return {
            "items": [
                {
                    "id": row.id,
                    "order_uid": row.order_uid,
                    "symbol": row.symbol,
                    "side": row.side,
                    "quantity": row.quantity,
                    "order_type": row.order_type,
                    "limit_price": row.limit_price,
                }
                for row in rows
            ]
        }

    def cancel_order(self, order_id: int) -> Dict[str, Any]:
        row = self.repository.get(order_id)
        if row is None:
            raise TradingOrderNotFoundError(f"Trading order not found: {order_id}")
        if row.status != "pending":
            raise ValueError(f"only pending orders can be cancelled, current status: {row.status}")
        updated = self.repository.update(order_id, status="cancelled", completed_at=datetime.now())
        return self.repository._payload(updated)

    def apply_callback(
        self,
        *,
        order_id: int,
        status: str,
        qmt_order_id: Optional[str] = None,
        filled_quantity: Optional[float] = None,
        filled_price: Optional[float] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        if status not in _CALLBACK_STATUSES:
            raise ValueError(f"status must be one of {sorted(_CALLBACK_STATUSES)}")

        row = self.repository.get(order_id)
        if row is None:
            raise TradingOrderNotFoundError(f"Trading order not found: {order_id}")

        if row.status in _TERMINAL_STATUSES:
            # 终态幂等：重复回调不再改写，直接返回当前记录。
            return self.repository._payload(row)

        if row.status == "submitted" and status == "submitted":
            return self.repository._payload(row)

        fields: Dict[str, Any] = {"status": status}
        if status == "submitted":
            fields["submitted_at"] = datetime.now()
        else:
            fields["completed_at"] = datetime.now()
            if qmt_order_id is not None:
                fields["qmt_order_id"] = qmt_order_id
            if filled_quantity is not None:
                fields["filled_quantity"] = filled_quantity
            if filled_price is not None:
                fields["filled_price"] = filled_price
            if error_message is not None:
                fields["error_message"] = error_message

        updated = self.repository.update(order_id, **fields)
        return self.repository._payload(updated)
