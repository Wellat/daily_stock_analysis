# -*- coding: utf-8 -*-
"""Strategy Lab data sync service."""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

from src.core.strategy_lab.fixture_engine import build_default_fixture_dataset
from src.repositories.strategy_lab.data_repo import StrategyLabDataRepository
from src.services.strategy_lab.cb_providers import (
    AkshareConvertibleBondProvider,
    ConvertibleBondDataProvider,
    ConvertibleBondOhlcFetcher,
    OpencliConvertibleBondProvider,
    get_convertible_bond_provider,
)
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

# 模块级锁：同一时间只允许一个外部数据源同步任务在后台运行，避免并发写库
_PROVIDER_SYNC_LOCK = threading.Lock()


class StrategyLabDataSyncService:
    """Sync convertible-bond master/factor data into Strategy Lab tables."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.repository = StrategyLabDataRepository(db_manager)

    def sync_fixture_convertible_bonds(self, *, market: str = "cn") -> Dict[str, Any]:
        dataset = build_default_fixture_dataset()
        payload = {
            "market": market,
            "source": "fixture",
            "instrument_count": len(dataset.instruments),
            "factor_count": sum(len(rows) for rows in dataset.bars.values()),
        }
        run = self.repository.create_sync_run(
            run_uid=uuid4().hex,
            sync_type="fixture_convertible_bond",
            market=market,
            payload=payload,
        )
        try:
            basics = [
                {
                    "bond_code": item.canonical_id.split(".")[-1],
                    "bond_name": item.name or item.symbol,
                    "stock_code": item.symbol,
                    "stock_name": f"{item.symbol} 正股",
                    "market": item.market,
                    "list_date": date(2024, 1, 1),
                    "maturity_date": date(2028, 1, 1),
                    "remaining_size": 50.0,
                    "current_premium_rate": 18.0,
                    "convert_price": 100.0,
                    "terms": {"source": "fixture", "strategy": "double-low"},
                }
                for item in dataset.instruments
            ]
            terms = [
                {
                    "bond_code": item.canonical_id.split(".")[-1],
                    "redeem_clause": "fixture redeem clause",
                    "down_revise_clause": "fixture down revise clause",
                    "put_clause": "fixture put clause",
                    "redeem_trigger_price": 130.0,
                    "down_revise_trigger_price": 80.0,
                    "put_trigger_price": 70.0,
                }
                for item in dataset.instruments
            ]
            factors: List[Dict[str, Any]] = []
            for item in dataset.instruments:
                bond_code = item.canonical_id.split(".")[-1]
                for bar in dataset.bars.get(item.canonical_id, []):
                    factors.append(
                        {
                            "bond_code": bond_code,
                            "trade_date": bar.trade_date,
                            "close": bar.close,
                            "premium_rate": bar.cb_premium_rate,
                            "remaining_size": 50.0,
                            "redeem_alert": bar.cb_premium_rate is not None and bar.cb_premium_rate < 10,
                            "down_revise_alert": False,
                            "put_alert": False,
                        }
                    )
            events = [
                {
                    "bond_code": item.canonical_id.split(".")[-1],
                    "event_date": date(2024, 1, 2),
                    "event_type": "strong_redeem",
                    "event_detail": "fixture strong redeem watch",
                }
                for item in dataset.instruments[:1]
            ]
            result = {
                "cb_basic_upserted": self.repository.upsert_cb_basic(basics, source="fixture"),
                "cb_terms_upserted": self.repository.upsert_cb_terms(terms, source="fixture"),
                "cb_factor_upserted": self.repository.upsert_cb_daily_factors(factors, source="fixture"),
                "cb_event_upserted": self.repository.upsert_cb_events(events, source="fixture"),
            }
            self.repository.complete_sync_run(run.id, result=result)
            return {"sync_run_id": run.id, **result}
        except Exception as exc:
            self.repository.fail_sync_run(run.id, str(exc))
            raise

    def list_sync_runs(self, *, page: int, limit: int) -> Dict[str, Any]:
        offset = (page - 1) * limit
        payload = self.repository.list_sync_runs(limit=limit, offset=offset)
        return {"page": page, "limit": limit, **payload}

    def list_instruments(
        self,
        *,
        market: str,
        keyword: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        status: Optional[str] = None,
        held_only: bool = False,
    ) -> Dict[str, Any]:
        offset = (page - 1) * limit
        payload = self.repository.list_cb_instruments(
            market=market,
            keyword=keyword,
            limit=limit,
            offset=offset,
            status=status,
            held_only=held_only,
        )
        return {"market": market, "page": page, "limit": limit, **payload}

    def get_instrument_detail(self, *, market: str, bond_code: str) -> Optional[Dict[str, Any]]:
        """Return instrument detail or None when the instrument does not exist."""
        return self.repository.get_cb_instrument_detail(bond_code=bond_code, market=market)

    def list_instrument_bars(
        self,
        *,
        market: str,
        bond_code: str,
        start_date: Any = None,
        end_date: Any = None,
        limit: int = 200,
    ) -> Optional[Dict[str, Any]]:
        """Return daily factors for one instrument, or None when it does not exist."""
        if self.repository.get_cb_instrument_detail(bond_code=bond_code, market=market) is None:
            return None
        return self.repository.list_cb_daily_factors(
            bond_code=bond_code,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )

    def list_instrument_events(
        self,
        *,
        market: str,
        bond_code: str,
        event_type: Optional[str] = None,
        limit: int = 50,
    ) -> Optional[Dict[str, Any]]:
        """Return events for one instrument, or None when it does not exist."""
        if self.repository.get_cb_instrument_detail(bond_code=bond_code, market=market) is None:
            return None
        return self.repository.list_cb_events(
            bond_code=bond_code,
            event_type=event_type,
            limit=limit,
        )

    def sync_payload_convertible_bonds(
        self,
        *,
        market: str,
        source: str,
        cb_basic: List[Dict[str, Any]],
        cb_terms: List[Dict[str, Any]],
        cb_daily_factors: List[Dict[str, Any]],
        cb_events: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        payload = {
            "market": market,
            "source": source,
            "cb_basic": len(cb_basic),
            "cb_terms": len(cb_terms),
            "cb_daily_factors": len(cb_daily_factors),
            "cb_events": len(cb_events),
        }
        run = self.repository.create_sync_run(
            run_uid=uuid4().hex,
            sync_type="payload_convertible_bond",
            market=market,
            payload=payload,
        )
        try:
            result = {
                "cb_basic_upserted": self.repository.upsert_cb_basic(
                    [self._normalize_basic_row(row, market=market) for row in cb_basic],
                    source=source,
                ),
                "cb_terms_upserted": self.repository.upsert_cb_terms(
                    [self._normalize_terms_row(row) for row in cb_terms],
                    source=source,
                ),
                "cb_factor_upserted": self.repository.upsert_cb_daily_factors(
                    [self._normalize_factor_row(row) for row in cb_daily_factors],
                    source=source,
                ),
                "cb_event_upserted": self.repository.upsert_cb_events(
                    [self._normalize_event_row(row) for row in cb_events],
                    source=source,
                ),
            }
            self.repository.complete_sync_run(run.id, result=result)
            return {"sync_run_id": run.id, **result}
        except Exception as exc:
            self.repository.fail_sync_run(run.id, str(exc))
            raise

    def sync_provider_convertible_bonds(
        self,
        *,
        market: str,
        source: str,
        symbols: Optional[List[str]] = None,
        provider: Optional[ConvertibleBondDataProvider] = None,
    ) -> Dict[str, Any]:
        provider = provider or get_convertible_bond_provider(source)
        payload = {
            "market": market,
            "source": provider.name,
            "symbols": symbols or [],
        }
        run = self.repository.create_sync_run(
            run_uid=uuid4().hex,
            sync_type=f"{provider.name}_convertible_bond",
            market=market,
            payload=payload,
        )
        try:
            dataset = provider.fetch(market=market, symbols=symbols)
            result = {
                "cb_basic_upserted": self.repository.upsert_cb_basic(
                    [self._normalize_basic_row(row, market=market) for row in dataset.cb_basic],
                    source=provider.name,
                ),
                "cb_terms_upserted": self.repository.upsert_cb_terms(
                    [self._normalize_terms_row(row) for row in dataset.cb_terms],
                    source=provider.name,
                ),
                "cb_factor_upserted": self.repository.upsert_cb_daily_factors(
                    [self._normalize_factor_row(row) for row in dataset.cb_daily_factors],
                    source=provider.name,
                ),
                "cb_event_upserted": self.repository.upsert_cb_events(
                    [self._normalize_event_row(row) for row in dataset.cb_events],
                    source=provider.name,
                ),
            }
            self.repository.complete_sync_run(run.id, result=result)
            return {"sync_run_id": run.id, **result}
        except Exception as exc:
            self.repository.fail_sync_run(run.id, str(exc))
            raise

    def start_provider_sync(
        self,
        *,
        market: str,
        source: str,
        symbols: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Start a provider sync in a background thread and return immediately.

        Upstream providers can be slow (e.g. AkShare pulls per-symbol daily bars),
        so the HTTP request must not block on them. The caller polls
        ``list_sync_runs`` for the final ``completed``/``failed`` status.
        """
        if not _PROVIDER_SYNC_LOCK.acquire(blocking=False):
            raise ValueError("已有数据源同步任务进行中，请等待完成后再试")
        provider = get_convertible_bond_provider(source)
        run = self.repository.create_sync_run(
            run_uid=uuid4().hex,
            sync_type=f"{provider.name}_convertible_bond",
            market=market,
            payload={"market": market, "source": provider.name, "symbols": symbols or []},
        )

        def _worker() -> None:
            try:
                result: Dict[str, Any] = {}
                if isinstance(provider, AkshareConvertibleBondProvider):
                    # AkShare 全量同步按标的逐只拉取并分批入库：先落列表/条款/事件，
                    # 再逐只拉日线边拉边写，定期把进度快照写入 result_json，
                    # 避免"全量拉完再一次性写入"在长时间任务中失败即全丢的问题。
                    basics, terms = provider.fetch_list(market=market, symbols=symbols)
                    result["cb_basic_upserted"] = self.repository.upsert_cb_basic(
                        [self._normalize_basic_row(row, market=market) for row in basics],
                        source=provider.name,
                    )
                    result["cb_terms_upserted"] = self.repository.upsert_cb_terms(
                        [self._normalize_terms_row(row) for row in terms],
                        source=provider.name,
                    )
                    events = provider.fetch_events(symbols=symbols)
                    result["cb_event_upserted"] = self.repository.upsert_cb_events(
                        [self._normalize_event_row(row) for row in events],
                        source=provider.name,
                    )
                    codes = [basic["bond_code"] for basic in basics]
                    factor_total = len(codes)
                    factor_upserted = 0
                    for idx, code in enumerate(codes, 1):
                        rows = provider.fetch_factors(code)
                        if rows:
                            factor_upserted += self.repository.upsert_cb_daily_factors(
                                [self._normalize_factor_row(row) for row in rows],
                                source=provider.name,
                            )
                        if idx % 10 == 0 or idx == factor_total:
                            self.repository.update_sync_run_progress(run.id, result={
                                "stage": "fetching",
                                "processed": idx,
                                "total": factor_total,
                                "cb_basic_upserted": result["cb_basic_upserted"],
                                "cb_terms_upserted": result["cb_terms_upserted"],
                                "cb_event_upserted": result["cb_event_upserted"],
                                "cb_factor_upserted": factor_upserted,
                            })
                    result["cb_factor_upserted"] = factor_upserted
                else:
                    dataset = provider.fetch(market=market, symbols=symbols)
                    result = {
                        "cb_basic_upserted": self.repository.upsert_cb_basic(
                            [self._normalize_basic_row(row, market=market) for row in dataset.cb_basic],
                            source=provider.name,
                        ),
                        "cb_terms_upserted": self.repository.upsert_cb_terms(
                            [self._normalize_terms_row(row) for row in dataset.cb_terms],
                            source=provider.name,
                        ),
                        "cb_factor_upserted": self.repository.upsert_cb_daily_factors(
                            [self._normalize_factor_row(row) for row in dataset.cb_daily_factors],
                            source=provider.name,
                        ),
                        "cb_event_upserted": self.repository.upsert_cb_events(
                            [self._normalize_event_row(row) for row in dataset.cb_events],
                            source=provider.name,
                        ),
                    }
                self.repository.complete_sync_run(run.id, result=result)
            except Exception as exc:
                logger.error("Background provider sync run=%s failed: %s", run.id, exc, exc_info=True)
                self.repository.fail_sync_run(run.id, str(exc))
            finally:
                _PROVIDER_SYNC_LOCK.release()

        threading.Thread(
            target=_worker,
            name=f"strategy-lab-sync-{run.id}",
            daemon=True,
        ).start()
        return {"sync_run_id": run.id, "status": "running"}

    # ------------------------------------------------------------------
    # 可转债基础数据同步（opencli cb-list + cb-detail）
    # ------------------------------------------------------------------

    def sync_cb_basic(
        self,
        *,
        market: str = "cn",
        include_delisted: bool = False,
        symbols: Optional[List[str]] = None,
        workers: int = 3,
    ) -> Dict[str, Any]:
        """Sync convertible-bond master data via local opencli.

        流程分两种：
        - 传入 ``symbols`` 时直接逐只拉 ``cb-detail``，跳过 ``cb-list``，适合调试单只或少量标的；
        - 不传 ``symbols`` 时先拉 ``cb-list``（默认活跃，``include_delisted`` 时拉已退市列表）再逐只补详情。
        单只详情失败不中断整体，失败代码记入 result 的 ``bonds_failed``。
        """
        provider = OpencliConvertibleBondProvider(workers=workers)
        symbol_codes = list(
            dict.fromkeys(
                str(symbol).strip().lower().split(".")[-1]
                for symbol in symbols or []
                if str(symbol).strip()
            )
        )
        payload = {
            "market": market,
            "source": provider.name,
            "include_delisted": include_delisted,
            "symbols": symbols or [],
            "direct_symbols": bool(symbol_codes),
        }
        run = self.repository.create_sync_run(
            run_uid=uuid4().hex,
            sync_type=f"{provider.name}_cb_basic",
            market=market,
            payload=payload,
        )
        try:
            basics: List[Dict[str, Any]] = []
            if symbol_codes:
                # 调试场景直接拉指定标的详情，避免先抓活跃/退市两套全量列表。
                codes = symbol_codes
                basics = [
                    {
                        "bond_code": code,
                        "bond_name": code,
                        "stock_code": "",
                        "stock_name": None,
                        "market": market,
                        "status": "正常",
                        "terms": {"provider": provider.name},
                    }
                    for code in codes
                ]
            else:
                codes = []
                for row in provider.fetch_list(include_delisted=include_delisted):
                    basic = provider.normalize_list_row(row)
                    if not basic:
                        continue
                    basics.append(basic)
                    codes.append(basic["bond_code"])
            result = {
                "bonds_total": len(codes),
                "cb_basic_upserted": 0,
                "cb_terms_upserted": 0,
                "cb_event_upserted": 0,
                "bonds_failed": [],
            }
            if not codes:
                self.repository.complete_sync_run(run.id, result=result)
                return {"sync_run_id": run.id, **result}

            _CHUNK = 20
            terms_all: List[Dict[str, Any]] = []
            events_all: List[Dict[str, Any]] = []
            failed: List[str] = []
            for chunk_start in range(0, len(codes), _CHUNK):
                chunk_codes = codes[chunk_start : chunk_start + _CHUNK]
                chunk_basics = basics[chunk_start : chunk_start + _CHUNK]
                successful_basics: List[Dict[str, Any]] = []
                detail_map = provider.fetch_detail_batch(chunk_codes, workers=workers)
                for basic in chunk_basics:
                    code = basic["bond_code"]
                    detail = detail_map.get(code)
                    if not detail:
                        failed.append(code)
                        logger.warning("[失败] 可转债 %s 详情拉取失败，跳过", code)
                        continue
                    normalized = provider.normalize_detail(detail)
                    if not normalized:
                        failed.append(code)
                        logger.warning("[失败] 可转债 %s 详情解析失败，跳过", code)
                        continue
                    basic.update({key: value for key, value in normalized["basic"].items() if value is not None})
                    basic["bond_name"] = basic.get("bond_name") or code
                    basic["terms"] = {**(basic.get("terms") or {}), **normalized["meta"]}
                    if normalized["status"]:
                        basic["status"] = normalized["status"]
                    successful_basics.append(basic)
                    terms_all.append(normalized["terms"])
                    events_all.extend(normalized["events"])
                    logger.info(
                        "[完成] 可转债 %s 基础数据：status=%s 条款=%d 事件=%d",
                        code,
                        basic["status"],
                        len(normalized["terms"]) - 1,
                        len(normalized["events"]),
                    )
                if terms_all:
                    result["cb_basic_upserted"] += self.repository.upsert_cb_basic(
                        (successful_basics if symbol_codes else chunk_basics), source=provider.name
                    )
                    result["cb_terms_upserted"] += self.repository.upsert_cb_terms(
                        terms_all, source=provider.name
                    )
                    result["cb_event_upserted"] += self.repository.upsert_cb_events(
                        events_all, source=provider.name
                    )
                    terms_all = []
                    events_all = []
                self.repository.update_sync_run_progress(run.id, result={
                    "stage": "fetching_detail",
                    "processed": min(chunk_start + _CHUNK, len(codes)),
                    "total": len(codes),
                    "cb_basic_upserted": result["cb_basic_upserted"],
                    "cb_terms_upserted": result["cb_terms_upserted"],
                    "cb_event_upserted": result["cb_event_upserted"],
                })
            result["bonds_failed"] = failed
            self.repository.complete_sync_run(run.id, result=result)
            return {"sync_run_id": run.id, **result}
        except Exception as exc:
            self.repository.fail_sync_run(run.id, str(exc))
            raise

    # ------------------------------------------------------------------
    # 可转债 OHLC 行情同步（东财优先，腾讯兜底；支持增量起始日期）
    # ------------------------------------------------------------------

    def sync_cb_ohlc(
        self,
        *,
        market: str = "cn",
        include_delisted: bool = False,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        symbols: Optional[List[str]] = None,
        workers: int = 4,
    ) -> Dict[str, Any]:
        """Sync convertible-bond daily OHLC into ``stock_daily``.

        - 遍历标的自 ``strategy_lab_cb_basic``（``include_delisted`` 控制状态）。
        - ``start_date`` 缺省时增量：有本地历史则从最后日期次日开始，否则从
          ``max(list_date, 2020-01-01)`` 开始；``end_date`` 缺省为今天。
        - OHLC 落 ``stock_daily``（instrument_type='convertible_bond'），close
          同步回填 ``strategy_lab_cb_daily_factors`` 供回测引擎读取。
        """
        fetcher = ConvertibleBondOhlcFetcher()
        status = "已退市" if include_delisted else "正常"
        codes = self.repository.list_cb_basic_codes(market=market, status=status)
        symbol_filter = {str(symbol).strip().lower().split(".")[-1] for symbol in symbols or [] if str(symbol).strip()}
        if symbol_filter:
            codes = [code for code in codes if code.lower() in symbol_filter]
        effective_end = end_date or date.today()
        payload = {
            "market": market,
            "status": status,
            "include_delisted": include_delisted,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": effective_end.isoformat(),
            "symbols": symbols or [],
            "bonds_total": len(codes),
        }
        run = self.repository.create_sync_run(
            run_uid=uuid4().hex,
            sync_type="cb_ohlc",
            market=market,
            payload=payload,
        )
        try:
            result = {
                "bonds_total": len(codes),
                "stock_daily_rows_new": 0,
                "cb_factor_upserted": 0,
                "bonds_skipped": 0,
                "bonds_failed": [],
            }
            if not codes:
                self.repository.complete_sync_run(run.id, result=result)
                return {"sync_run_id": run.id, **result}
            for idx, code in enumerate(codes, 1):
                effective_start = self._ohlc_start_date(code, start_date)
                try:
                    frame = fetcher.fetch_daily(code, effective_start, effective_end)
                    if frame.empty:
                        result["bonds_skipped"] += 1
                        logger.info(
                            "[跳过] 可转债 %s 无行情数据（%s~%s）", code, effective_start, effective_end
                        )
                    else:
                        result["stock_daily_rows_new"] += self.repository.db.save_daily_data(
                            frame,
                            code,
                            data_source=fetcher.last_source or "cb_ohlc",
                            instrument_type="convertible_bond",
                        )
                        factor_rows = [
                            {"bond_code": code, "trade_date": row["date"], "close": row["close"]}
                            for row in frame.to_dict(orient="records")
                        ]
                        if factor_rows:
                            result["cb_factor_upserted"] += self.repository.upsert_cb_daily_factors(
                                factor_rows, source="cb_ohlc"
                            )
                        logger.info(
                            "[完成] 可转债 %s 行情同步：%d 条（%s~%s，source=%s）",
                            code,
                            len(frame),
                            effective_start,
                            effective_end,
                            fetcher.last_source,
                        )
                except Exception as exc:  # noqa: BLE001 - single symbol failure never aborts the batch
                    result["bonds_failed"].append({"bond_code": code, "error": str(exc)})
                    logger.warning("[失败] 可转债 %s 行情同步失败: %s", code, exc)
                if idx % 20 == 0 or idx == len(codes):
                    self.repository.update_sync_run_progress(run.id, result={
                        "stage": "fetching_ohlc",
                        "processed": idx,
                        "total": len(codes),
                        "stock_daily_rows_new": result["stock_daily_rows_new"],
                        "cb_factor_upserted": result["cb_factor_upserted"],
                        "bonds_skipped": result["bonds_skipped"],
                    })
            self.repository.complete_sync_run(run.id, result=result)
            return {"sync_run_id": run.id, **result}
        except Exception as exc:
            self.repository.fail_sync_run(run.id, str(exc))
            raise

    def start_data_sync(
        self,
        *,
        market: str = "cn",
        sync_type: str = "cb_basic",
        include_delisted: bool = False,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        symbols: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Start a convertible-bond data sync in a background thread.

        ``sync_type``: ``cb_basic``（基础数据）/ ``cb_ohlc``（行情）/
        ``all``（先基础后行情）。后台任务复用 ``_PROVIDER_SYNC_LOCK`` 互斥，
        进度通过 ``list_sync_runs`` 轮询。
        """
        if sync_type not in ("cb_basic", "cb_ohlc", "all"):
            raise ValueError(f"Unsupported sync_type: {sync_type}")
        if not _PROVIDER_SYNC_LOCK.acquire(blocking=False):
            raise ValueError("已有数据源同步任务进行中，请等待完成后再试")

        def _worker() -> None:
            try:
                if sync_type in ("cb_basic", "all"):
                    self.sync_cb_basic(
                        market=market,
                        include_delisted=include_delisted,
                        symbols=symbols,
                    )
                if sync_type in ("cb_ohlc", "all"):
                    self.sync_cb_ohlc(
                        market=market,
                        include_delisted=include_delisted,
                        start_date=start_date,
                        end_date=end_date,
                        symbols=symbols,
                    )
            except Exception as exc:
                logger.error("Background data sync failed: %s", exc, exc_info=True)
            finally:
                _PROVIDER_SYNC_LOCK.release()

        threading.Thread(
            target=_worker,
            name=f"strategy-lab-data-sync-{sync_type}",
            daemon=True,
        ).start()
        return {"status": "running", "sync_type": sync_type}

    def _ohlc_start_date(self, bond_code: str, explicit_start: Optional[date]) -> date:
        """Resolve the OHLC start date: explicit, else incremental from local history."""
        if explicit_start is not None:
            return explicit_start
        latest = self.repository.get_cb_ohlc_latest_date(bond_code=bond_code)
        if latest is not None:
            return latest + timedelta(days=1)
        return date(2020, 1, 1)

    @staticmethod
    def _normalize_date(value: Any) -> date | None:
        if value in (None, ""):
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, float) and value != value:  # NaN
            return None
        text = str(value).strip()
        for candidate in (text, text[:10], text.replace("/", "-")[:10], text.replace(".", "-")[:10]):
            try:
                return date.fromisoformat(candidate)
            except ValueError:
                continue
        raise ValueError(f"Invalid date value: {value}")

    @classmethod
    def _normalize_basic_row(cls, row: Dict[str, Any], *, market: str) -> Dict[str, Any]:
        return {
            **row,
            "bond_code": str(row["bond_code"]),
            "market": row.get("market") or market,
            "list_date": cls._normalize_date(row.get("list_date")),
            "maturity_date": cls._normalize_date(row.get("maturity_date")),
        }

    @staticmethod
    def _normalize_terms_row(row: Dict[str, Any]) -> Dict[str, Any]:
        return {**row, "bond_code": str(row["bond_code"])}

    @classmethod
    def _normalize_factor_row(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            **row,
            "bond_code": str(row["bond_code"]),
            "trade_date": cls._normalize_date(row["trade_date"]),
        }

    @classmethod
    def _normalize_event_row(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            **row,
            "bond_code": str(row["bond_code"]),
            "event_date": cls._normalize_date(row["event_date"]),
        }
