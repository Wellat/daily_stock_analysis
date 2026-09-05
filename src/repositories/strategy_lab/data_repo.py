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
        run_kind: str = "after_close",
        trade_date: Optional[date] = None,
    ) -> StrategyLabSyncRun:
        with self.db.get_session() as session:
            row = StrategyLabSyncRun(
                run_uid=run_uid,
                sync_type=sync_type,
                market=market,
                run_kind=run_kind,
                trade_date=trade_date,
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
            if row.status == "cancelled":
                session.expunge(row)
                return row
            row.status = "completed"
            if hasattr(row, "quality_status"):
                row.quality_status = "usable"
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

    def request_sync_run_cancel(self, run_id: int) -> Optional[Dict[str, Any]]:
        """Request cooperative cancellation for a sync run.

        Terminal runs are returned unchanged so the cancel endpoint is idempotent.
        """
        with self.db.get_session() as session:
            row = session.get(StrategyLabSyncRun, run_id)
            if row is None:
                return None
            if row.status == "running":
                row.cancel_requested = True
                session.commit()
                session.refresh(row)
            return self._sync_run_payload(row)

    def is_sync_run_cancel_requested(self, run_id: int) -> bool:
        with self.db.get_session() as session:
            row = session.get(StrategyLabSyncRun, run_id)
            return bool(row and row.status == "running" and row.cancel_requested)

    def cancel_sync_run(self, run_id: int, *, result: Dict[str, Any]) -> StrategyLabSyncRun:
        with self.db.get_session() as session:
            row = session.get(StrategyLabSyncRun, run_id)
            if row is None:
                raise ValueError(f"Strategy Lab sync run not found: {run_id}")
            row.status = "cancelled"
            row.result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
            row.completed_at = datetime.now()
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def update_sync_run_progress(self, run_id: int, *, result: Dict[str, Any]) -> None:
        """Write an intermediate result snapshot for a running sync run."""
        with self.db.get_session() as session:
            row = session.get(StrategyLabSyncRun, run_id)
            if row is None:
                return
            if row.status != "running":
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
                stock_code = str(item.get("stock_code") or "").strip()
                if stock_code or row.stock_code is None:
                    row.stock_code = stock_code
                stock_name = item.get("stock_name")
                if stock_name not in (None, "") or row.stock_name is None:
                    row.stock_name = stock_name
                row.market = str(item.get("market") or "cn")
                row.list_date = item.get("list_date")
                row.maturity_date = item.get("maturity_date")
                row.status = item.get("status")
                remaining_size = item.get("remaining_size")
                if remaining_size is not None or row.remaining_size is None:
                    row.remaining_size = remaining_size
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
        # 数据源可能返回重复的 (bond_code, event_date, event_type)（实测 jisilu
        # 的 cb_event_list 会出现完全相同的重复事件）。本 session 使用
        # autoflush=False，未 flush 的新行无法被后续 select 命中，若不去重会在
        # commit 时触发 UNIQUE 约束冲突，因此先在内存按去重键过滤。
        seen: set[tuple] = set()
        unique_rows: List[Dict[str, Any]] = []
        for item in rows:
            key = (
                str(item["bond_code"]),
                item["event_date"],
                str(item["event_type"]).strip().lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            unique_rows.append(item)

        count = 0
        with self.db.get_session() as session:
            for item in unique_rows:
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
                base = base.where(StrategyLabCbBasic.status.in_(("active", "正常")))
            elif status == "delisted":
                base = base.where(StrategyLabCbBasic.status.in_(("delisted", "已退市")))
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

    def get_cb_ohlc_latest_date(
        self, *, code: str, instrument_type: str = "convertible_bond"
    ) -> Optional[date]:
        """Return the latest persisted ``stock_daily`` date for a code, or None.

        ``instrument_type`` selects which daily rows to look at; the OHLC sync
        uses it to resolve the incremental start date per symbol.
        """
        with self.db.get_session() as session:
            return session.execute(
                select(func.max(StockDaily.date)).where(
                    StockDaily.code == str(code),
                    StockDaily.instrument_type == instrument_type,
                )
            ).scalar_one_or_none()

    def list_cb_stock_codes(
        self,
        *,
        market: str,
        bond_codes: Optional[List[str]] = None,
    ) -> List[str]:
        """Return deduplicated underlying-stock codes of active convertible bonds.

        Only bonds with ``status == "正常"`` are considered, so underlying
        stocks of delisted bonds are excluded. ``bond_codes`` optionally
        restricts the bond scope for symbol-scoped debugging.
        """
        with self.db.get_session() as session:
            statement = select(StrategyLabCbBasic.stock_code).where(
                StrategyLabCbBasic.market == market,
                StrategyLabCbBasic.status.in_(("active", "正常")),
            )
            if bond_codes:
                statement = statement.where(StrategyLabCbBasic.bond_code.in_(bond_codes))
            rows = session.execute(
                statement.order_by(StrategyLabCbBasic.stock_code.asc())
            ).scalars().all()
            return sorted({str(row).strip() for row in rows if row and str(row).strip()})

    def list_cb_factor_inputs(
        self,
        *,
        market: str,
        trade_date: date,
    ) -> List[Dict[str, Any]]:
        """Return active bonds' factor inputs joined with the day's bond close.

        取 ``status == "正常"`` 转债的 ``stock_code`` / ``convert_price`` /
        ``remaining_size``，并 LEFT JOIN 当日因子行带出 ``close`` 作为转债价
        来源；供可转债因子计算遍历使用。
        """
        with self.db.get_session() as session:
            event_rows = session.execute(
                select(StrategyLabCbEvent.bond_code, StrategyLabCbEvent.event_type).where(
                    StrategyLabCbEvent.event_date == trade_date
                )
            ).all()
            event_flags: Dict[str, set[str]] = {}
            for bond_code, event_type in event_rows:
                event_flags.setdefault(str(bond_code), set()).add(str(event_type))
            rows = session.execute(
                select(
                    StrategyLabCbBasic.bond_code,
                    StrategyLabCbBasic.stock_code,
                    StrategyLabCbBasic.convert_price,
                    StrategyLabCbBasic.remaining_size,
                    StrategyLabCbDailyFactor.close,
                )
                .join(
                    StrategyLabCbDailyFactor,
                    and_(
                        StrategyLabCbDailyFactor.bond_code == StrategyLabCbBasic.bond_code,
                        StrategyLabCbDailyFactor.trade_date == trade_date,
                    ),
                    isouter=True,
                )
                .where(
                    StrategyLabCbBasic.market == market,
                    StrategyLabCbBasic.status.in_(("active", "正常")),
                )
                .order_by(StrategyLabCbBasic.bond_code.asc())
            ).all()
            return [
                {
                    "bond_code": str(row.bond_code),
                    "stock_code": str(row.stock_code or "").strip(),
                    "convert_price": row.convert_price,
                    "remaining_size": row.remaining_size,
                    "close": row.close,
                    "redeem_alert": "strong_redeem" in event_flags.get(str(row.bond_code), set()),
                    "down_revise_alert": "down_revise" in event_flags.get(str(row.bond_code), set()),
                    "put_alert": "put" in event_flags.get(str(row.bond_code), set()),
                }
                for row in rows
            ]

    def patch_cb_daily_factor_fields(
        self,
        rows: List[Dict[str, Any]],
        *,
        source: str,
    ) -> Dict[str, int]:
        """Patch missing premium / remaining-size fields on existing CB daily-factor rows."""
        counts = {
            "rows_examined": 0,
            "rows_matched": 0,
            "rows_updated": 0,
            "premium_rate_patched": 0,
            "remaining_size_patched": 0,
        }
        if not rows:
            return counts

        grouped: Dict[str, Dict[date, Dict[str, Any]]] = {}
        for item in rows:
            bond_code = str(item.get("bond_code") or "").strip()
            trade_date = item.get("trade_date")
            if not bond_code or not isinstance(trade_date, date):
                continue
            grouped.setdefault(bond_code, {})[trade_date] = {
                "premium_rate": self._normalize_patch_float(item.get("premium_rate")),
                "remaining_size": self._normalize_patch_float(item.get("remaining_size")),
            }
            counts["rows_examined"] += 1

        if not grouped:
            return counts

        now = datetime.now()
        with self.db.get_session() as session:
            for bond_code, day_rows in grouped.items():
                existing_rows = session.execute(
                    select(StrategyLabCbDailyFactor).where(
                        and_(
                            StrategyLabCbDailyFactor.bond_code == bond_code,
                            StrategyLabCbDailyFactor.trade_date.in_(list(day_rows.keys())),
                        )
                    )
                ).scalars().all()
                existing_by_date = {row.trade_date: row for row in existing_rows}
                for trade_date, patch in day_rows.items():
                    row = existing_by_date.get(trade_date)
                    if row is None:
                        continue
                    counts["rows_matched"] += 1
                    updated = False
                    premium_rate = patch["premium_rate"]
                    if row.premium_rate is None and premium_rate is not None:
                        row.premium_rate = premium_rate
                        counts["premium_rate_patched"] += 1
                        updated = True
                    remaining_size = patch["remaining_size"]
                    if row.remaining_size is None and remaining_size is not None:
                        row.remaining_size = remaining_size
                        counts["remaining_size_patched"] += 1
                        updated = True
                    if updated:
                        row.updated_at = now
                        row.source = row.source or source
                        counts["rows_updated"] += 1
                session.commit()
        return counts

    def update_cb_daily_factor_fields(self, rows: List[Dict[str, Any]], *, source: str) -> int:
        """Overwrite provided factor fields on existing CB daily-factor rows.

        与 ``upsert_cb_daily_factors`` 的整行覆盖不同：只更新每行 dict 中实际
        出现的 ``stock_close`` / ``premium_rate`` / ``remaining_size``，不新建
        行，也不清空未提供的字段（close / alerts 等），返回命中并更新的行数。
        """
        if not rows:
            return 0
        now = datetime.now()
        count = 0
        with self.db.get_session() as session:
            for item in rows:
                fields = {
                    name: item.get(name)
                    for name in ("stock_close", "premium_rate", "remaining_size", "redeem_alert", "down_revise_alert", "put_alert")
                    if name in item
                }
                if not fields:
                    continue
                row = session.execute(
                    select(StrategyLabCbDailyFactor).where(
                        and_(
                            StrategyLabCbDailyFactor.bond_code == str(item["bond_code"]),
                            StrategyLabCbDailyFactor.trade_date == item["trade_date"],
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    continue
                for name, value in fields.items():
                    setattr(row, name, value)
                row.source = source
                row.updated_at = now
                count += 1
            session.commit()
        return count

    @staticmethod
    def _normalize_patch_float(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        if isinstance(value, float) and value != value:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

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
            "run_kind": getattr(row, "run_kind", "after_close"),
            "trade_date": row.trade_date.isoformat() if getattr(row, "trade_date", None) else None,
            "quality_status": getattr(row, "quality_status", "unknown"),
            "notification_status": getattr(row, "notification_status", "pending"),
            "market": row.market,
            "status": row.status,
            "cancel_requested": bool(row.cancel_requested),
            "result": json.loads(row.result_json) if row.result_json else {},
            "error_message": row.error_message,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    def latest_sync_run(self, *, run_kind: str, trade_date: date) -> Optional[StrategyLabSyncRun]:
        with self.db.get_session() as session:
            return session.execute(select(StrategyLabSyncRun).where(
                StrategyLabSyncRun.run_kind == run_kind,
                StrategyLabSyncRun.trade_date == trade_date,
            ).order_by(desc(StrategyLabSyncRun.id))).scalars().first()
