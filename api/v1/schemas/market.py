# -*- coding: utf-8 -*-
"""
===================================
市场数据 API Schemas
===================================

职责：
1. 定义市场行情和新闻接口的 Pydantic 响应模型
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class MarketIndexItem(BaseModel):
    """单个市场指数数据"""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "code": "sh000001",
            "name": "上证指数",
            "current": 3200.50,
            "change": 15.30,
            "change_pct": 0.48,
            "open": 3185.00,
            "high": 3210.00,
            "low": 3180.00,
            "prev_close": 3185.20,
            "volume": 2500000000,
            "amount": 350000000000,
            "amplitude": 0.94,
        }
    })

    code: str = Field(description="指数代码")
    name: str = Field(description="指数名称")
    current: float = Field(description="当前点位")
    change: float = Field(description="涨跌点数")
    change_pct: float = Field(description="涨跌幅(%)")
    open: float = Field(description="开盘点位")
    high: float = Field(description="最高点位")
    low: float = Field(description="最低点位")
    prev_close: float = Field(description="昨收点位")
    volume: float = Field(description="成交量")
    amount: float = Field(description="成交额")
    amplitude: float = Field(description="振幅(%)")


class MarketBreadthStats(BaseModel):
    """市场涨跌统计"""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "up_count": 2800,
            "down_count": 1500,
            "flat_count": 200,
            "limit_up_count": 45,
            "limit_down_count": 12,
            "total_amount": 8500.50,
        }
    })

    up_count: int = Field(0, description="上涨家数")
    down_count: int = Field(0, description="下跌家数")
    flat_count: int = Field(0, description="平盘家数")
    limit_up_count: int = Field(0, description="涨停家数")
    limit_down_count: int = Field(0, description="跌停家数")
    total_amount: float = Field(0.0, description="两市成交额（亿元）")


class SectorItem(BaseModel):
    """板块涨跌数据"""

    model_config = ConfigDict(json_schema_extra={
        "example": {"name": "半导体", "change_pct": 3.2}
    })

    name: str = Field(description="板块名称")
    change_pct: float = Field(description="涨跌幅(%)")


class MarketOverviewResponse(BaseModel):
    """单个区域市场行情响应"""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "region": "cn",
            "date": "2026-06-19",
            "indices": [{"code": "sh000001", "name": "上证指数", "current": 3200.50,
                         "change": 15.30, "change_pct": 0.48, "open": 3185.00,
                         "high": 3210.00, "low": 3180.00, "prev_close": 3185.20,
                         "volume": 2500000000, "amount": 350000000000, "amplitude": 0.94}],
            "stats": {"up_count": 2800, "down_count": 1500, "flat_count": 200,
                      "limit_up_count": 45, "limit_down_count": 12, "total_amount": 8500.50},
            "top_sectors": [{"name": "半导体", "change_pct": 3.2}],
            "bottom_sectors": [{"name": "房地产", "change_pct": -2.1}],
        }
    })

    region: str = Field(description="市场区域 cn/us/hk")
    date: str = Field(description="数据日期")
    indices: List[MarketIndexItem] = Field(default_factory=list, description="主要指数列表")
    stats: MarketBreadthStats = Field(description="涨跌统计")
    top_sectors: List[SectorItem] = Field(default_factory=list, description="涨幅前N板块")
    bottom_sectors: List[SectorItem] = Field(default_factory=list, description="跌幅前N板块")


class AllMarketOverviewResponse(BaseModel):
    """所有区域市场行情响应"""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "regions": [{"region": "cn", "date": "2026-06-19",
                         "indices": [{"code": "sh000001", "name": "上证指数",
                                      "current": 3200.50, "change": 15.30,
                                      "change_pct": 0.48, "open": 3185.00,
                                      "high": 3210.00, "low": 3180.00,
                                      "prev_close": 3185.20, "volume": 2500000000,
                                      "amount": 350000000000, "amplitude": 0.94}],
                         "stats": {"up_count": 2800, "down_count": 1500,
                                   "flat_count": 200, "limit_up_count": 45,
                                   "limit_down_count": 12, "total_amount": 8500.50},
                         "top_sectors": [{"name": "半导体", "change_pct": 3.2}],
                         "bottom_sectors": [{"name": "房地产", "change_pct": -2.1}]}]
        }
    })

    regions: List[MarketOverviewResponse] = Field(description="各区域行情列表")


class MarketNewsItem(BaseModel):
    """单条市场新闻"""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "title": "标题",
            "summary": "摘要内容",
            "source": "来源媒体",
            "url": "https://...",
            "published_at": "2026-06-19T10:00:00",
        }
    })

    title: str = Field(description="新闻标题")
    summary: Optional[str] = Field(None, description="新闻摘要")
    source: Optional[str] = Field(None, description="来源媒体")
    url: Optional[str] = Field(None, description="新闻链接")
    published_at: Optional[str] = Field(None, description="发布时间")


class MarketNewsResponse(BaseModel):
    """单个区域市场新闻响应"""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "region": "cn",
            "news": [{"title": "标题", "summary": "摘要内容",
                      "source": "来源媒体", "url": "https://...",
                      "published_at": "2026-06-19T10:00:00"}],
        }
    })

    region: str = Field(description="市场区域 cn/us/hk")
    news: List[MarketNewsItem] = Field(default_factory=list, description="新闻列表")


class AllMarketNewsResponse(BaseModel):
    """所有区域市场新闻响应"""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "regions": [{"region": "cn",
                         "news": [{"title": "标题", "summary": "摘要内容",
                                   "source": "来源媒体", "url": "https://...",
                                   "published_at": "2026-06-19T10:00:00"}]}]
        }
    })

    regions: List[MarketNewsResponse] = Field(description="各区域新闻列表")
