# -*- coding: utf-8 -*-
"""Strategy Lab batch tests."""

from __future__ import annotations

import asyncio
import threading
from datetime import date

import pytest

from src.services.strategy_lab.batch_service import (
    StrategyLabBatchService,
    add_batch_listener,
    remove_batch_listener,
    set_batch_event_loop,
)
from src.services.strategy_lab.data_sync_service import StrategyLabDataSyncService
from src.storage import DatabaseManager


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def test_batch_service_runs_parameter_grid(db_manager: DatabaseManager) -> None:
    StrategyLabDataSyncService(db_manager).sync_fixture_convertible_bonds()
    service = StrategyLabBatchService(db_manager)

    payload = service.create_batch(
        strategy_id="double-low",
        market="cn",
        instrument_type="convertible_bond",
        base_config={
            "strategy_id": "double-low",
            "market": "cn",
            "instrument_type": "convertible_bond",
            "start_date": date(2024, 1, 2),
            "end_date": date(2024, 1, 4),
            "initial_cash": 100000,
        },
        parameter_grid={"max_positions": [1, 2]},
    )

    assert payload["total_tasks"] == 2
    assert payload["completed_tasks"] == 2
    assert len(payload["items"]) == 2


class _FlakyRunService:
    def __init__(self) -> None:
        self.calls = 0

    def create_run(self, config) -> dict:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("first run failed")
        return {"id": self.calls}


def test_batch_service_retries_failed_items(db_manager: DatabaseManager) -> None:
    service = StrategyLabBatchService(db_manager)
    service.run_service = _FlakyRunService()

    payload = service.create_batch(
        strategy_id="double-low",
        market="cn",
        instrument_type="convertible_bond",
        base_config={
            "strategy_id": "double-low",
            "market": "cn",
            "instrument_type": "convertible_bond",
            "start_date": date(2024, 1, 2),
            "end_date": date(2024, 1, 4),
            "initial_cash": 100000,
        },
        parameter_grid={"max_positions": [1, 2]},
    )

    assert payload["status"] == "partial_failed"
    assert payload["completed_tasks"] == 2
    assert payload["success_tasks"] == 1
    assert payload["failed_tasks"] == 1
    assert [item["status"] for item in payload["items"]] == ["failed", "completed"]

    retried = service.retry_failed_items(payload["id"])

    assert retried["status"] == "completed"
    assert retried["success_tasks"] == 2
    assert retried["failed_tasks"] == 0
    assert [item["status"] for item in retried["items"]] == ["completed", "completed"]


def test_batch_service_resumes_item_left_running_after_interruption(db_manager: DatabaseManager) -> None:
    service = StrategyLabBatchService(db_manager)
    service.run_service = _FlakyRunService()
    payload = service.create_batch(
        strategy_id="double-low",
        market="cn",
        instrument_type="convertible_bond",
        base_config={
            "strategy_id": "double-low",
            "market": "cn",
            "instrument_type": "convertible_bond",
            "start_date": date(2024, 1, 2),
            "end_date": date(2024, 1, 4),
            "initial_cash": 100000,
        },
        parameter_grid={"max_positions": [1]},
    )
    item_id = payload["items"][0]["id"]
    service.repository.mark_item_running(item_id)

    resumed = service.resume_incomplete_items(payload["id"])

    assert resumed["status"] == "completed"
    assert resumed["items"][0]["status"] == "completed"


def test_batch_service_rejects_empty_parameter_axis(db_manager: DatabaseManager) -> None:
    service = StrategyLabBatchService(db_manager)

    with pytest.raises(ValueError, match="non-empty lists"):
        service.create_batch(
            strategy_id="double-low",
            market="cn",
            instrument_type="convertible_bond",
            base_config={},
            parameter_grid={"max_positions": []},
        )


def test_batch_service_deletes_batch_and_items(db_manager: DatabaseManager) -> None:
    service = StrategyLabBatchService(db_manager)
    payload = service.create_batch(
        strategy_id="double-low",
        market="cn",
        instrument_type="convertible_bond",
        base_config={
            "strategy_id": "double-low",
            "market": "cn",
            "instrument_type": "convertible_bond",
            "start_date": date(2024, 1, 2),
            "end_date": date(2024, 1, 4),
            "initial_cash": 100000,
        },
        parameter_grid={"max_positions": [1]},
    )

    assert service.repository.delete_batch(payload["id"]) is True
    assert service.get_batch(payload["id"]) is None


class _BlockingRunService:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def create_run(self, config) -> dict:
        self.calls += 1
        self.started.set()
        assert self.release.wait(timeout=2)
        return {"id": self.calls}


def test_async_batch_persists_progress_and_broadcasts_sse_events(db_manager: DatabaseManager) -> None:
    async def observe() -> None:
        set_batch_event_loop(asyncio.get_running_loop())
        service = StrategyLabBatchService(db_manager)
        blocking_service = _BlockingRunService()
        service.run_service = blocking_service
        payload = service.create_batch(
            strategy_id="double-low",
            market="cn",
            instrument_type="convertible_bond",
            base_config={
                "strategy_id": "double-low",
                "market": "cn",
                "instrument_type": "convertible_bond",
                "start_date": date(2024, 1, 2),
                "end_date": date(2024, 1, 4),
                "initial_cash": 100000,
            },
            parameter_grid={"max_positions": [1]},
            run_async=True,
        )
        assert payload["status"] in {"pending", "running"}
        assert await asyncio.to_thread(blocking_service.started.wait, 1)
        queue = add_batch_listener(payload["id"])
        try:
            blocking_service.release.set()
            events = [await asyncio.wait_for(queue.get(), timeout=2) for _ in range(3)]
        finally:
            remove_batch_listener(payload["id"], queue)
        assert [event["event"] for event in events] == ["item_done", "progress", "batch_done"]
        completed = service.get_batch(payload["id"])
        assert completed is not None
        assert completed["status"] == "completed"
        assert completed["success_tasks"] == 1

    asyncio.run(observe())
