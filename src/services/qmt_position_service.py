# -*- coding: utf-8 -*-
"""QMT 上报持仓业务层。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.repositories.qmt_position_repo import QmtPositionRepository
from src.storage import DatabaseManager


class QmtPositionService:
    """Receive and query QMT-reported account positions."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.repository = QmtPositionRepository(db_manager)

    def report_positions(self, *, account: str, positions: List[Dict[str, Any]]) -> Dict[str, Any]:
        account = (account or "").strip()
        if not account:
            raise ValueError("account must not be empty")
        if not isinstance(positions, list):
            raise ValueError("positions must be a list")

        for item in positions:
            symbol = str(item.get("symbol") or "").strip()
            if not symbol.isdigit() or len(symbol) != 6:
                raise ValueError("symbol must be a 6-digit code")
            volume = item.get("volume")
            can_use_volume = item.get("can_use_volume")
            if volume is None or not isinstance(volume, (int, float)):
                raise ValueError("volume must be numeric")
            if can_use_volume is None or not isinstance(can_use_volume, (int, float)):
                raise ValueError("can_use_volume must be numeric")

        reported = self.repository.replace(account=account, positions=positions)
        return {"account": account, "reported": reported}

    def list_positions(self, *, account: Optional[str] = None) -> Dict[str, Any]:
        rows = self.repository.list(account=account)
        return {"items": [self.repository._payload(row) for row in rows]}
