# -*- coding: utf-8 -*-
"""Rotation strategy on backtrader.

执行语义（与禄得网对齐）：
- 换仓日按当日因子快照过滤 → 打分排序 → 目标持仓（数量区间 + 等金额权重）；
- 与现持仓做差集，退出者收盘卖出、新进者收盘买入（``cheat-on-close``）；
- ``rebalance_weights`` 为真时保留持仓也调回目标权重（再平衡换手来源）；
- 每个交易日收盘后记录权益/持仓快照（在下一根 bar 补记，保证含当日成交）。

终局语义（到期/摘牌）：
- 终止交易日前最后一个交易日（T-1）收盘对持有仓强制平仓（对齐实盘
  「最后交易日的 T-1 日提前平仓」），此后该标的不再新开仓；
- 行情断更（最后真实 bar 之后）的持仓按最后可得收盘价兜底强平；
  断更后的填充 bar 为幽灵报价，不参与打分与买入。

所有候选过滤与打分都经由 :mod:`src.core.strategy_lab.factors` 通用框架，
本类不感知具体因子。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple

import backtrader as bt

from src.core.strategies.sizing import price_size_ratio
from src.core.strategy_lab import factors as F
from src.core.strategy_lab.backtest.params import RotationParams


@dataclass
class InstrumentMeta:
    """Per-symbol metadata prepared by the engine (not in feed lines)."""

    symbol: str
    name: str
    canonical_id: str
    first_bar_date: Optional[date] = None
    last_trading_date: Optional[date] = None
    list_date: Optional[date] = None
    # 区间内最后一天有真实行情的日期；之后的 bar 为前向填充的幽灵 bar
    final_data_date: Optional[date] = None
    # 终止交易日前最后一个交易日（T-1）：持有仓在此日强制平仓，此后禁止新开仓
    terminal_exit_date: Optional[date] = None


@dataclass
class DayRecord:
    """One trading day's post-settlement snapshot."""

    trade_date: date
    cash: float
    positions_value: float
    equity: float
    holdings: List[str] = field(default_factory=list)
    buy_amount: float = 0.0
    sell_amount: float = 0.0


@dataclass
class ExecutionRecord:
    """One executed order (buy/sell) for persistence as a trade row."""

    trade_date: date
    symbol: str
    side: str
    quantity: float
    price: float
    amount: float
    fee: float
    reason: str


def _nan(value: Optional[float]) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


