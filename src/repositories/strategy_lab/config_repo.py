# -*- coding: utf-8 -*-
"""Repository for saved Strategy Lab backtest configuration presets."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import desc, select

from src.storage import DatabaseManager, StrategyLabStrategyConfig


class StrategyLabConfigRepository:
    """CRUD for research-side backtest configuration presets."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def create_config(
        self,
        *,
        name: str,
        description: Optional[str],
        strategy_id: str,
        market: str,
        instrument_type: str,
        parameters: Dict[str, Any],
        symbols: List[str],
    ) -> StrategyLabStrategyConfig:
        now = datetime.now()
        with self.db.get_session() as session:
            row = StrategyLabStrategyConfig(
                config_uid=uuid4().hex,
                name=name,
                description=description,
                strategy_id=strategy_id,
                market=market,
                instrument_type=instrument_type,
                parameters_json=json.dumps(parameters, ensure_ascii=False, sort_keys=True),
                symbols_json=json.dumps(symbols, ensure_ascii=False),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def update_config(self, config_id: int, updates: Dict[str, Any]) -> Optional[StrategyLabStrategyConfig]:
        with self.db.get_session() as session:
            row = session.get(StrategyLabStrategyConfig, config_id)
            if row is None:
                return None
            if updates.get("name") is not None:
                row.name = str(updates["name"])
            if "description" in updates:
                row.description = updates["description"]
            if updates.get("strategy_id") is not None:
                row.strategy_id = str(updates["strategy_id"])
            if updates.get("market") is not None:
                row.market = str(updates["market"])
            if updates.get("instrument_type") is not None:
                row.instrument_type = str(updates["instrument_type"])
            if updates.get("parameters") is not None:
                row.parameters_json = json.dumps(updates["parameters"], ensure_ascii=False, sort_keys=True)
            if updates.get("symbols") is not None:
                row.symbols_json = json.dumps(updates["symbols"], ensure_ascii=False)
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def delete_config(self, config_id: int) -> bool:
        with self.db.get_session() as session:
            row = session.get(StrategyLabStrategyConfig, config_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def get_config(self, config_id: int) -> Optional[StrategyLabStrategyConfig]:
        with self.db.get_session() as session:
            row = session.get(StrategyLabStrategyConfig, config_id)
            if row is not None:
                session.expunge(row)
            return row

    def list_configs(self, *, limit: int = 100, offset: int = 0) -> Tuple[List[StrategyLabStrategyConfig], int]:
        with self.db.get_session() as session:
            total = session.execute(select(StrategyLabStrategyConfig.id)).scalars().all()
            rows = session.execute(
                select(StrategyLabStrategyConfig)
                .order_by(desc(StrategyLabStrategyConfig.updated_at), desc(StrategyLabStrategyConfig.id))
                .offset(offset)
                .limit(limit)
            ).scalars().all()
            for row in rows:
                session.expunge(row)
            return list(rows), len(total)


def config_payload(row: StrategyLabStrategyConfig) -> Dict[str, Any]:
    parameters = json.loads(row.parameters_json) if row.parameters_json else {}
    symbols = json.loads(row.symbols_json) if row.symbols_json else []
    return {
        "id": row.id,
        "config_uid": row.config_uid,
        "name": row.name,
        "description": row.description,
        "strategy_id": row.strategy_id,
        "market": row.market,
        "instrument_type": row.instrument_type,
        "parameters": parameters if isinstance(parameters, dict) else {},
        "symbols": symbols if isinstance(symbols, list) else [],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
