# -*- coding: utf-8 -*-
"""
===================================
市场数据接口
===================================

职责：
1. GET /api/v1/market/overview 市场行情数据
2. GET /api/v1/market/news 市场新闻数据
"""

import logging

from fastapi import APIRouter, Query

from api.v1.errors import api_error
from api.v1.schemas.market import (
    AllMarketNewsResponse,
    AllMarketOverviewResponse,
    MarketNewsResponse,
    MarketOverviewResponse,
)
from src.services import market_data_service

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_REGIONS = ("cn", "us", "hk")


def _validate_region(region: str) -> str:
    """验证并标准化 region 参数。"""
    region = region.lower().strip()
    if region not in _VALID_REGIONS:
        raise api_error(
            status_code=400,
            error="invalid_region",
            message=f"region must be one of {', '.join(_VALID_REGIONS)}",
        )
    return region


@router.get(
    "/overview",
    response_model=MarketOverviewResponse | AllMarketOverviewResponse,
    summary="获取市场行情数据",
    description="返回指定区域或所有区域的指数行情、涨跌统计和板块排行",
    responses={
        400: {"description": "无效的 region 参数"},
        503: {"description": "数据源不可用"},
    },
)
def get_market_overview(
    region: str = Query("cn", description="市场区域 cn/us/hk"),
    all: bool = Query(False, description="是否返回所有区域数据"),
):
    if all:
        regions_data = market_data_service.get_all_regions_overview()
        return AllMarketOverviewResponse(regions=[
            MarketOverviewResponse(**r) for r in regions_data
        ])

    region = _validate_region(region)
    try:
        data = market_data_service.get_market_overview(region)
    except Exception:
        logger.exception("market endpoint action=get_overview region=%s status=error", region)
        raise api_error(
            status_code=503,
            error="data_source_unavailable",
            message=f"Failed to fetch market data for region {region}",
        )
    return MarketOverviewResponse(**data)


@router.get(
    "/news",
    response_model=MarketNewsResponse | AllMarketNewsResponse,
    summary="获取市场新闻",
    description="返回指定区域或所有区域的市场新闻，含来源信息",
    responses={
        400: {"description": "无效的 region 参数"},
    },
)
def get_market_news(
    region: str = Query("cn", description="市场区域 cn/us/hk"),
    all: bool = Query(False, description="是否返回所有区域数据"),
    limit: int = Query(10, ge=1, le=50, description="每区域返回条数上限"),
):
    if all:
        regions_data = market_data_service.get_all_regions_news(limit=limit)
        return AllMarketNewsResponse(regions=[
            MarketNewsResponse(**r) for r in regions_data
        ])

    region = _validate_region(region)
    data = market_data_service.get_market_news(region, limit=limit)
    return MarketNewsResponse(**data)
