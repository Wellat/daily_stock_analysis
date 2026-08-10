# -*- coding: utf-8 -*-
"""Strategy Lab batch repository."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select

from src.storage import DatabaseManager, StrategyLabBatch, StrategyLabBatchItem


class StrategyLabBatchRepository:
    """Persist batch backtest state."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def create_batch(
        self,
        *,
        batch_uid: str,
        strategy_id: str,
        strategy_name: str,
        market: str,
        instrument_type: str,
        parameters_grid: List[Dict[str, Any]],
    ) -> StrategyLabBatch:
        with self.db.get_session() as session:
            row = StrategyLabBatch(
                batch_uid=batch_uid,
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                market=market,
                instrument_type=instrument_type,
                status="running",
                total_tasks=len(parameters_grid),
                parameters_grid_json=json.dumps(parameters_grid, ensure_ascii=False, sort_keys=True),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def add_item(
        self,
        *,
        batch_id: int,
        parameters: Dict[str, Any],
        status: str,
        run_id: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> StrategyLabBatchItem:
        with self.db.get_session() as session:
            row = StrategyLabBatchItem(
                batch_id=batch_id,
                run_id=run_id,
                parameters_json=json.dumps(parameters, ensure_ascii=False, sort_keys=True, default=str),
                status=status,
                error_message=error_message,
                completed_at=datetime.now() if status in {"completed", "failed"} else None,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def mark_item_running(self, item_id: int) -> StrategyLabBatchItem:
        with self.db.get_session() as session:
            row = session.get(StrategyLabBatchItem, item_id)
            if row is None:
                raise ValueError(f"Strategy Lab batch item not found: {item_id}")
            row.status = "running"
            row.error_message = None
            row.completed_at = None
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def mark_item_completed(self, item_id: int, *, run_id: int) -> StrategyLabBatchItem:
        with self.db.get_session() as session:
            row = session.get(StrategyLabBatchItem, item_id)
            if row is None:
                raise ValueError(f"Strategy Lab batch item not found: {item_id}")
            row.run_id = run_id
            row.status = "completed"
            row.error_message = None
            row.completed_at = datetime.now()
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def mark_item_failed(self, item_id: int, *, error_message: str) -> StrategyLabBatchItem:
        with self.db.get_session() as session:
            row = session.get(StrategyLabBatchItem, item_id)
            if row is None:
                raise ValueError(f"Strategy Lab batch item not found: {item_id}")
            row.status = "failed"
            row.error_message = error_message
            row.completed_at = datetime.now()
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def list_failed_items(self, batch_id: int) -> List[Dict[str, Any]]:
        return self._list_items_by_status(batch_id, {"failed"})

    def list_pending_items(self, batch_id: int) -> List[Dict[str, Any]]:
        return self._list_items_by_status(batch_id, {"pending"})

    def delete_batch(self, batch_id: int) -> bool:
        with self.db.get_session() as session:
            row = session.get(StrategyLabBatch, batch_id)
            if row is None:
                return False
            session.query(StrategyLabBatchItem).filter(
                StrategyLabBatchItem.batch_id == batch_id
            ).delete(synchronize_session=False)
            session.delete(row)
            session.commit()
            return True

    def reclaim_incomplete_items(self, batch_id: int) -> List[Dict[str, Any]]:
        """Make explicit operator recovery safe after a process interruption."""
        with self.db.get_session() as session:
            rows = session.execute(
                select(StrategyLabBatchItem).where(
                    StrategyLabBatchItem.batch_id == batch_id,
                    StrategyLabBatchItem.status.in_(("pending", "running")),
                )
            ).scalars().all()
            for row in rows:
                row.status = "pending"
                row.error_message = None
                row.completed_at = None
            session.commit()
        return self._list_items_by_status(batch_id, {"pending"})

    def _list_items_by_status(self, batch_id: int, statuses: set[str]) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(StrategyLabBatchItem)
                .where(
                    StrategyLabBatchItem.batch_id == batch_id,
                    StrategyLabBatchItem.status.in_(tuple(statuses)),
                )
                .order_by(StrategyLabBatchItem.id.asc())
            ).scalars().all()
            return [
                {
                    "id": row.id,
                    "parameters": json.loads(row.parameters_json) if row.parameters_json else {},
                }
                for row in rows
            ]

    def complete_batch(self, batch_id: int, *, summary: Dict[str, Any]) -> StrategyLabBatch:
        with self.db.get_session() as session:
            row = session.get(StrategyLabBatch, batch_id)
            if row is None:
                raise ValueError(f"Strategy Lab batch not found: {batch_id}")
            row.completed_tasks = int(summary.get("completed_tasks") or 0)
            row.success_tasks = int(summary.get("success_tasks") or 0)
            row.failed_tasks = int(summary.get("failed_tasks") or 0)
            row.status = "completed" if row.failed_tasks == 0 else "partial_failed"
            row.summary_json = json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str)
            row.completed_at = datetime.now()
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def refresh_batch_summary(self, batch_id: int) -> StrategyLabBatch:
        with self.db.get_session() as session:
            row = session.get(StrategyLabBatch, batch_id)
            if row is None:
                raise ValueError(f"Strategy Lab batch not found: {batch_id}")
            items = session.execute(
                select(StrategyLabBatchItem).where(StrategyLabBatchItem.batch_id == batch_id)
            ).scalars().all()
            completed = sum(1 for item in items if item.status in {"completed", "failed"})
            success = sum(1 for item in items if item.status == "completed")
            failed = sum(1 for item in items if item.status == "failed")
            running = sum(1 for item in items if item.status == "running")
            row.completed_tasks = completed
            row.success_tasks = success
            row.failed_tasks = failed
            if running:
                row.status = "running"
                row.completed_at = None
            elif completed >= row.total_tasks:
                row.status = "completed" if failed == 0 else "partial_failed"
                row.completed_at = datetime.now()
            else:
                row.status = "pending"
                row.completed_at = None
            row.summary_json = json.dumps(
                {
                    "completed_tasks": completed,
                    "success_tasks": success,
                    "failed_tasks": failed,
                    "running_tasks": running,
                    "pending_tasks": max(0, row.total_tasks - completed - running),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def get_batch(self, batch_id: int) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.get(StrategyLabBatch, batch_id)
            if row is None:
                return None
            items = session.execute(
                select(StrategyLabBatchItem)
                .where(StrategyLabBatchItem.batch_id == batch_id)
                .order_by(StrategyLabBatchItem.id.asc())
            ).scalars().all()
            return self._batch_payload(row, items)

    def list_batches(self, *, limit: int, offset: int) -> Dict[str, Any]:
        with self.db.get_session() as session:
            total = session.execute(select(StrategyLabBatch.id)).scalars().all()
            rows = session.execute(
                select(StrategyLabBatch)
                .order_by(desc(StrategyLabBatch.created_at), desc(StrategyLabBatch.id))
                .offset(offset)
                .limit(limit)
            ).scalars().all()
            return {
                "total": len(total),
                "items": [self._batch_summary_payload(row) for row in rows],
            }

    def _batch_payload(self, row: StrategyLabBatch, items: List[StrategyLabBatchItem]) -> Dict[str, Any]:
        payload = self._batch_summary_payload(row)
        payload["parameters_grid"] = json.loads(row.parameters_grid_json) if row.parameters_grid_json else []
        payload["summary"] = json.loads(row.summary_json) if row.summary_json else {}
        payload["items"] = [
            {
                "id": item.id,
                "batch_id": item.batch_id,
                "run_id": item.run_id,
                "parameters": json.loads(item.parameters_json) if item.parameters_json else {},
                "status": item.status,
                "error_message": item.error_message,
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "completed_at": item.completed_at.isoformat() if item.completed_at else None,
            }
            for item in items
        ]
        return payload

    @staticmethod
    def _batch_summary_payload(row: StrategyLabBatch) -> Dict[str, Any]:
        return {
            "id": row.id,
            "batch_uid": row.batch_uid,
            "strategy_id": row.strategy_id,
            "strategy_name": row.strategy_name,
            "market": row.market,
            "instrument_type": row.instrument_type,
            "status": row.status,
            "total_tasks": row.total_tasks,
            "completed_tasks": row.completed_tasks,
            "success_tasks": row.success_tasks,
            "failed_tasks": row.failed_tasks,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
