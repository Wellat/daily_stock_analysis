# -*- coding: utf-8 -*-
"""ETF 信号实盘通知（live_mode=notify 的首个落地策略族）。

交易日按配置 schedule_time 触发：EOD 后复权历史 + 实时行情拼当日 bar，
复用策略实验室信号引擎的因子与规则判定（同一语义），对配置标的产出
买入/卖出信号，汇总成一条消息直发飞书群机器人（FEISHU_WEBHOOK_URL），
不下单。持仓状态从历史信号决策推导，不落独立状态表。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import select

from src.core.strategy_lab.engine import list_builtin_strategies
from src.core.strategy_lab.series_factors import get_series_factor
from src.core.strategy_lab.signal_engine import SignalEngine
from src.core.strategy_lab.signal_params import SignalParams
from src.notification import NotificationService
from src.repositories.strategy_decision_repo import StrategyDecisionRepository
from src.repositories.strategy_lab.data_repo import StrategyLabDataRepository
from src.storage import DatabaseManager, LiveStrategyConfig, LiveStrategyRun

logger = logging.getLogger(__name__)


def _normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().lower().split(".")[-1]


# 因子 → 计算该因子实际依赖的策略参数；通知「参数」行只展示生效参数，
# 不再罗列与当前规则无关的窗口（如 signal 策略显示 macd_* 全家桶）。
_FACTOR_PARAM_KEYS = {
    "ma_gap_pct": ("ma_window",),
    "roc_pct": ("roc_window",),
    "macd_dif": ("macd_fast", "macd_slow", "macd_signal"),
    "macd_dea": ("macd_fast", "macd_slow", "macd_signal"),
    "macd_hist": ("macd_fast", "macd_slow", "macd_signal"),
    "macd_cross": ("macd_fast", "macd_slow", "macd_signal"),
}


def _effective_param_summary(sp: SignalParams) -> str:
    active_factors = {r.factor for r in [*sp.entry_rules, *sp.exit_rules]}
    keys = [
        key for key in ("ma_window", "roc_window", "macd_fast", "macd_slow", "macd_signal")
        if any(key in _FACTOR_PARAM_KEYS.get(factor, ()) for factor in active_factors)
    ]
    parts = [f"{k}={getattr(sp, k)}" for k in keys]
    # 股息率阈值不在 SignalParams 字段里，值就是规则本身
    dividend_rule = next((r for r in sp.entry_rules if r.factor == "dividend_yield"), None)
    if dividend_rule is not None:
        parts.append(f"dividend_yield_min={dividend_rule.value:g}")
    return ", ".join(parts)


class EtfSignalNotifyService:
    """对 live_mode=notify 的 ETF 信号策略做每日信号评估与飞书通知。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()
        self.data = StrategyLabDataRepository(self.db)
        self.decisions = StrategyDecisionRepository(self.db)
        self.notifier: Optional[NotificationService] = None
        self._fetcher: Any = None

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    def run(self, *, config_id: int, trade_date: date, preview: bool = False) -> Dict[str, Any]:
        config = self._notify_config(config_id)
        symbols = [_normalize_symbol(s) for s in json.loads(config.symbols_json or "[]") if str(s).strip()]
        if not symbols:
            raise ValueError("notify config requires at least one symbol")

        # 当日已有成功通知：幂等返回（当日失败允许重试复用原行）
        if not preview:
            with self.db.get_session() as session:
                existing = session.execute(select(LiveStrategyRun).where(
                    LiveStrategyRun.config_id == config.id,
                    LiveStrategyRun.trade_date == trade_date,
                    LiveStrategyRun.mode == "notify",
                )).scalars().first()
            if existing is not None and existing.status == "completed":
                return self._run_payload(existing)

        sp = self._signal_params(config)
        results = self._evaluate_symbols(config, sp, symbols, trade_date)
        message = self._build_message(config, sp, trade_date, results)
        payload = {
            "trade_date": trade_date.isoformat(),
            "mode": "notify",
            "config_id": config.id,
            "strategy_id": config.strategy_id,
            "symbols": symbols,
            "signals": results,
            "message": message,
            "feishu_sent": False,
        }
        if preview:
            return payload

        sent = self._send_feishu(message)
        payload["feishu_sent"] = sent
        if not sent:
            logger.error("[EtfSignalNotify] feishu send failed for config=%s %s", config.id, trade_date)
        run = self._record_run(config, trade_date, results, sent, message)
        payload.update(self._run_payload(run))
        return payload

    # ------------------------------------------------------------------
    # 信号评估
    # ------------------------------------------------------------------

    def _evaluate_symbols(self, config: LiveStrategyConfig, sp: SignalParams,
                          symbols: List[str], trade_date: date) -> List[Dict[str, Any]]:
        warmup = sp.warmup_trading_days()
        rows = self.data.load_etf_signal_rows(
            symbols=symbols,
            start_date=trade_date - timedelta(days=int(warmup * 1.6) + 10),
            end_date=trade_date,
            lookback_days=0,
        )
        per_symbol: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            if row.get("close") is not None:
                per_symbol.setdefault(str(row["symbol"]), []).append(row)

        last_actions = self.decisions.latest_signal_actions(config_id=config.id, symbols=symbols)
        factors = sorted({r.factor for r in list(sp.entry_rules) + list(sp.exit_rules)})

        results: List[Dict[str, Any]] = []
        for symbol in symbols:
            history = per_symbol.get(symbol, [])
            entry: Dict[str, Any] = {"symbol": symbol, "signal": None, "reason": "no_signal"}
            quote = self._fetch_quote(symbol)
            if quote is None or quote.price is None or quote.price <= 0:
                entry.update(status="quote_unavailable", history_bars=len(history))
                results.append(entry)
                continue
            entry["name"] = quote.name or ""
            entry["price"] = round(float(quote.price), 3)
            entry["change_pct"] = round(float(quote.change_pct), 2) if quote.change_pct is not None else None
            if len(history) < warmup:
                entry.update(status="insufficient_history", history_bars=len(history))
                results.append(entry)
                continue
            # 历史过期守卫：库内最后行情距评估日过远（如 etf_daily 长期未同步），
            # 拼当日 bar 只会叠加失真，标注跳过而不是产出错误信号
            if (trade_date - history[-1]["trade_date"]).days > 10:
                entry.update(status="stale_history", last_history_date=history[-1]["trade_date"].isoformat())
                results.append(entry)
                continue

            series = list(history)
            if series[-1]["trade_date"] == trade_date:
                # 盘后手动重跑：当日行已入库，直接用库内口径
                pass
            else:
                bar = self._synthetic_today_bar(symbol, trade_date, history[-1]["close"], quote, entry)
                if bar is None:
                    entry.update(status="adjust_unavailable")
                    results.append(entry)
                    continue
                series.append(bar)

            values: Dict[str, Optional[float]] = {}
            for factor_id in factors:
                computed = get_series_factor(factor_id).compute(series, sp.window_params())
                values[factor_id] = computed[-1] if computed else None
            entry["factors"] = {k: (round(v, 4) if v is not None else None) for k, v in values.items()}

            holding = last_actions.get(symbol) == "buy"
            entry["holding"] = holding
            if not holding and SignalEngine._all_rules_hold(sp.entry_rules, values):
                entry.update(signal="buy", reason="signal_entry")
            elif holding and SignalEngine._any_rule_holds(sp.exit_rules, values):
                entry.update(signal="sell", reason="signal_exit")
            results.append(entry)
        return results

    @staticmethod
    def _synthetic_today_bar(symbol: str, trade_date: date, last_db_close: float,
                             quote: Any, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """实时价折算后复权当日 bar：hfq_today = 库内昨收 × 当日涨幅。

        当日涨幅优先用 现价/pre_close（交易所 pre_close 除权日已调整），缺
        pre_close 时用行情涨跌幅 change_pct（实测主行情源对 ETF 普遍不回
        pre_close）；两者都缺则返回 None 跳过评估——未复权现价与后复权
        序列不可比（510880 后复权价约为现价 1.7 倍，直接拼接会把乖离率
        算出 -40% 的垃圾值）。
        """
        price = float(quote.price)
        pre_close = getattr(quote, "pre_close", None)
        change_pct = getattr(quote, "change_pct", None)
        if not last_db_close or last_db_close <= 0:
            return None
        if pre_close and pre_close > 0:
            change_ratio = price / float(pre_close)
        elif change_pct is not None:
            change_ratio = 1.0 + float(change_pct) / 100.0
        else:
            return None
        entry["hfq_scaled"] = True
        return {"symbol": symbol, "trade_date": trade_date, "close": last_db_close * change_ratio,
                "dividend_yield": None}

    # ------------------------------------------------------------------
    # 消息与通知
    # ------------------------------------------------------------------

    def _build_message(self, config: LiveStrategyConfig, sp: SignalParams,
                       trade_date: date, results: List[Dict[str, Any]]) -> str:
        name = next((s["name"] for s in list_builtin_strategies() if s["strategy_id"] == config.strategy_id),
                    config.strategy_id)
        lines = [f"ETF 信号通知 · {trade_date.isoformat()}", f"策略：{name}"]
        summary = _effective_param_summary(sp)
        if summary:
            lines.append(f"参数：{summary}")
        for item in results:
            symbol = item["symbol"]
            label = f"{item.get('name') or symbol}（{symbol}）"
            if item.get("status") == "quote_unavailable":
                lines.append(f"⚠️ {label}：行情不可用，跳过评估")
                continue
            if item.get("status") == "insufficient_history":
                lines.append(f"⚠️ {label}：历史数据不足（{item.get('history_bars', 0)} 根，需预热更多交易日），跳过评估")
                continue
            if item.get("status") == "stale_history":
                lines.append(f"⚠️ {label}：历史数据过期（最后 {item.get('last_history_date')}），请先同步 ETF 日线，跳过评估")
                continue
            if item.get("status") == "adjust_unavailable":
                lines.append(f"⚠️ {label}：行情缺昨收/涨跌幅、无法折算后复权口径，跳过评估")
                continue
            change = item.get("change_pct")
            change_text = f"{change:+.2f}%" if change is not None else "--"
            factor_text = "，".join(
                f"{get_series_factor(k).label} {v}" for k, v in (item.get("factors") or {}).items()
            )
            signal = item.get("signal")
            # 表情放行首，手机通知列表里一眼区分方向（红涨绿跌，与 A 股习惯一致）
            if signal == "buy":
                verdict = "🔴📈 买入信号"
            elif signal == "sell":
                verdict = "🟢📉 卖出信号"
            else:
                verdict = "⚪️ 无信号"
            holding_text = "持仓中" if item.get("holding") else "未持仓"
            lines.append(f"{verdict}｜{label}：现价 {item['price']}（{change_text}）｜{factor_text}（{holding_text}）")
        return "\n".join(lines)

    def _send_feishu(self, message: str) -> bool:
        try:
            notifier = self.notifier or NotificationService()
            # 卡片标题「信号通知」+ 消息体首行「ETF 信号通知 · 日期」，不再套 build_simple_alert 标题壳
            return bool(notifier.send_to_feishu(message, title="信号通知"))
        except Exception:
            logger.exception("[EtfSignalNotify] feishu send raised")
            return False

    # ------------------------------------------------------------------
    # 落库
    # ------------------------------------------------------------------

    def _record_run(self, config: LiveStrategyConfig, trade_date: date,
                    results: List[Dict[str, Any]], feishu_sent: bool, message: str) -> LiveStrategyRun:
        # 信号明细（含因子值/持仓状态/降级标注）完整落 rebalance_json，运行详情与 API 同源
        signals_payload = results
        with self.db.get_session() as session:
            run = session.execute(select(LiveStrategyRun).where(
                LiveStrategyRun.config_id == config.id,
                LiveStrategyRun.trade_date == trade_date,
                LiveStrategyRun.mode == "notify",
            )).scalars().first()
            if run is None:
                run = LiveStrategyRun(config_id=config.id, qmt_account=config.qmt_account, trade_date=trade_date)
                session.add(run)
            run.run_uid = uuid4().hex
            run.mode = "notify"
            run.status = "completed" if feishu_sent else "failed"
            run.strategy_id = config.strategy_id
            run.strategy_version = config.strategy_version
            run.decision_count = len(results)
            run.order_count = 0
            run.risk_status = "passed" if feishu_sent else "failed"
            run.skip_reason = None if feishu_sent else "feishu_send_failed"
            run.data_snapshot_at = datetime.now()
            run.target_json = json.dumps({})
            run.current_json = json.dumps({})
            run.rebalance_json = json.dumps(signals_payload, ensure_ascii=False)
            run.risk_json = json.dumps({"feishu_sent": feishu_sent, "message": message}, ensure_ascii=False)
            run.completed_at = datetime.now() if feishu_sent else None
            run.error_message = None if feishu_sent else "feishu notification failed"
            session.commit()
            run_id = int(run.id)
        # 决策仅在通知成功后落库：状态机以决策历史为真源，发送失败不能推进状态
        if feishu_sent:
            for item in results:
                self.decisions.create(
                    strategy_id=config.strategy_id, mode="notify", trade_date=trade_date,
                    action=item.get("signal") or "hold", symbol=item["symbol"],
                    symbol_name=item.get("name") or None, account=config.qmt_account,
                    market="cn", instrument_type="etf", reason=item["reason"],
                    decision_data={k: item.get(k) for k in ("price", "change_pct", "factors", "holding")},
                    live_run_id=run_id,
                )
        with self.db.get_session() as session:
            return session.get(LiveStrategyRun, run_id)

    @staticmethod
    def _run_payload(row: LiveStrategyRun) -> Dict[str, Any]:
        def _ts(value):
            return value.isoformat(sep=" ") if value else None
        return {"run_id": row.id, "run_uid": row.run_uid, "trade_date": row.trade_date.isoformat(),
                "status": row.status, "mode": row.mode, "config_id": row.config_id,
                "strategy_id": row.strategy_id, "feishu_sent": json.loads(row.risk_json or "{}").get("feishu_sent"),
                "signals": json.loads(row.rebalance_json or "[]"),
                "message": json.loads(row.risk_json or "{}").get("message"),
                "completed_at": _ts(row.completed_at), "error_message": row.error_message}

    # ------------------------------------------------------------------
    # 配置与数据源
    # ------------------------------------------------------------------

    def _notify_config(self, config_id: int) -> LiveStrategyConfig:
        with self.db.get_session() as session:
            config = session.get(LiveStrategyConfig, int(config_id))
        if config is None:
            raise ValueError(f"live strategy config {config_id} not found")
        metadata = next((s for s in list_builtin_strategies() if s["strategy_id"] == config.strategy_id), None)
        if metadata is None or metadata.get("live_mode") != "notify":
            raise ValueError(f"strategy {config.strategy_id} is not a notify-mode live strategy")
        return config

    @staticmethod
    def _signal_params(config: LiveStrategyConfig) -> SignalParams:
        metadata = next((s for s in list_builtin_strategies() if s["strategy_id"] == config.strategy_id), {})
        defaults = {p["key"]: p.get("default") for p in metadata.get("parameters", [])}
        saved = json.loads(config.parameters_json or "{}")
        params = dict(defaults)
        params.update(saved)
        # 股息率过滤 0=关闭由 SignalParams 引擎层保证（无数据源时 >0 阈值
        # 仍会让买入规则持续不成立，属文档已记的已知边界）。
        # MACD 变体的默认规则（金叉买/死叉卖）为服务端注入，不占参数面板
        if config.strategy_id == "signal_macd":
            params.setdefault("default_rules", "macd_cross")
        return SignalParams.from_parameters(params)

    def _fetch_quote(self, symbol: str):
        if self._fetcher is None:
            from data_provider import DataFetcherManager
            self._fetcher = DataFetcherManager()
        try:
            return self._fetcher.get_realtime_quote(symbol, log_final_failure=False)
        except Exception:
            logger.exception("[EtfSignalNotify] realtime quote failed for %s", symbol)
            return None
