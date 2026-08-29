# -*- coding: utf-8 -*-
"""QMT 上报持仓数据访问层。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select

from src.storage import DatabaseManager, QmtPosition


class QmtPositionRepository:
    """Persist QMT-reported account positions."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def replace(
        self,
        *,
        account: str,
        positions: List[Dict[str, Any]],
    ) -> int:
        """全量替换某账户持仓：删除本次未上报的旧记录，再 upsert 本次上报。

        在单个事务内完成，避免并发上报产生中间态。
        返回本次上报的条数。
        """
        with self.db.get_session() as session:
            existing = session.execute(
                select(QmtPosition).where(QmtPosition.account == account)
            ).scalars().all()

            reported_symbols = {str(item["symbol"]) for item in positions}
            stale_ids = [row.id for row in existing if row.symbol not in reported_symbols]
            if stale_ids:
                session.execute(
                    delete(QmtPosition).where(QmtPosition.id.in_(stale_ids))
                )

            for item in positions:
                symbol = str(item["symbol"])
                row = next((r for r in existing if r.symbol == symbol), None)
                if row is None:
                    row = QmtPosition(
                        account=account,
                        symbol=symbol,
                        name=item.get("name"),
                        volume=float(item["volume"]),
                        can_use_volume=float(item["can_use_volume"]),
                        open_price=item.get("open_price"),
                        float_profit=item.get("float_profit"),
                        created_at=datetime.now(),
                        updated_at=datetime.now(),
                    )
                    session.add(row)
                else:
                    if item.get("name") is not None:
                        row.name = item["name"]
                    row.volume = float(item["volume"])
                    row.can_use_volume = float(item["can_use_volume"])
                    row.open_price = item.get("open_price")
                    row.float_profit = item.get("float_profit")
                    row.updated_at = datetime.now()

            session.commit()
            return len(positions)

    def list(self, account: Optional[str] = None) -> List[QmtPosition]:
        with self.db.get_session() as session:
            stmt = select(QmtPosition)
            if account:
                stmt = stmt.where(QmtPosition.account == account)
            rows = session.execute(
                stmt.order_by(QmtPosition.account, QmtPosition.symbol)
            ).scalars().all()
            for row in rows:
                session.expunge(row)
            return rows

    @staticmethod
    def _payload(row: QmtPosition) -> Dict[str, Any]:
        return {
            "id": row.id,
            "account": row.account,
            "symbol": row.symbol,
            "name": row.name,
            "volume": row.volume,
            "can_use_volume": row.can_use_volume,
            "open_price": row.open_price,
            "float_profit": row.float_profit,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
