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
from src.core.strategies import (MarketContext, InstrumentSnapshot, Bar, StrategyDecision,
    FactorSnapshot, MarketEvent, PositionSnapshot, get_strategy)
from src.repositories.strategy_decision_repo import StrategyDecisionRepository
from src.services.strategy_context_service import StrategyContextService
from src.core.strategies.execution_planner import ExecutionPlanner
from src.core.strategies.executors import LiveExecutor

LIVE_ACCOUNTS = {"testS", "135129739"}


class LiveStrategyService:
    """Calculate a target portfolio and materialize its delta as QMT orders."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()
        self.data = StrategyLabDataRepository(self.db)
        self.contexts = StrategyContextService(self.db)
        self.positions = QmtPositionRepository(self.db)
        self.orders = TradingOrderService(self.db)
        self.decisions = StrategyDecisionRepository(self.db)
        self.planner = ExecutionPlanner()

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
            row.rebalance_frequency_days = int(payload.get("rebalance_frequency_days", 1))
            row.event_check_enabled = bool(payload.get("event_check_enabled", True))
            row.data_sync_before_run = bool(payload.get("data_sync_before_run", True))
            row.updated_at = datetime.now()
            session.commit(); session.refresh(row)
            return self._config_payload(row)

    def run(self, *, trade_date: date, preview: bool = False, mode: str = "rebalance") -> Dict[str, Any]:
        if mode not in {"rebalance", "event_check"}:
            raise ValueError("mode must be rebalance or event_check")
        config = self._latest_config()
        if config is None:
            raise ValueError("live strategy config is not configured")
        # 盘中数据检查：当日必须存在 run_kind='intraday' 且已完成的同步记录，
        # 避免用旧数据/空数据下单；新鲜度由 trade_date 精确匹配当日保证。
        sync = self.data.latest_sync_run(run_kind="intraday", trade_date=trade_date)
        if config.data_sync_before_run and (sync is None or sync.status != "completed" or getattr(sync, "quality_status", "usable") not in ("usable", "unknown")):
            payload = {"trade_date": trade_date.isoformat(), "mode": mode, "target": {}, "current": {}, "rebalance": [], "decisions": [], "strategy_version": config.strategy_version, "risk": {"passed": False, "reason": "intraday_sync_unavailable"}, "skip_reason": "intraday_sync_unavailable"}
            if preview: return payload
            raise ValueError("intraday data sync is not completed; order generation is blocked")
        with self.db.get_session() as session:
            existing = session.execute(select(LiveStrategyRun).where(
                LiveStrategyRun.config_id == config.id, LiveStrategyRun.trade_date == trade_date, LiveStrategyRun.mode == mode,
            )).scalars().first()
            if existing is not None:
                return self._run_payload(existing)

        params = json.loads(config.parameters_json or "{}")
        if mode == "rebalance" and (config.rebalance_frequency_days or 1) > 1:
            with self.db.get_session() as session:
                previous = session.execute(select(LiveStrategyRun).where(LiveStrategyRun.config_id == config.id, LiveStrategyRun.mode == "rebalance", LiveStrategyRun.status == "completed").order_by(desc(LiveStrategyRun.trade_date))).scalars().first()
            if previous and (trade_date - previous.trade_date).days < int(config.rebalance_frequency_days):
                return {"trade_date": trade_date.isoformat(), "mode": mode, "target": {}, "current": {}, "rebalance": [], "decisions": [], "strategy_version": config.strategy_version, "skip_reason": "rebalance_frequency"}
        metadata = next((x for x in list_builtin_strategies() if x["strategy_id"] == config.strategy_id), None)
        if metadata is None:
            raise ValueError(f"unsupported strategy_id: {config.strategy_id}")
        defaults = {p["key"]: p.get("default") for p in metadata.get("parameters", [])}
        defaults.update(params)
        params = defaults
        symbols = json.loads(config.symbols_json or "[]")
        current_rows = self.positions.list(account=config.qmt_account)
        # QMT reports the whole account (stocks, funds, convertible bonds, ...).
        # Live CB strategies must never create orders for non-CB holdings.
        cb_symbols = set(self.data.list_cb_basic_codes(market="cn"))
        current = {r.symbol: float(r.volume) for r in current_rows if r.symbol in cb_symbols}
        context = self.contexts.convertible_bonds(
            trade_date=trade_date, symbols=symbols,
            positions={s: PositionSnapshot(quantity=v, available_quantity=v) for s, v in current.items()},
            account=config.qmt_account,
        )
        bars = context.bars
        decisions = get_strategy(config.strategy_id).evaluate(context, mode=mode, parameters=params)
        # Target exits are portfolio reconciliation, not strategy logic.
        if mode == "rebalance":
            selected = {d.symbol for d in decisions if d.action == "buy" and d.symbol}
            decisions = list(decisions) + [StrategyDecision("sell", symbol=symbol,
                suggested_quantity=volume, reason="live_target_exit")
                for symbol, volume in current.items() if symbol not in selected and volume > 0]
        plan = self.planner.plan(decisions, context, lot_size=int(params.get("lot_size", 10)))
        target = {d.symbol: {"symbol": d.symbol, "symbol_name": d.symbol_name,
            "price": (bars.get(d.symbol) or [None])[-1].close if bars.get(d.symbol) else None,
            "quantity": next((o.quantity for o in plan.orders if o.symbol == d.symbol and o.side == "buy"), 0)}
            for d in decisions if d.action == "buy" and d.symbol}
        rebalance = [{"symbol": o.symbol, "side": o.side, "quantity": o.quantity,
                      "reason": o.decision.reason if o.decision else "planned"}
                     for o in plan.orders]
        diagnostics = {"skipped": [{"symbol": s.decision.symbol, "reason": s.reason} for s in plan.skipped],
                       "risk_checks": [c.__dict__ for c in plan.risk_checks]}
        payload = {"trade_date": trade_date.isoformat(), "mode": mode, "target": target, "current": current, "rebalance": rebalance, "decisions": [d.__dict__ for d in decisions], "strategy_version": config.strategy_version, "diagnostics": diagnostics}
        if preview:
            return payload
        with self.db.get_session() as session:
            run_uid = uuid4().hex
            run = LiveStrategyRun(run_uid=run_uid, config_id=config.id, qmt_account=config.qmt_account, trade_date=trade_date,
                                  status="completed", mode=mode, strategy_id=config.strategy_id, strategy_version=config.strategy_version,
                                  decision_count=len(decisions), order_count=len(rebalance), risk_status="passed",
                                  data_snapshot_at=datetime.now(), target_json=json.dumps(target), current_json=json.dumps(current), rebalance_json=json.dumps(rebalance), risk_json=json.dumps({"passed": all(c.passed for c in plan.risk_checks), **diagnostics}), completed_at=datetime.now())
            session.add(run); session.flush()
            run_id = int(run.id)
            batch_uid = uuid4().hex
            batch = LiveRebalanceBatch(batch_uid=batch_uid, run_id=run_id, qmt_account=config.qmt_account, status="pending", summary_json=json.dumps({"count": len(rebalance)}))
            session.add(batch); session.commit()
            batch_id = int(batch.id)
        names = {k: v.get("symbol_name") for k, v in target.items()}
        decision_ids = {}
        for d in decisions:
            record = self.decisions.create(strategy_id=config.strategy_id, mode=mode, trade_date=trade_date,
                action=d.action, symbol=d.symbol, symbol_name=d.symbol_name,
                strategy_version=config.strategy_version, account=config.qmt_account,
                market="cn", instrument_type="convertible_bond", target_amount=d.target_amount,
                suggested_quantity=d.suggested_quantity, reason=d.reason, risk_status=d.risk_status,
                decision_data=d.decision_data, live_run_id=run_id)
            decision_ids[d.symbol] = record.id
        LiveExecutor(self.orders).execute(plan, run_id=run_id, batch_id=batch_id,
            symbol_names=names, decision_ids=decision_ids)
        return {**payload, "run_id": run_id, "run_uid": run_uid, "batch_uid": batch_uid}

    def _latest_config(self):
        with self.db.get_session() as session:
            return session.execute(select(LiveStrategyConfig).order_by(desc(LiveStrategyConfig.id))).scalars().first()

    @staticmethod
    def _config_payload(row):
        return {"id": row.id, "name": row.name, "strategy_id": row.strategy_id, "strategy_version": row.strategy_version, "qmt_account": row.qmt_account, "enabled": row.enabled, "symbols": json.loads(row.symbols_json or "[]"), "parameters": json.loads(row.parameters_json or "{}"), "rebalance_frequency_days": row.rebalance_frequency_days or 1, "event_check_enabled": row.event_check_enabled if row.event_check_enabled is not None else True, "data_sync_before_run": row.data_sync_before_run if row.data_sync_before_run is not None else True}

    @staticmethod
    def _run_payload(row):
        return {"id": row.id, "run_uid": row.run_uid, "trade_date": row.trade_date.isoformat(), "status": row.status, "mode": getattr(row, "mode", "rebalance"), "strategy_id": getattr(row, "strategy_id", None), "strategy_version": getattr(row, "strategy_version", None), "decision_count": getattr(row, "decision_count", 0), "order_count": getattr(row, "order_count", 0), "target": json.loads(row.target_json or "{}"), "current": json.loads(row.current_json or "{}"), "rebalance": json.loads(row.rebalance_json or "[]"), "risk": json.loads(row.risk_json or "{}"), "error_message": row.error_message}
