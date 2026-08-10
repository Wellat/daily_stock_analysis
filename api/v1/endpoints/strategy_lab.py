# -*- coding: utf-8 -*-
"""Strategy Lab endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.deps import get_database_manager
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.strategy_lab import (
    StrategyLabBarListResponse,
    StrategyLabBatchCreateRequest,
    StrategyLabBatchItemResponse,
    StrategyLabBatchListResponse,
    StrategyLabDataSyncRequest,
    StrategyLabDataSyncResponse,
    StrategyLabEventListResponse,
    StrategyLabEventStudyRequest,
    StrategyLabEventStudyResponse,
    StrategyLabInstrumentDetailItem,
    StrategyLabInstrumentListResponse,
    StrategyLabRunCreateRequest,
    StrategyLabRunItem,
    StrategyLabRunListResponse,
    StrategyLabStrategyListResponse,
    StrategyLabSignalCreateRequest,
    StrategyLabSignalConfirmRequest,
    StrategyLabSignalItem,
    StrategyLabSignalListResponse,
    StrategyLabSyncRunListResponse,
    StrategyLabTradeListResponse,
)
from src.core.strategy_lab.models import StrategyLabRunConfig
from src.services.strategy_lab.batch_service import (
    StrategyLabBatchService,
    add_batch_listener,
    remove_batch_listener,
    set_batch_event_loop,
)
from src.services.strategy_lab.data_sync_service import StrategyLabDataSyncService
from src.services.strategy_lab.event_study_service import StrategyLabEventStudyService
from src.services.strategy_lab.signal_service import StrategyLabSignalService
from src.services.strategy_lab.service import StrategyLabService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/strategies",
    response_model=StrategyLabStrategyListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List Strategy Lab strategies",
)
def list_strategies(
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabStrategyListResponse:
    try:
        return StrategyLabStrategyListResponse(items=StrategyLabService(db_manager).list_strategies())
    except Exception as exc:
        logger.error("List Strategy Lab strategies failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "List strategies failed"})


@router.post(
    "/runs",
    response_model=StrategyLabRunItem,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Create and execute a Strategy Lab run",
)
def create_run(
    request: StrategyLabRunCreateRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabRunItem:
    try:
        config = StrategyLabRunConfig(
            strategy_id=request.strategy_id,
            market=request.market,
            instrument_type=request.instrument_type,
            start_date=request.start_date,
            end_date=request.end_date,
            initial_cash=request.initial_cash,
            benchmark_symbol=request.benchmark_symbol,
            symbols=request.symbols,
            parameters=request.parameters,
            portfolio_account_id=request.portfolio_account_id,
        )
        return StrategyLabRunItem(**StrategyLabService(db_manager).create_run(config))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})
    except Exception as exc:
        logger.error("Create Strategy Lab run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Create run failed"})


@router.get(
    "/runs",
    response_model=StrategyLabRunListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List Strategy Lab runs",
)
def list_runs(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabRunListResponse:
    try:
        return StrategyLabRunListResponse(**StrategyLabService(db_manager).list_runs(page=page, limit=limit))
    except Exception as exc:
        logger.error("List Strategy Lab runs failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "List runs failed"})


@router.get(
    "/runs/{run_id}",
    response_model=StrategyLabRunItem,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get one Strategy Lab run",
)
def get_run(
    run_id: int,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabRunItem:
    try:
        payload = StrategyLabService(db_manager).get_run(run_id)
        if payload is None:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Strategy Lab run not found"})
        return StrategyLabRunItem(**payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Get Strategy Lab run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Get run failed"})


@router.get(
    "/runs/{run_id}/trades",
    response_model=StrategyLabTradeListResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="List trades for one Strategy Lab run",
)
def list_run_trades(
    run_id: int,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabTradeListResponse:
    try:
        return StrategyLabTradeListResponse(
            run_id=run_id,
            items=StrategyLabService(db_manager).list_trades(run_id),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)})
    except Exception as exc:
        logger.error("List Strategy Lab trades failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "List trades failed"})


@router.post(
    "/data-sync",
    response_model=StrategyLabDataSyncResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Sync Strategy Lab convertible-bond data",
)
def sync_data(
    request: StrategyLabDataSyncRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabDataSyncResponse:
    try:
        service = StrategyLabDataSyncService(db_manager)
        if request.source == "fixture":
            return StrategyLabDataSyncResponse(**service.sync_fixture_convertible_bonds(market=request.market))
        if request.source in {"akshare", "jisilu", "opencli"}:
            return StrategyLabDataSyncResponse(
                **service.sync_provider_convertible_bonds(
                    market=request.market,
                    source=request.source,
                    symbols=request.symbols,
                )
            )
        if not any([request.cb_basic, request.cb_terms, request.cb_daily_factors, request.cb_events]):
            raise ValueError("payload data is required when source is not fixture")
        return StrategyLabDataSyncResponse(
            **service.sync_payload_convertible_bonds(
                market=request.market,
                source=request.source,
                cb_basic=request.cb_basic,
                cb_terms=request.cb_terms,
                cb_daily_factors=request.cb_daily_factors,
                cb_events=request.cb_events,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})
    except Exception as exc:
        logger.error("Sync Strategy Lab data failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Sync data failed"})


@router.get(
    "/data-sync/runs",
    response_model=StrategyLabSyncRunListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List Strategy Lab data-sync runs",
)
def list_sync_runs(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabSyncRunListResponse:
    try:
        return StrategyLabSyncRunListResponse(
            **StrategyLabDataSyncService(db_manager).list_sync_runs(page=page, limit=limit)
        )
    except Exception as exc:
        logger.error("List Strategy Lab sync runs failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "List sync runs failed"})


@router.get(
    "/instruments",
    response_model=StrategyLabInstrumentListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List Strategy Lab convertible-bond instruments",
)
def list_instruments(
    market: str = Query("cn", description="市场"),
    keyword: str | None = Query(None, description="代码/名称/正股代码模糊搜索"),
    status: str | None = Query(None, pattern="^(active|delisted)$", description="状态筛选：active=未退市 / delisted=已退市"),
    held_only: bool = Query(False, description="仅看 Portfolio 仍持有的标的"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabInstrumentListResponse:
    try:
        return StrategyLabInstrumentListResponse(
            **StrategyLabDataSyncService(db_manager).list_instruments(
                market=market,
                keyword=keyword,
                page=page,
                limit=limit,
                status=status,
                held_only=held_only,
            )
        )
    except Exception as exc:
        logger.error("List Strategy Lab instruments failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "List instruments failed"})


@router.get(
    "/instruments/{bond_code}",
    response_model=StrategyLabInstrumentDetailItem,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get one Strategy Lab convertible-bond instrument detail",
)
def get_instrument(
    bond_code: str,
    market: str = Query("cn", description="市场"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabInstrumentDetailItem:
    try:
        payload = StrategyLabDataSyncService(db_manager).get_instrument_detail(
            market=market,
            bond_code=bond_code,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Instrument not found"})
        return StrategyLabInstrumentDetailItem(**payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Get Strategy Lab instrument failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Get instrument failed"})


@router.get(
    "/instruments/{bond_code}/bars",
    response_model=StrategyLabBarListResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="List daily bars/factors for one Strategy Lab convertible-bond instrument",
)
def list_instrument_bars(
    bond_code: str,
    market: str = Query("cn", description="市场"),
    start_date: date | None = Query(None, description="起始日期"),
    end_date: date | None = Query(None, description="结束日期"),
    limit: int = Query(200, ge=1, le=2000),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabBarListResponse:
    try:
        payload = StrategyLabDataSyncService(db_manager).list_instrument_bars(
            market=market,
            bond_code=bond_code,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Instrument not found"})
        return StrategyLabBarListResponse(**payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("List Strategy Lab instrument bars failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "List bars failed"})


@router.get(
    "/instruments/{bond_code}/events",
    response_model=StrategyLabEventListResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="List events for one Strategy Lab convertible-bond instrument",
)
def list_instrument_events(
    bond_code: str,
    market: str = Query("cn", description="市场"),
    event_type: str | None = Query(None, description="事件类型筛选"),
    limit: int = Query(50, ge=1, le=500),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabEventListResponse:
    try:
        payload = StrategyLabDataSyncService(db_manager).list_instrument_events(
            market=market,
            bond_code=bond_code,
            event_type=event_type,
            limit=limit,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Instrument not found"})
        return StrategyLabEventListResponse(**payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("List Strategy Lab instrument events failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "List events failed"})


@router.post(
    "/studies/events",
    response_model=StrategyLabEventStudyResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Study returns around Strategy Lab convertible-bond events",
)
def study_events(
    request: StrategyLabEventStudyRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabEventStudyResponse:
    try:
        return StrategyLabEventStudyResponse(
            **StrategyLabEventStudyService(db_manager).study_convertible_bond_events(
                market=request.market,
                event_type=request.event_type,
                offsets=request.offsets,
                symbols=request.symbols,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})
    except Exception as exc:
        logger.error("Study Strategy Lab events failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Study events failed"})


@router.post(
    "/batches",
    response_model=StrategyLabBatchItemResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Run a Strategy Lab parameter batch",
)
def create_batch(
    request: StrategyLabBatchCreateRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabBatchItemResponse:
    try:
        return StrategyLabBatchItemResponse(
            **StrategyLabBatchService(db_manager).create_batch(
                strategy_id=request.strategy_id,
                market=request.market,
                instrument_type=request.instrument_type,
                base_config=request.base_config,
                parameter_grid=request.parameter_grid,
                run_async=request.run_async,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})
    except Exception as exc:
        logger.error("Create Strategy Lab batch failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Create batch failed"})


@router.get(
    "/batches",
    response_model=StrategyLabBatchListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List Strategy Lab batches",
)
def list_batches(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabBatchListResponse:
    try:
        return StrategyLabBatchListResponse(**StrategyLabBatchService(db_manager).list_batches(page=page, limit=limit))
    except Exception as exc:
        logger.error("List Strategy Lab batches failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "List batches failed"})


@router.get(
    "/batches/{batch_id}",
    response_model=StrategyLabBatchItemResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get one Strategy Lab batch",
)
def get_batch(
    batch_id: int,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabBatchItemResponse:
    try:
        payload = StrategyLabBatchService(db_manager).get_batch(batch_id)
        if payload is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_found", "message": "Strategy Lab batch not found"},
            )
        return StrategyLabBatchItemResponse(**payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Get Strategy Lab batch failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Get batch failed"})


@router.get(
    "/batches/{batch_id}/stream",
    responses={404: {"model": ErrorResponse}},
    summary="Stream Strategy Lab batch progress",
)
async def stream_batch(
    batch_id: int,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StreamingResponse:
    """Replay durable state, then stream in-process background batch events."""
    service = StrategyLabBatchService(db_manager)
    queue = add_batch_listener(batch_id)
    batch = service.get_batch(batch_id)
    if batch is None:
        remove_batch_listener(batch_id, queue)
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Strategy Lab batch not found"})

    set_batch_event_loop(asyncio.get_running_loop())
    replay = {
        "event": "progress",
        "batch_id": batch_id,
        "completed_tasks": batch["completed_tasks"],
        "total_tasks": batch["total_tasks"],
        "status": batch["status"],
    }

    async def event_generator():
        yield f"event: progress\ndata: {json.dumps(replay)}\n\n"
        if batch["status"] in {"completed", "partial_failed"}:
            done = {"event": "batch_done", **replay}
            yield f"event: batch_done\ndata: {json.dumps(done)}\n\n"
            return
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    latest = service.get_batch(batch_id)
                    if latest is None:
                        return
                    yield ": keepalive\n\n"
                    if latest["status"] in {"completed", "partial_failed"}:
                        done = {"event": "batch_done", "batch_id": batch_id, "status": latest["status"]}
                        yield f"event: batch_done\ndata: {json.dumps(done)}\n\n"
                        return
                    continue
                event_name = str(event.get("event", "progress"))
                yield f"event: {event_name}\ndata: {json.dumps(event)}\n\n"
                if event_name == "batch_done":
                    return
        finally:
            remove_batch_listener(batch_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/batches/{batch_id}/retry",
    response_model=StrategyLabBatchItemResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Retry failed Strategy Lab batch items",
)
def retry_batch(
    batch_id: int,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabBatchItemResponse:
    try:
        return StrategyLabBatchItemResponse(**StrategyLabBatchService(db_manager).retry_failed_items(batch_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})
    except Exception as exc:
        logger.error("Retry Strategy Lab batch failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Retry batch failed"})


@router.post(
    "/batches/{batch_id}/resume",
    response_model=StrategyLabBatchItemResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Resume incomplete Strategy Lab batch items",
)
def resume_batch(
    batch_id: int,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabBatchItemResponse:
    try:
        return StrategyLabBatchItemResponse(**StrategyLabBatchService(db_manager).resume_incomplete_items(batch_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})
    except Exception as exc:
        logger.error("Resume Strategy Lab batch failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Resume batch failed"})


@router.delete(
    "/batches/{batch_id}",
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Delete a Strategy Lab batch",
)
def delete_batch(
    batch_id: int,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> dict[str, bool]:
    try:
        if not StrategyLabBatchService(db_manager).repository.delete_batch(batch_id):
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Strategy Lab batch not found"})
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Delete Strategy Lab batch failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Delete batch failed"})


@router.post(
    "/signals",
    response_model=StrategyLabSignalItem,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Create a Strategy Lab signal from one run",
)
def create_signal(
    request: StrategyLabSignalCreateRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabSignalItem:
    try:
        return StrategyLabSignalItem(
            **StrategyLabSignalService(db_manager).create_from_run(
                run_id=request.run_id,
                portfolio_account_id=request.portfolio_account_id,
                suggested_action=request.suggested_action,
                signal_type=request.signal_type,
                confidence=request.confidence,
                reason=request.reason,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})
    except Exception as exc:
        logger.error("Create Strategy Lab signal failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Create signal failed"})


@router.get(
    "/signals",
    response_model=StrategyLabSignalListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List Strategy Lab signals",
)
def list_signals(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabSignalListResponse:
    try:
        return StrategyLabSignalListResponse(
            **StrategyLabSignalService(db_manager).list_signals(page=page, limit=limit)
        )
    except Exception as exc:
        logger.error("List Strategy Lab signals failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "List signals failed"})


@router.post(
    "/signals/{signal_id}/confirm",
    response_model=StrategyLabSignalItem,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Confirm a Strategy Lab signal into Portfolio trades",
)
def confirm_signal(
    signal_id: int,
    request: StrategyLabSignalConfirmRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StrategyLabSignalItem:
    try:
        return StrategyLabSignalItem(
            **StrategyLabSignalService(db_manager).confirm_signal_trade(
                signal_id=signal_id,
                portfolio_account_id=request.portfolio_account_id,
                trade_date=request.trade_date,
                quantity=request.quantity,
                price=request.price,
                side=request.side,
                fee=request.fee,
                tax=request.tax,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})
    except Exception as exc:
        logger.error("Confirm Strategy Lab signal failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Confirm signal failed"})
