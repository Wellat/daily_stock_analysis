# -*- coding: utf-8 -*-
"""Live convertible-bond strategy orchestration."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy import desc, select

from src.repositories.strategy_lab.data_repo import StrategyLabDataRepository
from src.repositories.qmt_position_repo import QmtPositionRepository
from src.services.trading_order_service import TradingOrderService
from src.storage import (DatabaseManager, LiveRebalanceBatch, LiveStrategyConfig, LiveStrategyRun,
    StrategyLabCbBasic, StrategyLabCbDailyFactor)
from src.core.strategy_lab.engine import list_builtin_strategies
from src.core.strategies import (MarketContext, InstrumentSnapshot, Bar, StrategyDecision,
    FactorSnapshot, MarketEvent, PositionSnapshot, get_strategy)
from src.repositories.strategy_decision_repo import StrategyDecisionRepository
from src.services.strategy_context_service import StrategyContextService
from src.core.strategies.execution_planner import ExecutionPlanner
from src.core.strategies.executors import LiveExecutor

logger = logging.getLogger(__name__)

LIVE_ACCOUNTS = {"testS", "135129739"}


class LiveStrategyService:
    """Calculate a target portfolio and materialize its delta as QMT orders."""

    # 溢价率覆盖率下单门禁：当日 active 转债溢价率缺失比例超过 5% 视为
    # 因子数据被污染/未就绪，禁止从残缺候选生成调仓订单（preview 放行并标注）。
    # 2026-09-24 事故：转股价被同步清空导致最低溢价一批全部缺失，
    # 策略从残缺候选选债并把全部低溢价持仓卖出。
    PREMIUM_COVERAGE_MIN_RATIO = 0.95

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
            row.last_trading_day_exit_enabled = bool(payload.get("last_trading_day_exit_enabled", True))
            row.last_trading_day_exit_buffer_days = max(int(payload.get("last_trading_day_exit_buffer_days", 1)), 0)
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
            logger.warning("[LiveStrategy] %s blocked for %s: no completed intraday sync run", "preview" if preview else "run", trade_date)
            payload = {"trade_date": trade_date.isoformat(), "mode": mode, "target": {}, "current": {}, "rebalance": [], "decisions": [], "strategy_version": config.strategy_version, "risk": {"passed": False, "reason": "intraday_sync_unavailable"}, "skip_reason": "intraday_sync_unavailable"}
            if preview: return payload
            raise ValueError("intraday data sync is not completed; order generation is blocked")
        # 数据完整性校验：统计当日 active 转债中溢价率缺失的标的，
        # 供观测日志与后面的下单门禁共用。
        total_count, missing_codes = self._premium_data_completeness(trade_date)
        self._log_data_completeness(trade_date, total_count, missing_codes)
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
                    logger.info("[LiveStrategy] rebalance already completed for %s, returning existing run id=%s", trade_date, done.id)
                    return self._run_payload(done)
        resolved_mode = mode if mode != "auto" else ("rebalance" if schedule["due"] else "event_check")
        # 非调仓日的事件检查受总闸控制：event_check_enabled=False 时 auto 直接跳过
        # （不落 run 记录）；显式 mode="event_check" 不受总闸限制，仍可强制执行。
        event_check_enabled = config.event_check_enabled if config.event_check_enabled is not None else True
        if mode == "auto" and not schedule["due"] and not event_check_enabled:
            logger.info("[LiveStrategy] event check disabled for %s, auto run skipped (anchor=%s next_rebalance=%s)",
                        trade_date.isoformat(), schedule["anchor"], schedule["next_rebalance_date"])
            return {"trade_date": trade_date.isoformat(), "mode": "event_check", "target": {}, "current": {},
                    "rebalance": [], "decisions": [], "strategy_version": config.strategy_version,
                    "skip_reason": "event_check_disabled"}
        logger.info("[LiveStrategy] run start trade_date=%s mode=%s -> %s (anchor=%s next_rebalance=%s due=%s)",
                    trade_date.isoformat(), mode, resolved_mode,
                    schedule["anchor"], schedule["next_rebalance_date"], schedule["due"])
        with self.db.get_session() as session:
            existing = session.execute(select(LiveStrategyRun).where(
                LiveStrategyRun.config_id == config.id, LiveStrategyRun.trade_date == trade_date, LiveStrategyRun.mode == resolved_mode,
            )).scalars().first()
            # 幂等只认成功 run；当日失败的 run 允许重试（复用原行）
            if existing is not None and existing.status == "completed":
                return self._run_payload(existing)
        # 显式 rebalance 未到期仍跳过（显式请求同样尊重频率约束）
        if mode == "rebalance" and not schedule["due"]:
            logger.info("[LiveStrategy] explicit rebalance skipped for %s: not due (next=%s)",
                        trade_date, schedule["next_rebalance_date"])
            return {"trade_date": trade_date.isoformat(), "mode": resolved_mode, "target": {}, "current": {}, "rebalance": [], "decisions": [], "strategy_version": config.strategy_version, "skip_reason": "rebalance_frequency"}
        # ---- 溢价率覆盖率门禁：缺失过多说明因子数据被污染/未就绪，
        # 从残缺候选里选债会系统性买贵卖贱，禁止下单（event_check 不依赖溢价率，不受限）。
        if resolved_mode == "rebalance" and total_count > 0:
            coverage = 1.0 - len(missing_codes) / total_count
            if coverage < self.PREMIUM_COVERAGE_MIN_RATIO:
                logger.warning(
                    "[LiveStrategy] %s blocked for %s: premium coverage %.1f%% < %.0f%% (missing %d/%d)",
                    "preview" if preview else "run", trade_date,
                    coverage * 100.0, self.PREMIUM_COVERAGE_MIN_RATIO * 100.0,
                    len(missing_codes), total_count,
                )
                payload = {"trade_date": trade_date.isoformat(), "mode": mode, "target": {}, "current": {},
                    "rebalance": [], "decisions": [], "strategy_version": config.strategy_version,
                    "risk": {"passed": False, "reason": "premium_coverage_low",
                             "coverage": round(coverage, 4), "missing": len(missing_codes), "total": total_count},
                    "skip_reason": "premium_coverage_low"}
                if preview:
                    return payload
                raise ValueError(
                    f"premium coverage {coverage:.1%} is below {self.PREMIUM_COVERAGE_MIN_RATIO:.0%}; "
                    "order generation is blocked"
                )
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
        # ---- 最后交易日风控层：强赎/到期停止交易前强制退出 + 买入排除 ----
        # last_trading_date 来自盘后 cb_basic 同步（T-1 口径）；退市后持仓会被
        # active 过滤隐身、永远无法再生成卖单，所以退出必须发生在还 active 时。
        exit_buffer = getattr(config, "last_trading_day_exit_buffer_days", 1)
        if exit_buffer is None:
            exit_buffer = 1
        forced_exit: Dict[str, Dict[str, Any]] = {}
        if getattr(config, "last_trading_day_exit_enabled", True):
            ltd_map = self.data.get_cb_last_trading_dates(
                market="cn", codes=[i.symbol for i in context.instruments] + list(current)
            )
            remaining = {
                symbol: self._remaining_trading_days(trade_date, last_day)
                for symbol, last_day in ltd_map.items()
            }
            # 买入排除：剩余交易日（含当日）<= 提前量+3 的标的不允许新建仓
            buy_blocked = {s for s, n in remaining.items() if n <= exit_buffer + 3}
            if buy_blocked:
                context = replace(
                    context,
                    instruments=[
                        replace(i, tradable=False) if i.symbol in buy_blocked else i
                        for i in context.instruments
                    ],
                )
            # 强制卖出：剩余交易日（含当日）<= 提前量+1 的持仓全量退出
            for symbol, n in remaining.items():
                if symbol in current and current[symbol] > 0 and n <= exit_buffer + 1:
                    forced_exit[symbol] = {
                        "last_trading_date": ltd_map[symbol].isoformat(),
                        "remaining_trading_days": n,
                    }
        bars = context.bars
        decisions = get_strategy(config.strategy_id).evaluate(context, mode=resolved_mode, parameters=params)
        # 强制退出覆盖策略对同一标的的全部决策（hold/exit/buy），保证全量卖出
        if forced_exit:
            exit_decisions = [
                StrategyDecision(
                    "exit", symbol=symbol, suggested_quantity=current[symbol],
                    reason="last_trading_day_exit", decision_data=info,
                )
                for symbol, info in forced_exit.items()
            ]
            decisions = [d for d in decisions if d.symbol not in forced_exit] + exit_decisions
            logger.info("[LiveStrategy] last-trading-day exit: %s",
                        {s: info["remaining_trading_days"] for s, info in forced_exit.items()})
        # ---- 对账补卖：持仓中不在目标内的转债补卖出，使组合向目标收敛 ----
        # Target exits are portfolio reconciliation, not strategy logic.
        if resolved_mode == "rebalance":
            selected = {d.symbol for d in decisions if d.action == "buy" and d.symbol}
            decisions = list(decisions) + [StrategyDecision("sell", symbol=symbol,
                suggested_quantity=volume, reason="live_target_exit")
                for symbol, volume in current.items()
                if symbol not in selected and symbol not in forced_exit and volume > 0]
        # 对账卖出/事件检查等路径只带代码不带名称，统一从上下文补齐，
        # 保证决策记录、订单的 symbol_name 完整（历史缺口曾导致前端名称列为空）
        names_by_symbol = {i.symbol: i.name for i in context.instruments if i.symbol and i.name}
        decisions = [d if d.symbol_name else replace(d, symbol_name=names_by_symbol.get(d.symbol))
                     for d in decisions]
        # 三阶段输出逐段落日志（evaluate → plan → execute），便于事后按 run 排查
        grouped: Dict[str, list] = {}
        for d in decisions:
            amount = d.suggested_quantity if d.suggested_quantity is not None else d.target_amount
            grouped.setdefault(d.action, []).append(f"{d.symbol}({amount})" if amount is not None else str(d.symbol))
        logger.info("[LiveStrategy] evaluate: %d decisions %s", len(decisions), grouped)
        # ---- 排订单：决策转执行计划（手数取整、风控检查）----
        plan = self.planner.plan(decisions, context, lot_size=int(params.get("lot_size", 10)))
        logger.info("[LiveStrategy] plan: orders=[%s] skipped=[%s] risk_checks=%s",
                    ", ".join(f"{o.symbol}:{o.side}:{o.quantity:g}" for o in plan.orders) or "-",
                    ", ".join(f"{s.decision.symbol}({s.reason})" for s in plan.skipped) or "-",
                    [(c.name, c.passed) for c in plan.risk_checks])
        # 组装结果载荷：目标组合 / 当前持仓 / 调仓明细 / 诊断信息
        target = {d.symbol: {"symbol": d.symbol, "symbol_name": d.symbol_name,
            "price": (bars.get(d.symbol) or [None])[-1].close if bars.get(d.symbol) else None,
            "quantity": next((o.quantity for o in plan.orders if o.symbol == d.symbol and o.side == "buy"), 0)}
            for d in decisions if d.action == "buy" and d.symbol}
        # 调仓明细补展示字段：名称取策略上下文、溢价率取当日因子快照（缺失回退空）
        premium_by_symbol = {s: f.premium_rate for s, f in context.factors.items()}
        rebalance = [{"symbol": o.symbol, "symbol_name": names_by_symbol.get(o.symbol),
                      "premium_rate": premium_by_symbol.get(o.symbol),
                      "side": o.side, "quantity": o.quantity,
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
        # 订单名称覆盖全部买卖标的（卖出单此前无名称）
        names = dict(names_by_symbol)
        names.update({k: v.get("symbol_name") for k, v in target.items() if v.get("symbol_name")})
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
            executed = LiveExecutor(self.orders).execute(plan, run_id=run_id, batch_id=batch_id,
                symbol_names=names, decision_ids=decision_ids)
            reused = [item for item in executed if isinstance(item, dict) and item.get("reused")]
            logger.info("[LiveStrategy] execute: %d order(s) created for run_id=%s%s",
                        len(executed), run_id,
                        f", reused existing order ids={[item.get('id') for item in reused]}" if reused else "")
        except Exception as exc:
            # 下单链路失败：run 落 failed 且不前移调仓锚点（status!=completed），下个交易日自动补跑
            logger.exception("[LiveStrategy] run failed run_id=%s: %s", run_id, exc)
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
        logger.info("[LiveStrategy] run completed run_id=%s batch=%s mode=%s decisions=%d orders=%d",
                    run_id, batch_uid, resolved_mode, len(decisions), len(rebalance))
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

    @staticmethod
    def _remaining_trading_days(trade_date: date, last_day: date) -> int:
        """trade_date 当日起至最后交易日（含两端）的交易日数。

        已过最后交易日返回 0（临近退市但仍未 delisted 的持仓必须立刻退出）；
        超过 30 个自然日即超出任何风控窗口，直接返回大数避免无谓逐日计数。
        """
        from src.core.trading_calendar import is_market_open

        if last_day < trade_date:
            return 0
        if (last_day - trade_date).days > 30:
            return 999
        count = 0
        cursor = trade_date
        while cursor <= last_day:
            if is_market_open("cn", cursor):
                count += 1
            cursor += timedelta(days=1)
        return count

    def _premium_data_completeness(self, trade_date: date) -> tuple[int, list[str]]:
        """统计当日 active 转债（有 close）总数与溢价率缺失代码，供观测日志与下单门禁共用。

        查询失败时返回 (0, [])：门禁视为无法判定而放行，不因观测查询故障拦截交易。
        """
        try:
            with self.db.get_session() as session:
                total = session.execute(
                    select(StrategyLabCbDailyFactor.bond_code)
                    .join(StrategyLabCbBasic, StrategyLabCbBasic.bond_code == StrategyLabCbDailyFactor.bond_code)
                    .where(
                        StrategyLabCbBasic.market == "cn",
                        StrategyLabCbBasic.status == "active",
                        StrategyLabCbDailyFactor.trade_date == trade_date,
                        StrategyLabCbDailyFactor.close.is_not(None),
                    )
                ).scalars().all()
                missing = session.execute(
                    select(StrategyLabCbDailyFactor.bond_code)
                    .join(StrategyLabCbBasic, StrategyLabCbBasic.bond_code == StrategyLabCbDailyFactor.bond_code)
                    .where(
                        StrategyLabCbBasic.market == "cn",
                        StrategyLabCbBasic.status == "active",
                        StrategyLabCbDailyFactor.trade_date == trade_date,
                        StrategyLabCbDailyFactor.close.is_not(None),
                        StrategyLabCbDailyFactor.premium_rate.is_(None),
                    )
                ).scalars().all()
            return len(total), sorted(missing)
        except Exception as exc:  # noqa: BLE001 - 观测/门禁查询失败不影响主流程
            logger.warning("[LiveStrategy] 溢价率数据完整性查询失败: %s", exc)
            return 0, []

    def _log_data_completeness(self, trade_date: date, total_count: int, missing: list[str]) -> None:
        """打印当日溢价率完整性观测日志（数据由 _premium_data_completeness 提供）。

        盘中同步可能因正股行情拉取失败或 cb_basic 转股价缺失导致部分标的
        premium_rate 缺失，策略会把这些标的当作"无溢价率"过滤，从而从残缺
        候选里选标的。这里只做观测性告警，帮助定位此类数据缺口。
        """
        if total_count <= 0:
            logger.warning("[LiveStrategy] %s 无当日 active 转债因子数据，策略可能选不出标的", trade_date)
            return
        ratio = len(missing) / total_count * 100.0
        logger.info(
            "[LiveStrategy] %s 数据完整性：active 转债 %d 只，溢价率缺失 %d 只（%.1f%%）",
            trade_date, total_count, len(missing), ratio,
        )
        if missing:
            logger.warning(
                "[LiveStrategy] %s 溢价率缺失标的（前 20 只）：%s",
                trade_date, ", ".join(sorted(missing)[:20]),
            )

    def _latest_config(self):
        with self.db.get_session() as session:
            return session.execute(select(LiveStrategyConfig).order_by(desc(LiveStrategyConfig.id))).scalars().first()

    @staticmethod
    def _config_payload(row):
        buffer_days = getattr(row, "last_trading_day_exit_buffer_days", None)
        return {"id": row.id, "name": row.name, "strategy_id": row.strategy_id, "strategy_version": row.strategy_version, "qmt_account": row.qmt_account, "enabled": row.enabled, "symbols": json.loads(row.symbols_json or "[]"), "parameters": json.loads(row.parameters_json or "{}"), "rebalance_frequency_days": row.rebalance_frequency_days or 1, "event_check_enabled": row.event_check_enabled if row.event_check_enabled is not None else True, "data_sync_before_run": row.data_sync_before_run if row.data_sync_before_run is not None else True, "last_trading_day_exit_enabled": getattr(row, "last_trading_day_exit_enabled", True) if getattr(row, "last_trading_day_exit_enabled", None) is not None else True, "last_trading_day_exit_buffer_days": buffer_days if buffer_days is not None else 1}

    @staticmethod
    def _run_payload(row):
        def _ts(value):
            return value.isoformat(sep=" ") if value else None
        return {"id": row.id, "run_uid": row.run_uid, "trade_date": row.trade_date.isoformat(), "status": row.status, "mode": getattr(row, "mode", "rebalance"), "strategy_id": getattr(row, "strategy_id", None), "strategy_version": getattr(row, "strategy_version", None), "qmt_account": row.qmt_account, "decision_count": getattr(row, "decision_count", 0), "order_count": getattr(row, "order_count", 0), "skip_reason": getattr(row, "skip_reason", None), "data_snapshot_at": _ts(getattr(row, "data_snapshot_at", None)), "completed_at": _ts(getattr(row, "completed_at", None)), "target": json.loads(row.target_json or "{}"), "current": json.loads(row.current_json or "{}"), "rebalance": json.loads(row.rebalance_json or "[]"), "risk": json.loads(row.risk_json or "{}"), "error_message": row.error_message}
