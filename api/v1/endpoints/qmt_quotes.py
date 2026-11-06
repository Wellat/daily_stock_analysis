# -*- coding: utf-8 -*-
"""QMT 本地行情桥（Redis）状态与订阅管理端点。

订阅列表 ``qmt:cfg:symbols`` 的整组写回只应经由这些入口（单一写入者），
契约见 ``docs/qmt-quotes-link-01.md``。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from api.v1.schemas.qmt_quotes import QmtManualSymbolsRequest, QmtSettingsUpdateRequest
from src.services.qmt_quotes_service import QmtQuotesService, QmtQuotesServiceError

logger = logging.getLogger(__name__)

router = APIRouter()


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": "validation_error", "message": str(exc)},
    )


def _service_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"error": "qmt_bridge_unavailable", "message": str(exc)},
    )


@router.get(
    "/status",
    summary="Get QMT quotes-bridge status",
    description="Heartbeat aliveness, subscription counts and source toggles of the QMT local quotes bridge.",
)
def get_qmt_status() -> dict:
    try:
        return QmtQuotesService().get_status()
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Get QMT quotes status failed")
        raise _service_unavailable(exc) from exc


@router.get(
    "/subscription",
    summary="Get QMT subscription detail",
    description="Current Redis subscription list merged with holdings/alert/manual origin tags.",
)
def get_qmt_subscription() -> dict:
    try:
        return QmtQuotesService().get_subscription_detail()
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Get QMT subscription detail failed")
        raise _service_unavailable(exc) from exc


@router.get(
    "/data/quote",
    summary="Inspect raw QMT realtime snapshot",
    description="Return the raw quote JSON stored in Redis for one subscribed symbol (debug view, no unit conversion).",
)
def get_qmt_quote_data(symbol: str) -> dict:
    try:
        return QmtQuotesService().get_quote_detail(symbol)
    except QmtQuotesServiceError as exc:
        raise _bad_request(exc) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Get QMT quote data failed")
        raise _service_unavailable(exc) from exc


@router.get(
    "/data/daily",
    summary="Inspect raw QMT daily bars",
    description="Return the raw daily-bar JSON stored in Redis for one subscribed symbol (last 30 bars, no unit conversion).",
)
def get_qmt_daily_data(symbol: str) -> dict:
    try:
        return QmtQuotesService().get_daily_detail(symbol)
    except QmtQuotesServiceError as exc:
        raise _bad_request(exc) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Get QMT daily data failed")
        raise _service_unavailable(exc) from exc


@router.put(
    "/subscription/manual",
    summary="Replace manual QMT subscription symbols",
    description="Whole-replace the manual symbol list (persisted to QMT_MANUAL_SYMBOLS in .env).",
)
def update_qmt_manual_symbols(payload: QmtManualSymbolsRequest) -> dict:
    try:
        return QmtQuotesService.set_manual_symbols(payload.symbols)
    except QmtQuotesServiceError as exc:
        raise _bad_request(exc) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Update QMT manual symbols failed")
        raise _service_unavailable(exc) from exc


@router.post(
    "/subscription/sync",
    summary="Sync QMT subscription now",
    description="Merge holdings/alert/manual symbols and whole-replace qmt:cfg:symbols; returns the diff.",
)
def sync_qmt_subscription() -> dict:
    try:
        return QmtQuotesService().sync_subscription()
    except QmtQuotesServiceError as exc:
        raise _service_unavailable(exc) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Sync QMT subscription failed")
        raise _service_unavailable(exc) from exc


@router.put(
    "/settings",
    summary="Update QMT source toggles",
    description="Update QMT_QUOTES_ENABLED / QMT_AUTO_INCLUDE_HOLDINGS / QMT_INCLUDE_ALERT_SYMBOLS (persisted to .env).",
)
def update_qmt_settings(payload: QmtSettingsUpdateRequest) -> dict:
    if payload.enabled is None and payload.auto_include_holdings is None and payload.include_alert_symbols is None:
        raise _bad_request(ValueError("at least one setting is required"))
    try:
        QmtQuotesService.update_settings(
            enabled=payload.enabled,
            auto_include_holdings=payload.auto_include_holdings,
            include_alert_symbols=payload.include_alert_symbols,
        )
        return QmtQuotesService().get_status()
    except QmtQuotesServiceError as exc:
        raise _bad_request(exc) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Update QMT settings failed")
        raise _service_unavailable(exc) from exc
