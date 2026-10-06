# -*- coding: utf-8 -*-
"""In-memory daily feed adapter for backtrader.

输入是紧凑元组 ``(datenum, close, premium_rate, remaining_size, event_blocked)``，
由引擎层把仓库行统一到全市场交易日历并做前值填充（停牌日沿用最近收盘价、
因子沿用最近值；上市前的填充行价格与因子均为 NaN，
策略层用 ``first_bar_date`` 元数据把上市前填充行挡在候选池外，
NaN 不会进入估值——未持有的标的不参与 broker 估值）。
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

import backtrader as bt

FeedRow = Tuple[float, float, float, float, float]


class CbDailyFeed(bt.feed.DataBase):
    """One instrument's padded daily factor series as a backtrader feed."""

    lines = ("premium_rate", "remaining_size", "event_blocked")

    params = (
        ("rows", None),
        ("symbol", ""),
    )

    def start(self):
        super().start()
        self._iter = iter(self.p.rows or ())

    def _load(self):
        try:
            datenum, close, premium, remaining_size, event_blocked = next(self._iter)
        except StopIteration:
            return False
        self.l.datetime[0] = datenum
        self.l.open[0] = close
        self.l.high[0] = close
        self.l.low[0] = close
        self.l.close[0] = close
        self.l.volume[0] = 0.0
        self.l.openinterest[0] = 0.0
        self.l.premium_rate[0] = premium
        self.l.remaining_size[0] = remaining_size
        self.l.event_blocked[0] = event_blocked
        return True


def build_feed_rows(
    calendar: Sequence,
    *,
    datenums: Sequence[float],
    values_by_date: dict,
    first_index: int,
) -> List[FeedRow]:
    """Pad one symbol's factor rows onto the shared calendar (forward fill)."""

    rows: List[FeedRow] = []
    close = premium = remaining_size = float("nan")
    blocked = 0.0
    started = False
    for index, trade_date in enumerate(calendar):
        row = values_by_date.get(trade_date)
        if row is not None:
            close = float(row["close"])
            premium = float(row["premium_rate"]) if row.get("premium_rate") is not None else float("nan")
            remaining_size = (
                float(row["remaining_size"]) if row.get("remaining_size") is not None else float("nan")
            )
            blocked = 1.0 if row.get("event_blocked") else 0.0
            started = True
        if not started and index < first_index:
            # 上市前的占位行：价格与因子均为 NaN，策略层按 first_bar_date 排除，
            # NaN 不会参与估值（未持有标的不进入 broker.getvalue）。
            rows.append((datenums[index], float("nan"), float("nan"), float("nan"), 0.0))
            continue
        rows.append((datenums[index], close, premium, remaining_size, blocked))
    return rows
