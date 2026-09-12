# -*- coding: utf-8 -*-
"""可转债实盘交易指令业务层。"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from src.repositories.trading_order_repo import TradingOrderRepository
from src.storage import DatabaseManager

_TERMINAL_STATUSES = frozenset({"filled", "rejected", "cancelled"})
_CALLBACK_STATUSES = frozenset({"submitted", "filled", "rejected"})
_SIDES = frozenset({"buy", "sell"})
_ORDER_TYPES = frozenset({"limit", "market"})

logger = logging.getLogger(__name__)


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
        symbol_name: Optional[str] = None,
        live_run_id: Optional[int] = None,
        rebalance_batch_id: Optional[int] = None,
        decision_id: Optional[int] = None,
        client_order_key: Optional[str] = None,
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

        if client_order_key:
            existing = self.repository.get_by_client_key(client_order_key)
            if existing is not None:
                # 命中幂等键复用旧单：打日志并在返回值标记，避免静默丢单不可见
                logger.info("reuse existing order id=%s status=%s for client_order_key=%s",
                            existing.id, existing.status, client_order_key)
                payload = self.repository._payload(existing)
                payload["reused"] = True
                return payload
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
            symbol_name=symbol_name,
            live_run_id=live_run_id,
            rebalance_batch_id=rebalance_batch_id,
            decision_id=decision_id,
            client_order_key=client_order_key,
        )
        return self.repository._payload(row)

    def list_orders(
        self, *, page: int, limit: int, status: Optional[str] = None
    ) -> Dict[str, Any]:
        offset = (page - 1) * limit
        payload = self.repository.list(status=status, limit=limit, offset=offset)
        return {"page": page, "limit": limit, **payload}

    def get_dashboard(
        self, *, start: Optional[date] = None, end: Optional[date] = None
    ) -> Dict[str, Any]:
        """策略看板：时间范围内已成交订单的 FIFO 配对盈亏汇总。

        只计算已实现盈亏（卖出按 FIFO 与买入配对）；未平仓部分无现价来源，
        仅展示数量与成本。卖出超出已有买入 lot 的部分计为无配对卖出，不计盈亏。
        """
        rows = self.repository.list_filled(start_date=start, end_date=end)

        lots: Dict[str, List[Dict[str, float]]] = {}
        symbol_stats: Dict[str, Dict[str, Any]] = {}
        daily_pnl: Dict[str, float] = {}
        total = {
            "total_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "buy_amount": 0.0,
            "sell_amount": 0.0,
            "realized_pnl": 0.0,
            "win_count": 0,
            "loss_count": 0,
            "unmatched_sell_quantity": 0.0,
        }

        for row in rows:
            qty = float(row.filled_quantity or row.quantity or 0.0)
            price = float(row.filled_price or 0.0)
            stat = symbol_stats.setdefault(
                row.symbol,
                {
                    "symbol": row.symbol,
                    "symbol_name": row.symbol_name,
                    "buy_count": 0,
                    "buy_quantity": 0.0,
                    "buy_amount": 0.0,
                    "sell_count": 0,
                    "sell_quantity": 0.0,
                    "sell_amount": 0.0,
                    "realized_pnl": 0.0,
                    "open_quantity": 0.0,
                    "open_cost": 0.0,
                    "unmatched_sell_quantity": 0.0,
                },
            )
            if row.symbol_name and not stat["symbol_name"]:
                stat["symbol_name"] = row.symbol_name
            total["total_count"] += 1

            if row.side == "buy":
                total["buy_count"] += 1
                total["buy_amount"] += qty * price
                stat["buy_count"] += 1
                stat["buy_quantity"] += qty
                stat["buy_amount"] += qty * price
                lots.setdefault(row.symbol, []).append({"quantity": qty, "price": price})
                continue

            total["sell_count"] += 1
            total["sell_amount"] += qty * price
            stat["sell_count"] += 1
            stat["sell_quantity"] += qty
            stat["sell_amount"] += qty * price
            queue = lots.setdefault(row.symbol, [])
            remaining = qty
            order_pnl = 0.0
            matched_qty = 0.0
            while remaining > 1e-9 and queue:
                lot = queue[0]
                matched = min(remaining, lot["quantity"])
                order_pnl += (price - lot["price"]) * matched
                matched_qty += matched
                lot["quantity"] -= matched
                remaining -= matched
                if lot["quantity"] <= 1e-9:
                    queue.pop(0)
            if remaining > 1e-9:
                stat["unmatched_sell_quantity"] += remaining
                total["unmatched_sell_quantity"] += remaining
            total["realized_pnl"] += order_pnl
            stat["realized_pnl"] += order_pnl
            if order_pnl > 1e-9:
                total["win_count"] += 1
            elif order_pnl < -1e-9:
                total["loss_count"] += 1
            event_time = row.completed_at or row.created_at
            # 完全无配对的卖出不计盈亏，也不产生曲线数据点
            if event_time is not None and matched_qty > 1e-9:
                day = event_time.date().isoformat()
                daily_pnl[day] = daily_pnl.get(day, 0.0) + order_pnl

        for stat in symbol_stats.values():
            queue = lots.get(stat["symbol"], [])
            stat["open_quantity"] = round(sum(lot["quantity"] for lot in queue), 6)
            stat["open_cost"] = round(sum(lot["quantity"] * lot["price"] for lot in queue), 6)

        curve: List[Dict[str, Any]] = []
        cumulative = 0.0
        for day in sorted(daily_pnl):
            cumulative += daily_pnl[day]
            curve.append(
                {
                    "date": day,
                    "daily_pnl": round(daily_pnl[day], 6),
                    "cumulative_pnl": round(cumulative, 6),
                }
            )

        decided = total["win_count"] + total["loss_count"]
        summary = dict(total)
        summary["win_rate"] = round(total["win_count"] / decided, 6) if decided else None
        return {
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "summary": summary,
            "curve": curve,
            "symbols": sorted(
                symbol_stats.values(), key=lambda item: item["realized_pnl"], reverse=True
            ),
        }

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

        if status == "filled":
            if filled_quantity is None or float(filled_quantity) <= 0:
                raise ValueError("filled callback requires positive filled_quantity")
            if abs(float(filled_quantity) - float(row.quantity)) > 1e-9:
                raise ValueError("partial fills are not supported; filled_quantity must equal quantity")
            if filled_price is None or float(filled_price) <= 0:
                raise ValueError("filled callback requires positive filled_price")
        if status == "rejected" and not (error_message or "").strip():
            raise ValueError("rejected callback requires error_message")

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
