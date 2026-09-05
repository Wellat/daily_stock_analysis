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

class LiveStrategyConfigResponse(LiveStrategyConfigRequest):
    id: Optional[int] = None

class LiveStrategyRunItem(BaseModel):
    id: int
    run_uid: str
    trade_date: str
    status: str
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
    mode: str = Field("rebalance", pattern="^(rebalance|event_check)$")
