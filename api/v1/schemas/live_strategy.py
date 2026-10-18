from __future__ import annotations
from datetime import date
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class LiveStrategyConfigRequest(BaseModel):
    """PUT /config 兼容层请求体（单实盘配置时代语义，仅 order 实例）。"""
    strategy_id: str = "double-low"
    strategy_version: str = "v1"
    qmt_account: str
    enabled: bool = False
    symbols: List[str] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    rebalance_frequency_days: int = Field(1, ge=1)
    event_check_enabled: bool = True
    data_sync_before_run: bool = True
    # 最后交易日风控：强赎/到期停止交易前（含 buffer_days 个交易日提前量）
    # 强制全量卖出持仓，买入侧排除剩余交易日不足的标的
    last_trading_day_exit_enabled: bool = True
    last_trading_day_exit_buffer_days: int = Field(1, ge=0, le=10)
    # 交易日自动运行触发时刻（HH:MM），缺省保留库内现值（默认 14:35）
    schedule_time: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

class LiveStrategyInstanceRequest(BaseModel):
    """策略实例创建/更新请求体（模板 + 参数 + 标的 + 调度统一载体）。

    校验按模板 live_mode 分支在服务层完成：order 需要 qmt_account 与调仓/
    风控字段；notify 需要非空标的；仅回测模板不参与调度（enabled 恒 False）。
    """
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    strategy_id: str
    strategy_version: str = "v1"
    qmt_account: Optional[str] = None
    enabled: bool = False
    symbols: List[str] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    rebalance_frequency_days: Optional[int] = Field(None, ge=1)
    event_check_enabled: Optional[bool] = None
    data_sync_before_run: Optional[bool] = None
    last_trading_day_exit_enabled: Optional[bool] = None
    last_trading_day_exit_buffer_days: Optional[int] = Field(None, ge=0, le=10)
    schedule_time: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

class LiveStrategyConfigResponse(LiveStrategyConfigRequest):
    id: Optional[int] = None

class LiveStrategyInstanceResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    strategy_id: str
    strategy_version: str
    qmt_account: str
    enabled: bool
    symbols: List[str] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    rebalance_frequency_days: int = 1
    event_check_enabled: bool = True
    data_sync_before_run: bool = True
    last_trading_day_exit_enabled: bool = True
    last_trading_day_exit_buffer_days: int = 1
    schedule_time: str = "14:35"
    next_rebalance_date: Optional[str] = None
    last_run: Optional[Dict[str, Any]] = None

class LiveStrategyInstanceListResponse(BaseModel):
    items: List[LiveStrategyInstanceResponse] = Field(default_factory=list)

class LiveStrategyRunItem(BaseModel):
    id: int
    run_uid: str
    config_id: Optional[int] = None
    trade_date: str
    status: str
    mode: Optional[str] = None
    strategy_id: Optional[str] = None
    strategy_version: Optional[str] = None
    qmt_account: Optional[str] = None
    decision_count: Optional[int] = None
    order_count: Optional[int] = None
    skip_reason: Optional[str] = None
    data_snapshot_at: Optional[str] = None
    completed_at: Optional[str] = None
    target: Dict[str, Any] = Field(default_factory=dict)
    current: Dict[str, Any] = Field(default_factory=dict)
    rebalance: List[Dict[str, Any]] = Field(default_factory=list)
    risk: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None

class LiveStrategyRunListResponse(BaseModel):
    total: int
    items: List[LiveStrategyRunItem] = Field(default_factory=list)

class LiveStrategyRunRequest(BaseModel):
    trade_date: Optional[date] = None
    # auto：按调仓节奏推导（到期 rebalance / 未到期 event_check）
    mode: str = Field("auto", pattern="^(auto|rebalance|event_check)$")
    # 指定策略实例运行（实盘页多实例选择）；缺省回落最新 order 实例
    config_id: Optional[int] = None
