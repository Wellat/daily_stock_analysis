# -*- coding: utf-8 -*-
"""Portfolio 每日快照趋势服务。

趋势数据来自 ``portfolio_daily_snapshots``（account_id + snapshot_date +
cost_method 唯一）：盘后定时任务每个交易日生成当日快照，趋势查询对缺失
日期按需重放回补（幂等；账本回溯修改会自动失效受影响日期的快照，之后
自愈重算）。汇总视图按日期跨活跃账户直接求和（基准币假定 CNY）。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from src.core.trading_calendar import cn_trading_days_between
from src.repositories.portfolio_repo import PortfolioRepository
from src.services.portfolio_service import PortfolioService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 90
BACKFILL_ACCOUNT_DAY_LIMIT = 400
_VALID_COST_METHODS = {"fifo", "avg"}


class PortfolioTrendService:
    """组合每日快照的趋势查询与回补。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()
        self.repo = PortfolioRepository(self.db)
        self.portfolio_service = PortfolioService(repo=self.repo)

    def get_trend(
        self,
        *,
        account_id: Optional[int] = None,
        start: Optional[date] = None,
        end: Optional[date] = None,
        cost_method: str = "fifo",
    ) -> Dict[str, Any]:
        """查询 [start, end] 内的每日快照趋势，缺失日期按需回补。

        - account_id 缺省时汇总全部活跃账户（按日期求和）；指定时校验存在且活跃。
        - start 缺省为 end 往前推 90 个自然日；只取范围内的 CN 交易日且不晚于今天。
        - 回补单次上限 400 账户日，超出截断并在响应中标记 truncated。
        """
        method = (cost_method or "fifo").strip().lower()
        if method not in _VALID_COST_METHODS:
            raise ValueError("cost_method must be fifo or avg")
        end_date = end or date.today()
        start_date = start or (end_date - timedelta(days=DEFAULT_LOOKBACK_DAYS - 1))
        if start_date > end_date:
            raise ValueError("start must not be later than end")

        if account_id is not None:
            account = self.repo.get_account(account_id)
            if account is None or not account.is_active:
                raise ValueError(f"portfolio account {account_id} not found or inactive")
            accounts = [account]
        else:
            accounts = [item for item in self.repo.list_accounts() if item.is_active]

        today = date.today()
        target_dates = [day for day in cn_trading_days_between(start_date, end_date) if day <= today]

        backfilled = 0
        truncated = False
        for account in accounts:
            earliest = self.repo.earliest_event_date(account.id)
            if earliest is None:
                continue
            existing = {
                row.snapshot_date
                for row in self.repo.list_daily_snapshots(
                    date_from=start_date, date_to=end_date, cost_method=method, account_id=account.id
                )
            }
            missing = [day for day in target_dates if day >= earliest and day not in existing]
            if not missing:
                continue
            if backfilled + len(missing) > BACKFILL_ACCOUNT_DAY_LIMIT:
                missing = missing[: max(0, BACKFILL_ACCOUNT_DAY_LIMIT - backfilled)]
                truncated = True
            for day in missing:
                self.portfolio_service.replay_daily_snapshot(
                    account=account, as_of=day, cost_method=method
                )
                backfilled += 1
        if backfilled:
            logger.info(
                "[PortfolioTrend] backfilled %d daily snapshots (%s, account_id=%s)",
                backfilled, method, account_id,
            )

        rows = self.repo.list_daily_snapshots(
            date_from=start_date, date_to=end_date, cost_method=method, account_id=account_id
        )
        by_date: Dict[date, Dict[str, float]] = {}
        for row in rows:
            bucket = by_date.setdefault(
                row.snapshot_date,
                {
                    "total_cash": 0.0,
                    "total_market_value": 0.0,
                    "total_equity": 0.0,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0,
                },
            )
            bucket["total_cash"] += float(row.total_cash or 0.0)
            bucket["total_market_value"] += float(row.total_market_value or 0.0)
            bucket["total_equity"] += float(row.total_equity or 0.0)
            bucket["realized_pnl"] += float(row.realized_pnl or 0.0)
            bucket["unrealized_pnl"] += float(row.unrealized_pnl or 0.0)

        items: List[Dict[str, Any]] = []
        for day in sorted(by_date):
            bucket = by_date[day]
            items.append(
                {
                    "date": day.isoformat(),
                    "total_cash": round(bucket["total_cash"], 6),
                    "total_market_value": round(bucket["total_market_value"], 6),
                    "total_equity": round(bucket["total_equity"], 6),
                    "realized_pnl": round(bucket["realized_pnl"], 6),
                    "unrealized_pnl": round(bucket["unrealized_pnl"], 6),
                    "total_pnl": round(bucket["realized_pnl"] + bucket["unrealized_pnl"], 6),
                }
            )
        return {
            "account_id": account_id,
            "cost_method": method,
            "currency": "CNY",
            "from_date": start_date.isoformat(),
            "to_date": end_date.isoformat(),
            "backfilled": backfilled,
            "truncated": truncated,
            "items": items,
        }

    def run_daily_snapshot(self, trade_date: Optional[date] = None) -> Dict[str, Any]:
        """每个活跃账户 × 两种成本法生成当日快照（盘后定时任务入口）。

        幂等：已有当日快照会被重放结果覆盖（upsert），晚间行情补齐后重跑可刷新。
        """
        day = trade_date or date.today()
        accounts = [item for item in self.repo.list_accounts() if item.is_active]
        written = 0
        for account in accounts:
            if self.repo.earliest_event_date(account.id) is None:
                continue
            for method in sorted(_VALID_COST_METHODS):
                self.portfolio_service.replay_daily_snapshot(
                    account=account, as_of=day, cost_method=method
                )
                written += 1
        logger.info(
            "[PortfolioTrend] daily snapshots written=%d date=%s accounts=%d",
            written, day.isoformat(), len(accounts),
        )
        return {"trade_date": day.isoformat(), "accounts": len(accounts), "snapshots": written}
