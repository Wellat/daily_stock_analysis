# -*- coding: utf-8 -*-
"""Strategy Lab data sync service."""

from __future__ import annotations

import logging
import json
import threading
from datetime import date, timedelta
import time
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

from src.repositories.strategy_lab.data_repo import StrategyLabDataRepository
from src.services.strategy_lab.cb_providers import (
    CbUnderlyingStockOhlcFetcher,
    ConvertibleBondOhlcFetcher,
    OpencliConvertibleBondProvider,
)
from src.storage import DatabaseManager
from src.config import get_config

logger = logging.getLogger(__name__)

# 模块级锁：同一时间只允许一个外部数据源同步任务在后台运行，避免并发写库
_PROVIDER_SYNC_LOCK = threading.Lock()


class _DataSyncCancelled(Exception):
    """Internal signal used for cooperative Strategy Lab data-sync cancellation."""


class StrategyLabDataSyncService:
    """Sync convertible-bond master/factor data into Strategy Lab tables."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.repository = StrategyLabDataRepository(db_manager)
        self.task_start_time = time.time()

    def run_scheduled_sync_after_close(
        self,
        *,
        run_kind: str,
        market: str = "cn",
        trade_date: Optional[date] = None,
        symbols: Optional[List[str]] = None,
        _run_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run the configured provider synchronously for scheduler use.

        ``symbols`` 缺省时全量同步；传入时仅同步指定标的（透传给
        ``sync_cb_basic`` / ``sync_cb_ohlc`` / ``sync_cb_factors``）。
        传入 ``_run_id`` 时复用外层 sync run（各子任务不再各自建 run，
        取消与进度挂在外层 run 上，成功后由本方法统一 complete），
        供 ``start_data_sync`` 后台线程复用。
        """
        if run_kind not in {"intraday", "after_close"}:
            raise ValueError("run_kind must be intraday or after_close")
        try:
            result: Dict[str, Any] = {}
            stages = (
                ("cb_basic", self.sync_cb_basic),
                ("cb_ohlc", self.sync_cb_ohlc),
                ("cb_factors", self.sync_cb_factors),
            )
            for key, sync_method in stages:
                stage_result = sync_method(
                    market=market,
                    symbols=symbols,
                    _run_id=_run_id,
                    _complete_on_success=_run_id is None,
                )
                if stage_result.get("status") == "cancelled":
                    # 子任务已把共享 run 标记为 cancelled，直接返回不再继续
                    result[key] = stage_result
                    result["cancelled"] = True
                    return result
                result[key] = stage_result
                if _run_id is not None and self.repository.is_sync_run_cancel_requested(_run_id):
                    result["cancelled"] = True
                    self.repository.cancel_sync_run(_run_id, result=result)
                    return result
            result.update({"run_kind": run_kind, "trade_date": (trade_date or date.today()).isoformat(), "quality_status": "usable"})
            self._notify_sync(run_kind, result, success=True)
            if _run_id is not None:
                self.repository.complete_sync_run(_run_id, result=result)
            return result
        except Exception as exc:
            self._notify_sync(run_kind, {"run_kind": run_kind, "error": str(exc)}, success=False)
            raise

    def _notify_sync(self, run_kind: str, result: Dict[str, Any], *, success: bool) -> None:
        try:
            config = get_config()
            if not getattr(config, "cb_sync_notify_email_enabled", True): return
            from src.notification_sender import EmailSender
            title = f"可转债{'盘中' if run_kind == 'intraday' else '盘后'}同步{'成功' if success else '失败'}"
            result.update({"duration_seconds": time.time() - self.task_start_time})
            EmailSender(config).send_to_email(json.dumps(result, ensure_ascii=False, indent=2), subject=title, timeout_seconds=20)
        except Exception:
            logger.exception("CB sync notification failed")

    def list_sync_runs(self, *, page: int, limit: int) -> Dict[str, Any]:
        offset = (page - 1) * limit
        payload = self.repository.list_sync_runs(limit=limit, offset=offset)
        return {"page": page, "limit": limit, **payload}

    def request_data_sync_cancel(self, run_id: int) -> Optional[Dict[str, Any]]:
        """Request cooperative cancellation for a data-sync run."""
        return self.repository.request_sync_run_cancel(run_id)

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

    # ------------------------------------------------------------------
    # 可转债基础数据同步（opencli cb-list + cb-detail）
    # ------------------------------------------------------------------

    def sync_cb_basic(
        self,
        *,
        market: str = "cn",
        include_delisted: bool = False,
        symbols: Optional[List[str]] = None,
        workers: int = 1,
        _run_id: Optional[int] = None,
        _complete_on_success: bool = True,
    ) -> Dict[str, Any]:
        """Sync convertible-bond master data via local opencli.

        流程分两种：
        - 传入 ``symbols`` 时直接逐只拉 ``cb-detail``，跳过 ``cb-list``，适合调试单只或少量标的；
        - 不传 ``symbols`` 时先拉 ``cb-list``（默认活跃；``include_delisted`` 时合并活跃与已退市列表）再逐只补详情。
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
        if _run_id is None:
            run = self.repository.create_sync_run(
                run_uid=uuid4().hex,
                sync_type=f"{provider.name}_cb_basic",
                market=market,
                payload=payload,
            )
            run_id = run.id
        else:
            run_id = _run_id
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
                seen_codes: Set[str] = set()
                # include_delisted=True 时合并活跃与已退市两份列表（去重），否则仅活跃。
                list_flags = [False, True] if include_delisted else [False]
                for flag in list_flags:
                    for row in provider.fetch_list(include_delisted=flag):
                        basic = provider.normalize_list_row(row)
                        if not basic:
                            continue
                        code = basic["bond_code"]
                        if code in seen_codes:
                            continue
                        seen_codes.add(code)
                        basics.append(basic)
                        codes.append(code)
            result = {
                "bonds_total": len(codes),
                "cb_basic_upserted": 0,
                "cb_terms_upserted": 0,
                "cb_event_upserted": 0,
                "bonds_failed": [],
            }
            if not codes:
                self._raise_if_cancel_requested(run_id)
                if _complete_on_success:
                    self.repository.complete_sync_run(run_id, result=result)
                return {"sync_run_id": run_id, **result}

            _CHUNK = 20
            failed: List[str] = []
            for chunk_start in range(0, len(codes), _CHUNK):
                self._raise_if_cancel_requested(run_id)
                chunk_codes = codes[chunk_start : chunk_start + _CHUNK]
                chunk_basics = basics[chunk_start : chunk_start + _CHUNK]
                detail_map = provider.fetch_detail_batch(chunk_codes, workers=workers)
                self._raise_if_cancel_requested(run_id)
                for basic in chunk_basics:
                    code = basic["bond_code"]
                    detail = detail_map.get(code)
                    if not detail:
                        failed.append(code)
                        logger.warning("[失败] 可转债 %s 详情拉取失败，跳过，返回信息：%s", code, basic)
                        # 非调试场景下，即使详情缺失也落库列表基础信息，单只失败仅告警
                        if not symbol_codes:
                            try:
                                result["cb_basic_upserted"] += self.repository.upsert_cb_basic(
                                    [basic], source=provider.name
                                )
                            except Exception as exc:  # noqa: BLE001 - 单只入库失败不中断整体
                                logger.warning("[失败] 可转债 %s 基础信息入库失败: %s", code, exc)
                        continue
                    normalized = provider.normalize_detail(detail)
                    if not normalized:
                        failed.append(code)
                        logger.warning("[失败] 可转债 %s 详情解析失败，跳过，返回信息：%s", code, basic)
                        # 非调试场景下，即使详情缺失也落库列表基础信息，单只失败仅告警
                        if not symbol_codes:
                            try:
                                result["cb_basic_upserted"] += self.repository.upsert_cb_basic(
                                    [basic], source=provider.name
                                )
                            except Exception as exc:  # noqa: BLE001 - 单只入库失败不中断整体
                                logger.warning("[失败] 可转债 %s 基础信息入库失败: %s", code, exc)
                        continue
                    basic.update({key: value for key, value in normalized["basic"].items() if value is not None})
                    basic["bond_name"] = basic.get("bond_name") or code
                    basic["terms"] = {**(basic.get("terms") or {}), **normalized["meta"]}
                    if normalized["status"]:
                        basic["status"] = normalized["status"]
                    # 逐只入库，单只失败仅记录日志并跳过，不拖垮整个同步任务
                    try:
                        result["cb_basic_upserted"] += self.repository.upsert_cb_basic(
                            [basic], source=provider.name
                        )
                        result["cb_terms_upserted"] += self.repository.upsert_cb_terms(
                            [normalized["terms"]], source=provider.name
                        )
                        result["cb_event_upserted"] += self.repository.upsert_cb_events(
                            normalized["events"], source=provider.name
                        )
                        logger.info(
                            "[完成] 可转债 %s 基础数据：status=%s 条款=%d 事件=%d",
                            code,
                            basic["status"],
                            len(normalized["terms"]) - 1,
                            len(normalized["events"]),
                        )
                    except Exception as exc:  # noqa: BLE001 - 单只入库失败不中断整体
                        failed.append(code)
                        logger.warning("[失败] 可转债 %s 入库失败，跳过: %s", code, exc)
                self.repository.update_sync_run_progress(run_id, result={
                    "stage": "fetching_detail",
                    "processed": min(chunk_start + _CHUNK, len(codes)),
                    "total": len(codes),
                    "cb_basic_upserted": result["cb_basic_upserted"],
                    "cb_terms_upserted": result["cb_terms_upserted"],
                    "cb_event_upserted": result["cb_event_upserted"],
                })
            result["bonds_failed"] = failed
            if _complete_on_success:
                self.repository.complete_sync_run(run_id, result=result)
            return {"sync_run_id": run_id, **result}
        except _DataSyncCancelled:
            result = locals().get("result", {})
            result["cancelled"] = True
            self.repository.cancel_sync_run(run_id, result=result)
            return {"sync_run_id": run_id, "status": "cancelled", **result}
        except Exception as exc:
            self.repository.fail_sync_run(run_id, str(exc))
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
        _run_id: Optional[int] = None,
        _complete_on_success: bool = True,
    ) -> Dict[str, Any]:
        """Sync convertible-bond daily OHLC into ``stock_daily``.

        - 遍历标的自 ``strategy_lab_cb_basic``（``include_delisted`` 控制状态）。
        - ``start_date`` 缺省时增量：有本地历史则从最后日期次日开始，否则从
          ``max(list_date, 2020-01-01)`` 开始；``end_date`` 缺省为今天。
        - OHLC 落 ``stock_daily``（instrument_type='convertible_bond'），close
          同步回填 ``strategy_lab_cb_daily_factors`` 供回测引擎读取。
        """
        fetcher = ConvertibleBondOhlcFetcher()
        # include_delisted=True 时处理全部（活跃 + 已退市），否则仅活跃。
        codes = self.repository.list_cb_basic_codes(
            market=market, status=None if include_delisted else "正常"
        )
        status = "全部" if include_delisted else "正常"
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
        if _run_id is None:
            run = self.repository.create_sync_run(
                run_uid=uuid4().hex,
                sync_type="cb_ohlc",
                market=market,
                payload=payload,
            )
            run_id = run.id
        else:
            run_id = _run_id
        try:
            result = {
                "bonds_total": len(codes),
                "stock_daily_rows_new": 0,
                "cb_factor_upserted": 0,
                "bonds_skipped": 0,
                "bonds_failed": [],
            }
            if not codes:
                self._raise_if_cancel_requested(run_id)
                if _complete_on_success:
                    self.repository.complete_sync_run(run_id, result=result)
                return {"sync_run_id": run_id, **result}
            for idx, code in enumerate(codes, 1):
                self._raise_if_cancel_requested(run_id)
                effective_start = self._ohlc_start_date(code, start_date)
                try:
                    frame = fetcher.fetch_daily(code, effective_start, effective_end)
                    self._raise_if_cancel_requested(run_id)
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
                except _DataSyncCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - single symbol failure never aborts the batch
                    result["bonds_failed"].append({"bond_code": code, "error": str(exc)})
                    logger.warning("[失败] 可转债 %s 行情同步失败: %s", code, exc)
                if idx % 20 == 0 or idx == len(codes):
                    self.repository.update_sync_run_progress(run_id, result={
                        "stage": "fetching_ohlc",
                        "processed": idx,
                        "total": len(codes),
                        "stock_daily_rows_new": result["stock_daily_rows_new"],
                        "cb_factor_upserted": result["cb_factor_upserted"],
                        "bonds_skipped": result["bonds_skipped"],
                    })
            if _complete_on_success:
                self.repository.complete_sync_run(run_id, result=result)
            return {"sync_run_id": run_id, **result}
        except _DataSyncCancelled:
            result = locals().get("result", {})
            result["cancelled"] = True
            self.repository.cancel_sync_run(run_id, result=result)
            return {"sync_run_id": run_id, "status": "cancelled", **result}
        except Exception as exc:
            self.repository.fail_sync_run(run_id, str(exc))
            raise

    def sync_cb_premium_history(
        self,
        *,
        market: str = "cn",
        include_delisted: bool = False,
        symbols: Optional[List[str]] = None,
        _run_id: Optional[int] = None,
        _complete_on_success: bool = True,
    ) -> Dict[str, Any]:
        """Backfill missing premium/remaining-size fields on convertible-bond daily-factor rows."""
        provider = OpencliConvertibleBondProvider()
        codes = self.repository.list_cb_basic_codes(
            market=market, status=None if include_delisted else "正常"
        )
        symbol_filter = {str(symbol).strip().lower().split(".")[-1] for symbol in symbols or [] if str(symbol).strip()}
        if symbol_filter:
            codes = [code for code in codes if code.lower() in symbol_filter]
        payload = {
            "market": market,
            "source": provider.name,
            "include_delisted": include_delisted,
            "symbols": symbols or [],
            "bonds_total": len(codes),
        }
        if _run_id is None:
            run = self.repository.create_sync_run(
                run_uid=uuid4().hex,
                sync_type="cb_premium_history",
                market=market,
                payload=payload,
            )
            run_id = run.id
        else:
            run_id = _run_id
        try:
            result = {
                "bonds_total": len(codes),
                "rows_examined": 0,
                "rows_matched": 0,
                "cb_factor_rows_patched": 0,
                "premium_rate_patched": 0,
                "remaining_size_patched": 0,
                "bonds_skipped": 0,
                "bonds_failed": [],
            }
            if not codes:
                self._raise_if_cancel_requested(run_id)
                if _complete_on_success:
                    self.repository.complete_sync_run(run_id, result=result)
                return {"sync_run_id": run_id, **result}
            for idx, code in enumerate(codes, 1):
                self._raise_if_cancel_requested(run_id)
                try:
                    rows = provider.fetch_premium_history(code)
                    self._raise_if_cancel_requested(run_id)
                    if not rows:
                        result["bonds_skipped"] += 1
                        logger.info("[跳过] 可转债 %s 溢价历史为空", code)
                    else:
                        patch_result = self.repository.patch_cb_daily_factor_fields(rows, source=provider.name)
                        result["rows_examined"] += patch_result["rows_examined"]
                        result["rows_matched"] += patch_result["rows_matched"]
                        result["cb_factor_rows_patched"] += patch_result["rows_updated"]
                        result["premium_rate_patched"] += patch_result["premium_rate_patched"]
                        result["remaining_size_patched"] += patch_result["remaining_size_patched"]
                        logger.info(
                            "[完成] 可转债 %s 溢价历史补数：匹配=%d 更新=%d 溢价率=%d 剩余规模=%d",
                            code,
                            patch_result["rows_matched"],
                            patch_result["rows_updated"],
                            patch_result["premium_rate_patched"],
                            patch_result["remaining_size_patched"],
                        )
                except _DataSyncCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - single symbol failure never aborts the batch
                    result["bonds_failed"].append({"bond_code": code, "error": str(exc)})
                    logger.warning("[失败] 可转债 %s 溢价历史补数失败: %s", code, exc)
                if idx % 20 == 0 or idx == len(codes):
                    self.repository.update_sync_run_progress(run_id, result={
                        "stage": "fetching_premium_history",
                        "processed": idx,
                        "total": len(codes),
                        "rows_examined": result["rows_examined"],
                        "rows_matched": result["rows_matched"],
                        "cb_factor_rows_patched": result["cb_factor_rows_patched"],
                        "premium_rate_patched": result["premium_rate_patched"],
                        "remaining_size_patched": result["remaining_size_patched"],
                        "bonds_skipped": result["bonds_skipped"],
                    })
            if _complete_on_success:
                self.repository.complete_sync_run(run_id, result=result)
            return {"sync_run_id": run_id, **result}
        except _DataSyncCancelled:
            result = locals().get("result", {})
            result["cancelled"] = True
            self.repository.cancel_sync_run(run_id, result=result)
            return {"sync_run_id": run_id, "status": "cancelled", **result}
        except Exception as exc:
            self.repository.fail_sync_run(run_id, str(exc))
            raise

    # ------------------------------------------------------------------
    # 可转债正股 OHLC 行情同步（独立入口：腾讯日线，仅活跃转债的正股） ———— 未使用
    # ------------------------------------------------------------------
    def sync_cb_stock_ohlc(
        self,
        *,
        market: str = "cn",
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        symbols: Optional[List[str]] = None,
        _run_id: Optional[int] = None,
        _complete_on_success: bool = True,
    ) -> Dict[str, Any]:
        """Sync underlying-stock daily OHLC of active convertible bonds into ``stock_daily``.

        - 遍历标的自 ``strategy_lab_cb_basic.stock_code``（去重；仅 ``status='正常'``
          的转债，已退市转债的正股不同步）。
        - ``start_date`` 缺省时增量：有本地历史则从最后日期次日开始，否则从
          ``2020-01-01`` 开始；``end_date`` 缺省为今天。
        - OHLC 落 ``stock_daily``（instrument_type='stock'），不回填转债因子表。
        - ``symbols`` 语义与其他同步方法一致，为转债代码过滤。
        """
        fetcher = CbUnderlyingStockOhlcFetcher()
        symbol_filter = {str(symbol).strip().lower().split(".")[-1] for symbol in symbols or [] if str(symbol).strip()}
        codes = self.repository.list_cb_stock_codes(
            market=market, bond_codes=list(symbol_filter) if symbol_filter else None
        )
        effective_end = end_date or date.today()
        payload = {
            "market": market,
            "status": "正常",
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": effective_end.isoformat(),
            "symbols": symbols or [],
            "stocks_total": len(codes),
        }
        if _run_id is None:
            run = self.repository.create_sync_run(
                run_uid=uuid4().hex,
                sync_type="cb_stock_ohlc",
                market=market,
                payload=payload,
            )
            run_id = run.id
        else:
            run_id = _run_id
        try:
            result = {
                "stocks_total": len(codes),
                "stock_daily_rows_new": 0,
                "stocks_skipped": 0,
                "stocks_failed": [],
            }
            if not codes:
                self._raise_if_cancel_requested(run_id)
                if _complete_on_success:
                    self.repository.complete_sync_run(run_id, result=result)
                return {"sync_run_id": run_id, **result}
            for idx, code in enumerate(codes, 1):
                self._raise_if_cancel_requested(run_id)
                effective_start = self._ohlc_start_date(code, start_date, instrument_type="stock")
                try:
                    frame = fetcher.fetch_daily(code, effective_start, effective_end)
                    self._raise_if_cancel_requested(run_id)
                    if frame.empty:
                        result["stocks_skipped"] += 1
                        logger.info(
                            "[跳过] 正股 %s 无行情数据（%s~%s）", code, effective_start, effective_end
                        )
                    else:
                        result["stock_daily_rows_new"] += self.repository.db.save_daily_data(
                            frame,
                            code,
                            data_source=fetcher.last_source or "cb_stock_ohlc",
                            instrument_type="stock",
                        )
                        logger.info(
                            "[完成] 正股 %s 行情同步：%d 条（%s~%s，source=%s）",
                            code,
                            len(frame),
                            effective_start,
                            effective_end,
                            fetcher.last_source,
                        )
                except _DataSyncCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - single symbol failure never aborts the batch
                    result["stocks_failed"].append({"stock_code": code, "error": str(exc)})
                    logger.warning("[失败] 正股 %s 行情同步失败: %s", code, exc)
                if idx % 20 == 0 or idx == len(codes):
                    self.repository.update_sync_run_progress(run_id, result={
                        "stage": "fetching_stock_ohlc",
                        "processed": idx,
                        "total": len(codes),
                        "stock_daily_rows_new": result["stock_daily_rows_new"],
                        "stocks_skipped": result["stocks_skipped"],
                    })
            if _complete_on_success:
                self.repository.complete_sync_run(run_id, result=result)
            return {"sync_run_id": run_id, **result}
        except _DataSyncCancelled:
            result = locals().get("result", {})
            result["cancelled"] = True
            self.repository.cancel_sync_run(run_id, result=result)
            return {"sync_run_id": run_id, "status": "cancelled", **result}
        except Exception as exc:
            self.repository.fail_sync_run(run_id, str(exc))
            raise

    # ------------------------------------------------------------------
    # 可转债因子计算（正股价 / 溢价率 / 剩余规模 → strategy_lab_cb_daily_factors）
    # ------------------------------------------------------------------

    def sync_cb_factors(
        self,
        *,
        market: str = "cn",
        trade_date: Optional[date] = None,
        symbols: Optional[List[str]] = None,
        _run_id: Optional[int] = None,
        _complete_on_success: bool = True,
    ) -> Dict[str, Any]:
        """Compute and persist CB daily factors for one trade date.

        - 遍历 ``status='正常'`` 的转债；转债价取当日因子行的 ``close``（由
          ``sync_cb_ohlc`` 写入），正股价用 ``CbUnderlyingStockOhlcFetcher``
          拉当日 bar（盘中当天那根 close 即实时最新价）。
        - ``premium_rate = (转债 close ÷ 转股价值 − 1) × 100``，其中转股价值 =
          ``正股 close × 100 ÷ convert_price``，存百分数并保留两位小数。
        - ``remaining_size`` 取 ``strategy_lab_cb_basic.remaining_size``；为空时
          不覆盖因子行已有值。字段按可得性写入，缺失项不落库。
        - ``symbols`` 语义与其他同步方法一致，为转债代码过滤。
        """
        fetcher = CbUnderlyingStockOhlcFetcher()
        effective_date = trade_date or date.today()
        symbol_filter = {str(symbol).strip().lower().split(".")[-1] for symbol in symbols or [] if str(symbol).strip()}
        inputs = self.repository.list_cb_factor_inputs(market=market, trade_date=effective_date)
        if symbol_filter:
            inputs = [item for item in inputs if item["bond_code"].lower() in symbol_filter]
        payload = {
            "market": market,
            "status": "正常",
            "trade_date": effective_date.isoformat(),
            "symbols": symbols or [],
            "bonds_total": len(inputs),
        }
        if _run_id is None:
            run = self.repository.create_sync_run(
                run_uid=uuid4().hex,
                sync_type="cb_factors",
                market=market,
                payload=payload,
            )
            run_id = run.id
        else:
            run_id = _run_id
        try:
            result = {
                "bonds_total": len(inputs),
                "cb_factor_rows_updated": 0,
                "premium_computed": 0,
                "stock_close_filled": 0,
                "remaining_filled": 0,
                "bonds_skipped_no_close": 0,
                "bonds_skipped_no_price": 0,
                "bonds_failed": [],
            }
            pending: List[Dict[str, Any]] = []
            stock_closes: Dict[str, Optional[float]] = {}
            for idx, item in enumerate(inputs, 1):
                self._raise_if_cancel_requested(run_id)
                bond_code = item["bond_code"]
                try:
                    bond_close = item.get("close")
                    if bond_close is None:
                        result["bonds_skipped_no_close"] += 1
                        logger.info("[跳过] 可转债 %s 当日无 close，无法计算因子", bond_code)
                        continue
                    row: Dict[str, Any] = {"bond_code": bond_code, "trade_date": effective_date}
                    stock_code = item.get("stock_code") or ""
                    stock_close: Optional[float] = None
                    if stock_code:
                        if stock_code not in stock_closes:
                            frame = fetcher.fetch_daily(stock_code, effective_date, effective_date)
                            self._raise_if_cancel_requested(run_id)
                            candidate = float(frame.iloc[-1]["close"]) if not frame.empty else None
                            # candidate == candidate 过滤 NaN 行情
                            stock_closes[stock_code] = candidate if candidate == candidate else None
                        stock_close = stock_closes[stock_code]
                    if stock_close is not None:
                        row["stock_close"] = stock_close
                        result["stock_close_filled"] += 1
                    convert_price = item.get("convert_price")
                    premium = None
                    if stock_close is not None and convert_price:
                        convert_value = stock_close * 100.0 / float(convert_price)
                        premium = round((float(bond_close) / convert_value - 1.0) * 100.0, 2)
                        row["premium_rate"] = premium
                        result["premium_computed"] += 1
                    if item.get("remaining_size") is not None:
                        row["remaining_size"] = item["remaining_size"]
                        result["remaining_filled"] += 1
                    if premium is None:
                        result["bonds_skipped_no_price"] += 1
                        logger.info(
                            "[跳过] 可转债 %s 无法计算溢价率：正股价=%s 转股价=%s",
                            bond_code,
                            stock_close,
                            convert_price,
                        )
                    if any(key in row for key in ("stock_close", "premium_rate", "remaining_size")):
                        pending.append(row)
                        logger.info(
                            "[完成] 可转债 %s 因子：债价=%.2f 正股价=%s 溢价率=%s 剩余规模=%s",
                            bond_code,
                            float(bond_close),
                            stock_close,
                            premium,
                            item.get("remaining_size"),
                        )
                except _DataSyncCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - single symbol failure never aborts the batch
                    result["bonds_failed"].append({"bond_code": bond_code, "error": str(exc)})
                    logger.warning("[失败] 可转债 %s 因子计算失败: %s", bond_code, exc)
                if idx % 20 == 0 or idx == len(inputs):
                    self.repository.update_sync_run_progress(run_id, result={
                        "stage": "computing_factors",
                        "processed": idx,
                        "total": len(inputs),
                        "premium_computed": result["premium_computed"],
                        "stock_close_filled": result["stock_close_filled"],
                        "remaining_filled": result["remaining_filled"],
                        "bonds_skipped_no_close": result["bonds_skipped_no_close"],
                        "bonds_skipped_no_price": result["bonds_skipped_no_price"],
                    })
            if pending:
                result["cb_factor_rows_updated"] = self.repository.update_cb_daily_factor_fields(
                    pending, source="cb_factors"
                )
            if _complete_on_success:
                self.repository.complete_sync_run(run_id, result=result)
            return {"sync_run_id": run_id, **result}
        except _DataSyncCancelled:
            result = locals().get("result", {})
            result["cancelled"] = True
            self.repository.cancel_sync_run(run_id, result=result)
            return {"sync_run_id": run_id, "status": "cancelled", **result}
        except Exception as exc:
            self.repository.fail_sync_run(run_id, str(exc))
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
        ``cb_premium_history``（补空字段）/ ``cb_factors``（因子计算，日期取
        ``end_date or start_date``，缺省今天）/ ``cb_scheduled``（手动触发
        调度链路：基础 + 行情 + 因子 + 盘后通知，``run_kind='after_close'``）/
        ``all``（先基础后行情）。
        后台任务复用 ``_PROVIDER_SYNC_LOCK`` 互斥，
        进度通过 ``list_sync_runs`` 轮询。
        """
        if sync_type not in ("cb_basic", "cb_ohlc", "cb_premium_history", "cb_factors", "cb_scheduled", "all"):
            raise ValueError(f"Unsupported sync_type: {sync_type}")
        if not _PROVIDER_SYNC_LOCK.acquire(blocking=False):
            raise ValueError("已有数据源同步任务进行中，请等待完成后再试")
        run = self.repository.create_sync_run(
            run_uid=uuid4().hex,
            sync_type=sync_type,
            market=market,
            payload={
                "market": market,
                "source": "opencli",
                "sync_type": sync_type,
                "include_delisted": include_delisted,
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
                "symbols": symbols or [],
            },
        )

        def _worker() -> None:
            try:
                result: Dict[str, Any] = {}
                if sync_type in ("cb_basic", "all"):
                    result["cb_basic"] = self.sync_cb_basic(
                        market=market,
                        include_delisted=include_delisted,
                        symbols=symbols,
                        _run_id=run.id,
                        _complete_on_success=(sync_type == "cb_basic"),
                    )
                    if result["cb_basic"].get("status") == "cancelled":
                        return
                    if self.repository.is_sync_run_cancel_requested(run.id):
                        result["cancelled"] = True
                        self.repository.cancel_sync_run(run.id, result=result)
                        return
                if sync_type in ("cb_ohlc", "all"):
                    result["cb_ohlc"] = self.sync_cb_ohlc(
                        market=market,
                        include_delisted=include_delisted,
                        start_date=start_date,
                        end_date=end_date,
                        symbols=symbols,
                        _run_id=run.id,
                        _complete_on_success=(sync_type == "cb_ohlc"),
                    )
                    if result["cb_ohlc"].get("status") == "cancelled":
                        return
                if sync_type == "cb_premium_history":
                    result["cb_premium_history"] = self.sync_cb_premium_history(
                        market=market,
                        include_delisted=include_delisted,
                        symbols=symbols,
                        _run_id=run.id,
                        _complete_on_success=True,
                    )
                    if result["cb_premium_history"].get("status") == "cancelled":
                        return
                if sync_type == "cb_factors":
                    result["cb_factors"] = self.sync_cb_factors(
                        market=market,
                        trade_date=end_date or start_date,
                        symbols=symbols,
                        _run_id=run.id,
                        _complete_on_success=True,
                    )
                    if result["cb_factors"].get("status") == "cancelled":
                        return
                if sync_type == "cb_scheduled":
                    # 手动触发调度链路；run 的 complete/cancel 由
                    # run_scheduled_sync_after_close 统一处理（共享 run.id）
                    result["cb_scheduled"] = self.run_scheduled_sync_after_close(
                        run_kind="after_close",
                        market=market,
                        trade_date=end_date or start_date,
                        symbols=symbols,
                        _run_id=run.id,
                    )
                    if result["cb_scheduled"].get("status") == "cancelled":
                        return
                if sync_type == "all":
                    self.repository.complete_sync_run(run.id, result=result)
            except Exception as exc:
                logger.error("Background data sync failed: %s", exc, exc_info=True)
                self.repository.fail_sync_run(run.id, str(exc))
            finally:
                _PROVIDER_SYNC_LOCK.release()

        threading.Thread(
            target=_worker,
            name=f"strategy-lab-data-sync-{sync_type}",
            daemon=True,
        ).start()
        return {"sync_run_id": run.id, "status": "running", "sync_type": sync_type}

    def _raise_if_cancel_requested(self, run_id: int) -> None:
        if self.repository.is_sync_run_cancel_requested(run_id):
            raise _DataSyncCancelled()

    def _ohlc_start_date(
        self, code: str, explicit_start: Optional[date], *, instrument_type: str = "convertible_bond"
    ) -> date:
        """Resolve the OHLC start date: explicit, else incremental from local history."""
        if explicit_start is not None:
            return explicit_start
        latest = self.repository.get_cb_ohlc_latest_date(code=code, instrument_type=instrument_type)
        if latest is not None:
            return latest + timedelta(days=1)
        return date(2020, 1, 1)
