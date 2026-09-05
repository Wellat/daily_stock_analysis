# -*- coding: utf-8 -*-
"""Live convertible-bond strategy orchestration."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
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
        if row is None:
            return None
        payload = self._config_payload(row)
        # 下次调仓日按“最近一次成功调仓 + N 个交易日”推导，只读展示，不落库
        next_date = self._rebalance_schedule(row)["next_rebalance_date"]
        payload["next_rebalance_date"] = next_date.isoformat() if next_date else None
        return payload

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
            payload = self._config_payload(row)
        next_date = self._rebalance_schedule(row)["next_rebalance_date"]
        payload["next_rebalance_date"] = next_date.isoformat() if next_date else None
        return payload

    def run(self, *, trade_date: date, preview: bool = False, mode: str = "auto") -> Dict[str, Any]:
        # ---- 门禁：mode/配置校验、盘中数据同步、当日幂等、调仓频率 ----
        if mode not in {"auto", "rebalance", "event_check"}:
            raise ValueError("mode must be auto, rebalance or event_check")
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
        # mode=auto 按调仓节奏推导：到期调仓、未到期事件检查。锚点只在调仓
        # 真正成功后前移，失败/漏跑自然表现为“已过期”，下个交易日自动补跑。
        schedule = self._rebalance_schedule(config, trade_date=trade_date)
        # 当日已有成功调仓：auto 与显式 rebalance 均幂等返回该记录，不降级
        # 补跑 event_check（调仓当日的持仓风险已在调仓流程中处理）
        if mode != "event_check":
            with self.db.get_session() as session:
                done = session.execute(select(LiveStrategyRun).where(
                    LiveStrategyRun.config_id == config.id, LiveStrategyRun.trade_date == trade_date,
                    LiveStrategyRun.mode == "rebalance", LiveStrategyRun.status == "completed",
                )).scalars().first()
                if done is not None:
                    return self._run_payload(done)
        resolved_mode = mode if mode != "auto" else ("rebalance" if schedule["due"] else "event_check")
        with self.db.get_session() as session:
            existing = session.execute(select(LiveStrategyRun).where(
                LiveStrategyRun.config_id == config.id, LiveStrategyRun.trade_date == trade_date, LiveStrategyRun.mode == resolved_mode,
            )).scalars().first()
            # 幂等只认成功 run；当日失败的 run 允许重试（复用原行）
            if existing is not None and existing.status == "completed":
                return self._run_payload(existing)
        # 显式 rebalance 未到期仍跳过（显式请求同样尊重频率约束）
        if mode == "rebalance" and not schedule["due"]:
            return {"trade_date": trade_date.isoformat(), "mode": resolved_mode, "target": {}, "current": {}, "rebalance": [], "decisions": [], "strategy_version": config.strategy_version, "skip_reason": "rebalance_frequency"}
        # ---- 算目标组合：合并策略参数、取 CB 持仓、构建上下文、策略评估 ----
        params = json.loads(config.parameters_json or "{}")
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
        cb_symbols = set(self.data.list_cb_basic_codes(market="cn", status="active"))
        current = {r.symbol: float(r.volume) for r in current_rows if r.symbol in cb_symbols}
        context = self.contexts.convertible_bonds(
            trade_date=trade_date, symbols=symbols,
            positions={s: PositionSnapshot(quantity=v, available_quantity=v) for s, v in current.items()},
            account=config.qmt_account,
        )
        bars = context.bars
        decisions = get_strategy(config.strategy_id).evaluate(context, mode=resolved_mode, parameters=params)
        # ---- 对账补卖：持仓中不在目标内的转债补卖出，使组合向目标收敛 ----
        # Target exits are portfolio reconciliation, not strategy logic.
        if resolved_mode == "rebalance":
            selected = {d.symbol for d in decisions if d.action == "buy" and d.symbol}
            decisions = list(decisions) + [StrategyDecision("sell", symbol=symbol,
                suggested_quantity=volume, reason="live_target_exit")
                for symbol, volume in current.items() if symbol not in selected and volume > 0]
        # ---- 排订单：决策转执行计划（手数取整、风控检查）----
        plan = self.planner.plan(decisions, context, lot_size=int(params.get("lot_size", 10)))
        # 组装结果载荷：目标组合 / 当前持仓 / 调仓明细 / 诊断信息
        target = {d.symbol: {"symbol": d.symbol, "symbol_name": d.symbol_name,
            "price": (bars.get(d.symbol) or [None])[-1].close if bars.get(d.symbol) else None,
            "quantity": next((o.quantity for o in plan.orders if o.symbol == d.symbol and o.side == "buy"), 0)}
            for d in decisions if d.action == "buy" and d.symbol}
        rebalance = [{"symbol": o.symbol, "side": o.side, "quantity": o.quantity,
                      "reason": o.decision.reason if o.decision else "planned"}
                     for o in plan.orders]
        diagnostics = {"skipped": [{"symbol": s.decision.symbol, "reason": s.reason} for s in plan.skipped],
                       "risk_checks": [c.__dict__ for c in plan.risk_checks]}
        payload = {"trade_date": trade_date.isoformat(), "mode": resolved_mode, "requested_mode": mode, 
        "target": target, "current": current, "rebalance": rebalance, "decisions": [d.__dict__ for d in decisions], 
        "strategy_version": config.strategy_version, "diagnostics": diagnostics}
        if preview:
            # preview 模式到此返回：只计算展示，不落库、不下单
            return payload
        # ---- 落库：写入 run / batch / 决策记录 ----
        # run 先落 running，执行器返回后才置 completed；(config, trade_date, mode)
        # 有唯一约束，当日重试复用原行，不插重复记录。
        with self.db.get_session() as session:
            run_uid = uuid4().hex
            run = session.execute(select(LiveStrategyRun).where(
                LiveStrategyRun.config_id == config.id, LiveStrategyRun.trade_date == trade_date, LiveStrategyRun.mode == resolved_mode,
            )).scalars().first()
            if run is None:
                run = LiveStrategyRun(config_id=config.id, qmt_account=config.qmt_account, trade_date=trade_date)
                session.add(run)
            run.run_uid = run_uid
            run.mode = resolved_mode
            run.status = "running"
            run.strategy_id = config.strategy_id
            run.strategy_version = config.strategy_version
            run.decision_count = len(decisions)
            run.order_count = len(rebalance)
            run.risk_status = "passed"
            run.data_snapshot_at = datetime.now()
            run.target_json = json.dumps(target)
            run.current_json = json.dumps(current)
            run.rebalance_json = json.dumps(rebalance)
            run.risk_json = json.dumps({"passed": all(c.passed for c in plan.risk_checks), **diagnostics})
            run.completed_at = None
            run.error_message = None
            session.flush()
            run_id = int(run.id)
            # batch 与 run 一一对应（run_id 唯一约束），当日重试复用原行
            batch = session.execute(select(LiveRebalanceBatch).where(LiveRebalanceBatch.run_id == run_id)).scalars().first()
            batch_uid = uuid4().hex
            if batch is None:
                batch = LiveRebalanceBatch(run_id=run_id, qmt_account=config.qmt_account)
                session.add(batch)
            batch.batch_uid = batch_uid
            batch.status = "pending"
            batch.summary_json = json.dumps({"count": len(rebalance)})
            session.commit()
            batch_id = int(batch.id)
        names = {k: v.get("symbol_name") for k, v in target.items()}
        decision_ids = {}
        try:
            for d in decisions:
                record = self.decisions.create(strategy_id=config.strategy_id, mode=resolved_mode, trade_date=trade_date,
                    action=d.action, symbol=d.symbol, symbol_name=d.symbol_name,
                    strategy_version=config.strategy_version, account=config.qmt_account,
                    market="cn", instrument_type="convertible_bond", target_amount=d.target_amount,
                    suggested_quantity=d.suggested_quantity, reason=d.reason, risk_status=d.risk_status,
                    decision_data=d.decision_data, live_run_id=run_id)
                decision_ids[d.symbol] = record.id
            # ---- 下单：执行计划物化为 QMT 订单 ----
            LiveExecutor(self.orders).execute(plan, run_id=run_id, batch_id=batch_id,
                symbol_names=names, decision_ids=decision_ids)
        except Exception as exc:
            # 下单链路失败：run 落 failed 且不前移调仓锚点（status!=completed），下个交易日自动补跑
            with self.db.get_session() as session:
                failed = session.get(LiveStrategyRun, run_id)
                if failed is not None:
                    failed.status = "failed"
                    failed.error_message = str(exc)
                session.commit()
            raise
        with self.db.get_session() as session:
            finished = session.get(LiveStrategyRun, run_id)
            if finished is not None:
                finished.status = "completed"
                finished.completed_at = datetime.now()
            session.commit()
        return {**payload, "run_id": run_id, "run_uid": run_uid, "batch_uid": batch_uid}

    def _rebalance_schedule(self, config: LiveStrategyConfig, *, trade_date: Optional[date] = None) -> Dict[str, Any]:
        """按交易日频率推导调仓节奏（不落库，全部由 run 记录推导）。

        锚点取最近一次成功调仓（mode=rebalance 且 status=completed）的 trade_date。
        锚点只在调仓真正成功后前移：失败、门禁拦截、漏跑都不影响锚点，
        自然表现为“已过期”，到期判定在下个交易日重新成立，自动补跑。
        """
        frequency = max(int(config.rebalance_frequency_days or 1), 1)
        with self.db.get_session() as session:
            anchor = session.execute(
                select(LiveStrategyRun.trade_date).where(
                    LiveStrategyRun.config_id == config.id,
                    LiveStrategyRun.mode == "rebalance",
                    LiveStrategyRun.status == "completed",
                ).order_by(desc(LiveStrategyRun.trade_date)).limit(1)
            ).scalar_one_or_none()
        if anchor is None:
            return {"anchor": None, "next_rebalance_date": None, "due": True}
        next_date = self._nth_trading_day_after(anchor, frequency)
        return {"anchor": anchor, "next_rebalance_date": next_date, "due": trade_date is None or trade_date >= next_date}

    @staticmethod
    def _nth_trading_day_after(anchor: date, count: int) -> date:
        """anchor 之后第 count 个交易日（含节假日跳过；日历不可用时退化为自然日）。"""
        from src.core.trading_calendar import is_market_open

        found = 0
        cursor = anchor + timedelta(days=1)
        while found < count:
            if is_market_open("cn", cursor):
                found += 1
            cursor += timedelta(days=1)
        return cursor - timedelta(days=1)

    def _latest_config(self):
        with self.db.get_session() as session:
            return session.execute(select(LiveStrategyConfig).order_by(desc(LiveStrategyConfig.id))).scalars().first()

    @staticmethod
    def _config_payload(row):
        return {"id": row.id, "name": row.name, "strategy_id": row.strategy_id, "strategy_version": row.strategy_version, "qmt_account": row.qmt_account, "enabled": row.enabled, "symbols": json.loads(row.symbols_json or "[]"), "parameters": json.loads(row.parameters_json or "{}"), "rebalance_frequency_days": row.rebalance_frequency_days or 1, "event_check_enabled": row.event_check_enabled if row.event_check_enabled is not None else True, "data_sync_before_run": row.data_sync_before_run if row.data_sync_before_run is not None else True}

    @staticmethod
    def _run_payload(row):
        return {"id": row.id, "run_uid": row.run_uid, "trade_date": row.trade_date.isoformat(), "status": row.status, "mode": getattr(row, "mode", "rebalance"), "strategy_id": getattr(row, "strategy_id", None), "strategy_version": getattr(row, "strategy_version", None), "decision_count": getattr(row, "decision_count", 0), "order_count": getattr(row, "order_count", 0), "target": json.loads(row.target_json or "{}"), "current": json.loads(row.current_json or "{}"), "rebalance": json.loads(row.rebalance_json or "[]"), "risk": json.loads(row.risk_json or "{}"), "error_message": row.error_message}
