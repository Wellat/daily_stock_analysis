# -*- coding: utf-8 -*-
"""QMT quotes-bridge API schemas."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class QmtSettingsUpdateRequest(BaseModel):
    """更新 QMT 本地源开关（写 .env 并热加载）。"""

    enabled: Optional[bool] = None
    auto_include_holdings: Optional[bool] = None
    include_alert_symbols: Optional[bool] = None


class QmtManualSymbolsRequest(BaseModel):
    """整组替换手动订阅列表（接受 600519 / 600519.SH 等混写形态）。"""

    symbols: List[str] = Field(default_factory=list)
