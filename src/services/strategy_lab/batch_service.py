# -*- coding: utf-8 -*-
"""Strategy Lab batch backtest service."""

from __future__ import annotations

from itertools import product
import asyncio
import threading
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional
from uuid import uuid4

from src.core.strategy_lab.models import StrategyLabRunConfig
from src.core.strategy_lab.engine import list_builtin_strategies
from src.repositories.strategy_lab.batch_repo import StrategyLabBatchRepository
from src.services.strategy_lab.service import StrategyLabService
from src.storage import DatabaseManager


_batch_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="strategy_lab_batch")
_batch_event_loop: asyncio.AbstractEventLoop | None = None
_batch_listeners: dict[int, set[asyncio.Queue]] = {}
_batch_listener_lock = threading.Lock()


def set_batch_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _batch_event_loop
    _batch_event_loop = loop


def add_batch_listener(batch_id: int) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    with _batch_listener_lock:
        _batch_listeners.setdefault(batch_id, set()).add(queue)
    return queue


def remove_batch_listener(batch_id: int, queue: asyncio.Queue) -> None:
    with _batch_listener_lock:
        listeners = _batch_listeners.get(batch_id)
        if not listeners:
            return
        listeners.discard(queue)
        if not listeners:
            _batch_listeners.pop(batch_id, None)


def _broadcast_batch_event(batch_id: int, event: Dict[str, Any]) -> None:
    loop = _batch_event_loop
    if loop is None or not loop.is_running():
        return
    with _batch_listener_lock:
        listeners = list(_batch_listeners.get(batch_id, set()))
    for queue in listeners:
        loop.call_soon_threadsafe(queue.put_nowait, event)


class StrategyLabBatchService:
    """Run parameter sweeps for Strategy Lab."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.repository = StrategyLabBatchRepository(db_manager)
        self.run_service = StrategyLabService(db_manager)

    def create_batch(
        self,
        *,
        strategy_id: str,
        market: str,
        instrument_type: str,
        base_config: Dict[str, Any],
        parameter_grid: Dict[str, List[Any]],
        run_async: bool = False,
    ) -> Dict[str, Any]:
        strategy = self._get_strategy(strategy_id)
        combinations = self._expand_grid(parameter_grid)
        batch = self.repository.create_batch(
            batch_uid=uuid4().hex,
            strategy_id=strategy_id,
            strategy_name=strategy["name"],
            market=market,
            instrument_type=instrument_type,
            parameters_grid=combinations,
        )
        for params in combinations:
            merged = {**base_config}
            merged["parameters"] = {**merged.get("parameters", {}), **params}
            item = self.repository.add_item(
                batch_id=batch.id,
                parameters=merged,
                status="pending",
            )
        if run_async:
            _batch_executor.submit(self.execute_pending_items, batch.id)
        else:
            self.execute_pending_items(batch.id)
        return self.get_batch(batch.id) or {}

    def retry_failed_items(self, batch_id: int) -> Dict[str, Any]:
        if self.repository.get_batch(batch_id) is None:
            raise ValueError(f"Strategy Lab batch not found: {batch_id}")
        failed_items = self.repository.list_failed_items(batch_id)
        if not failed_items:
            return self.get_batch(batch_id) or {}
        for item in failed_items:
            parameters = dict(item["parameters"])
            parameters["_strategy_lab_batch_id"] = batch_id
            self._execute_item(item["id"], parameters)
        self.repository.refresh_batch_summary(batch_id)
        return self.get_batch(batch_id) or {}

    def resume_incomplete_items(self, batch_id: int) -> Dict[str, Any]:
        """Resume pending work and reclaim items left running by an interrupted worker."""
        if self.repository.get_batch(batch_id) is None:
            raise ValueError(f"Strategy Lab batch not found: {batch_id}")
        items = self.repository.reclaim_incomplete_items(batch_id)
        for item in items:
            parameters = dict(item["parameters"])
            parameters["_strategy_lab_batch_id"] = batch_id
            self._execute_item(item["id"], parameters)
        self.repository.refresh_batch_summary(batch_id)
        return self.get_batch(batch_id) or {}

    def _execute_item(self, item_id: int, merged: Dict[str, Any]) -> None:
        batch_id = int(merged.get("_strategy_lab_batch_id") or 0)
        self.repository.mark_item_running(item_id)
        if batch_id:
            _broadcast_batch_event(batch_id, {"event": "item_started", "item_id": item_id})
        try:
            payload = self.run_service.create_run(StrategyLabRunConfig(**self._normalize_run_config(merged)))
            self.repository.mark_item_completed(item_id, run_id=payload["id"])
            if batch_id:
                _broadcast_batch_event(batch_id, {"event": "item_done", "item_id": item_id, "run_id": payload["id"]})
        except Exception as exc:
            self.repository.mark_item_failed(item_id, error_message=str(exc))
            if batch_id:
                _broadcast_batch_event(batch_id, {"event": "item_failed", "item_id": item_id, "error": str(exc)})

    def execute_pending_items(self, batch_id: int) -> Dict[str, Any]:
        """Execute pending items and broadcast durable progress events."""
        batch = self.repository.get_batch(batch_id)
        if batch is None:
            raise ValueError(f"Strategy Lab batch not found: {batch_id}")
        items = self.repository.list_pending_items(batch_id)
        for item in items:
            parameters = dict(item["parameters"])
            parameters["_strategy_lab_batch_id"] = batch_id
            self._execute_item(item["id"], parameters)
            self.repository.refresh_batch_summary(batch_id)
            current = self.repository.get_batch(batch_id) or {}
            _broadcast_batch_event(batch_id, {
                "event": "progress",
                "completed_tasks": current.get("completed_tasks", 0),
                "total_tasks": current.get("total_tasks", 0),
                "status": current.get("status"),
            })
        final = self.repository.refresh_batch_summary(batch_id)
        payload = self.get_batch(batch_id) or {}
        if final.status in {"completed", "partial_failed"}:
            _broadcast_batch_event(batch_id, {
                "event": "batch_done",
                "batch_id": batch_id,
                "status": final.status,
                "completed_tasks": final.completed_tasks,
                "total_tasks": final.total_tasks,
            })
        return payload

    def get_batch(self, batch_id: int) -> Optional[Dict[str, Any]]:
        return self.repository.get_batch(batch_id)

    def list_batches(self, *, page: int, limit: int) -> Dict[str, Any]:
        offset = (page - 1) * limit
        payload = self.repository.list_batches(limit=limit, offset=offset)
        return {"page": page, "limit": limit, **payload}

    @staticmethod
    def _expand_grid(parameter_grid: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
        if not parameter_grid:
            return [{}]
        keys = list(parameter_grid)
        values = [parameter_grid[key] for key in keys]
        if any(not isinstance(value, list) or not value for value in values):
            raise ValueError("parameter_grid values must be non-empty lists")
        return [dict(zip(keys, combo, strict=True)) for combo in product(*values)]

    @staticmethod
    def _normalize_run_config(config: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(config)
        normalized.pop("_strategy_lab_batch_id", None)
        for field_name in ("start_date", "end_date"):
            value = normalized.get(field_name)
            if isinstance(value, str):
                normalized[field_name] = date.fromisoformat(value)
        return normalized

    @staticmethod
    def _get_strategy(strategy_id: str) -> Dict[str, Any]:
        for strategy in list_builtin_strategies():
            if strategy["strategy_id"] == strategy_id:
                return strategy
        raise ValueError(f"Unsupported strategy_id: {strategy_id}")
