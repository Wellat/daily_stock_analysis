# -*- coding: utf-8 -*-
"""实盘可转债持仓聚合：QMT 持仓 × 最新因子 × 条款扁平化。

实盘-持仓 页的数据面：单账户、仅可转债、带溢价率/剩余年限/最后交易日/
强赎与下修倒计等基本面字段，供前端做关键日期突显。QMT 上报的是整账户
持仓（股票/基金/转债混在一起），非转债行在这里被过滤并计数（excluded_count）。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select

from src.core.trading_calendar import remaining_trading_days
from src.repositories.qmt_position_repo import QmtPositionRepository
from src.repositories.strategy_lab.data_repo import StrategyLabDataRepository
from src.services.live_strategy_service import LIVE_ACCOUNTS
from src.storage import DatabaseManager, LiveStrategyConfig

# remaining_trading_days 的哨兵值：间隔超过 30 个自然日时不再逐日计数，
# 前端展示上等同于「无倒计时」。
_REMAINING_FAR_AWAY = 999


class QmtCbHoldingsService:
    """实盘-持仓（可转债视图）的读取聚合。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()
        self.positions = QmtPositionRepository(self.db)
        self.data = StrategyLabDataRepository(self.db)

    def _resolve_account(self, account: Optional[str]) -> str:
        """显式账户优先；缺省回落到实盘实例的 qmt_account，并校验白名单。

        回落时只认 live_mode=order 策略的实例——notify/回测实例的 qmt_account
        是占位符；多实盘实例并存时优先取已启用实例，其次最新 order 行。
        """
        resolved = (account or "").strip()
        if not resolved:
            from src.core.strategy_lab.engine import list_builtin_strategies
            order_ids = {s["strategy_id"] for s in list_builtin_strategies() if s.get("live_mode") == "order"}
            with self.db.get_session() as session:
                rows = session.execute(
                    select(LiveStrategyConfig).order_by(desc(LiveStrategyConfig.id))
                ).scalars().all()
            order_rows = [r for r in rows if r.strategy_id in order_ids]
            enabled = next((r for r in order_rows if r.enabled), None)
            row = enabled or (order_rows[0] if order_rows else None) or (rows[0] if rows else None)
            resolved = (row.qmt_account if row else "") or ""
        if resolved not in LIVE_ACCOUNTS:
            raise ValueError(f"account must be one of {', '.join(sorted(LIVE_ACCOUNTS))}")
        return resolved

    def list_holdings(self, *, account: Optional[str] = None) -> Dict[str, Any]:
        account = self._resolve_account(account)
        rows = [r for r in self.positions.list(account=account) if float(r.volume or 0) > 0]
        # 全状态代码集（含已退市）：持仓里退市未清的债也要出行，而不是静默消失
        cb_codes = set(self.data.list_cb_basic_codes(market="cn"))
        cb_rows = [r for r in rows if r.symbol in cb_codes]
        excluded_count = len(rows) - len(cb_rows)
        enriched = (
            self.data.list_cb_holding_enrichments(market="cn", codes=[r.symbol for r in cb_rows])
            if cb_rows else {}
        )

        today = date.today()
        items: List[Dict[str, Any]] = []
        total_market_value = 0.0
        total_pnl = 0.0
        total_cost = 0.0
        as_of_trade_date: Optional[str] = None
        positions_updated_at: Optional[str] = None
        for row in cb_rows:
            info = enriched.get(row.symbol) or {}
            latest_close = info.get("latest_close")
            market_value = float(row.volume) * float(latest_close) if latest_close else None
            pnl = pnl_pct = None
            if latest_close and row.open_price:
                pnl = (float(latest_close) - float(row.open_price)) * float(row.volume)
                pnl_pct = (float(latest_close) / float(row.open_price) - 1.0) * 100.0
                total_pnl += pnl
                total_cost += float(row.open_price) * float(row.volume)
            if market_value is not None:
                total_market_value += market_value
            maturity_date = info.get("maturity_date")
            remaining_years = None
            if maturity_date:
                try:
                    remaining_years = round(
                        (date.fromisoformat(maturity_date) - today).days / 365.0, 2
                    )
                except ValueError:
                    remaining_years = None
            last_trading_date = info.get("last_trading_date")
            remaining_days: Optional[int] = None
            if last_trading_date:
                try:
                    counted = remaining_trading_days("cn", today, date.fromisoformat(last_trading_date))
                    remaining_days = None if counted >= _REMAINING_FAR_AWAY else counted
                except ValueError:
                    remaining_days = None
            updated_at = row.updated_at.isoformat(sep=" ") if row.updated_at else None
            if updated_at and (positions_updated_at is None or updated_at > positions_updated_at):
                positions_updated_at = updated_at
            factor_date = info.get("latest_trade_date")
            if factor_date and (as_of_trade_date is None or factor_date > as_of_trade_date):
                as_of_trade_date = factor_date
            items.append({
                "symbol": row.symbol,
                "bond_name": info.get("bond_name") or row.name,
                "stock_name": info.get("stock_name"),
                "volume": float(row.volume),
                "can_use_volume": float(row.can_use_volume or 0),
                "open_price": row.open_price,
                "latest_close": latest_close,
                "latest_premium_rate": info.get("latest_premium_rate"),
                "market_value": market_value,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "remaining_years": remaining_years,
                "maturity_date": maturity_date,
                "last_trading_date": last_trading_date,
                "remaining_trading_days": remaining_days,
                "force_redeem_countdown": info.get("force_redeem_countdown"),
                "down_revise_countdown": info.get("down_revise_countdown"),
                "put_countdown": info.get("put_countdown"),
                "bond_rating": info.get("bond_rating"),
                "remaining_size": info.get("remaining_size"),
                "status": info.get("status"),
                "updated_at": updated_at,
            })

        # 市值降序，缺行情的排最后
        items.sort(key=lambda x: (x["market_value"] is None, -(x["market_value"] or 0.0), x["symbol"]))
        for item in items:
            item["weight_pct"] = (
                round(item["market_value"] / total_market_value * 100.0, 2)
                if item["market_value"] is not None and total_market_value > 0 else None
            )
        summary = {
            "count": len(items),
            "total_volume": round(sum(item["volume"] for item in items), 2),
            "total_market_value": round(total_market_value, 2) if total_market_value else None,
            "total_pnl": round(total_pnl, 2) if total_cost else None,
            "total_pnl_pct": round(total_pnl / total_cost * 100.0, 2) if total_cost else None,
        }
        return {
            "account": account,
            "as_of_trade_date": as_of_trade_date,
            "positions_updated_at": positions_updated_at,
            "excluded_count": excluded_count,
            "summary": summary,
            "items": items,
        }
