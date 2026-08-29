# -*- coding: utf-8 -*-
"""可转债实盘交易指令数据访问层。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import asc, desc, select

from src.storage import DatabaseManager, TradingOrder


class TradingOrderRepository:
    """Persist convertible-bond trading order instructions."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def create(self, **fields: Any) -> TradingOrder:
        with self.db.get_session() as session:
            row = TradingOrder(
                **fields,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def get(self, order_id: int) -> Optional[TradingOrder]:
        with self.db.get_session() as session:
            row = session.get(TradingOrder, order_id)
            if row is None:
                return None
            session.expunge(row)
            return row

    def get_by_uid(self, order_uid: str) -> Optional[TradingOrder]:
        with self.db.get_session() as session:
            row = session.execute(
                select(TradingOrder).where(TradingOrder.order_uid == order_uid)
            ).scalar_one_or_none()
            if row is None:
                return None
            session.expunge(row)
            return row

    def list(self, *, status: Optional[str], limit: int, offset: int) -> Dict[str, Any]:
        with self.db.get_session() as session:
            count_stmt = select(TradingOrder.id)
            stmt = select(TradingOrder)
            if status:
                count_stmt = count_stmt.where(TradingOrder.status == status)
                stmt = stmt.where(TradingOrder.status == status)
            total = len(session.execute(count_stmt).scalars().all())
            rows = session.execute(
                stmt.order_by(desc(TradingOrder.created_at), desc(TradingOrder.id))
                .offset(offset)
                .limit(limit)
            ).scalars().all()
            return {"total": total, "items": [self._payload(row) for row in rows]}

    def list_pending(self) -> List[TradingOrder]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(TradingOrder)
                .where(TradingOrder.status == "pending")
                .order_by(asc(TradingOrder.created_at), asc(TradingOrder.id))
            ).scalars().all()
            for row in rows:
                session.expunge(row)
            return rows

    def update(self, order_id: int, **fields: Any) -> TradingOrder:
        with self.db.get_session() as session:
            row = session.get(TradingOrder, order_id)
            if row is None:
                raise ValueError(f"Trading order not found: {order_id}")
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    @staticmethod
    def _payload(row: TradingOrder) -> Dict[str, Any]:
        return {
            "id": row.id,
            "order_uid": row.order_uid,
            "symbol": row.symbol,
            "symbol_name": row.symbol_name,
            "live_run_id": row.live_run_id,
            "rebalance_batch_id": row.rebalance_batch_id,
            "market": row.market,
            "instrument_type": row.instrument_type,
            "side": row.side,
            "quantity": row.quantity,
            "order_type": row.order_type,
            "limit_price": row.limit_price,
            "status": row.status,
            "qmt_order_id": row.qmt_order_id,
            "filled_quantity": row.filled_quantity,
            "filled_price": row.filled_price,
            "error_message": row.error_message,
            "source": row.source,
            "reason": row.reason,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
