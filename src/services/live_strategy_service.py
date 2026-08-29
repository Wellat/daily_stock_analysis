# -*- coding: utf-8 -*-
"""Live convertible-bond strategy orchestration."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy import desc, select

from src.repositories.strategy_lab.data_repo import StrategyLabDataRepository
from src.repositories.qmt_position_repo import QmtPositionRepository
from src.services.trading_order_service import TradingOrderService
from src.storage import DatabaseManager, LiveRebalanceBatch, LiveStrategyConfig, LiveStrategyRun
from src.core.strategy_lab.engine import list_builtin_strategies

LIVE_ACCOUNTS = {"testS", "135129739"}


class LiveStrategyService:
    """Calculate a target portfolio and materialize its delta as QMT orders."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()
        self.data = StrategyLabDataRepository(self.db)
        self.positions = QmtPositionRepository(self.db)
        self.orders = TradingOrderService(self.db)

    def get_config(self) -> Dict[str, Any] | None:
        with self.db.get_session() as session:
            row = session.execute(select(LiveStrategyConfig).order_by(desc(LiveStrategyConfig.id))).scalars().first()
            return self._config_payload(row) if row else None

    def save_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        account = str(payload.get("qmt_account") or "").strip()
        if account not in LIVE_ACCOUNTS:
            raise ValueError("qmt_account must be one of testS, 135129739")
        strategy_id = str(payload.get("strategy_id") or "double-low")
        metadata = next((x for x in list_builtin_strategies() if x["strategy_id"] == strategy_id), None)
        if metadata is None:
            raise ValueError(f"unsupported strategy_id: {strategy_id}")
        symbols = [str(x).strip() for x in payload.get("symbols", []) if str(x).strip()]
        params = dict(payload.get("parameters") or {})
        with self.db.get_session() as session:
            row = session.execute(select(LiveStrategyConfig).order_by(desc(LiveStrategyConfig.id))).scalars().first()
            if row is None:
                row = LiveStrategyConfig(name="default", qmt_account=account)
                session.add(row)
            row.strategy_id = strategy_id
            row.strategy_version = str(payload.get("strategy_version") or "v1")
            row.qmt_account = account
            row.enabled = bool(payload.get("enabled", False))
            row.symbols_json = json.dumps(symbols, ensure_ascii=False)
            row.parameters_json = json.dumps(params, ensure_ascii=False)
            row.updated_at = datetime.now()
            session.commit(); session.refresh(row)
            return self._config_payload(row)

    def run(self, *, trade_date: date, preview: bool = False) -> Dict[str, Any]:
        config = self._latest_config()
        if config is None:
            raise ValueError("live strategy config is not configured")
        with self.db.get_session() as session:
            existing = session.execute(select(LiveStrategyRun).where(
                LiveStrategyRun.config_id == config.id, LiveStrategyRun.trade_date == trade_date,
            )).scalars().first()
            if existing is not None:
                return self._run_payload(existing)

        params = json.loads(config.parameters_json or "{}")
        metadata = next((x for x in list_builtin_strategies() if x["strategy_id"] == config.strategy_id), None)
        if metadata is None:
            raise ValueError(f"unsupported strategy_id: {config.strategy_id}")
        defaults = {p["key"]: p.get("default") for p in metadata.get("parameters", [])}
        defaults.update(params)
        params = defaults
        symbols = json.loads(config.symbols_json or "[]")
        rows = self.data.load_cb_backtest_rows(market="cn", start_date=trade_date, end_date=trade_date, symbols=symbols)
        latest = {}
        for row in rows:
            if row.get("premium_rate") is not None and row.get("close") and (not params.get("exclude_event_blocked", True) or not row.get("event_blocked")) and abs(float(row["premium_rate"])) <= float(params.get("max_abs_premium", 200)):
                latest[row["bond_code"]] = row
        max_positions = max(1, int(params.get("max_positions", 2)))
        mode = config.strategy_id
        ranked = sorted(latest.values(), key=lambda r: (float(r["premium_rate"]) + (float(r["close"]) if mode == "double-low" else 0.0)))
        selected = ranked[:max_positions]
        target_cash = float(params.get("per_position_cash", 10000))
        lot = max(1, int(params.get("lot_size", 10)))
        target = {r["bond_code"]: {"symbol": r["bond_code"], "symbol_name": r.get("bond_name"), "price": float(r["close"]), "quantity": int(target_cash / float(r["close"]) / lot) * lot} for r in selected}
        current_rows = self.positions.list(account=config.qmt_account)
        # QMT reports the whole account (stocks, funds, convertible bonds, ...).
        # Live CB strategies must never create orders for non-CB holdings.
        cb_symbols = set(self.data.list_cb_basic_codes(market="cn"))
        current = {r.symbol: float(r.volume) for r in current_rows if r.symbol in cb_symbols}
        rebalance = []
        for symbol, item in target.items():
            delta = item["quantity"] - current.get(symbol, 0)
            if delta > 0: rebalance.append({"symbol": symbol, "side": "buy", "quantity": delta, "reason": "live_target_entry"})
        for symbol, volume in current.items():
            if symbol not in target and volume > 0: rebalance.append({"symbol": symbol, "side": "sell", "quantity": volume, "reason": "live_target_exit"})
        payload = {"trade_date": trade_date.isoformat(), "target": target, "current": current, "rebalance": rebalance, "strategy_version": config.strategy_version}
        if preview:
            return payload
        with self.db.get_session() as session:
            run_uid = uuid4().hex
            run = LiveStrategyRun(run_uid=run_uid, config_id=config.id, qmt_account=config.qmt_account, trade_date=trade_date,
                                  status="completed", data_snapshot_at=datetime.now(), target_json=json.dumps(target), current_json=json.dumps(current), rebalance_json=json.dumps(rebalance), risk_json=json.dumps({"passed": True}), completed_at=datetime.now())
            session.add(run); session.flush()
            run_id = int(run.id)
            batch_uid = uuid4().hex
            batch = LiveRebalanceBatch(batch_uid=batch_uid, run_id=run_id, qmt_account=config.qmt_account, status="pending", summary_json=json.dumps({"count": len(rebalance)}))
            session.add(batch); session.commit()
            batch_id = int(batch.id)
        names = {k: v.get("symbol_name") for k, v in target.items()}
        for order in rebalance:
            self.orders.create_order(symbol=order["symbol"], side=order["side"], quantity=order["quantity"], order_type="market", limit_price=None, source="live_strategy", reason=order["reason"], symbol_name=names.get(order["symbol"]), live_run_id=run_id, rebalance_batch_id=batch_id)
        return {**payload, "run_id": run_id, "run_uid": run_uid, "batch_uid": batch_uid}

    def _latest_config(self):
        with self.db.get_session() as session:
            return session.execute(select(LiveStrategyConfig).order_by(desc(LiveStrategyConfig.id))).scalars().first()

    @staticmethod
    def _config_payload(row):
        return {"id": row.id, "name": row.name, "strategy_id": row.strategy_id, "strategy_version": row.strategy_version, "qmt_account": row.qmt_account, "enabled": row.enabled, "symbols": json.loads(row.symbols_json or "[]"), "parameters": json.loads(row.parameters_json or "{}")}

    @staticmethod
    def _run_payload(row):
        return {"id": row.id, "run_uid": row.run_uid, "trade_date": row.trade_date.isoformat(), "status": row.status, "target": json.loads(row.target_json or "{}"), "current": json.loads(row.current_json or "{}"), "rebalance": json.loads(row.rebalance_json or "[]"), "risk": json.loads(row.risk_json or "{}"), "error_message": row.error_message}
