# -*- coding: utf-8 -*-
"""可转债实盘交易指令 API 端点（QMT 对接）。"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from api.deps import get_database_manager
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.trading import (
    QmtPositionListResponse,
    QmtPositionReportRequest,
    QmtPositionReportResponse,
    TradingOrderCallbackRequest,
    TradingOrderCreateRequest,
    TradingOrderItem,
    TradingOrderListResponse,
    TradingOrderPendingListResponse,
)
from src.services.qmt_position_service import QmtPositionService
from src.services.trading_order_service import (
    TradingOrderNotFoundError,
    TradingOrderService,
)
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()

QMT_TOKEN_HEADER = "X-QMT-Token"


def _qmt_token_valid(token: Optional[str]) -> bool:
    expected = (os.getenv("QMT_API_TOKEN") or "").strip()
    if not expected:
        return True
    return bool(token) and secrets.compare_digest(token, expected)


def require_qmt_token(
    x_qmt_token: Optional[str] = Header(None, alias=QMT_TOKEN_HEADER),
) -> None:
    """校验 QMT 回调/拉取 token；未配置 QMT_API_TOKEN 时放行。"""
    if not _qmt_token_valid(x_qmt_token):
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Invalid QMT token"},
        )


@router.post(
    "/orders",
    response_model=TradingOrderItem,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="创建可转债待交易指令",
)
def create_order(
    request: TradingOrderCreateRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> TradingOrderItem:
    try:
        return TradingOrderItem(
            **TradingOrderService(db_manager).create_order(
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                order_type=request.order_type,
                limit_price=request.limit_price,
                source=request.source,
                reason=request.reason,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})
    except Exception as exc:
        logger.error("Create trading order failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Create order failed"})


@router.get(
    "/orders",
    response_model=TradingOrderListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="分页查询可转债交易指令",
)
def list_orders(
    status: Optional[str] = Query(None, description="可选状态过滤"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> TradingOrderListResponse:
    try:
        return TradingOrderListResponse(
            **TradingOrderService(db_manager).list_orders(page=page, limit=limit, status=status)
        )
    except Exception as exc:
        logger.error("List trading orders failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "List orders failed"})


@router.post(
    "/orders/{order_id}/cancel",
    response_model=TradingOrderItem,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="取消待执行的交易指令",
)
def cancel_order(
    order_id: int,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> TradingOrderItem:
    try:
        return TradingOrderItem(**TradingOrderService(db_manager).cancel_order(order_id))
    except TradingOrderNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})
    except Exception as exc:
        logger.error("Cancel trading order failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Cancel order failed"})


@router.get(
    "/qmt/pending",
    response_model=TradingOrderPendingListResponse,
    dependencies=[Depends(require_qmt_token)],
    responses={401: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="QMT 拉取待执行交易指令",
)
def list_pending(
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> TradingOrderPendingListResponse:
    try:
        return TradingOrderPendingListResponse(**TradingOrderService(db_manager).list_pending())
    except Exception as exc:
        logger.error("List pending trading orders failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "List pending orders failed"})


@router.post(
    "/qmt/orders/{order_id}/callback",
    response_model=TradingOrderItem,
    dependencies=[Depends(require_qmt_token)],
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="QMT 回写交易执行结果",
)
def callback_order(
    order_id: int,
    request: TradingOrderCallbackRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> TradingOrderItem:
    try:
        return TradingOrderItem(
            **TradingOrderService(db_manager).apply_callback(
                order_id=order_id,
                status=request.status,
                qmt_order_id=request.qmt_order_id,
                filled_quantity=request.filled_quantity,
                filled_price=request.filled_price,
                error_message=request.error_message,
            )
        )
    except TradingOrderNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})
    except Exception as exc:
        logger.error("Trading order callback failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Callback failed"})


@router.post(
    "/qmt/positions",
    response_model=QmtPositionReportResponse,
    dependencies=[Depends(require_qmt_token)],
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="QMT 上报账户持仓",
)
def report_positions(
    request: QmtPositionReportRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> QmtPositionReportResponse:
    try:
        return QmtPositionReportResponse(
            **QmtPositionService(db_manager).report_positions(
                account=request.account,
                positions=[p.model_dump() for p in request.positions],
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "message": str(exc)})
    except Exception as exc:
        logger.error("Report QMT positions failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Report positions failed"})


@router.get(
    "/positions",
    response_model=QmtPositionListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="查询账户持仓",
)
def list_positions(
    account: Optional[str] = Query(None, description="可选按资金账号过滤"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> QmtPositionListResponse:
    try:
        return QmtPositionListResponse(**QmtPositionService(db_manager).list_positions(account=account))
    except Exception as exc:
        logger.error("List QMT positions failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "List positions failed"})
