from __future__ import annotations
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from api.deps import get_database_manager
from api.v1.schemas.live_strategy import LiveStrategyConfigRequest, LiveStrategyRunRequest, LiveStrategyRunItem, LiveStrategyRunListResponse
from sqlalchemy import desc, select
from src.storage import LiveStrategyRun, LiveRebalanceBatch, StrategyDecisionRecord
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
        return LiveStrategyService(db_manager).run(trade_date=request.trade_date or date.today(), preview=True, mode=request.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})

@router.post("/runs")
def run_strategy(request: LiveStrategyRunRequest, db_manager: DatabaseManager = Depends(get_database_manager)):
    try:
        return LiveStrategyService(db_manager).run(trade_date=request.trade_date or date.today(), mode=request.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})

@router.get("/runs", response_model=LiveStrategyRunListResponse)
def list_runs(db_manager: DatabaseManager = Depends(get_database_manager)):
    with db_manager.get_session() as session:
        rows = session.execute(select(LiveStrategyRun).order_by(desc(LiveStrategyRun.created_at)).limit(100)).scalars().all()
        return {"total": len(rows), "items": [LiveStrategyService._run_payload(row) for row in rows]}

@router.get("/runs/{run_id}")
def get_run(run_id: int, db_manager: DatabaseManager = Depends(get_database_manager)):
    with db_manager.get_session() as session:
        row = session.get(LiveStrategyRun, run_id)
        if row is None: raise HTTPException(status_code=404, detail="run not found")
        return LiveStrategyService._run_payload(row)

@router.get("/runs/{run_id}/rebalance")
def get_run_rebalance(run_id: int, db_manager: DatabaseManager = Depends(get_database_manager)):
    with db_manager.get_session() as session:
        row = session.get(LiveStrategyRun, run_id)
        if row is None: raise HTTPException(status_code=404, detail="run not found")
        import json
        return {"run_id": run_id, "items": json.loads(row.rebalance_json or "[]")}

@router.get("/runs/{run_id}/orders")
def get_run_orders(run_id: int, db_manager: DatabaseManager = Depends(get_database_manager)):
    from src.storage import TradingOrder
    with db_manager.get_session() as session:
        rows = session.execute(select(TradingOrder).where(TradingOrder.live_run_id == run_id).order_by(TradingOrder.id)).scalars().all()
        return {"total": len(rows), "items": [{"id": r.id, "order_uid": r.order_uid, "symbol": r.symbol, "symbol_name": r.symbol_name, "side": r.side, "quantity": r.quantity, "status": r.status, "decision_id": r.decision_id, "qmt_order_id": r.qmt_order_id} for r in rows]}

@router.get("/strategies")
def list_strategies():
    from src.core.strategy_lab.engine import list_builtin_strategies
    return {"items": list_builtin_strategies()}

@router.get("/data-sync/status")
def data_sync_status(db_manager: DatabaseManager = Depends(get_database_manager)):
    from src.repositories.strategy_lab.data_repo import StrategyLabDataRepository
    today = date.today()
    repo = StrategyLabDataRepository(db_manager)
    return {"trade_date": today.isoformat(), "intraday": repo._sync_run_payload(repo.latest_sync_run(run_kind="intraday", trade_date=today)) if repo.latest_sync_run(run_kind="intraday", trade_date=today) else None,
            "after_close": repo._sync_run_payload(repo.latest_sync_run(run_kind="after_close", trade_date=today)) if repo.latest_sync_run(run_kind="after_close", trade_date=today) else None}

@router.get("/strategies/{strategy_id}")
def get_strategy_metadata(strategy_id: str):
    from src.core.strategy_lab.engine import list_builtin_strategies
    item = next((x for x in list_builtin_strategies() if x["strategy_id"] == strategy_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    return item

@router.get("/runs/{run_id}/decisions")
def list_run_decisions(run_id: int, db_manager: DatabaseManager = Depends(get_database_manager)):
    with db_manager.get_session() as session:
        rows = session.execute(select(StrategyDecisionRecord).where(StrategyDecisionRecord.live_run_id == run_id).order_by(StrategyDecisionRecord.id)).scalars().all()
        import json
        return {"total": len(rows), "items": [{"id": r.id, "decision_uid": r.decision_uid, "strategy_id": r.strategy_id, "strategy_version": r.strategy_version, "mode": r.mode, "action": r.action, "symbol": r.symbol, "symbol_name": r.symbol_name, "target_weight": r.target_weight, "target_amount": r.target_amount, "suggested_quantity": r.suggested_quantity, "reason": r.reason, "decision_data": json.loads(r.decision_data_json or '{}'), "risk_status": r.risk_status} for r in rows]}

@router.get("/batches")
def list_batches(db_manager: DatabaseManager = Depends(get_database_manager)):
    with db_manager.get_session() as session:
        rows = session.execute(select(LiveRebalanceBatch).order_by(desc(LiveRebalanceBatch.created_at)).limit(100)).scalars().all()
        return {"total": len(rows), "items": [{"id": r.id, "batch_uid": r.batch_uid, "run_id": r.run_id, "qmt_account": r.qmt_account, "status": r.status, "summary": __import__('json').loads(r.summary_json or '{}'), "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]}
