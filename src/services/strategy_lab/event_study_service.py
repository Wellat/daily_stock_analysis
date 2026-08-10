# -*- coding: utf-8 -*-
"""Structured event-return studies for synchronized convertible-bond data."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from src.repositories.strategy_lab.data_repo import StrategyLabDataRepository
from src.storage import DatabaseManager


class StrategyLabEventStudyService:
    """Calculate close-to-close returns at trading-day offsets from CB events."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.repository = StrategyLabDataRepository(db_manager)

    def study_convertible_bond_events(
        self,
        *,
        market: str,
        event_type: Optional[str],
        offsets: List[int],
        symbols: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        offsets = sorted(set(offsets))
        if not offsets:
            raise ValueError("offsets must contain at least one trading-day offset")
        rows = self.repository.load_cb_event_study_rows(
            market=market,
            event_type=event_type,
            symbols=symbols,
        )
        grouped: Dict[tuple[str, str, Any], List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[(row["bond_code"], row["event_type"], row["event_date"])].append(row)

        items: List[Dict[str, Any]] = []
        by_offset: Dict[int, List[float]] = defaultdict(list)
        for (bond_code, row_event_type, event_date), series in grouped.items():
            event_index = next((index for index, row in enumerate(series) if row["trade_date"] >= event_date), None)
            if event_index is None:
                continue
            base_close = series[event_index]["close"]
            returns: Dict[str, Optional[float]] = {}
            for offset in offsets:
                target_index = event_index + offset
                value: Optional[float] = None
                if 0 <= target_index < len(series) and base_close > 0:
                    value = round((series[target_index]["close"] / base_close - 1.0) * 100, 4)
                    by_offset[offset].append(value)
                returns[str(offset)] = value
            items.append(
                {
                    "bond_code": bond_code,
                    "bond_name": series[0]["bond_name"],
                    "event_date": event_date.isoformat(),
                    "event_type": row_event_type,
                    "base_trade_date": series[event_index]["trade_date"].isoformat(),
                    "base_close": base_close,
                    "returns_pct": returns,
                }
            )
        summary = {
            str(offset): {
                "count": len(values),
                "average_return_pct": round(sum(values) / len(values), 4) if values else None,
            }
            for offset, values in by_offset.items()
        }
        return {"market": market, "event_type": event_type, "offsets": offsets, "total": len(items), "summary": summary, "items": items}
