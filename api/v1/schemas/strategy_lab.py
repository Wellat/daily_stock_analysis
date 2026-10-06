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
    parameters: List[Dict[str, Any]] = Field(default_factory=list, description="策略参数元数据（rotation 含枚举选项）")
    factors: List[Dict[str, Any]] = Field(default_factory=list, description="可用因子注册表元数据（rotation 提供）")
    score_presets: List[Dict[str, Any]] = Field(default_factory=list, description="打分预设（rotation 提供）")


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
    # rotation 引擎扩展指标（旧引擎为 None）
    turnover_avg_pct: Optional[float] = None
    period_count: Optional[int] = None
    profit_periods: Optional[int] = None
    loss_periods: Optional[int] = None
    benchmark_metrics: Dict[str, Any] = Field(default_factory=dict)


class StrategyLabEquityPointItem(BaseModel):
    trade_date: str
    equity: float
    cash: float
    positions_value: float
    benchmark_equity: Optional[float] = None
    drawdown_pct: Optional[float] = None
    daily_return_pct: Optional[float] = None
    turnover_pct: Optional[float] = None
    holdings: Optional[List[str]] = None
    holdings_count: Optional[int] = None


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
    # 列表摘要增强（additive）：紧凑指标与参数快照，详情接口仍返回完整 metrics
    metrics: Optional[StrategyLabMetricItem] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)


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
    symbol_name: Optional[str] = None
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
    sync_type: str = Field("", description="opencli 同步类型：cb_basic / cb_ohlc / cb_premium_history / cb_factors / cb_scheduled / portfolio_holdings / all；cb_factors 的因子日期取 end_date，缺省 start_date，均缺省为今天；cb_scheduled 手动触发盘后调度链路（基础+行情+因子+持仓股票/ETF行情+盘后通知，run_kind=after_close，与每日 20:00 定时任务同链路）；portfolio_holdings 同步 Portfolio 持仓中 A股/ETF/港股 的日线（转债持仓跳过，由 cb_ohlc 覆盖；港股收盘价为港币原币）")
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
    force_redeem_countdown: Optional[str] = None
    down_revise_countdown: Optional[str] = None
    put_countdown: Optional[str] = None
    last_trading_date: Optional[str] = None
    bond_rating: Optional[str] = None
    industry: Optional[str] = None
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


class StrategyLabStockBarItem(BaseModel):
    trade_date: Optional[str] = None
    close: Optional[float] = None


class StrategyLabStockBarListResponse(BaseModel):
    bond_code: str
    stock_code: str = ""
    stock_name: Optional[str] = None
    total: int = 0
    source: Optional[str] = None
    items: List[StrategyLabStockBarItem] = Field(default_factory=list)


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


class StrategyLabPremiumTrackBondItem(BaseModel):
    bond_code: str
    bond_name: Optional[str] = None
    days_count: int = Field(0, description="在榜交易日数")
    ratio: float = Field(0, description="在榜天数 / 窗口交易日数")
    first_date: Optional[str] = None
    last_date: Optional[str] = None
    avg_premium: Optional[float] = Field(None, description="在榜期间平均溢价率（百分数）")
    best_rank: int = Field(0, description="窗口内最好名次")
    day_indexes: List[int] = Field(default_factory=list, description="在榜日期在 dates 中的下标")
    premiums: List[float] = Field(default_factory=list, description="与 day_indexes 一一对应的当日溢价率")


class StrategyLabPremiumTrackTurnoverItem(BaseModel):
    date: str
    overlap: Optional[int] = Field(None, description="与前一日榜单的重叠只数（首日为空）")
    entered: Optional[int] = Field(None, description="当日新进榜只数（首日为空）")
    exited: Optional[int] = Field(None, description="当日退出只数（首日为空）")
    threshold: Optional[float] = Field(None, description="当日第 top_n 名（榜单内最高）溢价率")


class StrategyLabPremiumTrackStatsItem(BaseModel):
    window_days: int = 0
    distinct_bonds: int = 0
    avg_overlap: Optional[float] = None
    avg_entered: Optional[float] = None


class StrategyLabPremiumTrackResponse(BaseModel):
    market: str
    top_n: int
    start: str
    end: str
    dates: List[str] = Field(default_factory=list)
    bonds: List[StrategyLabPremiumTrackBondItem] = Field(default_factory=list)
    turnover: List[StrategyLabPremiumTrackTurnoverItem] = Field(default_factory=list)
    stats: StrategyLabPremiumTrackStatsItem = Field(default_factory=StrategyLabPremiumTrackStatsItem)


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


# ---------------------------------------------------------------------------
# 回测配置预设（保存/加载）
# ---------------------------------------------------------------------------

class StrategyLabConfigCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="配置名称")
    description: Optional[str] = Field(None, max_length=500, description="配置说明")
    strategy_id: str = Field("rotation", description="策略 ID")
    market: StrategyLabMarket = Field("cn", description="市场")
    instrument_type: StrategyLabInstrumentType = Field("convertible_bond", description="品种类型")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="策略参数（含打分/排除因子表）")
    symbols: List[str] = Field(default_factory=list, description="标的筛选")


class StrategyLabConfigUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    strategy_id: Optional[str] = None
    market: Optional[StrategyLabMarket] = None
    instrument_type: Optional[StrategyLabInstrumentType] = None
    parameters: Optional[Dict[str, Any]] = None
    symbols: Optional[List[str]] = None


class StrategyLabConfigItem(BaseModel):
    id: int
    config_uid: str
    name: str
    description: Optional[str] = None
    strategy_id: str
    market: str
    instrument_type: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    symbols: List[str] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class StrategyLabConfigListResponse(BaseModel):
    total: int
    items: List[StrategyLabConfigItem] = Field(default_factory=list)
