# -*- coding: utf-8 -*-
"""可转债实盘交易指令 API schemas。"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


TradingOrderSide = Literal["buy", "sell"]
TradingOrderType = Literal["limit", "market"]
TradingOrderCallbackStatus = Literal["submitted", "filled", "rejected"]


class TradingOrderCreateRequest(BaseModel):
    symbol: str = Field(..., description="可转债代码（6 位数字）")
    side: TradingOrderSide = Field(..., description="买卖方向")
    quantity: float = Field(..., gt=0, description="数量（张）")
    order_type: TradingOrderType = Field("limit", description="订单类型")
    limit_price: Optional[float] = Field(None, gt=0, description="限价（limit 必填）")
    source: str = Field("api", description="来源")
    reason: Optional[str] = Field(None, description="信号理由")


class TradingOrderItem(BaseModel):
    id: int
    order_uid: str
    symbol: str
    symbol_name: Optional[str] = None
    live_run_id: Optional[int] = None
    rebalance_batch_id: Optional[int] = None
    market: str
    instrument_type: str
    side: str
    quantity: float
    order_type: str
    limit_price: Optional[float] = None
    status: str
    qmt_order_id: Optional[str] = None
    filled_quantity: Optional[float] = None
    filled_price: Optional[float] = None
    error_message: Optional[str] = None
    source: str
    reason: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    submitted_at: Optional[str] = None
    completed_at: Optional[str] = None


class TradingOrderListResponse(BaseModel):
    page: int
    limit: int
    total: int
    items: List[TradingOrderItem] = Field(default_factory=list)


class TradingOrderPendingItem(BaseModel):
    id: int
    order_uid: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    limit_price: Optional[float] = None


class TradingOrderPendingListResponse(BaseModel):
    items: List[TradingOrderPendingItem] = Field(default_factory=list)


class TradingOrderCallbackRequest(BaseModel):
    status: TradingOrderCallbackStatus = Field(..., description="回调状态")
    qmt_order_id: Optional[str] = Field(None, description="QMT 侧订单号")
    filled_quantity: Optional[float] = Field(None, ge=0, description="成交数量")
    filled_price: Optional[float] = Field(None, gt=0, description="成交价")
    error_message: Optional[str] = Field(None, description="失败原因")


class TradingDashboardSummary(BaseModel):
    total_count: int = Field(..., description="已成交订单总数")
    buy_count: int = Field(..., description="买入笔数")
    sell_count: int = Field(..., description="卖出笔数")
    buy_amount: float = Field(..., description="买入总金额")
    sell_amount: float = Field(..., description="卖出总金额")
    realized_pnl: float = Field(..., description="已实现盈亏（FIFO 配对）")
    win_count: int = Field(..., description="盈利卖出笔数")
    loss_count: int = Field(..., description="亏损卖出笔数")
    win_rate: Optional[float] = Field(None, description="胜率（盈利/（盈利+亏损），无平仓时为空）")
    unmatched_sell_quantity: float = Field(..., description="无买入配对的卖出总量")


class TradingDashboardCurvePoint(BaseModel):
    date: str = Field(..., description="交易日 YYYY-MM-DD")
    daily_pnl: float = Field(..., description="当日已实现盈亏")
    cumulative_pnl: float = Field(..., description="累计已实现盈亏")


class TradingDashboardSymbolItem(BaseModel):
    symbol: str = Field(..., description="证券代码")
    symbol_name: Optional[str] = Field(None, description="标的中文名称")
    buy_count: int = Field(..., description="买入笔数")
    buy_quantity: float = Field(..., description="买入总量")
    buy_amount: float = Field(..., description="买入总金额")
    sell_count: int = Field(..., description="卖出笔数")
    sell_quantity: float = Field(..., description="卖出总量")
    sell_amount: float = Field(..., description="卖出总金额")
    realized_pnl: float = Field(..., description="已实现盈亏")
    open_quantity: float = Field(..., description="未平仓数量")
    open_cost: float = Field(..., description="未平仓成本")
    unmatched_sell_quantity: float = Field(..., description="无买入配对的卖出量")


class TradingDashboardResponse(BaseModel):
    start: Optional[str] = Field(None, description="查询开始日期（含）")
    end: Optional[str] = Field(None, description="查询结束日期（含）")
    summary: TradingDashboardSummary
    curve: List[TradingDashboardCurvePoint] = Field(default_factory=list, description="累计收益曲线数据点")
    symbols: List[TradingDashboardSymbolItem] = Field(default_factory=list, description="分标的盈亏明细")


class QmtPositionItem(BaseModel):
    symbol: str = Field(..., description="证券代码（6 位数字）")
    name: Optional[str] = Field(None, description="标的中文名称")
    volume: float = Field(..., description="总持仓数量")
    can_use_volume: float = Field(..., description="可用数量")
    open_price: Optional[float] = Field(None, description="持仓成本价")
    float_profit: Optional[float] = Field(None, description="浮动盈亏")


class QmtPositionReportRequest(BaseModel):
    account: str = Field(..., description="资金账号")
    positions: List[QmtPositionItem] = Field(default_factory=list, description="持仓列表")


class QmtPositionReportResponse(BaseModel):
    account: str
    reported: int


class QmtPositionListItem(BaseModel):
    id: int
    account: str
    symbol: str
    name: Optional[str] = None
    volume: float
    can_use_volume: float
    open_price: Optional[float] = None
    float_profit: Optional[float] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class QmtPositionListResponse(BaseModel):
    items: List[QmtPositionListItem] = Field(default_factory=list)
