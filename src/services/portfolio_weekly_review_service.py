# -*- coding: utf-8 -*-
"""周度持仓复盘（每周六凌晨定时生成上一自然周一~周五的持仓复盘）。

数据源与口径：
- ``portfolio_daily_snapshots``（固定 fifo）：权益/浮盈/已实现均为**累计值**，
  周指标一律期末−期初 diff；期初 = period_start 前最后一个快照日。
- 快照 payload 内的逐标的仓位：期初/期末两份做 flow-adjusted 归因
  （pnl = mv_end − mv_start − buys + sells_net）。
- ``portfolio_trades`` + ``portfolio_cash_ledger``：当周成交与资金流；
  资金流修正只计外部出入金（note 不带 qmt_deal_cash 前缀），
  交易结算型资金流水（qmt_deal_cash:*）不视为外部流入流出。
- ``strategy_lab_cb_*``：转债溢价率/条款/事件；``live_strategy_runs``/
  ``trading_orders``：自动策略周内活动；``decision_signals``：信号对照。

生成结果按 (period_start, period_end) upsert 到 ``portfolio_weekly_reviews``：
重复生成只覆盖自动区 payload，不触碰 weekly_note 与计划子表；
飞书推送失败不影响落库。
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select

from src.core.trading_calendar import cn_trading_days_between
from src.storage import (
    DatabaseManager,
    DecisionSignalRecord,
    LiveStrategyRun,
    PortfolioAccount,
    PortfolioCashLedger,
    PortfolioDailySnapshot,
    PortfolioPositionLot,
    PortfolioPositionMeta,
    PortfolioTrade,
    PortfolioWeeklyPlan,
    PortfolioWeeklyReview,
    StockDaily,
    StrategyLabCbBasic,
    StrategyLabCbDailyFactor,
    StrategyLabCbEvent,
)

logger = logging.getLogger(__name__)

COST_METHOD = "fifo"
# QMT 自动同步成交的 note 前缀（成交行与结算资金流水共用），用于区分自动/手动
AUTO_TRADE_NOTE_PREFIX = "qmt_deal"
TRADE_CASH_NOTE_PREFIX = "qmt_deal_cash"
DEEP_LOSS_PCT = -20.0          # 深套阈值（浮动盈亏比例，百分数）
CONCENTRATION_FLAG_PCT = 20.0  # 单票集中度提示阈值（占总权益百分数）
CB_MATURITY_WARN_DAYS = 90     # 转债临期提示阈值（天）
PLAN_ACTIONS = {"add", "reduce", "clear", "watch"}
PLAN_STATUSES = {"pending", "done", "skipped"}


def resolve_week(anchor: Optional[date] = None) -> Tuple[date, date]:
    """把任意日期折算成所在自然周的周一与周五；缺省取上一自然周。"""
    base = anchor or date.today()
    monday = base - timedelta(days=base.weekday())
    if anchor is None:
        monday -= timedelta(days=7)
    return monday, monday + timedelta(days=4)


def _round_money(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 2)


def _fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


class PortfolioWeeklyReviewService:
    """周度持仓复盘：计算 → 落库 → 推送，以及人工区（手记/计划）读写。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # ------------------------------------------------------------------
    # 生成入口
    # ------------------------------------------------------------------

    def run(self, *, period_end: Optional[date] = None, push: bool = True) -> Dict[str, Any]:
        """生成一个自然周的复盘；period_end 为该周内任意一天，缺省上一自然周。"""
        period_start, period_end_date = resolve_week(period_end)
        trading_days = cn_trading_days_between(period_start, period_end_date)
        if not trading_days:
            logger.info(
                "[PortfolioWeeklyReview] skip %s~%s: no trading days", period_start, period_end_date
            )
            return {
                "skipped": "no_trading_days",
                "period_start": period_start.isoformat(),
                "period_end": period_end_date.isoformat(),
            }
        try:
            payload = self._build_payload(period_start, period_end_date, trading_days)
        except Exception as exc:
            logger.exception(
                "[PortfolioWeeklyReview] build failed for %s~%s", period_start, period_end_date
            )
            self._upsert_review(
                period_start, period_end_date, status="failed", error=str(exc)
            )
            raise

        payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        review_id = self._upsert_review(
            period_start, period_end_date, status="completed", payload_json=payload_json
        )
        feishu_sent = False
        if push:
            feishu_sent = self._send_feishu(self.build_message(payload))
            self._mark_feishu_sent(review_id, feishu_sent)
        payload["review_id"] = review_id
        payload["feishu_sent"] = feishu_sent
        logger.info(
            "[PortfolioWeeklyReview] generated %s~%s review_id=%s feishu_sent=%s",
            period_start, period_end_date, review_id, feishu_sent,
        )
        return payload

    # ------------------------------------------------------------------
    # 自动区计算
    # ------------------------------------------------------------------

    def _build_payload(
        self, period_start: date, period_end: date, trading_days: List[date]
    ) -> Dict[str, Any]:
        accounts = self._load_active_accounts()
        if not accounts:
            raise ValueError("no active portfolio accounts")

        snapshots = self._load_bookend_snapshots(accounts, period_start, period_end)
        week_trades = self._load_trades(accounts, period_start, period_end)
        cash_flows = self._load_external_cash_flows(accounts, period_start, period_end)
        realized = self._compute_realized(accounts, period_end, period_start)

        start_positions = {
            account_id: self._positions_of(snap.get("start"))
            for account_id, snap in snapshots.items()
        }
        end_positions = {
            account_id: self._positions_of(snap.get("end"))
            for account_id, snap in snapshots.items()
        }
        names = self._symbol_names(accounts, start_positions, end_positions, week_trades)
        cb_basic = self._load_cb_basic()
        cb_codes = set(cb_basic)

        overview = self._section_overview(
            accounts, snapshots, cash_flows, period_start, period_end
        )
        attribution = self._section_attribution(
            accounts, start_positions, end_positions, week_trades, names, period_start, period_end
        )
        structure = self._section_structure(
            accounts, end_positions, names, overview, cb_codes
        )
        tiers = self._section_position_tiers(
            accounts, end_positions, names, period_end
        )
        cb_zone = self._section_cb_zone(
            accounts, end_positions, cb_basic, week_trades, realized,
            period_start, period_end, names
        )
        signals = self._section_signals(week_trades, period_start, period_end)
        risks = self._section_risks(
            structure, tiers, cb_zone,
            self._stop_loss_risk_items(accounts, end_positions, names),
        )

        summary = self._build_summary(
            overview, attribution, risks, structure, len(trading_days), len(week_trades)
        )
        limitations = self._collect_limitations(accounts, snapshots, attribution, realized)

        return {
            "period": {
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "trading_days": [day.isoformat() for day in trading_days],
            },
            "summary": summary,
            "overview": overview,
            "attribution": attribution,
            "realized": realized,
            "structure": structure,
            "position_tiers": tiers,
            "cb_zone": cb_zone,
            "signals_vs_trades": signals,
            "risk_list": risks,
            "limitations": limitations,
        }

    # -- 数据装载 ------------------------------------------------------

    def _load_active_accounts(self) -> List[PortfolioAccount]:
        with self.db.get_session() as session:
            return list(
                session.execute(
                    select(PortfolioAccount)
                    .where(PortfolioAccount.is_active.is_(True))
                    .order_by(PortfolioAccount.id.asc())
                ).scalars().all()
            )

    def _load_bookend_snapshots(
        self, accounts: List[PortfolioAccount], period_start: date, period_end: date
    ) -> Dict[int, Dict[str, Any]]:
        """每个账户取期初（period_start 前最后一份）与期末（period_end 前最后一份）快照。"""
        result: Dict[int, Dict[str, Any]] = {}
        with self.db.get_session() as session:
            for account in accounts:
                end_row = session.execute(
                    select(PortfolioDailySnapshot)
                    .where(
                        PortfolioDailySnapshot.account_id == account.id,
                        PortfolioDailySnapshot.cost_method == COST_METHOD,
                        PortfolioDailySnapshot.snapshot_date <= period_end,
                    )
                    .order_by(PortfolioDailySnapshot.snapshot_date.desc())
                    .limit(1)
                ).scalar_one_or_none()
                start_row = session.execute(
                    select(PortfolioDailySnapshot)
                    .where(
                        PortfolioDailySnapshot.account_id == account.id,
                        PortfolioDailySnapshot.cost_method == COST_METHOD,
                        PortfolioDailySnapshot.snapshot_date < period_start,
                    )
                    .order_by(PortfolioDailySnapshot.snapshot_date.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if end_row is None:
                    continue
                result[account.id] = {"start": start_row, "end": end_row}
        return result

    def _load_trades(
        self, accounts: List[PortfolioAccount], date_from: date, date_to: date
    ) -> List[PortfolioTrade]:
        account_ids = [account.id for account in accounts]
        if not account_ids:
            return []
        with self.db.get_session() as session:
            return list(
                session.execute(
                    select(PortfolioTrade)
                    .where(
                        PortfolioTrade.account_id.in_(account_ids),
                        PortfolioTrade.trade_date >= date_from,
                        PortfolioTrade.trade_date <= date_to,
                    )
                    .order_by(PortfolioTrade.trade_date.asc(), PortfolioTrade.id.asc())
                ).scalars().all()
            )

    def _load_external_cash_flows(
        self, accounts: List[PortfolioAccount], period_start: date, period_end: date
    ) -> Dict[int, float]:
        """周内外部出入金净额（in 计正、out 计负），剔除交易结算型流水。"""
        account_ids = [account.id for account in accounts]
        if not account_ids:
            return {}
        net: Dict[int, float] = {account_id: 0.0 for account_id in account_ids}
        with self.db.get_session() as session:
            rows = session.execute(
                select(PortfolioCashLedger)
                .where(
                    PortfolioCashLedger.account_id.in_(account_ids),
                    PortfolioCashLedger.event_date >= period_start,
                    PortfolioCashLedger.event_date <= period_end,
                )
            ).scalars().all()
        for row in rows:
            note = (row.note or "").strip()
            if note.startswith(TRADE_CASH_NOTE_PREFIX):
                continue
            amount = float(row.amount or 0.0)
            net[row.account_id] += amount if row.direction == "in" else -amount
        return net

    def _load_cb_basic(self) -> Dict[str, StrategyLabCbBasic]:
        with self.db.get_session() as session:
            rows = session.execute(select(StrategyLabCbBasic)).scalars().all()
        return {row.bond_code: row for row in rows}

    @staticmethod
    def _positions_of(snapshot_row: Any) -> Dict[str, Dict[str, Any]]:
        if snapshot_row is None or not snapshot_row.payload:
            return {}
        try:
            data = json.loads(snapshot_row.payload)
        except (TypeError, ValueError):
            return {}
        positions = data.get("positions") or []
        if isinstance(positions, dict):
            positions = list(positions.values())
        return {
            str(item.get("symbol")): item
            for item in positions
            if isinstance(item, dict) and item.get("symbol")
        }

    def _symbol_names(
        self,
        accounts: List[PortfolioAccount],
        start_positions: Dict[int, Dict[str, Dict[str, Any]]],
        end_positions: Dict[int, Dict[str, Dict[str, Any]]],
        week_trades: List[PortfolioTrade],
    ) -> Dict[str, str]:
        names: Dict[str, str] = {}
        for positions in (start_positions, end_positions):
            for items in positions.values():
                for symbol, item in items.items():
                    name = str(item.get("symbol_name") or "").strip()
                    if name:
                        names.setdefault(symbol, name)
        for trade in week_trades:
            name = str(trade.symbol_name or "").strip()
            if name:
                names.setdefault(trade.symbol, name)
        unresolved = {
            symbol
            for positions in (start_positions, end_positions)
            for items in positions.values()
            for symbol in items
        } - set(names)
        unresolved |= {trade.symbol for trade in week_trades} - set(names)
        if unresolved:
            with self.db.get_session() as session:
                for bond_code, bond_name in session.execute(
                    select(StrategyLabCbBasic.bond_code, StrategyLabCbBasic.bond_name)
                    .where(StrategyLabCbBasic.bond_code.in_(unresolved))
                ):
                    if bond_name:
                        names[bond_code] = bond_name
        return names

    # -- overview -------------------------------------------------------

    def _section_overview(
        self,
        accounts: List[PortfolioAccount],
        snapshots: Dict[int, Dict[str, Any]],
        cash_flows: Dict[int, float],
        period_start: date,
        period_end: date,
    ) -> Dict[str, Any]:
        account_names = {account.id: account.name for account in accounts}
        history = self._equity_history(accounts, period_end)
        items: List[Dict[str, Any]] = []
        totals: Dict[str, float] = {
            "equity_start": 0.0, "equity_end": 0.0, "net_flow_in": 0.0,
            "cash_end": 0.0, "realized_delta_week": 0.0,
        }
        for account_id, snap in sorted(snapshots.items()):
            start_row, end_row = snap["start"], snap["end"]
            equity_start = float(start_row.total_equity or 0.0) if start_row else 0.0
            equity_end = float(end_row.total_equity or 0.0)
            realized_start = float(start_row.realized_pnl or 0.0) if start_row else 0.0
            realized_end = float(end_row.realized_pnl or 0.0)
            net_flow = float(cash_flows.get(account_id, 0.0))
            return_amount = equity_end - equity_start - net_flow
            return_pct = (return_amount / equity_start * 100.0) if equity_start else None
            peak = max(
                (float(row.get("total_equity") or 0.0) for row in history.get(account_id, [])),
                default=0.0,
            )
            drawdown = (equity_end - peak) / peak * 100.0 if peak > 0 else None
            cash_ratio = (
                float(end_row.total_cash or 0.0) / equity_end * 100.0 if equity_end else None
            )
            items.append({
                "account_id": account_id,
                "account_name": account_names.get(account_id, str(account_id)),
                "equity_start": _round_money(equity_start),
                "equity_end": _round_money(equity_end),
                "net_flow_in": _round_money(net_flow),
                "weekly_return_amount": _round_money(return_amount),
                "weekly_return_pct": _round_money(return_pct),
                "cash_end": _round_money(float(end_row.total_cash or 0.0)),
                "cash_ratio_pct": _round_money(cash_ratio),
                "realized_delta_week": _round_money(realized_end - realized_start),
                "drawdown_from_peak_pct": _round_money(drawdown),
                "data_start_in_week": start_row is None,
            })
            totals["equity_start"] += equity_start
            totals["equity_end"] += equity_end
            totals["net_flow_in"] += net_flow
            totals["cash_end"] += float(end_row.total_cash or 0.0)
            totals["realized_delta_week"] += realized_end - realized_start

        total_return = totals["equity_end"] - totals["equity_start"] - totals["net_flow_in"]
        total_return_pct = (
            total_return / totals["equity_start"] * 100.0 if totals["equity_start"] else None
        )
        # 合计回撤：按日期跨账户汇总权益后取峰值
        by_date: Dict[date, float] = defaultdict(float)
        for rows in history.values():
            for row in rows:
                by_date[row["date"]] += float(row.get("total_equity") or 0.0)
        total_peak = max(by_date.values(), default=0.0)
        total_drawdown = (
            (totals["equity_end"] - total_peak) / total_peak * 100.0 if total_peak > 0 else None
        )
        total = {
            "equity_start": _round_money(totals["equity_start"]),
            "equity_end": _round_money(totals["equity_end"]),
            "net_flow_in": _round_money(totals["net_flow_in"]),
            "weekly_return_amount": _round_money(total_return),
            "weekly_return_pct": _round_money(total_return_pct),
            "cash_end": _round_money(totals["cash_end"]),
            "cash_ratio_pct": _round_money(
                totals["cash_end"] / totals["equity_end"] * 100.0 if totals["equity_end"] else None
            ),
            "realized_delta_week": _round_money(totals["realized_delta_week"]),
            "drawdown_from_peak_pct": _round_money(total_drawdown),
        }
        return {"accounts": items, "total": total}

    def _equity_history(
        self, accounts: List[PortfolioAccount], period_end: date
    ) -> Dict[int, List[Dict[str, Any]]]:
        history: Dict[int, List[Dict[str, Any]]] = {}
        with self.db.get_session() as session:
            for account in accounts:
                rows = session.execute(
                    select(
                        PortfolioDailySnapshot.snapshot_date,
                        PortfolioDailySnapshot.total_equity,
                    ).where(
                        PortfolioDailySnapshot.account_id == account.id,
                        PortfolioDailySnapshot.cost_method == COST_METHOD,
                        PortfolioDailySnapshot.snapshot_date <= period_end,
                    )
                    .order_by(PortfolioDailySnapshot.snapshot_date.asc())
                ).all()
                history[account.id] = [
                    {"date": row[0], "total_equity": float(row[1] or 0.0)} for row in rows
                ]
        return history

    # -- attribution ------------------------------------------------------

    def _section_attribution(
        self,
        accounts: List[PortfolioAccount],
        start_positions: Dict[int, Dict[str, Dict[str, Any]]],
        end_positions: Dict[int, Dict[str, Dict[str, Any]]],
        week_trades: List[PortfolioTrade],
        names: Dict[str, str],
        period_start: date,
        period_end: date,
    ) -> Dict[str, Any]:
        account_names = {account.id: account.name for account in accounts}
        # 当周逐标的资金流：买入含费用、卖出为净回款
        flows: Dict[Tuple[int, str], Dict[str, float]] = defaultdict(
            lambda: {"buy": 0.0, "sell": 0.0}
        )
        for trade in week_trades:
            key = (trade.account_id, trade.symbol)
            amount = float(trade.quantity or 0.0) * float(trade.price or 0.0)
            extras = float(trade.fee or 0.0) + float(trade.tax or 0.0)
            if trade.side == "sell":
                flows[key]["sell"] += amount - extras
            else:
                flows[key]["buy"] += amount + extras

        keys: set = set()
        for positions in (start_positions, end_positions):
            for account_id, items in positions.items():
                keys.update((account_id, symbol) for symbol in items)
        keys.update(flows.keys())

        per_symbol: Dict[str, Dict[str, Any]] = {}
        for account_id, symbol in keys:
            mv_start = float(
                start_positions.get(account_id, {}).get(symbol, {}).get("market_value_base") or 0.0
            )
            mv_end = float(
                end_positions.get(account_id, {}).get(symbol, {}).get("market_value_base") or 0.0
            )
            flow = flows.get((account_id, symbol), {"buy": 0.0, "sell": 0.0})
            pnl = mv_end - mv_start - flow["buy"] + flow["sell"]
            bucket = per_symbol.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "name": names.get(symbol, ""),
                    "mv_start": 0.0,
                    "mv_end": 0.0,
                    "buy_amount": 0.0,
                    "sell_amount": 0.0,
                    "pnl": 0.0,
                    "accounts": set(),
                },
            )
            bucket["mv_start"] += mv_start
            bucket["mv_end"] += mv_end
            bucket["buy_amount"] += flow["buy"]
            bucket["sell_amount"] += flow["sell"]
            bucket["pnl"] += pnl
            bucket["accounts"].add(account_names.get(account_id, str(account_id)))

        price_changes = self._weekly_price_changes(sorted(per_symbol), period_start, period_end)
        items: List[Dict[str, Any]] = []
        for bucket in per_symbol.values():
            symbol = bucket["symbol"]
            items.append({
                "symbol": symbol,
                "name": bucket["name"],
                "mv_start": _round_money(bucket["mv_start"]),
                "mv_end": _round_money(bucket["mv_end"]),
                "buy_amount": _round_money(bucket["buy_amount"]),
                "sell_amount": _round_money(bucket["sell_amount"]),
                "pnl": _round_money(bucket["pnl"]),
                "weekly_price_change_pct": price_changes.get(symbol),
                "accounts": sorted(bucket["accounts"]),
                "new_position": bucket["mv_start"] == 0.0 and bucket["buy_amount"],
                "exited_position": bucket["mv_end"] == 0.0 and bucket["sell_amount"],
            })
        items.sort(key=lambda item: item["pnl"], reverse=True)
        return {
            "items": items,
            "top_contributors": items[:5],
            "top_draggers": sorted(items, key=lambda item: item["pnl"])[:5],
        }

    def _weekly_price_changes(
        self, symbols: List[str], period_start: date, period_end: date
    ) -> Dict[str, Optional[float]]:
        """周涨跌幅 = 周内最后收盘 ÷ 期初前最后收盘 − 1（stock_daily 的 pct_chg 对非场外基金为空，不可用）。"""
        changes: Dict[str, Optional[float]] = {}
        with self.db.get_session() as session:
            for symbol in symbols:
                end_close = session.execute(
                    select(StockDaily.close)
                    .where(StockDaily.code == symbol, StockDaily.date <= period_end)
                    .order_by(StockDaily.date.desc())
                    .limit(1)
                ).scalar_one_or_none()
                prev_close = session.execute(
                    select(StockDaily.close)
                    .where(StockDaily.code == symbol, StockDaily.date < period_start)
                    .order_by(StockDaily.date.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if end_close and prev_close:
                    changes[symbol] = round((end_close / prev_close - 1.0) * 100.0, 2)
                else:
                    changes[symbol] = None
        return changes

    # -- realized ---------------------------------------------------------

    def _compute_realized(
        self,
        accounts: List[PortfolioAccount],
        period_end: date,
        period_start: date,
    ) -> Dict[str, Any]:
        """全量 FIFO 重放（账户×标的），输出周内已实现明细与统计。

        卖出超过可用 lot 时只按已匹配部分计盈亏并打 oversell 标记
        （历史不完整的账户会触发，属数据边界而非计算错误）。
        """
        account_ids = [account.id for account in accounts]
        if not account_ids:
            return self._empty_realized()
        with self.db.get_session() as session:
            all_trades = list(
                session.execute(
                    select(PortfolioTrade)
                    .where(
                        PortfolioTrade.account_id.in_(account_ids),
                        PortfolioTrade.trade_date <= period_end,
                    )
                    .order_by(PortfolioTrade.trade_date.asc(), PortfolioTrade.id.asc())
                ).scalars().all()
            )
        grouped: Dict[Tuple[int, str], List[PortfolioTrade]] = defaultdict(list)
        for trade in all_trades:
            grouped[(trade.account_id, trade.symbol)].append(trade)

        events: List[Dict[str, Any]] = []
        oversell = False
        for (account_id, symbol), trades in sorted(grouped.items()):
            lots: List[List[float]] = []  # [剩余数量, 摊薄单价]
            for trade in trades:
                quantity = float(trade.quantity or 0.0)
                price = float(trade.price or 0.0)
                extras = float(trade.fee or 0.0) + float(trade.tax or 0.0)
                if trade.side != "sell":
                    unit_cost = (quantity * price + extras) / quantity if quantity else 0.0
                    lots.append([quantity, unit_cost])
                    continue
                proceeds_net = quantity * price - extras
                remaining = quantity
                matched_cost = 0.0
                while remaining > 1e-9 and lots:
                    take = min(remaining, lots[0][0])
                    matched_cost += take * lots[0][1]
                    lots[0][0] -= take
                    remaining -= take
                    if lots[0][0] <= 1e-9:
                        lots.pop(0)
                if remaining > 1e-9:
                    oversell = True
                events.append({
                    "account_id": account_id,
                    "symbol": symbol,
                    "trade_date": trade.trade_date,
                    "quantity": quantity,
                    "realized": proceeds_net - matched_cost,
                    "auto": str(trade.note or "").strip().startswith(AUTO_TRADE_NOTE_PREFIX),
                })

        week_events = [
            event for event in events if period_start <= event["trade_date"] <= period_end
        ]
        week_events.sort(key=lambda event: event["trade_date"])
        return {
            "week_events": [
                {
                    **event,
                    "trade_date": event["trade_date"].isoformat(),
                    "realized": _round_money(event["realized"]),
                }
                for event in week_events
            ],
            "week_realized_total": _round_money(
                sum(event["realized"] for event in week_events)
            ),
            "auto_realized": _round_money(
                sum(event["realized"] for event in week_events if event["auto"])
            ),
            "manual_realized": _round_money(
                sum(event["realized"] for event in week_events if not event["auto"])
            ),
            "oversell_detected": oversell,
        }

    @staticmethod
    def _empty_realized() -> Dict[str, Any]:
        return {
            "week_events": [],
            "week_realized_total": 0.0,
            "auto_realized": 0.0,
            "manual_realized": 0.0,
            "oversell_detected": False,
        }

    # -- structure --------------------------------------------------------

    def _section_structure(
        self,
        accounts: List[PortfolioAccount],
        end_positions: Dict[int, Dict[str, Dict[str, Any]]],
        names: Dict[str, str],
        overview: Dict[str, Any],
        cb_codes: set,
    ) -> Dict[str, Any]:
        account_names = {account.id: account.name for account in accounts}
        rows: List[Dict[str, Any]] = []
        for account_id, items in end_positions.items():
            for symbol, item in items.items():
                mv = float(item.get("market_value_base") or 0.0)
                rows.append({
                    "account_id": account_id,
                    "symbol": symbol,
                    "name": str(item.get("symbol_name") or "").strip() or names.get(symbol, ""),
                    "market_value": mv,
                    "category": item.get("category") or self._derive_category(symbol, cb_codes),
                    "industry": str(item.get("industry") or "").strip() or None,
                })
        total_mv = sum(row["market_value"] for row in rows)
        total_equity = float(overview["total"]["equity_end"] or 0.0)

        def _distribution(key: str) -> List[Dict[str, Any]]:
            buckets: Dict[str, float] = defaultdict(float)
            for row in rows:
                label = (
                    account_names.get(row["account_id"], str(row["account_id"]))
                    if key == "account"
                    else (row.get(key) or "未分类")
                )
                buckets[label] += row["market_value"]
            items = [
                {"label": label, "market_value": _round_money(value)}
                for label, value in sorted(buckets.items(), key=lambda kv: -kv[1])
            ]
            for item in items:
                item["pct"] = _round_money(item["market_value"] / total_mv * 100.0 if total_mv else None)
            return items

        sorted_rows = sorted(rows, key=lambda row: -row["market_value"])
        top1 = sorted_rows[0] if sorted_rows else None
        top5_pct = (
            sum(row["market_value"] for row in sorted_rows[:5]) / total_equity * 100.0
            if total_equity
            else None
        )

        by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_symbol[row["symbol"]].append(row)
        overlap = [
            {
                "symbol": symbol,
                "name": entries[0]["name"],
                "accounts": sorted(
                    account_names.get(entry["account_id"], str(entry["account_id"]))
                    for entry in entries
                ),
                "market_value": _round_money(sum(entry["market_value"] for entry in entries)),
            }
            for symbol, entries in sorted(by_symbol.items())
            if len({entry["account_id"] for entry in entries}) > 1
        ]
        overlap.sort(key=lambda item: -item["market_value"])

        missing_logic = self._missing_buy_logic(rows)

        return {
            "position_count": len(rows),
            "total_market_value": _round_money(total_mv),
            "by_category": _distribution("category"),
            "by_account": _distribution("account"),
            "by_industry": _distribution("industry"),
            "concentration": {
                "top1": {
                    "symbol": top1["symbol"],
                    "name": top1["name"],
                    "pct": _round_money(
                        top1["market_value"] / total_equity * 100.0 if total_equity else None
                    ),
                }
                if top1
                else None,
                "top5_pct": _round_money(top5_pct),
                "top1_flagged": bool(
                    top1 and total_equity
                    and top1["market_value"] / total_equity * 100.0 >= CONCENTRATION_FLAG_PCT
                ),
            },
            "cash_ratio_pct": overview["total"]["cash_ratio_pct"],
            "cross_account_overlap": overlap,
            "missing_buy_logic": missing_logic,
        }

    @staticmethod
    def _derive_category(symbol: str, cb_codes: set) -> str:
        code = str(symbol).split(".")[0]
        if symbol in cb_codes or (len(code) == 6 and code[:2] in {"11", "12", "13"}):
            return "convertible_bond"
        if len(code) == 6 and (code[0] == "5" or code[:2] in {"15", "16", "18"}):
            return "etf"
        return "stock"

    def _missing_buy_logic(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not rows:
            return []
        keys = [(row["account_id"], row["symbol"]) for row in rows]
        metas: Dict[Tuple[int, str], PortfolioPositionMeta] = {}
        with self.db.get_session() as session:
            for account_id, symbol in keys:
                meta = session.execute(
                    select(PortfolioPositionMeta)
                    .where(
                        PortfolioPositionMeta.account_id == account_id,
                        PortfolioPositionMeta.symbol == symbol,
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if meta is not None:
                    metas[(account_id, symbol)] = meta
        missing = []
        for row in rows:
            meta = metas.get((row["account_id"], row["symbol"]))
            buy_logic = (meta.buy_logic if meta else None) or None
            buy_logic_text = str(meta.buy_logic_text or "").strip() if meta else ""
            if buy_logic in (None, "", "undefined") and not buy_logic_text:
                missing.append({
                    "account_id": row["account_id"],
                    "symbol": row["symbol"],
                    "name": row["name"],
                })
        return missing

    # -- position tiers ----------------------------------------------------

    def _section_position_tiers(
        self,
        accounts: List[PortfolioAccount],
        end_positions: Dict[int, Dict[str, Dict[str, Any]]],
        names: Dict[str, str],
        period_end: date,
    ) -> Dict[str, Any]:
        account_names = {account.id: account.name for account in accounts}
        open_dates = self._lot_open_dates(accounts)
        earliest_trades = self._earliest_trade_dates(accounts)
        items: List[Dict[str, Any]] = []
        for account_id, positions in sorted(end_positions.items()):
            for symbol, position in sorted(positions.items()):
                pnl_pct = position.get("unrealized_pnl_pct")
                if pnl_pct is None:
                    avg_cost = float(position.get("avg_cost") or 0.0)
                    last_price = float(position.get("last_price") or 0.0)
                    pnl_pct = (
                        (last_price - avg_cost) / avg_cost * 100.0 if avg_cost else None
                    )
                pnl_pct = round(float(pnl_pct), 2) if pnl_pct is not None else None
                if pnl_pct is None:
                    tier = "unknown"
                elif pnl_pct <= DEEP_LOSS_PCT:
                    tier = "deep_loss"
                elif pnl_pct < 0:
                    tier = "loss"
                else:
                    tier = "profit"
                min_open = open_dates.get((account_id, symbol))
                holding_days = (period_end - min_open).days if min_open else None
                duration_capped = bool(
                    min_open
                    and earliest_trades.get(account_id)
                    and min_open <= earliest_trades[account_id]
                )
                items.append({
                    "account_id": account_id,
                    "account_name": account_names.get(account_id, str(account_id)),
                    "symbol": symbol,
                    "name": str(position.get("symbol_name") or "").strip() or names.get(symbol, ""),
                    "quantity": float(position.get("quantity") or 0.0),
                    "market_value": _round_money(float(position.get("market_value_base") or 0.0)),
                    "pnl_pct": pnl_pct,
                    "tier": tier,
                    "industry": str(position.get("industry") or "").strip() or None,
                    "holding_days": holding_days,
                    "duration_capped": duration_capped,
                })
        items.sort(key=lambda item: (item["pnl_pct"] is None, item["pnl_pct"] or 0.0))
        counts = defaultdict(int)
        for item in items:
            counts[item["tier"]] += 1
        return {
            "items": items,
            "counts": dict(counts),
            "deep_loss_threshold_pct": DEEP_LOSS_PCT,
        }

    def _lot_open_dates(
        self, accounts: List[PortfolioAccount]
    ) -> Dict[Tuple[int, str], date]:
        account_ids = [account.id for account in accounts]
        if not account_ids:
            return {}
        with self.db.get_session() as session:
            rows = session.execute(
                select(
                    PortfolioPositionLot.account_id,
                    PortfolioPositionLot.symbol,
                    func.min(PortfolioPositionLot.open_date),
                ).where(
                    PortfolioPositionLot.account_id.in_(account_ids),
                    PortfolioPositionLot.cost_method == COST_METHOD,
                    PortfolioPositionLot.remaining_quantity > 0,
                ).group_by(PortfolioPositionLot.account_id, PortfolioPositionLot.symbol)
            ).all()
        return {(row[0], row[1]): row[2] for row in rows}

    def _earliest_trade_dates(
        self, accounts: List[PortfolioAccount]
    ) -> Dict[int, Optional[date]]:
        account_ids = [account.id for account in accounts]
        if not account_ids:
            return {}
        with self.db.get_session() as session:
            rows = session.execute(
                select(
                    PortfolioTrade.account_id, func.min(PortfolioTrade.trade_date)
                ).where(PortfolioTrade.account_id.in_(account_ids))
                .group_by(PortfolioTrade.account_id)
            ).all()
        return {row[0]: row[1] for row in rows}

    # -- cb zone -------------------------------------------------------------

    def _section_cb_zone(
        self,
        accounts: List[PortfolioAccount],
        end_positions: Dict[int, Dict[str, Dict[str, Any]]],
        cb_basic: Dict[str, StrategyLabCbBasic],
        week_trades: List[PortfolioTrade],
        realized: Dict[str, Any],
        period_start: date,
        period_end: date,
        names: Dict[str, str],
    ) -> Dict[str, Any]:
        held_symbols = {
            symbol
            for items in end_positions.values()
            for symbol in items
            if symbol in cb_basic
        }
        account_names = {account.id: account.name for account in accounts}

        held: List[Dict[str, Any]] = []
        with self.db.get_session() as session:
            for symbol in sorted(held_symbols):
                basic = cb_basic[symbol]
                factor = session.execute(
                    select(
                        StrategyLabCbDailyFactor.trade_date,
                        StrategyLabCbDailyFactor.close,
                        StrategyLabCbDailyFactor.premium_rate,
                        StrategyLabCbDailyFactor.remaining_size,
                    )
                    .where(
                        StrategyLabCbDailyFactor.bond_code == symbol,
                        StrategyLabCbDailyFactor.trade_date <= period_end,
                    )
                    .order_by(StrategyLabCbDailyFactor.trade_date.desc())
                    .limit(1)
                ).first()
                days_to_maturity = (
                    (basic.maturity_date - period_end).days if basic.maturity_date else None
                )
                countdown = ""
                if basic.terms_json:
                    try:
                        countdown = str(
                            (json.loads(basic.terms_json) or {}).get("force_redeem_countdown") or ""
                        )
                    except (TypeError, ValueError):
                        countdown = ""
                close = float(factor[1]) if factor and factor[1] else None
                held.append({
                    "symbol": symbol,
                    "name": basic.bond_name or names.get(symbol, ""),
                    "factor_date": factor[0].isoformat() if factor and factor[0] else None,
                    "close": close,
                    "premium_rate": round(float(factor[2]), 2) if factor and factor[2] is not None else None,
                    "price_band": self._cb_price_band(close),
                    "remaining_size": factor[3] if factor and factor[3] is not None else basic.remaining_size,
                    "maturity_date": basic.maturity_date.isoformat() if basic.maturity_date else None,
                    "days_to_maturity": days_to_maturity,
                    "maturity_warn": bool(
                        days_to_maturity is not None and days_to_maturity <= CB_MATURITY_WARN_DAYS
                    ),
                    "force_redeem_countdown": countdown,
                })

            events: List[Dict[str, Any]] = []
            if held_symbols:
                rows = session.execute(
                    select(StrategyLabCbEvent)
                    .where(
                        StrategyLabCbEvent.bond_code.in_(held_symbols),
                        StrategyLabCbEvent.event_date >= period_start,
                        StrategyLabCbEvent.event_date <= period_end,
                    )
                    .order_by(StrategyLabCbEvent.event_date.asc())
                ).scalars().all()
                events = [
                    {
                        "symbol": row.bond_code,
                        "name": cb_basic[row.bond_code].bond_name if row.bond_code in cb_basic else "",
                        "event_date": row.event_date.isoformat(),
                        "event_type": row.event_type,
                        "event_detail": row.event_detail,
                    }
                    for row in rows
                ]

        strategy_runs = self._strategy_runs_summary(period_start, period_end)

        auto_trades = [t for t in week_trades if str(t.note or "").startswith(AUTO_TRADE_NOTE_PREFIX)]
        manual_trades = [t for t in week_trades if not str(t.note or "").startswith(AUTO_TRADE_NOTE_PREFIX)]

        def _trade_stats(trades: List[PortfolioTrade]) -> Dict[str, Any]:
            buy_amount = sum(
                float(t.quantity or 0) * float(t.price or 0)
                for t in trades
                if t.side != "sell"
            )
            sell_amount = sum(
                float(t.quantity or 0) * float(t.price or 0)
                for t in trades
                if t.side == "sell"
            )
            return {
                "count": len(trades),
                "buy_amount": _round_money(buy_amount),
                "sell_amount": _round_money(sell_amount),
            }

        return {
            "held": held,
            "events": events,
            "strategy_runs": strategy_runs,
            "trades_split": {
                "auto": _trade_stats(auto_trades),
                "manual": _trade_stats(manual_trades),
                "auto_account_names": sorted(
                    {account_names.get(t.account_id, str(t.account_id)) for t in auto_trades}
                ),
            },
            "realized_split": {
                "auto_realized": realized.get("auto_realized"),
                "manual_realized": realized.get("manual_realized"),
            },
        }

    @staticmethod
    def _cb_price_band(close: Optional[float]) -> Optional[str]:
        if close is None:
            return None
        if close <= 110:
            return "<=110"
        if close <= 130:
            return "110-130"
        if close <= 150:
            return "130-150"
        return ">150"

    def _strategy_runs_summary(self, period_start: date, period_end: date) -> Dict[str, Any]:
        with self.db.get_session() as session:
            runs = session.execute(
                select(LiveStrategyRun)
                .where(
                    LiveStrategyRun.trade_date >= period_start,
                    LiveStrategyRun.trade_date <= period_end,
                )
                .order_by(LiveStrategyRun.trade_date.asc())
            ).scalars().all()
        modes: Dict[str, int] = defaultdict(int)
        rebalance_entries = buy_entries = sell_entries = 0
        for run in runs:
            modes[run.mode or "unknown"] += 1
            if run.mode != "rebalance" or not run.rebalance_json:
                continue
            try:
                entries = json.loads(run.rebalance_json) or []
            except (TypeError, ValueError):
                continue
            if not isinstance(entries, list):
                continue
            rebalance_entries += len(entries)
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("side")) == "sell":
                    sell_entries += 1
                else:
                    buy_entries += 1
        return {
            "run_count": len(runs),
            "modes": dict(modes),
            "rebalance_entries": rebalance_entries,
            "buy_entries": buy_entries,
            "sell_entries": sell_entries,
        }

    # -- signals vs trades -----------------------------------------------------

    def _section_signals(
        self,
        week_trades: List[PortfolioTrade],
        period_start: date,
        period_end: date,
    ) -> Dict[str, Any]:
        with self.db.get_session() as session:
            signals = session.execute(
                select(DecisionSignalRecord)
                .where(
                    DecisionSignalRecord.created_at >= datetime.combine(period_start, datetime.min.time()),
                    DecisionSignalRecord.created_at < datetime.combine(
                        period_end + timedelta(days=1), datetime.min.time()
                    ),
                )
                .order_by(DecisionSignalRecord.created_at.asc())
            ).scalars().all()
        items: List[Dict[str, Any]] = []
        for signal in signals:
            created_day = signal.created_at.date() if signal.created_at else None
            expires_day = signal.expires_at.date() if signal.expires_at else period_end
            buys = sells = 0
            buy_amount = sell_amount = 0.0
            for trade in week_trades:
                if trade.symbol != signal.stock_code or created_day is None:
                    continue
                if not (created_day <= trade.trade_date <= expires_day):
                    continue
                amount = float(trade.quantity or 0) * float(trade.price or 0)
                if trade.side == "sell":
                    sells += 1
                    sell_amount += amount
                else:
                    buys += 1
                    buy_amount += amount
            action = signal.action or ""
            if action in {"buy", "sell", "reduce"}:
                followed = buys > 0 if action == "buy" else sells > 0
            else:  # watch/hold：信号建议不动作
                followed = (buys + sells) == 0
            items.append({
                "stock_code": signal.stock_code,
                "stock_name": signal.stock_name,
                "action": action,
                "action_label": signal.action_label,
                "created_date": created_day.isoformat() if created_day else None,
                "expires_date": expires_day.isoformat() if expires_day else None,
                "matched_buys": buys,
                "matched_sells": sells,
                "matched_buy_amount": _round_money(buy_amount),
                "matched_sell_amount": _round_money(sell_amount),
                "followed": followed,
            })
        return {"items": items, "matched_total": sum(
            1 for item in items if item["matched_buys"] or item["matched_sells"]
        )}

    # -- risks / summary / limitations ------------------------------------------

    def _stop_loss_risk_items(
        self,
        accounts: List[PortfolioAccount],
        end_positions: Dict[int, Dict[str, Dict[str, Any]]],
        names: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """距人工维护止损价 ≤5% 的持仓（多头视角：现价高于止损价时距离为正）。"""
        account_names = {account.id: account.name for account in accounts}
        held_keys = {
            (account_id, symbol)
            for account_id, positions in end_positions.items()
            for symbol in positions
        }
        if not held_keys:
            return []
        stop_map: Dict[Tuple[int, str], float] = {}
        with self.db.get_session() as session:
            rows = session.execute(
                select(
                    PortfolioPositionMeta.account_id,
                    PortfolioPositionMeta.symbol,
                    PortfolioPositionMeta.stop_loss_price,
                ).where(
                    PortfolioPositionMeta.stop_loss_price.is_not(None),
                    PortfolioPositionMeta.stop_loss_price > 0,
                )
            ).all()
        for account_id, symbol, stop in rows:
            if (account_id, symbol) in held_keys:
                stop_map[(account_id, symbol)] = float(stop)

        items: List[Dict[str, Any]] = []
        for (account_id, symbol), stop in sorted(stop_map.items()):
            position = end_positions.get(account_id, {}).get(symbol) or {}
            last_price = float(position.get("last_price") or 0.0)
            if last_price <= 0:
                continue
            distance_pct = (last_price / stop - 1.0) * 100.0
            if distance_pct > 5.0:
                continue
            name = str(position.get("symbol_name") or "").strip() or names.get(symbol, "")
            items.append({
                "type": "stop_loss_near",
                "severity": "high",
                "symbol": symbol,
                "message": (
                    f"{name or symbol} 现价 {last_price:.2f} 距止损价 {stop:.2f} "
                    f"仅 {max(distance_pct, 0.0):.1f}%（{account_names.get(account_id, account_id)}）"
                ),
            })
        return items

    def _section_risks(
        self,
        structure: Dict[str, Any],
        tiers: Dict[str, Any],
        cb_zone: Dict[str, Any],
        stop_loss_items: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        items: List[Dict[str, Any]] = list(stop_loss_items or [])
        deep = [item for item in tiers["items"] if item["tier"] == "deep_loss"]
        for item in deep:
            items.append({
                "type": "deep_loss",
                "severity": "high",
                "symbol": item["symbol"],
                "message": f"{item['name'] or item['symbol']} 浮亏 {item['pnl_pct']:.1f}%（{item['account_name']}）",
            })
        for event in cb_zone["events"]:
            if event["event_type"] in {"strong_redeem", "down_revise", "bond_rating_change"}:
                items.append({
                    "type": "cb_event",
                    "severity": "high" if event["event_type"] == "strong_redeem" else "medium",
                    "symbol": event["symbol"],
                    "message": f"{event['name'] or event['symbol']} {event['event_date']} "
                    f"{event['event_type']}：{(event['event_detail'] or '')[:80]}",
                })
        for bond in cb_zone["held"]:
            if bond["maturity_warn"]:
                items.append({
                    "type": "cb_maturity",
                    "severity": "medium",
                    "symbol": bond["symbol"],
                    "message": f"{bond['name'] or bond['symbol']} 距到期 {bond['days_to_maturity']} 天",
                })
        concentration = structure.get("concentration") or {}
        if concentration.get("top1_flagged") and concentration.get("top1"):
            top1 = concentration["top1"]
            items.append({
                "type": "concentration",
                "severity": "medium",
                "symbol": top1["symbol"],
                "message": f"单票集中度 {top1['pct']:.1f}%：{top1['name'] or top1['symbol']}",
            })
        missing = structure.get("missing_buy_logic") or []
        if missing:
            items.append({
                "type": "missing_buy_logic",
                "severity": "low",
                "symbol": None,
                "message": f"{len(missing)} 个持仓未填写买入逻辑（buy_logic/buy_logic_text 均为空）",
            })
        return {"items": items}

    @staticmethod
    def _build_summary(
        overview: Dict[str, Any],
        attribution: Dict[str, Any],
        risks: Dict[str, Any],
        structure: Dict[str, Any],
        trading_days_count: int,
        trade_count: int,
    ) -> Dict[str, Any]:
        total = overview["total"]

        def _brief(item: Dict[str, Any]) -> Dict[str, Any]:
            return {"symbol": item["symbol"], "name": item["name"], "pnl": item["pnl"]}

        return {
            "total_equity_end": total["equity_end"],
            "weekly_return_amount": total["weekly_return_amount"],
            "weekly_return_pct": total["weekly_return_pct"],
            "cash_ratio_pct": total["cash_ratio_pct"],
            "realized_delta_week": total["realized_delta_week"],
            "top_contributors": [_brief(item) for item in attribution["top_contributors"][:3]],
            "top_draggers": [_brief(item) for item in attribution["top_draggers"][:3]],
            "risk_count": len(risks["items"]),
            "missing_buy_logic_count": len(structure.get("missing_buy_logic") or []),
            "trading_days_count": trading_days_count,
            "trade_count": trade_count,
        }

    @staticmethod
    def _collect_limitations(
        accounts: List[PortfolioAccount],
        snapshots: Dict[int, Dict[str, Any]],
        attribution: Dict[str, Any],
        realized: Dict[str, Any],
    ) -> List[str]:
        limitations: List[str] = []
        names = {account.id: account.name for account in accounts}
        started_in_week = [
            names.get(account_id, str(account_id))
            for account_id, snap in snapshots.items()
            if snap["start"] is None
        ]
        if started_in_week:
            limitations.append(
                f"账户 {', '.join(started_in_week)} 在本周内才开始有快照，周收益以首日为基期"
            )
        no_price = [
            item["symbol"]
            for item in attribution["items"]
            if item["weekly_price_change_pct"] is None
        ]
        if no_price:
            limitations.append(
                f"{len(no_price)} 个标的缺周涨跌幅行情（{', '.join(no_price[:5])}"
                f"{'…' if len(no_price) > 5 else ''}）"
            )
        if realized.get("oversell_detected"):
            limitations.append(
                "存在卖出量超出已记录持仓的账户（历史成交不完整），逐标的已实现盈亏按已匹配部分计算"
            )
        return limitations

    # ------------------------------------------------------------------
    # 消息推送
    # ------------------------------------------------------------------

    @staticmethod
    def build_message(payload: Dict[str, Any]) -> str:
        period = payload.get("period") or {}
        summary = payload.get("summary") or {}
        overview = payload.get("overview") or {}
        risks = payload.get("risk_list") or {}
        lines = [
            f"周度持仓复盘 · {period.get('period_start')} ~ {period.get('period_end')}"
            f"（{summary.get('trading_days_count', 0)} 个交易日）",
            "",
            f"期末总权益 {_fmt_money(summary.get('total_equity_end'))} 元"
            f"｜周收益 {_fmt_money(summary.get('weekly_return_amount'))}"
            f"（{_fmt_pct(summary.get('weekly_return_pct'))}）",
            f"现金占比 {_fmt_pct(summary.get('cash_ratio_pct'))}"
            f"｜本周已实现 {_fmt_money(summary.get('realized_delta_week'))} 元"
            f"｜成交 {summary.get('trade_count', 0)} 笔",
        ]
        contributors = summary.get("top_contributors") or []
        draggers = summary.get("top_draggers") or []
        if contributors:
            lines.append("")
            lines.append("📈 贡献 Top3")
            for item in contributors:
                lines.append(f"• {item.get('name') or item.get('symbol')} {_fmt_money(item.get('pnl'))}")
        if draggers:
            lines.append("")
            lines.append("📉 拖累 Top3")
            for item in draggers:
                lines.append(f"• {item.get('name') or item.get('symbol')} {_fmt_money(item.get('pnl'))}")
        risk_items = risks.get("items") or []
        if risk_items:
            lines.append("")
            lines.append(f"⚠️ 风险与提醒 {len(risk_items)} 项")
            for item in risk_items[:8]:
                lines.append(f"• {item.get('message')}")
        accounts = overview.get("accounts") or []
        if accounts:
            lines.append("")
            lines.append("账户周收益")
            for account in accounts:
                lines.append(
                    f"• {account['account_name']} {_fmt_money(account['weekly_return_amount'])}"
                    f"（{_fmt_pct(account['weekly_return_pct'])}）"
                )
        lines.append("")
        lines.append("详情见 Web 持仓页「周复盘」")
        return "\n".join(lines)

    @staticmethod
    def _send_feishu(message: str) -> bool:
        try:
            from src.notification import NotificationService
            return bool(
                NotificationService().send_to_feishu(message, title="周度持仓复盘")
            )
        except Exception:
            logger.exception("[PortfolioWeeklyReview] feishu send raised")
            return False

    # ------------------------------------------------------------------
    # 落库
    # ------------------------------------------------------------------

    def _upsert_review(
        self,
        period_start: date,
        period_end: date,
        *,
        status: str,
        payload_json: Optional[str] = None,
        error: Optional[str] = None,
    ) -> int:
        with self.db.get_session() as session:
            row = session.execute(
                select(PortfolioWeeklyReview)
                .where(
                    PortfolioWeeklyReview.period_start == period_start,
                    PortfolioWeeklyReview.period_end == period_end,
                )
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                row = PortfolioWeeklyReview(
                    period_start=period_start, period_end=period_end
                )
                session.add(row)
            row.status = status
            row.error = error
            if payload_json is not None:
                row.payload = payload_json
            session.commit()
            session.refresh(row)
            return int(row.id)

    def _mark_feishu_sent(self, review_id: int, sent: bool) -> None:
        with self.db.get_session() as session:
            row = session.get(PortfolioWeeklyReview, review_id)
            if row is None:
                return
            row.feishu_sent = bool(sent)
            row.feishu_sent_at = datetime.now() if sent else None
            session.commit()

    # ------------------------------------------------------------------
    # 查询与人工区
    # ------------------------------------------------------------------

    def list_reviews(self, *, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        with self.db.get_session() as session:
            total = session.execute(select(func.count(PortfolioWeeklyReview.id))).scalar() or 0
            rows = session.execute(
                select(PortfolioWeeklyReview)
                .order_by(PortfolioWeeklyReview.period_start.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).scalars().all()
            plan_counts: Dict[int, Dict[str, int]] = defaultdict(
                lambda: {"pending": 0, "done": 0, "skipped": 0, "total": 0}
            )
            if rows:
                stat_rows = session.execute(
                    select(
                        PortfolioWeeklyPlan.review_id,
                        PortfolioWeeklyPlan.status,
                        func.count(PortfolioWeeklyPlan.id),
                    ).where(
                        PortfolioWeeklyPlan.review_id.in_([row.id for row in rows])
                    ).group_by(PortfolioWeeklyPlan.review_id, PortfolioWeeklyPlan.status)
                ).all()
                for review_id, status, count in stat_rows:
                    bucket = plan_counts[review_id]
                    bucket[status if status in PLAN_STATUSES else "pending"] += int(count)
                    bucket["total"] += int(count)
        items = []
        for row in rows:
            summary: Dict[str, Any] = {}
            if row.payload:
                try:
                    summary = (json.loads(row.payload) or {}).get("summary") or {}
                except (TypeError, ValueError):
                    summary = {}
            items.append({
                "review_id": row.id,
                "period_start": row.period_start.isoformat(),
                "period_end": row.period_end.isoformat(),
                "status": row.status,
                "feishu_sent": bool(row.feishu_sent),
                "has_note": bool((row.weekly_note or "").strip()),
                "plan_counts": plan_counts.get(row.id, {"pending": 0, "done": 0, "skipped": 0, "total": 0}),
                "summary": summary,
                "error": row.error,
            })
        return {"items": items, "total": int(total), "page": page, "page_size": page_size}

    def get_review_detail(self, period_start: date) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = session.execute(
                select(PortfolioWeeklyReview)
                .where(PortfolioWeeklyReview.period_start == period_start)
                .order_by(PortfolioWeeklyReview.period_end.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                raise ValueError(f"weekly review for {period_start.isoformat()} not found")
            plans = self._plans_of(session, row.id)
            prev_row = session.execute(
                select(PortfolioWeeklyReview)
                .where(PortfolioWeeklyReview.period_end < row.period_start)
                .order_by(PortfolioWeeklyReview.period_end.desc())
                .limit(1)
            ).scalar_one_or_none()
            prev_plans = self._plans_of(session, prev_row.id) if prev_row else []
            prev_period = (
                {
                    "period_start": prev_row.period_start.isoformat(),
                    "period_end": prev_row.period_end.isoformat(),
                }
                if prev_row
                else None
            )
        try:
            payload = json.loads(row.payload) if row.payload else {}
        except (TypeError, ValueError):
            payload = {}
        return {
            "review_id": row.id,
            "period_start": row.period_start.isoformat(),
            "period_end": row.period_end.isoformat(),
            "status": row.status,
            "feishu_sent": bool(row.feishu_sent),
            "weekly_note": row.weekly_note or "",
            "error": row.error,
            "plans": plans,
            "prev_period": prev_period,
            "prev_plans": prev_plans,
            "payload": payload,
        }

    @staticmethod
    def _plans_of(session: Any, review_id: int) -> List[Dict[str, Any]]:
        rows = session.execute(
            select(PortfolioWeeklyPlan)
            .where(PortfolioWeeklyPlan.review_id == review_id)
            .order_by(PortfolioWeeklyPlan.sort_order.asc(), PortfolioWeeklyPlan.id.asc())
        ).scalars().all()
        return [
            {
                "plan_id": row.id,
                "action": row.action,
                "symbol": row.symbol,
                "content": row.content,
                "status": row.status,
            }
            for row in rows
        ]

    def _get_review_by_period(self, session: Any, period_start: date) -> PortfolioWeeklyReview:
        row = session.execute(
            select(PortfolioWeeklyReview)
            .where(PortfolioWeeklyReview.period_start == period_start)
            .order_by(PortfolioWeeklyReview.period_end.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"weekly review for {period_start.isoformat()} not found")
        return row

    def update_weekly_note(self, period_start: date, note: str) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = self._get_review_by_period(session, period_start)
            row.weekly_note = note or None
            session.commit()
            return {"review_id": row.id, "weekly_note": note or ""}

    def create_plan(
        self,
        period_start: date,
        *,
        action: str,
        content: str,
        symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        action = (action or "").strip()
        content = (content or "").strip()
        if action not in PLAN_ACTIONS:
            raise ValueError(f"action must be one of {sorted(PLAN_ACTIONS)}")
        if not content:
            raise ValueError("content must not be empty")
        with self.db.get_session() as session:
            row = self._get_review_by_period(session, period_start)
            max_order = session.execute(
                select(func.max(PortfolioWeeklyPlan.sort_order))
                .where(PortfolioWeeklyPlan.review_id == row.id)
            ).scalar()
            plan = PortfolioWeeklyPlan(
                review_id=row.id,
                sort_order=int(max_order or 0) + 1,
                action=action,
                symbol=(symbol or None),
                content=content,
                status="pending",
            )
            session.add(plan)
            session.commit()
            session.refresh(plan)
            return {
                "plan_id": plan.id,
                "review_id": row.id,
                "action": plan.action,
                "symbol": plan.symbol,
                "content": plan.content,
                "status": plan.status,
            }

    def update_plan(
        self,
        plan_id: int,
        *,
        action: Optional[str] = None,
        content: Optional[str] = None,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.db.get_session() as session:
            plan = session.get(PortfolioWeeklyPlan, plan_id)
            if plan is None:
                raise ValueError(f"plan {plan_id} not found")
            if action is not None:
                action = action.strip()
                if action not in PLAN_ACTIONS:
                    raise ValueError(f"action must be one of {sorted(PLAN_ACTIONS)}")
                plan.action = action
            if content is not None:
                content = content.strip()
                if not content:
                    raise ValueError("content must not be empty")
                plan.content = content
            if symbol is not None:
                plan.symbol = symbol.strip() or None
            if status is not None:
                status = status.strip()
                if status not in PLAN_STATUSES:
                    raise ValueError(f"status must be one of {sorted(PLAN_STATUSES)}")
                plan.status = status
            session.commit()
            session.refresh(plan)
            return {
                "plan_id": plan.id,
                "review_id": plan.review_id,
                "action": plan.action,
                "symbol": plan.symbol,
                "content": plan.content,
                "status": plan.status,
            }

    def delete_plan(self, plan_id: int) -> Dict[str, Any]:
        with self.db.get_session() as session:
            plan = session.get(PortfolioWeeklyPlan, plan_id)
            if plan is None:
                raise ValueError(f"plan {plan_id} not found")
            session.delete(plan)
            session.commit()
        return {"deleted": True, "plan_id": plan_id}
