# -*- coding: utf-8 -*-
"""Lowest-premium top-N membership tracking for synchronized CB factors."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from src.repositories.strategy_lab.data_repo import StrategyLabDataRepository
from src.storage import DatabaseManager


class StrategyLabPremiumTrackService:
    """Track how stable the daily lowest-premium top-N convertible-bond set is.

    输入为 ``strategy_lab_cb_daily_factors`` 的每日溢价率快照，输出三类信息：
    每只上榜转债的在榜日期分布（散点图数据）、逐日与前一日集合的重叠/进出
    （稳定性曲线）、以及窗口汇总统计。
    """

    DEFAULT_LOOKBACK_DAYS = 90
    MAX_TOP_N = 50
    MAX_LOOKBACK_DAYS = 750

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.repository = StrategyLabDataRepository(db_manager)

    def premium_top_track(
        self,
        *,
        market: str = "cn",
        start: Optional[date] = None,
        end: Optional[date] = None,
        top_n: int = 10,
    ) -> Dict[str, Any]:
        end = end or date.today()
        start = start or end - timedelta(days=self.DEFAULT_LOOKBACK_DAYS - 1)
        if start > end:
            raise ValueError("start must not be after end")
        if (end - start).days > self.MAX_LOOKBACK_DAYS:
            raise ValueError(f"range must not exceed {self.MAX_LOOKBACK_DAYS} days")
        top_n = max(1, min(int(top_n), self.MAX_TOP_N))
        rows = self.repository.load_cb_premium_top_rows(
            market=market, start_date=start, end_date=end, top_n=top_n
        )

        dates: List[date] = sorted({row["trade_date"] for row in rows})
        date_index = {d: i for i, d in enumerate(dates)}
        membership: Dict[date, List[Dict[str, Any]]] = {}
        for row in rows:
            membership.setdefault(row["trade_date"], []).append(row)
        for members in membership.values():
            members.sort(key=lambda item: item["rank"])

        bonds: Dict[str, Dict[str, Any]] = {}
        for trade_date, members in membership.items():
            for row in members:
                code = row["bond_code"]
                info = bonds.setdefault(
                    code,
                    {
                        "bond_code": code,
                        "bond_name": row["bond_name"] or code,
                        "day_indexes": [],
                        "premiums": [],
                        "best_rank": row["rank"],
                        "first_date": trade_date,
                        "last_date": trade_date,
                    },
                )
                info["day_indexes"].append(date_index[trade_date])
                info["premiums"].append(round(row["premium_rate"], 4))
                info["best_rank"] = min(info["best_rank"], row["rank"])
                info["first_date"] = min(info["first_date"], trade_date)
                info["last_date"] = max(info["last_date"], trade_date)

        window_days = len(dates)
        bond_items: List[Dict[str, Any]] = []
        for info in bonds.values():
            days_count = len(info["day_indexes"])
            bond_items.append(
                {
                    **info,
                    "first_date": info["first_date"].isoformat(),
                    "last_date": info["last_date"].isoformat(),
                    "days_count": days_count,
                    "ratio": round(days_count / window_days, 4) if window_days else 0.0,
                    "avg_premium": round(sum(info["premiums"]) / days_count, 4) if days_count else None,
                }
            )
        # 在榜天数降序（最稳定的排最前，与图表 y 轴顺序一致），同天数按代码
        bond_items.sort(key=lambda item: (-item["days_count"], item["bond_code"]))

        turnover: List[Dict[str, Any]] = []
        overlaps: List[int] = []
        entered_counts: List[int] = []
        prev_set: set[str] | None = None
        for trade_date in dates:
            current = {row["bond_code"] for row in membership.get(trade_date, [])}
            # 门槛 = 当日第 top_n 名（榜单内最高）溢价率
            members = membership.get(trade_date, [])
            threshold = members[-1]["premium_rate"] if members else None
            if prev_set is None:
                turnover.append(
                    {"date": trade_date.isoformat(), "overlap": None, "entered": None, "exited": None, "threshold": round(threshold, 4) if threshold is not None else None}
                )
            else:
                overlap = len(current & prev_set)
                entered = len(current - prev_set)
                overlaps.append(overlap)
                entered_counts.append(entered)
                turnover.append(
                    {
                        "date": trade_date.isoformat(),
                        "overlap": overlap,
                        "entered": entered,
                        "exited": len(prev_set - current),
                        "threshold": round(threshold, 4) if threshold is not None else None,
                    }
                )
            prev_set = current

        stats = {
            "window_days": window_days,
            "distinct_bonds": len(bond_items),
            "avg_overlap": round(sum(overlaps) / len(overlaps), 2) if overlaps else None,
            "avg_entered": round(sum(entered_counts) / len(entered_counts), 2) if entered_counts else None,
        }
        return {
            "market": market,
            "top_n": top_n,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "dates": [d.isoformat() for d in dates],
            "bonds": bond_items,
            "turnover": turnover,
            "stats": stats,
        }
