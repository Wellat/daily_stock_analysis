# -*- coding: utf-8 -*-
"""Strategy Lab API schemas."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


StrategyLabMarket = Literal["cn", "hk", "us"]
StrategyLabInstrumentType = Literal["convertible_bond", "stock", "hk_stock", "us_stock"]
StrategyLabRunStatus = Literal["pending", "running", "completed", "failed"]
StrategyLabTradeSide = Literal["buy", "sell"]


class StrategyLabStrategyItem(BaseModel):
    strategy_id: str
    name: str
    instrument_types: List[str] = Field(default_factory=list)
    markets: List[str] = Field(default_factory=list)
    description: Optional[str] = None


class StrategyLabStrategyListResponse(BaseModel):
    items: List[StrategyLabStrategyItem] = Field(default_factory=list)


class StrategyLabRunCreateRequest(BaseModel):
    strategy_id: str = Field("double-low", description="策略 ID")
    market: StrategyLabMarket = Field("cn", description="市场")
    instrument_type: StrategyLabInstrumentType = Field("convertible_bond", description="品种类型")
    start_date: date = Field(..., description="回测起始日期")
    end_date: date = Field(..., description="回测结束日期")
    initial_cash: float = Field(100000.0, gt=0, description="初始资金")
    benchmark_symbol: Optional[str] = Field(None, description="基准标的")
    symbols: List[str] = Field(default_factory=list, description="可选标的筛选")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="策略参数")
    portfolio_account_id: Optional[int] = Field(None, description="关联的 Portfolio 账户 ID")


class StrategyLabMetricItem(BaseModel):
    total_return_pct: Optional[float] = None
    annualized_return_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    calmar_ratio: Optional[float] = None
    win_rate_pct: Optional[float] = None
    trade_count: int = 0
    exposure_days: int = 0
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


class StrategyLabEquityPointItem(BaseModel):
    trade_date: str
    equity: float
    cash: float
    positions_value: float


class StrategyLabRunSummaryItem(BaseModel):
    id: int
    run_uid: str
    strategy_id: str
    strategy_name: str
    engine_name: str
    status: StrategyLabRunStatus
    market: str
    instrument_type: str
    start_date: str
    end_date: str
    initial_cash: float
    final_equity: Optional[float] = None
    benchmark_symbol: Optional[str] = None
    benchmark_return_pct: Optional[float] = None
    portfolio_account_id: Optional[int] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class StrategyLabRunItem(StrategyLabRunSummaryItem):
    parameters: Dict[str, Any] = Field(default_factory=dict)
    symbols: List[str] = Field(default_factory=list)
    metrics: Optional[StrategyLabMetricItem] = None
    equity_curve: List[StrategyLabEquityPointItem] = Field(default_factory=list)


class StrategyLabRunListResponse(BaseModel):
    total: int
    page: int
    limit: int
    items: List[StrategyLabRunSummaryItem] = Field(default_factory=list)


class StrategyLabTradeItem(BaseModel):
    id: int
    run_id: int
    trade_date: str
    canonical_id: str
    symbol: str
    market: str
    instrument_type: str
    side: StrategyLabTradeSide
    quantity: float
    price: float
    amount: float
    fee: float = 0.0
    reason: Optional[str] = None
    portfolio_trade_id: Optional[int] = None


class StrategyLabTradeListResponse(BaseModel):
    run_id: int
    items: List[StrategyLabTradeItem] = Field(default_factory=list)


class StrategyLabDataSyncRequest(BaseModel):
    market: StrategyLabMarket = Field("cn", description="市场")
    source: str = Field("opencli", description="同步来源")
    sync_type: str = Field("", description="opencli 同步类型：cb_basic / cb_ohlc / cb_premium_history / all")
    include_delisted: bool = Field(False, description="是否同步已退市可转债（默认仅活跃）")
    start_date: Optional[date] = Field(None, description="行情同步起始日期（缺省时增量）")
    end_date: Optional[date] = Field(None, description="行情同步结束日期（默认今天）")
    symbols: List[str] = Field(default_factory=list, description="可选标的筛选")


class StrategyLabDataSyncResponse(BaseModel):
    sync_run_id: int = 0
    status: str = "running"
    sync_type: str = ""
    cb_basic_upserted: int = 0
    cb_terms_upserted: int = 0
    cb_factor_upserted: int = 0
    cb_event_upserted: int = 0
    ohlc_bars_upserted: int = 0
    ohlc_skipped: int = 0
    cb_factor_rows_patched: int = 0
    premium_rate_patched: int = 0
    remaining_size_patched: int = 0
    failed_bonds: List[Any] = Field(default_factory=list)


class StrategyLabSyncRunItem(BaseModel):
    id: int
    run_uid: str
    sync_type: str
    market: str
    status: str
    cancel_requested: bool = False
    result: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class StrategyLabSyncRunListResponse(BaseModel):
    page: int
    limit: int
    total: int
    items: List[StrategyLabSyncRunItem] = Field(default_factory=list)


class StrategyLabInstrumentItem(BaseModel):
    bond_code: str
    bond_name: str
    stock_code: str
    stock_name: Optional[str] = None
    market: str
    list_date: Optional[str] = None
    maturity_date: Optional[str] = None
    status: Optional[str] = None
    remaining_size: Optional[float] = None
    current_premium_rate: Optional[float] = None
    convert_price: Optional[float] = None
    latest_close: Optional[float] = None
    latest_premium_rate: Optional[float] = None
    event_count: int = 0
    source: Optional[str] = None
    updated_at: Optional[str] = None


class StrategyLabInstrumentListResponse(BaseModel):
    market: str
    total: int
    page: int
    limit: int
    items: List[StrategyLabInstrumentItem] = Field(default_factory=list)


class StrategyLabInstrumentDetailItem(BaseModel):
    bond_code: str
    bond_name: str
    stock_code: str
    stock_name: Optional[str] = None
    market: str
    list_date: Optional[str] = None
    maturity_date: Optional[str] = None
    status: Optional[str] = None
    remaining_size: Optional[float] = None
    current_premium_rate: Optional[float] = None
    convert_price: Optional[float] = None
    latest_close: Optional[float] = None
    latest_premium_rate: Optional[float] = None
    industry: Optional[str] = None
    terms: Dict[str, Any] = Field(default_factory=dict)
    redeem_clause: Optional[str] = None
    down_revise_clause: Optional[str] = None
    put_clause: Optional[str] = None
    redeem_trigger_price: Optional[float] = None
    down_revise_trigger_price: Optional[float] = None
    put_trigger_price: Optional[float] = None
    source: Optional[str] = None
    updated_at: Optional[str] = None
    bar_count: int = 0
    event_count: int = 0


class StrategyLabBarItem(BaseModel):
    trade_date: Optional[str] = None
    close: Optional[float] = None
    premium_rate: Optional[float] = None
    remaining_size: Optional[float] = None
    redeem_alert: bool = False
    down_revise_alert: bool = False
    put_alert: bool = False
    source: Optional[str] = None


class StrategyLabBarListResponse(BaseModel):
    bond_code: str
    total: int
    items: List[StrategyLabBarItem] = Field(default_factory=list)


class StrategyLabEventItem(BaseModel):
    event_date: str
    event_type: str
    event_detail: Optional[str] = None
    source: Optional[str] = None
    created_at: Optional[str] = None


class StrategyLabEventListResponse(BaseModel):
    bond_code: str
    total: int
    items: List[StrategyLabEventItem] = Field(default_factory=list)


class StrategyLabEventStudyRequest(BaseModel):
    market: StrategyLabMarket = Field("cn", description="市场")
    event_type: Optional[str] = Field(None, description="可选事件类型筛选")
    offsets: List[int] = Field(default_factory=lambda: [-5, -1, 1, 5], description="相对事件日的交易日偏移")
    symbols: List[str] = Field(default_factory=list, description="可选可转债代码筛选")


class StrategyLabEventStudyItem(BaseModel):
    bond_code: str
    bond_name: str
    event_date: str
    event_type: str
    base_trade_date: str
    base_close: float
    returns_pct: Dict[str, Optional[float]] = Field(default_factory=dict)


class StrategyLabEventStudyResponse(BaseModel):
    market: str
    event_type: Optional[str] = None
    offsets: List[int]
    total: int
    summary: Dict[str, Dict[str, Optional[float] | int]] = Field(default_factory=dict)
    items: List[StrategyLabEventStudyItem] = Field(default_factory=list)


class StrategyLabBatchCreateRequest(BaseModel):
    strategy_id: str = Field("double-low", description="策略 ID")
    market: StrategyLabMarket = Field("cn", description="市场")
    instrument_type: StrategyLabInstrumentType = Field("convertible_bond", description="品种类型")
    base_config: Dict[str, Any] = Field(default_factory=dict, description="基础运行参数")
    parameter_grid: Dict[str, List[Any]] = Field(default_factory=dict, description="参数网格")
    run_async: bool = Field(False, description="是否交给后台任务执行并通过 SSE 观察进度")


class StrategyLabBatchItemItem(BaseModel):
    id: int
    batch_id: int
    run_id: Optional[int] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    status: str
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class StrategyLabBatchSummaryItem(BaseModel):
    id: int
    batch_uid: str
    strategy_id: str
    strategy_name: str
    market: str
    instrument_type: str
    status: str
    total_tasks: int
    completed_tasks: int
    success_tasks: int
    failed_tasks: int
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class StrategyLabBatchItemResponse(StrategyLabBatchSummaryItem):
    parameters_grid: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    items: List[StrategyLabBatchItemItem] = Field(default_factory=list)


class StrategyLabBatchListResponse(BaseModel):
    page: int
    limit: int
    total: int
    items: List[StrategyLabBatchSummaryItem] = Field(default_factory=list)


class StrategyLabSignalCreateRequest(BaseModel):
    run_id: int
    portfolio_account_id: Optional[int] = None
    suggested_action: str = Field("hold", description="建议动作")
    signal_type: str = Field("strategy_recommendation", description="信号类型")
    confidence: Optional[float] = Field(None, ge=0, le=1, description="置信度")
    reason: Optional[str] = None


class StrategyLabSignalItem(BaseModel):
    id: int
    run_id: int
    portfolio_account_id: Optional[int] = None
    canonical_id: str
    symbol: str
    market: str
    instrument_type: str
    signal_type: str
    suggested_action: str
    confidence: Optional[float] = None
    reason: Optional[str] = None
    status: str
    portfolio_trade_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class StrategyLabSignalListResponse(BaseModel):
    page: int
    limit: int
    total: int
    items: List[StrategyLabSignalItem] = Field(default_factory=list)


class StrategyLabSignalConfirmRequest(BaseModel):
    portfolio_account_id: int
    trade_date: date
    quantity: float = Field(..., gt=0)
    price: float = Field(..., gt=0)
    side: Optional[StrategyLabTradeSide] = None
    fee: float = Field(0.0, ge=0)
    tax: float = Field(0.0, ge=0)
