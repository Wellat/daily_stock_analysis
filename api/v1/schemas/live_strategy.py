from __future__ import annotations
from datetime import date
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class LiveStrategyConfigRequest(BaseModel):
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

class LiveStrategyNotifyConfigRequest(BaseModel):
    """信号通知配置（live_mode=notify 策略，每策略一行，不涉及 QMT 账户）。"""
    strategy_id: str
    strategy_version: str = "v1"
    enabled: bool = False
    symbols: List[str] = Field(min_length=1)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    schedule_time: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

class LiveStrategyConfigResponse(LiveStrategyConfigRequest):
    id: Optional[int] = None

class LiveStrategyRunItem(BaseModel):
    id: int
    run_uid: str
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