class CbRotationStrategy(bt.Strategy):
    """Periodic convertible-bond rotation with cross-sectional factor scoring."""

    params = (
        ("cfg", None),   # RotationParams
        ("metas", None),  # Dict[str, InstrumentMeta]
    )

    def __init__(self):
        self.cfg: RotationParams = self.p.cfg
        self.metas: Dict[str, InstrumentMeta] = self.p.metas or {}
        self._data_by_symbol: Dict[str, Any] = {data._name: data for data in self.datas}
        self.records: List[DayRecord] = []
        self.executions: List[ExecutionRecord] = []
        self.rebalance_dates: List[date] = []
        self._pending_reasons: Dict[int, str] = {}
        # 成交金额按交易日归集：notify_order 发生在 next() 之后，
        # 不能用「当日计数器」否则会错位到下一根 bar
        self._amounts_by_date: Dict[date, Dict[str, float]] = {}
        self._bar_count = 0
        self._unit_key: Optional[Tuple[Any, ...]] = None
        self._unit_count = 0
        self._last_recorded: Optional[date] = None
        self._last_seen_date: Optional[date] = None

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def next(self):
        current_date = self.datas[0].datetime.date(0)
        self._snapshot_previous_day()
        self._bar_count += 1
        force_sold = self._apply_forced_exits(current_date)
        if self._is_rebalance_day(current_date):
            self.rebalance_dates.append(current_date)
            self._rebalance(current_date, skip_symbols=force_sold)
        self._last_seen_date = current_date

    def stop(self):
        # cerebro 循环结束后最后一根 bar 的成交已撮合，补记最终快照
        self._snapshot_previous_day(use_current_prices=True)

    # ------------------------------------------------------------------
    # 换仓节奏
    # ------------------------------------------------------------------

    def _unit_of(self, current_date: date) -> Tuple[Any, ...]:
        if self.cfg.rebalance_unit == "week":
            iso = current_date.isocalendar()
            return ("week", iso[0], iso[1])
        if self.cfg.rebalance_unit == "month":
            return ("month", current_date.year, current_date.month)
        return ("day",)

    def _is_rebalance_day(self, current_date: date) -> bool:
        if self.cfg.rebalance_unit == "trading_day":
            # 首个交易日即建仓，之后每 interval 个交易日换仓
            return (self._bar_count - 1) % self.cfg.rebalance_interval == 0
        unit_key = self._unit_of(current_date)
        if unit_key != self._unit_key:
            self._unit_key = unit_key
            self._unit_count += 1
            return (self._unit_count - 1) % self.cfg.rebalance_interval == 0
        return False

    # ------------------------------------------------------------------
    # 快照（估值含当日成交：在下一根 bar 补记上一日）
    # ------------------------------------------------------------------

    def _snapshot_previous_day(self, *, use_current_prices: bool = False) -> None:
        if use_current_prices:
            snapshot_date = self._last_seen_date
            price_offset = 0
        else:
            if self._bar_count == 0 or len(self.datas[0]) < 2:
                return
            snapshot_date = self.datas[0].datetime.date(-1)
            price_offset = -1
        if snapshot_date is None or snapshot_date == self._last_recorded:
            return
        cash = self.broker.get_cash()
        positions_value = 0.0
        holdings: List[str] = []
        # bt.Strategy.positions 以 data 对象为键
        for data, position in self.positions.items():
            size = getattr(position, "size", 0) or 0
            if size == 0:
                continue
            symbol = data._name
            close = data.close[price_offset]
            if _nan(close):
                close = data.close[0]
            positions_value += size * close
            holdings.append(str(symbol))
        amounts = self._amounts_by_date.get(snapshot_date, {})
        self.records.append(
            DayRecord(
                trade_date=snapshot_date,
                cash=round(cash, 4),
                positions_value=round(positions_value, 4),
                equity=round(cash + positions_value, 4),
                holdings=sorted(holdings),
                buy_amount=round(amounts.get("buy", 0.0), 4),
                sell_amount=round(amounts.get("sell", 0.0), 4),
            )
        )
        self._last_recorded = snapshot_date

    # ------------------------------------------------------------------
    # 换仓
    # ------------------------------------------------------------------

    def _candidate_rows(self, current_date: date) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for symbol, data in self._data_by_symbol.items():
            meta = self.metas.get(symbol)
            if meta is not None and meta.first_bar_date is not None and current_date < meta.first_bar_date:
                continue
            close = data.close[0]
            if _nan(close):
                continue
            if symbol.lower() in self.cfg.excluded_symbols:
                continue
            if self.cfg.exclude_event_blocked and (data.event_blocked[0] or 0.0) != 0.0:
                continue
            if meta is not None:
                # 新债排除以主数据上市日期为准；缺上市日期时退回窗口内首个 bar
                # （旧数据可能把老债误判为新债，v2 loader 会尽量提供 list_date）
                listed_since = meta.list_date or meta.first_bar_date
                if (
                    self.cfg.exclude_new_bond_days > 0
                    and listed_since is not None
                    and (current_date - listed_since).days < self.cfg.exclude_new_bond_days
                ):
                    continue
                if (
                    self.cfg.exclude_last_trading_days > 0
                    and meta.last_trading_date is not None
                    and 0 <= (meta.last_trading_date - current_date).days <= self.cfg.exclude_last_trading_days
                ):
                    continue
                # 终局排除：T-1 起买入即强平，不再新开仓；行情断更后的填充 bar
                # 是幽灵报价（冻结在最后真实收盘价），禁止按其买入
                if meta.terminal_exit_date is not None and current_date >= meta.terminal_exit_date:
                    continue
                if meta.final_data_date is not None and current_date > meta.final_data_date:
                    continue
            premium = data.premium_rate[0]
            remaining_size = data.remaining_size[0]
            rows.append(
                {
                    "symbol": symbol,
                    "close": float(close),
                    "premium_rate": None if _nan(premium) else float(premium),
                    "remaining_size": None if _nan(remaining_size) else float(remaining_size),
                }
            )
        return rows

    # ------------------------------------------------------------------
    # 终局强平（每个交易日执行，先于换仓）
    # ------------------------------------------------------------------

    def _apply_forced_exits(self, current_date: date) -> Set[str]:
        """到期/摘牌终局处理，返回当日已强平的标的集合。

        - ``terminal_exit_date``：终止交易日前最后一个交易日（T-1）收盘平仓，
          对齐实盘「最后交易日的 T-1 日提前平仓」规则，卖出价为真实收盘价；
        - ``final_data_date`` 之后：行情已断更（到期/摘牌未公告终止日、停牌），
          按最后可得收盘价强平兜底——否则持仓会以冻结价永久滞留、因子冻结霸榜。
        强平释放的现金等到下一个排定换仓日再重新部署，与实盘节奏一致。
        """
        sold: Set[str] = set()
        for data, position in list(self.positions.items()):
            size = getattr(position, "size", 0) or 0
            if size == 0:
                continue
            meta = self.metas.get(data._name)
            if meta is None:
                continue
            if meta.terminal_exit_date is not None and current_date >= meta.terminal_exit_date:
                reason = "t_minus_1_exit"
            elif meta.final_data_date is not None and current_date > meta.final_data_date:
                reason = "delisted_exit"
            else:
                continue
            close = data.close[0]
            if _nan(close) or close <= 0:
                continue
            self._submit(data, "sell", size, reason)
            sold.add(data._name)
        return sold

    def _rebalance(self, current_date: date, skip_symbols: Optional[Set[str]] = None) -> None:
        candidates = self._candidate_rows(current_date)
        eligible = [row for row in candidates if F.passes_exclusions(row, self.cfg.exclusion_rules)]
        scores = F.compose_scores(
            eligible,
            self.cfg.score_weights,
            missing_policy=self.cfg.score_missing,
        )
        ranked = sorted(scores.items(), key=lambda pair: (pair[1], pair[0]))
        target_count = min(self.cfg.max_positions, len(ranked))
        target_symbols = {symbol for symbol, _ in ranked[:target_count]}

        equity = self.broker.getvalue()
        base_target = equity / target_count if target_count else 0.0
        target_cap = equity * self.cfg.max_position_pct / 100.0
        lot = self.cfg.lot_size
        skip = skip_symbols or set()

        def _per_target(close: float) -> float:
            """单标的买入金额：等额基准 × 价格分档比例，再被单标的仓位上限封顶。"""
            ratio = price_size_ratio(close, self.cfg.price_tier_bounds, self.cfg.price_tier_ratios)
            return min(base_target * ratio, target_cap)

        # 先卖后买：同一根 bar 内先释放现金（coc 下按提交顺序撮合）
        for data, position in list(self.positions.items()):
            size = getattr(position, "size", 0) or 0
            if size == 0:
                continue
            symbol = data._name
            if symbol in skip:
                continue  # 本 bar 已由终局强平提交卖单，避免重复下单
            if symbol in target_symbols and not self.cfg.rebalance_weights:
                continue
            close = data.close[0]
            if _nan(close) or close <= 0:
                self._submit(data, "sell", size, "rotation_exit")
                continue
            if symbol in target_symbols:
                # 再平衡：调回目标权重（预留佣金余量，避免满仓被拒单）
                target_qty = math.floor(_per_target(close) / self._sizing_divisor(close) / lot) * lot
                delta = target_qty - size
                if delta > 0:
                    self._submit(data, "buy", delta, "rebalance_up")
                elif -delta >= lot:
                    self._submit(data, "sell", -delta, "rebalance_down")
            else:
                self._submit(data, "sell", size, "rotation_exit")

        for symbol in target_symbols:
            data = self._data_by_symbol[symbol]
            position = self.positions.get(data)
            size = (getattr(position, "size", 0) or 0) if position is not None else 0
            if size > 0:
                continue  # 已持有且无需再平衡
            close = data.close[0]
            if _nan(close) or close <= 0:
                continue
            quantity = math.floor(_per_target(close) / self._sizing_divisor(close) / lot) * lot
            if quantity >= lot:
                self._submit(data, "buy", quantity, "rotation_entry")

    def _sizing_divisor(self, close: float) -> float:
        """下单金额除数：价格 × (1 + 单边费率)，为佣金预留现金余量。"""
        return close * (1.0 + self.cfg.commission)

    def _submit(self, data: Any, side: str, size: float, reason: str) -> None:
        order = self.buy(data, size=size) if side == "buy" else self.sell(data, size=size)
        self._pending_reasons[order.ref] = reason

    # ------------------------------------------------------------------
    # 成交回报
    # ------------------------------------------------------------------

    def notify_order(self, order):
        if order.status in (order.Submitted, order.Accepted):
            return  # 只在终态取出 reason，避免中间态通知把它提前消费掉
        reason = self._pending_reasons.pop(order.ref, "unknown")
        if order.status != order.Completed or order.executed is None:
            return
        executed = order.executed
        size = abs(executed.size)
        if size <= 0:
            return
        record = ExecutionRecord(
            trade_date=bt.num2date(executed.dt).date(),
            symbol=order.data._name,
            side="buy" if executed.size > 0 else "sell",
            quantity=size,
            price=executed.price,
            amount=size * executed.price,
            fee=executed.comm,
            reason=reason,
        )
        self.executions.append(record)
        slot = self._amounts_by_date.setdefault(record.trade_date, {"buy": 0.0, "sell": 0.0})
        slot[record.side] += record.amount
