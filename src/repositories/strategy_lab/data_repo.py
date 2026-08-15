# -*- coding: utf-8 -*-
"""Strategy Lab data-sync repository."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, desc, func, or_, select

from src.storage import (
    DatabaseManager,
    PortfolioAccount,
    PortfolioPosition,
    StockDaily,
    StrategyLabCbBasic,
    StrategyLabCbDailyFactor,
    StrategyLabCbEvent,
    StrategyLabCbTerms,
    StrategyLabSyncRun,
)


class StrategyLabDataRepository:
    """Persist Strategy Lab instrument and factor data."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def create_sync_run(
        self,
        *,
        run_uid: str,
        sync_type: str,
        market: str,
        payload: Dict[str, Any],
    ) -> StrategyLabSyncRun:
        with self.db.get_session() as session:
            row = StrategyLabSyncRun(
                run_uid=run_uid,
                sync_type=sync_type,
                market=market,
                status="running",
                payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def complete_sync_run(self, run_id: int, *, result: Dict[str, Any]) -> StrategyLabSyncRun:
        with self.db.get_session() as session:
            row = session.get(StrategyLabSyncRun, run_id)
            if row is None:
                raise ValueError(f"Strategy Lab sync run not found: {run_id}")
            row.status = "completed"
            row.result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
            row.completed_at = datetime.now()
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def fail_sync_run(self, run_id: int, message: str) -> None:
        with self.db.get_session() as session:
            row = session.get(StrategyLabSyncRun, run_id)
            if row is None:
                return
            row.status = "failed"
            row.error_message = message
            row.completed_at = datetime.now()
            session.commit()

    def update_sync_run_progress(self, run_id: int, *, result: Dict[str, Any]) -> None:
        """Write an intermediate result snapshot for a running sync run."""
        with self.db.get_session() as session:
            row = session.get(StrategyLabSyncRun, run_id)
            if row is None:
                return
            row.result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
            session.commit()

    def upsert_cb_basic(self, rows: List[Dict[str, Any]], *, source: str) -> int:
        count = 0
        with self.db.get_session() as session:
            for item in rows:
                bond_code = str(item["bond_code"])
                row = session.get(StrategyLabCbBasic, bond_code)
                if row is None:
                    row = StrategyLabCbBasic(bond_code=bond_code)
                    session.add(row)
                row.bond_name = str(item.get("bond_name") or bond_code)
                row.stock_code = str(item.get("stock_code") or "")
                row.stock_name = item.get("stock_name")
                row.market = str(item.get("market") or "cn")
                row.list_date = item.get("list_date")
                row.maturity_date = item.get("maturity_date")
                row.status = item.get("status")
                row.remaining_size = item.get("remaining_size")
                row.current_premium_rate = item.get("current_premium_rate")
                row.convert_price = item.get("convert_price")
                row.terms_json = json.dumps(
                    item.get("terms") or {},
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,  # 兜底：任何非 JSON 类型降级为字符串，避免单行拖垮整个同步
                )
                row.source = source
                row.updated_at = datetime.now()
                count += 1
            session.commit()
        return count

    def upsert_cb_terms(self, rows: List[Dict[str, Any]], *, source: str) -> int:
        count = 0
        with self.db.get_session() as session:
            for item in rows:
                bond_code = str(item["bond_code"])
                row = session.get(StrategyLabCbTerms, bond_code)
                if row is None:
                    row = StrategyLabCbTerms(bond_code=bond_code)
                    session.add(row)
                row.redeem_clause = item.get("redeem_clause")
                row.down_revise_clause = item.get("down_revise_clause")
                row.put_clause = item.get("put_clause")
                row.redeem_trigger_price = item.get("redeem_trigger_price")
                row.down_revise_trigger_price = item.get("down_revise_trigger_price")
                row.put_trigger_price = item.get("put_trigger_price")
                row.source = source
                row.updated_at = datetime.now()
                count += 1
            session.commit()
        return count

    def upsert_cb_daily_factors(self, rows: List[Dict[str, Any]], *, source: str) -> int:
        count = 0
        with self.db.get_session() as session:
            for item in rows:
                row = session.execute(
                    select(StrategyLabCbDailyFactor).where(
                        and_(
                            StrategyLabCbDailyFactor.bond_code == str(item["bond_code"]),
                            StrategyLabCbDailyFactor.trade_date == item["trade_date"],
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = StrategyLabCbDailyFactor(
                        bond_code=str(item["bond_code"]),
                        trade_date=item["trade_date"],
                    )
                    session.add(row)
                row.close = item.get("close")
                row.premium_rate = item.get("premium_rate")
                row.remaining_size = item.get("remaining_size")
                row.redeem_alert = bool(item.get("redeem_alert") or False)
                row.down_revise_alert = bool(item.get("down_revise_alert") or False)
                row.put_alert = bool(item.get("put_alert") or False)
                row.source = source
                row.updated_at = datetime.now()
                count += 1
            session.commit()
        return count

    def upsert_cb_events(self, rows: List[Dict[str, Any]], *, source: str) -> int:
        """Upsert convertible-bond events.

        去重键：(bond_code, event_date, 小写 event_type)。写入前统一把
        event_type 归一化为小写，并用 ``func.lower`` 比对已有记录，避免同一
        事件因大小写变体被重复录入；命中已有记录时更新 event_detail。
        """
        count = 0
        with self.db.get_session() as session:
            for item in rows:
                bond_code = str(item["bond_code"])
                event_date = item["event_date"]
                event_type = str(item["event_type"]).strip().lower()
                matches = session.execute(
                    select(StrategyLabCbEvent).where(
                        and_(
                            StrategyLabCbEvent.bond_code == bond_code,
                            StrategyLabCbEvent.event_date == event_date,
                            func.lower(StrategyLabCbEvent.event_type) == event_type,
                        )
                    ).order_by(StrategyLabCbEvent.id.asc())
                ).scalars().all()
                if matches:
                    row = matches[0]
                    for duplicate in matches[1:]:
                        session.delete(duplicate)
                else:
                    row = StrategyLabCbEvent(
                        bond_code=bond_code,
                        event_date=event_date,
                        event_type=event_type,
                    )
                    session.add(row)
                row.event_type = event_type
                row.event_detail = item.get("event_detail")
                row.source = source
                count += 1
            session.commit()
        return count

    def list_sync_runs(self, *, limit: int, offset: int) -> Dict[str, Any]:
        with self.db.get_session() as session:
            total = session.execute(select(StrategyLabSyncRun.id)).scalars().all()
            rows = session.execute(
                select(StrategyLabSyncRun)
                .order_by(desc(StrategyLabSyncRun.created_at), desc(StrategyLabSyncRun.id))
                .offset(offset)
                .limit(limit)
            ).scalars().all()
            return {
                "total": len(total),
                "items": [self._sync_run_payload(row) for row in rows],
            }

    def list_cb_instruments(
        self,
        *,
        market: str,
        keyword: Optional[str] = None,
        limit: int,
        offset: int,
        status: Optional[str] = None,
        held_only: bool = False,
    ) -> Dict[str, Any]:
        """List convertible-bond instruments with latest factor and event counts.

        ``status`` accepts "active"（正常）or "delisted"（已退市）; ``held_only``
        restricts to symbols still held in active Portfolio accounts.
        """
        normalized_keyword = str(keyword).strip().lower() if keyword else None
        with self.db.get_session() as session:
            base = select(StrategyLabCbBasic).where(StrategyLabCbBasic.market == market)
            if normalized_keyword:
                pattern = f"%{normalized_keyword}%"
                base = base.where(
                    or_(
                        func.lower(StrategyLabCbBasic.bond_code).like(pattern),
                        func.lower(StrategyLabCbBasic.bond_name).like(pattern),
                        func.lower(StrategyLabCbBasic.stock_code).like(pattern),
                    )
                )
            if status == "active":
                base = base.where(StrategyLabCbBasic.status == "正常")
            elif status == "delisted":
                base = base.where(StrategyLabCbBasic.status == "已退市")
            if held_only:
                held_codes = (
                    select(PortfolioPosition.symbol)
                    .join(PortfolioAccount, PortfolioAccount.id == PortfolioPosition.account_id)
                    .where(
                        PortfolioAccount.is_active.is_(True),
                        PortfolioPosition.quantity != 0,
                        PortfolioPosition.market == market,
                    )
                )
                base = base.where(StrategyLabCbBasic.bond_code.in_(held_codes))
            total = session.execute(
                select(func.count()).select_from(base.subquery())
            ).scalar_one()
            rows = session.execute(
                base.order_by(StrategyLabCbBasic.updated_at.desc(), StrategyLabCbBasic.bond_code.asc())
                .offset(offset)
                .limit(limit)
            ).scalars().all()
            codes = [row.bond_code for row in rows]
            latest_by_code: Dict[str, StrategyLabCbDailyFactor] = {}
            event_counts: Dict[str, int] = {}
            if codes:
                latest_dates = session.execute(
                    select(
                        StrategyLabCbDailyFactor.bond_code,
                        func.max(StrategyLabCbDailyFactor.trade_date).label("max_date"),
                    )
                    .where(StrategyLabCbDailyFactor.bond_code.in_(codes))
                    .group_by(StrategyLabCbDailyFactor.bond_code)
                ).all()
                for code, max_date in latest_dates:
                    factor = session.execute(
                        select(StrategyLabCbDailyFactor).where(
                            StrategyLabCbDailyFactor.bond_code == code,
                            StrategyLabCbDailyFactor.trade_date == max_date,
                        )
                    ).scalar_one_or_none()
                    if factor is not None:
                        latest_by_code[code] = factor
                event_counts = dict(
                    session.execute(
                        select(StrategyLabCbEvent.bond_code, func.count(StrategyLabCbEvent.id))
                        .where(StrategyLabCbEvent.bond_code.in_(codes))
                        .group_by(StrategyLabCbEvent.bond_code)
                    ).all()
                )
            return {
                "total": int(total),
                "items": [
                    self._instrument_list_item(row, latest_by_code.get(row.bond_code), event_counts.get(row.bond_code, 0))
                    for row in rows
                ],
            }

    def list_cb_basic_codes(self, *, market: str, status: Optional[str] = None) -> List[str]:
        """Return bond codes in the master table, optionally filtered by status.

        ``status`` accepts "正常" (active) or "已退市" (delisted); None returns
        every code. Used by the OHLC sync to decide which symbols to walk.
        """
        with self.db.get_session() as session:
            statement = select(StrategyLabCbBasic.bond_code).where(StrategyLabCbBasic.market == market)
            if status:
                statement = statement.where(StrategyLabCbBasic.status == status)
            return list(
                session.execute(statement.order_by(StrategyLabCbBasic.bond_code.asc())).scalars().all()
            )

    def get_cb_ohlc_latest_date(self, *, bond_code: str) -> Optional[date]:
        """Return the latest persisted ``stock_daily`` date for a convertible bond, or None."""
        with self.db.get_session() as session:
            return session.execute(
                select(func.max(StockDaily.date)).where(
                    StockDaily.code == str(bond_code),
                    StockDaily.instrument_type == "convertible_bond",
                )
            ).scalar_one_or_none()

    def get_cb_instrument_detail(self, *, bond_code: str, market: str) -> Dict[str, Any] | None:
        """Return convertible-bond basic + terms detail, or None when missing."""
        with self.db.get_session() as session:
            basic = session.get(StrategyLabCbBasic, bond_code)
            if basic is None or basic.market != market:
                return None
            terms = session.get(StrategyLabCbTerms, bond_code)
            latest_factor = session.execute(
                select(StrategyLabCbDailyFactor)
                .where(StrategyLabCbDailyFactor.bond_code == bond_code)
                .order_by(desc(StrategyLabCbDailyFactor.trade_date), desc(StrategyLabCbDailyFactor.id))
                .limit(1)
            ).scalar_one_or_none()
            terms_data = json.loads(basic.terms_json) if basic.terms_json else {}
            bar_count = session.execute(
                select(func.count(StrategyLabCbDailyFactor.id)).where(
                    StrategyLabCbDailyFactor.bond_code == bond_code
                )
            ).scalar_one()
            event_count = session.execute(
                select(func.count(StrategyLabCbEvent.id)).where(
                    StrategyLabCbEvent.bond_code == bond_code
                )
            ).scalar_one()
            return {
                "bond_code": basic.bond_code,
                "bond_name": basic.bond_name,
                "stock_code": basic.stock_code,
                "stock_name": basic.stock_name,
                "market": basic.market,
                "list_date": basic.list_date.isoformat() if basic.list_date else None,
                "maturity_date": basic.maturity_date.isoformat() if basic.maturity_date else None,
                "status": basic.status,
                "remaining_size": basic.remaining_size,
                "current_premium_rate": basic.current_premium_rate,
                "convert_price": basic.convert_price,
                "latest_close": latest_factor.close if latest_factor else None,
                "latest_premium_rate": latest_factor.premium_rate if latest_factor else None,
                "industry": terms_data.get("industry"),
                "terms": terms_data,
                "redeem_clause": terms.redeem_clause if terms else None,
                "down_revise_clause": terms.down_revise_clause if terms else None,
                "put_clause": terms.put_clause if terms else None,
                "redeem_trigger_price": terms.redeem_trigger_price if terms else None,
                "down_revise_trigger_price": terms.down_revise_trigger_price if terms else None,
                "put_trigger_price": terms.put_trigger_price if terms else None,
                "source": basic.source,
                "updated_at": basic.updated_at.isoformat() if basic.updated_at else None,
                "bar_count": int(bar_count),
                "event_count": int(event_count),
            }

    def list_cb_daily_factors(
        self,
        *,
        bond_code: str,
        start_date: Any = None,
        end_date: Any = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """Return persisted daily factors for one instrument, ascending by date.

        ``limit`` means the most recent N rows: older rows are dropped when the
        stored history exceeds the limit, so the chart always ends at the latest
        available date.
        """
        with self.db.get_session() as session:
            statement = select(StrategyLabCbDailyFactor).where(
                StrategyLabCbDailyFactor.bond_code == bond_code
            )
            if start_date is not None:
                statement = statement.where(StrategyLabCbDailyFactor.trade_date >= start_date)
            if end_date is not None:
                statement = statement.where(StrategyLabCbDailyFactor.trade_date <= end_date)
            rows = session.execute(
                statement.order_by(StrategyLabCbDailyFactor.trade_date.desc()).limit(limit)
            ).scalars().all()
            rows = list(reversed(rows))
            return {
                "bond_code": bond_code,
                "total": len(rows),
                "items": [self._factor_payload(row) for row in rows],
            }

    def list_cb_events(
        self,
        *,
        bond_code: str,
        event_type: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Return persisted events for one instrument, newest first."""
        with self.db.get_session() as session:
            statement = select(StrategyLabCbEvent).where(
                StrategyLabCbEvent.bond_code == bond_code
            )
            if event_type:
                statement = statement.where(StrategyLabCbEvent.event_type == event_type)
            rows = session.execute(
                statement.order_by(desc(StrategyLabCbEvent.event_date), desc(StrategyLabCbEvent.id)).limit(limit)
            ).scalars().all()
            return {
                "bond_code": bond_code,
                "total": len(rows),
                "items": [
                    {
                        "event_date": row.event_date.isoformat() if row.event_date else None,
                        "event_type": row.event_type,
                        "event_detail": row.event_detail,
                        "source": row.source,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                    for row in rows
                ],
            }

    @staticmethod
    def _instrument_list_item(
        basic: StrategyLabCbBasic,
        latest_factor: Optional[StrategyLabCbDailyFactor],
        event_count: int,
    ) -> Dict[str, Any]:
        return {
            "bond_code": basic.bond_code,
            "bond_name": basic.bond_name,
            "stock_code": basic.stock_code,
            "stock_name": basic.stock_name,
            "market": basic.market,
            "list_date": basic.list_date.isoformat() if basic.list_date else None,
            "maturity_date": basic.maturity_date.isoformat() if basic.maturity_date else None,
            "status": basic.status,
            "remaining_size": basic.remaining_size,
            "current_premium_rate": basic.current_premium_rate,
            "convert_price": basic.convert_price,
            "latest_close": latest_factor.close if latest_factor else None,
            "latest_premium_rate": latest_factor.premium_rate if latest_factor else None,
            "event_count": event_count,
            "source": basic.source,
            "updated_at": basic.updated_at.isoformat() if basic.updated_at else None,
        }

    @staticmethod
    def _factor_payload(row: StrategyLabCbDailyFactor) -> Dict[str, Any]:
        return {
            "trade_date": row.trade_date.isoformat() if row.trade_date else None,
            "close": row.close,
            "premium_rate": row.premium_rate,
            "remaining_size": row.remaining_size,
            "redeem_alert": bool(row.redeem_alert),
            "down_revise_alert": bool(row.down_revise_alert),
            "put_alert": bool(row.put_alert),
            "source": row.source,
        }

    def load_cb_backtest_rows(
        self,
        *,
        market: str,
        start_date: Any,
        end_date: Any,
        symbols: List[str],
    ) -> List[Dict[str, Any]]:
        """Return normalized persisted CB prices and factors for one strategy run."""
        normalized_symbols = {str(symbol).strip().lower().split(".")[-1] for symbol in symbols if str(symbol).strip()}
        with self.db.get_session() as session:
            statement = (
                select(StrategyLabCbBasic, StrategyLabCbDailyFactor)
                .join(
                    StrategyLabCbDailyFactor,
                    StrategyLabCbDailyFactor.bond_code == StrategyLabCbBasic.bond_code,
                )
                .where(
                    StrategyLabCbBasic.market == market,
                    StrategyLabCbDailyFactor.trade_date >= start_date,
                    StrategyLabCbDailyFactor.trade_date <= end_date,
                    StrategyLabCbDailyFactor.close.is_not(None),
                )
                .order_by(StrategyLabCbBasic.bond_code.asc(), StrategyLabCbDailyFactor.trade_date.asc())
            )
            rows = session.execute(statement).all()
            return [
                {
                    "bond_code": basic.bond_code,
                    "bond_name": basic.bond_name,
                    "market": basic.market,
                    "trade_date": factor.trade_date,
                    "close": factor.close,
                    "premium_rate": factor.premium_rate,
                    "remaining_size": factor.remaining_size,
                    "event_blocked": bool(factor.redeem_alert or factor.down_revise_alert or factor.put_alert),
                }
                for basic, factor in rows
                if not normalized_symbols or basic.bond_code.lower() in normalized_symbols
            ]

    def load_cb_event_study_rows(
        self,
        *,
        market: str,
        event_type: Optional[str] = None,
        symbols: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        normalized_symbols = {str(symbol).strip().lower().split(".")[-1] for symbol in symbols or [] if str(symbol).strip()}
        with self.db.get_session() as session:
            statement = (
                select(StrategyLabCbBasic, StrategyLabCbEvent, StrategyLabCbDailyFactor)
                .join(StrategyLabCbEvent, StrategyLabCbEvent.bond_code == StrategyLabCbBasic.bond_code)
                .join(StrategyLabCbDailyFactor, StrategyLabCbDailyFactor.bond_code == StrategyLabCbBasic.bond_code)
                .where(StrategyLabCbBasic.market == market, StrategyLabCbDailyFactor.close.is_not(None))
                .order_by(StrategyLabCbEvent.event_date.asc(), StrategyLabCbBasic.bond_code.asc(), StrategyLabCbDailyFactor.trade_date.asc())
            )
            if event_type:
                statement = statement.where(StrategyLabCbEvent.event_type == event_type)
            rows = session.execute(statement).all()
            return [
                {
                    "bond_code": basic.bond_code,
                    "bond_name": basic.bond_name,
                    "event_date": event.event_date,
                    "event_type": event.event_type,
                    "trade_date": factor.trade_date,
                    "close": float(factor.close),
                }
                for basic, event, factor in rows
                if not normalized_symbols or basic.bond_code.lower() in normalized_symbols
            ]

    @staticmethod
    def _sync_run_payload(row: StrategyLabSyncRun) -> Dict[str, Any]:
        return {
            "id": row.id,
            "run_uid": row.run_uid,
            "sync_type": row.sync_type,
            "market": row.market,
            "status": row.status,
            "result": json.loads(row.result_json) if row.result_json else {},
            "error_message": row.error_message,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
