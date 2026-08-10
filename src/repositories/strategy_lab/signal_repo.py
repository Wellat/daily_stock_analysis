# -*- coding: utf-8 -*-
"""Strategy Lab signal repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select

from src.storage import DatabaseManager, StrategyLabSignal


class StrategyLabSignalRepository:
    """Persist Strategy Lab signal records."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def create_signal(self, **fields: Any) -> StrategyLabSignal:
        with self.db.get_session() as session:
            row = StrategyLabSignal(
                **fields,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def get_signal(self, signal_id: int) -> Optional[StrategyLabSignal]:
        with self.db.get_session() as session:
            row = session.get(StrategyLabSignal, signal_id)
            if row is None:
                return None
            session.expunge(row)
            return row

    def mark_confirmed(
        self,
        *,
        signal_id: int,
        portfolio_account_id: int,
        portfolio_trade_id: int,
    ) -> StrategyLabSignal:
        with self.db.get_session() as session:
            row = session.get(StrategyLabSignal, signal_id)
            if row is None:
                raise ValueError(f"Strategy Lab signal not found: {signal_id}")
            row.portfolio_account_id = portfolio_account_id
            row.portfolio_trade_id = portfolio_trade_id
            row.status = "confirmed"
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def list_signals(self, *, limit: int, offset: int) -> Dict[str, Any]:
        with self.db.get_session() as session:
            total = session.execute(select(StrategyLabSignal.id)).scalars().all()
            rows = session.execute(
                select(StrategyLabSignal)
                .order_by(desc(StrategyLabSignal.created_at), desc(StrategyLabSignal.id))
                .offset(offset)
                .limit(limit)
            ).scalars().all()
            return {"total": len(total), "items": [self._payload(row) for row in rows]}

    @staticmethod
    def _payload(row: StrategyLabSignal) -> Dict[str, Any]:
        return {
            "id": row.id,
            "run_id": row.run_id,
            "portfolio_account_id": row.portfolio_account_id,
            "canonical_id": row.canonical_id,
            "symbol": row.symbol,
            "market": row.market,
            "instrument_type": row.instrument_type,
            "signal_type": row.signal_type,
            "suggested_action": row.suggested_action,
            "confidence": row.confidence,
            "reason": row.reason,
            "status": row.status,
            "portfolio_trade_id": row.portfolio_trade_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
