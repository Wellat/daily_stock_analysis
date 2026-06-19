# -*- coding: utf-8 -*-
"""
===================================
市场数据服务
===================================

职责：
1. 封装 MarketAnalyzer，提供纯数据获取能力
2. 不触发 LLM 分析和通知
"""

import logging
from typing import Any, Dict, List

from src.market_analyzer import MarketAnalyzer, MarketIndex, MarketOverview

logger = logging.getLogger(__name__)

_VALID_REGIONS = ("cn", "us", "hk")


def _market_index_to_dict(idx: MarketIndex) -> Dict[str, Any]:
    """将 MarketIndex dataclass 转为 dict，包含 prev_close。"""
    return {
        "code": idx.code,
        "name": idx.name,
        "current": idx.current,
        "change": idx.change,
        "change_pct": idx.change_pct,
        "open": idx.open,
        "high": idx.high,
        "low": idx.low,
        "prev_close": idx.prev_close,
        "volume": idx.volume,
        "amount": idx.amount,
        "amplitude": idx.amplitude,
    }


def _overview_to_dict(region: str, overview: MarketOverview) -> Dict[str, Any]:
    """将 MarketOverview dataclass 转为 API 响应 dict。"""
    return {
        "region": region,
        "date": overview.date,
        "indices": [_market_index_to_dict(idx) for idx in overview.indices],
        "stats": {
            "up_count": overview.up_count,
            "down_count": overview.down_count,
            "flat_count": overview.flat_count,
            "limit_up_count": overview.limit_up_count,
            "limit_down_count": overview.limit_down_count,
            "total_amount": overview.total_amount,
        },
        "top_sectors": overview.top_sectors,
        "bottom_sectors": overview.bottom_sectors,
    }


def _serialize_news_item(item: Any) -> Dict[str, str]:
    """将 SearchResult 对象或 dict 序列化为 API 响应 dict。"""
    if hasattr(item, "title"):
        return {
            "title": getattr(item, "title", "") or "",
            "summary": getattr(item, "snippet", "") or "",
            "source": getattr(item, "source", "") or "",
            "url": getattr(item, "url", "") or "",
            "published_at": getattr(item, "published_date", "") or "",
        }
    if isinstance(item, dict):
        return {
            "title": item.get("title", ""),
            "summary": item.get("snippet", item.get("summary", "")),
            "source": item.get("source", ""),
            "url": item.get("url", ""),
            "published_at": item.get("published_date", item.get("published_at", "")),
        }
    return {"title": "", "summary": "", "source": "", "url": "", "published_at": ""}


def get_market_overview(region: str) -> Dict[str, Any]:
    """
    获取单个区域的市场行情数据。

    Args:
        region: 市场区域 cn/us/hk

    Returns:
        包含 indices、stats、sectors 的 dict

    Raises:
        ValueError: region 无效
    """
    if region not in _VALID_REGIONS:
        raise ValueError(f"region must be one of {_VALID_REGIONS}, got '{region}'")

    analyzer = MarketAnalyzer(region=region)
    overview = analyzer.get_market_overview()
    return _overview_to_dict(region, overview)


def get_market_news(region: str, limit: int = 10) -> Dict[str, Any]:
    """
    获取单个区域的市场新闻。

    Args:
        region: 市场区域 cn/us/hk
        limit: 返回条数上限

    Returns:
        {"region": region, "news": [...]}

    Raises:
        ValueError: region 无效
    """
    if region not in _VALID_REGIONS:
        raise ValueError(f"region must be one of {_VALID_REGIONS}, got '{region}'")

    analyzer = MarketAnalyzer(region=region)
    raw_news = analyzer.search_market_news()
    serialized = [_serialize_news_item(item) for item in raw_news[:limit]]
    return {"region": region, "news": serialized}


def get_all_regions_overview() -> List[Dict[str, Any]]:
    """获取所有区域（cn/hk/us）的市场行情数据。"""
    results = []
    for region in _VALID_REGIONS:
        try:
            results.append(get_market_overview(region))
        except Exception:
            logger.exception(
                "market_data_service action=get_overview region=%s status=error", region
            )
            results.append({
                "region": region,
                "date": "",
                "indices": [],
                "stats": {
                    "up_count": 0, "down_count": 0, "flat_count": 0,
                    "limit_up_count": 0, "limit_down_count": 0, "total_amount": 0.0,
                },
                "top_sectors": [],
                "bottom_sectors": [],
            })
    return results


def get_all_regions_news(limit: int = 10) -> List[Dict[str, Any]]:
    """获取所有区域（cn/hk/us）的市场新闻。"""
    results = []
    for region in _VALID_REGIONS:
        try:
            results.append(get_market_news(region, limit=limit))
        except Exception:
            logger.exception(
                "market_data_service action=get_news region=%s status=error", region
            )
            results.append({"region": region, "news": []})
    return results
