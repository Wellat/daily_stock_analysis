# -*- coding: utf-8 -*-
"""Live convertible-bond strategy orchestration."""

from __future__ import annotations

import json
import logging
import re
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
from src.core.strategy_lab.signal_params import SignalRule
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
        row = self._latest_config()
        if row is None:
            return None
        payload = self._config_payload(row)
        # 下次调仓日按“最近一次成功调仓 + N 个交易日”推导，只读展示，不落库
        next_date = self._rebalance_schedule(row)["next_rebalance_date"]
        payload["next_rebalance_date"] = next_date.isoformat() if next_date else None
        return payload

    def save_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """兼容层：PUT /config 旧「单实盘配置」语义 —— 更新最新 order 实例，无则新建。

        新调用方（策略管理页/实盘页表单）请走 create_instance / update_instance
        （按实例 id）；本方法保留单配置时代的调用语义（strategy_id 缺省
        double-low、可省略字段沿用现值），且只作用于 order 实例，不会把
        notify/回测实例抢占改写。允许在 order 模板间切换策略。
        """
        payload = {"strategy_id": "double-low", **payload}
        strategy_id = str(payload.get("strategy_id") or "double-low")
        if self._live_mode_of(strategy_id) != "order":
            raise ValueError(f"PUT /config only supports order-mode strategies, got: {strategy_id}")
        with self.db.get_session() as session:
            row = session.execute(select(LiveStrategyConfig).where(
                LiveStrategyConfig.strategy_id.in_(self._order_strategy_ids())
            ).order_by(desc(LiveStrategyConfig.id))).scalars().first()
            existing_id = row.id if row else None
        if existing_id is None:
            return self.create_instance({"name": "default", **payload})
        return self.update_instance(existing_id, payload, allow_strategy_switch=True)

    # ------------------------------------------------------------------
    # 策略实例 CRUD（模板 + 参数 + 标的 + 调度的统一载体）
    # ------------------------------------------------------------------

    def create_instance(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        values = self._validate_instance_payload(payload)
        with self.db.get_session() as session:
            row = LiveStrategyConfig(
                name=values["name"],
                description=values["description"],
                strategy_id=values["strategy_id"],
                strategy_version=values["strategy_version"],
                qmt_account=values["qmt_account"],
                enabled=values["enabled"],
                symbols_json=json.dumps(values["symbols"], ensure_ascii=False),
                parameters_json=json.dumps(values["parameters"], ensure_ascii=False),
                schedule_time=values["schedule_time"],
                **values["order_fields"],
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            out = self._config_payload(row)
        if self._live_mode_of(row.strategy_id) == "order":
            next_date = self._rebalance_schedule(row)["next_rebalance_date"]
            out["next_rebalance_date"] = next_date.isoformat() if next_date else None
        return out

    def update_instance(
        self, config_id: int, payload: Dict[str, Any], *, allow_strategy_switch: bool = False
    ) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = session.get(LiveStrategyConfig, config_id)
            if row is None:
                raise ValueError(f"strategy instance not found: {config_id}")
            values = self._validate_instance_payload(
                payload, existing=row, allow_strategy_switch=allow_strategy_switch
            )
            row.name = values["name"]
            row.description = values["description"]
            row.strategy_id = values["strategy_id"]
            row.strategy_version = values["strategy_version"]
            row.qmt_account = values["qmt_account"]
            row.enabled = values["enabled"]
            row.symbols_json = json.dumps(values["symbols"], ensure_ascii=False)
            row.parameters_json = json.dumps(values["parameters"], ensure_ascii=False)
            row.schedule_time = values["schedule_time"]
            for key, value in values["order_fields"].items():
                setattr(row, key, value)
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            out = self._config_payload(row)
        if self._live_mode_of(row.strategy_id) == "order":
            next_date = self._rebalance_schedule(row)["next_rebalance_date"]
            out["next_rebalance_date"] = next_date.isoformat() if next_date else None
        return out

    def delete_instance(self, config_id: int) -> Dict[str, Any]:
        """硬删实例；历史运行记录（config_id 悬空）由展示层按 strategy_id 兜底。"""
        with self.db.get_session() as session:
            row = session.get(LiveStrategyConfig, config_id)
            if row is None:
                raise ValueError(f"strategy instance not found: {config_id}")
            session.delete(row)
            session.commit()
        return {"deleted": True, "id": config_id}

    def _validate_instance_payload(
        self, payload: Dict[str, Any], *, existing=None, allow_strategy_switch: bool = False
    ) -> Dict[str, Any]:
        """校验并归一实例字段；existing 非 None 时未提供的字段沿用现行值。

        校验按模板 live_mode 分支：order 需要 QMT 账户与调仓/风控字段；
        notify 需要非空标的；仅回测模板（live_mode=None）不参与调度，
        强制未启用。strategy_id 创建后不可换模板（兼容层允许 order 内切换）。
        """
        strategy_id = str(payload.get("strategy_id") or "").strip()
        if existing is not None:
            if strategy_id and strategy_id != existing.strategy_id:
                same_mode = self._live_mode_of(strategy_id) == self._live_mode_of(existing.strategy_id)
                if not (allow_strategy_switch and same_mode):
                    raise ValueError(
                        "strategy_id cannot be changed after creation; create a new instance instead"
                    )
            strategy_id = existing.strategy_id if not strategy_id else strategy_id
        if not strategy_id:
            raise ValueError("strategy_id is required")
        metadata = next((x for x in list_builtin_strategies() if x["strategy_id"] == strategy_id), None)
        if metadata is None:
            raise ValueError(f"unsupported strategy_id: {strategy_id}")
        live_mode = metadata.get("live_mode")

        name = str(payload.get("name") or "").strip() if payload.get("name") is not None else ""
        if existing is None and not name:
            raise ValueError("instance name is required")
        if not name and existing is not None:
            name = existing.name
        if len(name) > 100:
            raise ValueError("instance name must be at most 100 characters")

        raw_desc = payload.get("description")
        description = (str(raw_desc).strip() if raw_desc is not None else None)
        if description is None and existing is not None:
            description = existing.description
        if description and len(description) > 500:
            raise ValueError("instance description must be at most 500 characters")

        symbols = [str(x).strip() for x in payload.get("symbols") or [] if str(x).strip()]
        if not symbols and existing is not None:
            symbols = json.loads(existing.symbols_json or "[]")
        if live_mode == "notify" and not symbols:
            raise ValueError("notify instance requires at least one symbol")

        params = self._normalize_strategy_parameters(metadata, payload.get("parameters"))
        if params is None and existing is not None:
            params = json.loads(existing.parameters_json or "{}")

        schedule_raw = payload.get("schedule_time")
        schedule_time = str(schedule_raw).strip() if schedule_raw is not None else None
        if schedule_time:
            if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", schedule_time):
                raise ValueError("schedule_time must be HH:MM (e.g. 14:35)")
        if not schedule_time and existing is not None:
            schedule_time = getattr(existing, "schedule_time", None) or "14:35"

        enabled = bool(payload.get("enabled")) if payload.get("enabled") is not None else (
            bool(existing.enabled) if existing is not None else False
        )
        if live_mode is None:
            enabled = False  # 仅回测实例不参与调度

        qmt_account = str(payload.get("qmt_account") or "").strip()
        if live_mode == "order":
            if not qmt_account and existing is not None:
                qmt_account = existing.qmt_account
            if qmt_account not in LIVE_ACCOUNTS:
                raise ValueError(f"qmt_account must be one of {', '.join(sorted(LIVE_ACCOUNTS))}")
        else:
            qmt_account = "-"

        order_fields: Dict[str, Any] = {}
        if live_mode == "order":
            def _keep(key: str, default, cast):
                raw = payload.get(key)
                if raw is None and existing is not None:
                    return cast(getattr(existing, key))
                return cast(raw) if raw is not None else default
            order_fields = {
                "rebalance_frequency_days": _keep("rebalance_frequency_days", 1, int),
                "event_check_enabled": _keep("event_check_enabled", True, bool),
                "data_sync_before_run": _keep("data_sync_before_run", True, bool),
                "last_trading_day_exit_enabled": _keep("last_trading_day_exit_enabled", True, bool),
                "last_trading_day_exit_buffer_days": max(_keep("last_trading_day_exit_buffer_days", 1, int), 0),
            }

        return {
            "name": name,
            "description": description or None,
            "strategy_id": strategy_id,
            "strategy_version": str(payload.get("strategy_version") or "v1"),
            "qmt_account": qmt_account,
            "enabled": enabled,
            "symbols": symbols,
            "parameters": params or {},
            "schedule_time": schedule_time or "14:35",
            "order_fields": order_fields,
        }

    @staticmethod
    def _normalize_strategy_parameters(metadata: Dict[str, Any], raw: Any) -> Optional[Dict[str, Any]]:
        """参数键按策略定义白名单校验并归一布尔；raw 为 None 时返回 None（沿用原值）。

        键名形态漂移（如驼峰）会绕过默认值合并、静默失效，必须在保存时拦截
        而不是等策略读参时走默认值。
        """
        if raw is None:
            return None
        definitions = {item["key"]: item for item in metadata.get("parameters", [])}
        params = dict(raw)
        # 信号族（notify）双轨参数：显式规则列表与默认规则族不占参数面板，
        # 但会被 SignalParams.from_parameters 消费（实验室保存实例、通知评估同源），
        # 按结构校验后收下；其余策略仍按白名单严格拒绝
        structured_keys = {"entry_rules", "exit_rules", "default_rules"}
        allow_structured = metadata.get("live_mode") == "notify"
        unknown = sorted(
            str(k) for k in params
            if k not in definitions and not (allow_structured and k in structured_keys)
        )
        if unknown:
            raise ValueError(f"unknown strategy parameters: {', '.join(unknown)}")
        if allow_structured:
            for key in ("entry_rules", "exit_rules"):
                if key not in params:
                    continue
                if not isinstance(params[key], list):
                    raise ValueError(f"{key} must be a list of rules")
                params[key] = [SignalRule.from_dict(item).to_dict() for item in params[key]]
            if "default_rules" in params:
                family = str(params["default_rules"]).strip()
                if family not in {"ma_gap", "macd_cross"}:
                    raise ValueError("default_rules must be one of ma_gap, macd_cross")
                params["default_rules"] = family
        for key, defn in definitions.items():
            if defn.get("type") != "boolean" or key not in params:
                continue
            value = params[key]
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value in (0, 1):
                params[key] = bool(value)
            elif isinstance(value, str) and value.strip().lower() in ("true", "false", "0", "1"):
                params[key] = value.strip().lower() in ("true", "1")
        return params

    def run(self, *, trade_date: date, preview: bool = False, mode: str = "auto", config_id: Optional[int] = None) -> Dict[str, Any]:
        # ---- 门禁：mode/配置校验、盘中数据同步、当日幂等、调仓频率 ----
        if mode not in {"auto", "rebalance", "event_check"}:
            raise ValueError("mode must be auto, rebalance or event_check")
        if config_id is not None:
            # 调度器按配置分派时显式指定行；notify 配置不属于本链路，直接拒绝
            with self.db.get_session() as session:
                config = session.get(LiveStrategyConfig, int(config_id))
            if config is not None and self._live_mode_of(config.strategy_id) != "order":
                raise ValueError(f"config {config_id} is not an order-mode live strategy")
        else:
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
        # ---- 止损风控层：持仓成本亏损达到阈值强制全量卖出 + 冷却期禁再买入 ----
        # 成本价取 QMT 持仓 open_price、现价取当日上下文最新收盘，与回测引擎同语义
        # （全量退出、止损日起 N 个交易日内不再入选候选）；终局强退优先级更高。
        stop_exit: Dict[str, Dict[str, Any]] = {}
        stop_cooldown: set = set()
        stop_loss_pct = float(params.get("stop_loss_pct") or 0)
        if stop_loss_pct > 0:
            cost_by_symbol = {r.symbol: r.open_price for r in current_rows if r.symbol in current}
            for symbol, volume in current.items():
                if symbol in forced_exit or volume <= 0:
                    continue
                cost = float(cost_by_symbol.get(symbol) or 0)
                close = (context.bars.get(symbol) or [None])[-1].close if context.bars.get(symbol) else None
                if cost <= 0 or close is None or close <= 0:
                    continue
                pnl_pct = (close - cost) / cost * 100.0
                if pnl_pct <= -stop_loss_pct + 1e-9:
                    stop_exit[symbol] = {"cost_price": cost, "close": close, "pnl_pct": round(pnl_pct, 2)}
            stop_cooldown |= set(stop_exit)
            cooldown_days = int(params.get("stop_loss_cooldown_days") or 0)
            if cooldown_days > 0:
                stop_cooldown |= self._stop_loss_cooldown_symbols(config, trade_date, cooldown_days)
            if stop_cooldown:
                context = replace(context, instruments=[
                    replace(i, tradable=False) if i.symbol in stop_cooldown else i
                    for i in context.instruments
                ])
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
        # 止损退出覆盖策略对同一标的的全部决策（hold/exit/buy），保证全量卖出
        if stop_exit:
            decisions = [d for d in decisions if d.symbol not in stop_exit] + [
                StrategyDecision("exit", symbol=symbol, suggested_quantity=current[symbol],
                    reason="stop_loss", decision_data=info)
                for symbol, info in stop_exit.items()
            ]
            logger.info("[LiveStrategy] stop-loss exit: %s",
                        {s: info["pnl_pct"] for s, info in stop_exit.items()})
        # ---- 对账补卖：持仓中不在目标内的转债补卖出，使组合向目标收敛 ----
        # Target exits are portfolio reconciliation, not strategy logic.
        if resolved_mode == "rebalance":
            selected = {d.symbol for d in decisions if d.action == "buy" and d.symbol}
            decisions = list(decisions) + [StrategyDecision("sell", symbol=symbol,
                suggested_quantity=volume, reason="live_target_exit")
                for symbol, volume in current.items()
                if symbol not in selected and symbol not in forced_exit and symbol not in stop_exit and volume > 0]
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
        # planner 的单只/单批硬顶对齐策略意图：入选数量与单只金额本身已是
        # 策略约束（max_positions、per_position_cash、权重上限），保留截断
        # 兜底但不允许默认硬顶（5 只、1 万元/只、5 万元/批）悄悄吃掉更大的
        # 目标持仓。单批买入预算以 account_capital 为准（QMT 链路无资金数据
        # 的近似闸门，存量持仓/实际现金由 QMT 侧最终兜底），未配置时退默认。
        buy_budget = self.planner.MAX_BUY_AMOUNT
        account_capital = float(params.get("account_capital") or 0)
        if account_capital > 0:
            buy_budget = max(buy_budget, account_capital)
        plan = self.planner.plan(decisions, context, lot_size=int(params.get("lot_size", 10)),
            cash=buy_budget,
            max_buy_symbols=max(int(params.get("max_positions") or 5), 1),
            max_buy_amount_each_symbol=max(self.planner.MAX_BUY_AMOUNT_EACH_SYMBOL, self._intended_max_buy_amount(decisions, context)))
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
    def _intended_max_buy_amount(decisions, context) -> float:
        """买入决策声明的单只最大金额（目标金额或固定张数×现价），供 planner 抬高单只硬顶。"""
        amounts = []
        for d in decisions:
            if d.action != "buy" or not d.symbol:
                continue
            if d.target_amount is not None:
                amounts.append(float(d.target_amount))
            elif d.suggested_quantity:
                bars = context.bars.get(d.symbol) or []
                price = bars[-1].close if bars else None
                if price:
                    amounts.append(float(d.suggested_quantity) * float(price))
        return max(amounts, default=0.0)

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
        """trade_date 当日起至最后交易日（含两端）的交易日数（真源在 trading_calendar）。"""
        from src.core.trading_calendar import remaining_trading_days

        return remaining_trading_days("cn", trade_date, last_day)

    def _stop_loss_cooldown_symbols(self, config: LiveStrategyConfig, trade_date: date, cooldown_days: int) -> set:
        """从历史止损决策推导仍在冷却期内的标的（止损日起 N 个交易日内禁再买入）。

        不落新表：止损事实以决策记录（reason=stop_loss）为准，冷却窗口由交易日历
        推导（(止损日, 当日] 间交易日数 <= N 即冷却中，与回测 bar 序号口径一致），
        历史决策修正/删除后冷却状态自然收敛。
        """
        from src.core.trading_calendar import cn_trading_days_between

        since = trade_date - timedelta(days=cooldown_days * 2 + 10)
        rows = self.decisions.list_by_reason_since(
            reason="stop_loss", trade_date_since=since,
            account=config.qmt_account, strategy_id=config.strategy_id,
        )
        blocked: set = set()
        for row in rows:
            if not row.symbol:
                continue
            sessions = cn_trading_days_between(row.trade_date, trade_date)
            if sessions and len(sessions) - 1 <= cooldown_days:
                blocked.add(row.symbol)
        return blocked

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

    def _latest_config(self, *, order_only: bool = True):
        """最新实盘配置行。

        order_only=True（默认）只取 live_mode=order 策略的行：notify 配置（ETF
        信号通知）也会落本表，任由「最新行」语义取到会抢占 GET /config 与
        转债实盘运行链路；无 order 行时回退最新行，保持单配置时代语义。
        """
        rows = self.list_config_rows()
        if not rows:
            return None
        if order_only:
            order_ids = self._order_strategy_ids()
            order_rows = [r for r in rows if r.strategy_id in order_ids]
            if order_rows:
                return order_rows[0]
        return rows[0]

    def list_config_rows(self):
        """全部实盘配置行（id 降序），多配置调度的数据源。"""
        with self.db.get_session() as session:
            return list(session.execute(
                select(LiveStrategyConfig).order_by(desc(LiveStrategyConfig.id))
            ).scalars())

    def list_configs(self) -> list[Dict[str, Any]]:
        """全部策略实例 payload（order/notify/仅回测），供配置列表 API/管理页合并。"""
        out = []
        last_runs = self._last_runs_by_config()
        for row in self.list_config_rows():
            payload = self._config_payload(row)
            next_date = self._rebalance_schedule(row)["next_rebalance_date"]
            payload["next_rebalance_date"] = next_date.isoformat() if next_date else None
            payload["last_run"] = last_runs.get(row.id)
            out.append(payload)
        return out

    def _last_runs_by_config(self) -> Dict[int, Optional[Dict[str, Any]]]:
        """每实例最近一次运行（管理页「最近运行」列），无运行为空。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(LiveStrategyRun).order_by(desc(LiveStrategyRun.id)).limit(500)
            ).scalars().all()
        latest: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            if row.config_id in latest:
                continue
            latest[row.config_id] = {
                "id": row.id,
                "trade_date": row.trade_date.isoformat(),
                "status": row.status,
                "mode": getattr(row, "mode", "rebalance"),
                "completed_at": row.completed_at.isoformat(sep=" ") if row.completed_at else None,
            }
        return latest

    @staticmethod
    def _order_strategy_ids() -> set:
        return {s["strategy_id"] for s in list_builtin_strategies() if s.get("live_mode") == "order"}

    @staticmethod
    def _live_mode_of(strategy_id: str) -> Optional[str]:
        metadata = next((s for s in list_builtin_strategies() if s["strategy_id"] == strategy_id), None)
        return (metadata or {}).get("live_mode")

    @staticmethod
    def _config_payload(row):
        buffer_days = getattr(row, "last_trading_day_exit_buffer_days", None)
        return {"id": row.id, "name": row.name, "description": getattr(row, "description", None), "strategy_id": row.strategy_id, "strategy_version": row.strategy_version, "qmt_account": row.qmt_account, "enabled": row.enabled, "symbols": json.loads(row.symbols_json or "[]"), "parameters": json.loads(row.parameters_json or "{}"), "rebalance_frequency_days": row.rebalance_frequency_days or 1, "event_check_enabled": row.event_check_enabled if row.event_check_enabled is not None else True, "data_sync_before_run": row.data_sync_before_run if row.data_sync_before_run is not None else True, "last_trading_day_exit_enabled": getattr(row, "last_trading_day_exit_enabled", True) if getattr(row, "last_trading_day_exit_enabled", None) is not None else True, "last_trading_day_exit_buffer_days": buffer_days if buffer_days is not None else 1, "schedule_time": getattr(row, "schedule_time", None) or "14:35"}

    @staticmethod
    def _run_payload(row):
        def _ts(value):
            return value.isoformat(sep=" ") if value else None
        return {"id": row.id, "run_uid": row.run_uid, "config_id": row.config_id, "trade_date": row.trade_date.isoformat(), "status": row.status, "mode": getattr(row, "mode", "rebalance"), "strategy_id": getattr(row, "strategy_id", None), "strategy_version": getattr(row, "strategy_version", None), "qmt_account": row.qmt_account, "decision_count": getattr(row, "decision_count", 0), "order_count": getattr(row, "order_count", 0), "skip_reason": getattr(row, "skip_reason", None), "data_snapshot_at": _ts(getattr(row, "data_snapshot_at", None)), "completed_at": _ts(getattr(row, "completed_at", None)), "target": json.loads(row.target_json or "{}"), "current": json.loads(row.current_json or "{}"), "rebalance": json.loads(row.rebalance_json or "[]"), "risk": json.loads(row.risk_json or "{}"), "error_message": row.error_message}
