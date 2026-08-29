from __future__ import annotations
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from api.deps import get_database_manager
from api.v1.schemas.live_strategy import LiveStrategyConfigRequest, LiveStrategyRunRequest, LiveStrategyRunItem, LiveStrategyRunListResponse
from sqlalchemy import desc, select
from src.storage import LiveStrategyRun, LiveRebalanceBatch
from src.services.live_strategy_service import LiveStrategyService
from src.storage import DatabaseManager

router = APIRouter()

@router.get("/config")
def get_config(db_manager: DatabaseManager = Depends(get_database_manager)):
    return LiveStrategyService(db_manager).get_config() or {}

@router.put("/config")
def save_config(request: LiveStrategyConfigRequest, db_manager: DatabaseManager = Depends(get_database_manager)):
    try:
        return LiveStrategyService(db_manager).save_config(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})

@router.post("/runs/preview")
def preview_run(request: LiveStrategyRunRequest, db_manager: DatabaseManager = Depends(get_database_manager)):
    try:
        return LiveStrategyService(db_manager).run(trade_date=request.trade_date or date.today(), preview=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})

@router.post("/runs")
def run_strategy(request: LiveStrategyRunRequest, db_manager: DatabaseManager = Depends(get_database_manager)):
    try:
        return LiveStrategyService(db_manager).run(trade_date=request.trade_date or date.today())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})

@router.get("/runs", response_model=LiveStrategyRunListResponse)
def list_runs(db_manager: DatabaseManager = Depends(get_database_manager)):
    with db_manager.get_session() as session:
        rows = session.execute(select(LiveStrategyRun).order_by(desc(LiveStrategyRun.created_at)).limit(100)).scalars().all()
        return {"total": len(rows), "items": [LiveStrategyService._run_payload(row) for row in rows]}

@router.get("/strategies")
def list_strategies():
    from src.core.strategy_lab.engine import list_builtin_strategies
    return {"items": list_builtin_strategies()}

@router.get("/batches")
def list_batches(db_manager: DatabaseManager = Depends(get_database_manager)):
    with db_manager.get_session() as session:
        rows = session.execute(select(LiveRebalanceBatch).order_by(desc(LiveRebalanceBatch.created_at)).limit(100)).scalars().all()
        return {"total": len(rows), "items": [{"id": r.id, "batch_uid": r.batch_uid, "run_id": r.run_id, "qmt_account": r.qmt_account, "status": r.status, "summary": __import__('json').loads(r.summary_json or '{}'), "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]}
