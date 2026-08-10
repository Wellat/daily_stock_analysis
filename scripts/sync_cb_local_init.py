#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可转债本地初始化同步脚本（一次性）。

从本地 `trading-backtest` 服务（默认 http://localhost:5273）拉取可转债数据，
映射并写入 DSA 数据库，用于初始化策略实验室数据。不走日常 data-sync 链路。

来源接口：
    1. 列表   GET /api/cb/page?page=&page_size=&bond_code=&sort_field=list_date&sort_order=desc
    2. 详情   GET /api/cb/{code}
    3. 行情   GET /api/cb/{code}/quotes?page=&page_size=
    4. 事件   GET /api/cb/{code}/events?page=&page_size=

落库目标：
    - stock_daily                     可转债 OHLCV（与股票行情共用一张表，instrument_type='convertible_bond'）
    - strategy_lab_cb_basic           基础信息 + 详情补充字段（terms_json 元数据）+ status（退市/上市状态）
    - strategy_lab_cb_daily_factors   策略因子（close 冗余一份供回测引擎读取；溢价率/剩余规模本次留空）
    - strategy_lab_cb_events          事件（event_type 归一化：NEW_ISSUE→new_issue / REDEEM→strong_redeem / DOWN_REVISE→down_revise）

字段缺口（来源无数据，本次留空，后续另找数据源补充）：
    - premium_rate（溢价率）/ remaining_size（剩余规模）/ convert_price（转股价）
    - strategy_lab_cb_terms 整表（条款数据）

用法：
    python scripts/sync_cb_local_init.py                    # 全量同步
    python scripts/sync_cb_local_init.py --bond 113708      # 单只
    python scripts/sync_cb_local_init.py --limit 5          # 前 5 只
    python scripts/sync_cb_local_init.py --only-empty       # 跳过已有数据的转债（断点续跑）
    python scripts/sync_cb_local_init.py --bond 113708 --dry-run   # 只打印映射不落库
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.repositories.strategy_lab.data_repo import StrategyLabDataRepository  # noqa: E402
from src.storage import DatabaseManager, StockDaily  # noqa: E402

logger = logging.getLogger("sync_cb_local_init")

SOURCE = "cb_local_init"
MARKET = "cn"
INSTRUMENT_TYPE = "convertible_bond"

# 事件类型归一化：只映射已知语义，其余原样保留
EVENT_TYPE_MAP = {
    "NEW_ISSUE": "new_issue",
    "REDEEM": "strong_redeem",
    "DOWN_REVISE": "down_revise",
}

# 详情接口中无独立列、统一收进 terms_json 元数据的字段
DETAIL_META_FIELDS = [
    "industry",
    "status",
    "interest_start_date",
    "issue_size",
    "issuer_rating",
    "bond_rating",
    "redeem_price_at_maturity",
    "convert_start_date",
    "put_start_date",
    "latest_redeem_date",
    "latest_down_revise_date",
    "delist_reason",
    "delisted_at",
]


def _to_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for candidate in (text, text[:10], text.replace("/", "-")[:10], text.replace(".", "-")[:10]):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_value(record: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


class LocalCbClient:
    """包装本地可转债接口，带超时与指数退避重试。"""

    def __init__(self, base_url: str, page_size: int, timeout: float = 15.0, retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.timeout = timeout
        self.retries = retries

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries):
            try:
                response = requests.get(
                    f"{self.base_url}{path}",
                    params=params,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"GET {path} failed after {self.retries} attempts: {last_exc}")

    def _paged(self, path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """翻页拉全量 items，直到 total 用尽或返回不足一页。"""
        page = 1
        collected: List[Dict[str, Any]] = []
        while True:
            payload = self._get(path, {**params, "page": page, "page_size": self.page_size})
            items = payload.get("items") or []
            collected.extend(items)
            total = payload.get("total")
            if total is not None:
                if not items or page * self.page_size >= int(total):
                    break
            elif len(items) < self.page_size:
                break
            page += 1
        return collected

    def list_bonds(self) -> List[Dict[str, Any]]:
        return self._paged(
            "/api/cb/page",
            {"bond_code": "", "sort_field": "list_date", "sort_order": "desc"},
        )

    def bond_detail(self, bond_code: str) -> Dict[str, Any]:
        return self._get(f"/api/cb/{bond_code}")

    def quotes(self, bond_code: str) -> List[Dict[str, Any]]:
        return self._paged(f"/api/cb/{bond_code}/quotes", {})

    def events(self, bond_code: str) -> List[Dict[str, Any]]:
        return self._paged(f"/api/cb/{bond_code}/events", {})


def build_basic_row(bond: Dict[str, Any], detail: Dict[str, Any]) -> Dict[str, Any]:
    """组装 strategy_lab_cb_basic 行：直接字段 + terms_json 元数据 + status。"""
    meta = {key: detail.get(key) for key in DETAIL_META_FIELDS if detail.get(key) is not None}
    meta["source"] = SOURCE
    return {
        "bond_code": str(bond["bond_code"]),
        "bond_name": str(bond.get("bond_name") or bond["bond_code"]),
        "stock_code": str(bond.get("stock_code") or ""),
        "stock_name": bond.get("stock_name"),
        "market": MARKET,
        "list_date": _to_date(bond.get("list_date")),
        "maturity_date": _to_date(bond.get("maturity_date")),
        "status": bond.get("status") or detail.get("status"),
        "remaining_size": None,
        "current_premium_rate": None,
        "convert_price": None,
        "terms": meta,
    }


def build_factor_rows(bond_code: str, quotes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从行情里抽 close，组装 strategy_lab_cb_daily_factors 行（溢价率/剩余规模/告警留空）。"""
    rows: List[Dict[str, Any]] = []
    for quote in quotes:
        trade_date = _to_date(quote.get("trade_date"))
        if trade_date is None:
            continue
        rows.append(
            {
                "bond_code": str(bond_code),
                "trade_date": trade_date,
                "close": _to_float(quote.get("close")),
                "premium_rate": None,
                "remaining_size": None,
                "redeem_alert": False,
                "down_revise_alert": False,
                "put_alert": False,
            }
        )
    return rows


def build_event_rows(bond_code: str, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for event in events:
        event_date = _to_date(event.get("event_date"))
        event_type = str(event.get("event_type") or "").strip().upper()
        if event_date is None or not event_type:
            continue
        rows.append(
            {
                "bond_code": str(bond_code),
                "event_date": event_date,
                "event_type": EVENT_TYPE_MAP.get(event_type, event_type),
                "event_detail": event.get("detail"),
            }
        )
    return rows


class SyncStats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.succeeded: List[str] = []
        self.failed: List[Dict[str, Any]] = []
        self.cb_basic = 0
        self.cb_factors = 0
        self.cb_events = 0
        self.stock_daily_new = 0

    def record_success(self, bond_code: str) -> None:
        with self.lock:
            self.succeeded.append(bond_code)

    def record_failure(self, bond_code: str, message: str) -> None:
        with self.lock:
            self.failed.append({"bond_code": bond_code, "error": message})

    def add_counts(self, *, cb_basic: int = 0, cb_factors: int = 0, cb_events: int = 0, stock_daily_new: int = 0) -> None:
        with self.lock:
            self.cb_basic += cb_basic
            self.cb_factors += cb_factors
            self.cb_events += cb_events
            self.stock_daily_new += stock_daily_new


def main() -> None:
    parser = argparse.ArgumentParser(description="可转债本地初始化同步脚本（一次性）")
    parser.add_argument("--base-url", default="http://localhost:5273", help="本地可转债服务地址")
    parser.add_argument("--page-size", type=int, default=200, help="接口分页大小（上限 200）")
    parser.add_argument("--workers", type=int, default=8, help="HTTP 抓取并发数")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（调试用）")
    parser.add_argument("--bond", default="", help="只处理单只转债代码")
    parser.add_argument("--only-empty", action="store_true", help="跳过 stock_daily 与因子表均已存在的转债")
    parser.add_argument("--dry-run", action="store_true", help="只打印映射样例，不落库")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    load_dotenv()
    db = DatabaseManager()
    repo = StrategyLabDataRepository(db)
    client = LocalCbClient(args.base_url, args.page_size)

    bonds = client.list_bonds()
    logger.info("列表接口返回 %d 只可转债", len(bonds))
    if args.bond:
        bonds = [bond for bond in bonds if str(bond.get("bond_code")) == args.bond]
        if not bonds:
            logger.error("未找到转债 %s", args.bond)
            sys.exit(2)
    elif args.limit > 0:
        bonds = bonds[: args.limit]

    if not bonds:
        logger.info("没有需要处理的转债")
        return

    if args.dry_run:
        logger.info("--dry-run：打印前 3 只的映射结果")
        for bond in bonds[:3]:
            code = str(bond["bond_code"])
            detail = client.bond_detail(code)
            quotes = client.quotes(code)
            events = client.events(code)
            print(json_dump({
                "basic": build_basic_row(bond, detail),
                "factor_rows": len(build_factor_rows(code, quotes)),
                "quotes_sample": quotes[:2],
                "events_sample": build_event_rows(code, events)[:2],
            }))
        return

    run = repo.create_sync_run(
        run_uid=uuid4().hex,
        sync_type=f"{SOURCE}_convertible_bond",
        market=MARKET,
        payload={"bonds_total": len(bonds), "source": SOURCE},
    )
    stats = SyncStats()
    write_lock = threading.Lock()

    def _bond_has_data(bond_code: str) -> bool:
        with db.get_session() as session:
            stock_rows = session.query(StockDaily).filter(StockDaily.code == bond_code).count()
        factor_rows = repo.list_cb_daily_factors(bond_code=bond_code, limit=1)["total"]
        return stock_rows > 0 and factor_rows > 0

    def process_bond(bond: Dict[str, Any]) -> None:
        code = str(bond["bond_code"])
        if args.only_empty and _bond_has_data(code):
            logger.info("[跳过] %s 已有行情与因子数据", code)
            stats.record_success(code)
            return
        try:
            detail = client.bond_detail(code)
            basic = build_basic_row(bond, detail)
            quotes = client.quotes(code)
            events = client.events(code)

            factor_rows = build_factor_rows(code, quotes)
            event_rows = build_event_rows(code, events)

            with write_lock:
                cb_basic = repo.upsert_cb_basic([basic], source=SOURCE)
                stock_daily_new = 0
                if quotes:
                    frame = pd.DataFrame(
                        [
                            {
                                "date": _to_date(quote.get("trade_date")),
                                "open": _to_float(quote.get("open")),
                                "high": _to_float(quote.get("high")),
                                "low": _to_float(quote.get("low")),
                                "close": _to_float(quote.get("close")),
                                "volume": _to_float(quote.get("volume")),
                                "amount": _to_float(quote.get("amount")),
                            }
                            for quote in quotes
                            if _to_date(quote.get("trade_date")) is not None
                        ]
                    )
                    if not frame.empty:
                        stock_daily_new = db.save_daily_data(
                            frame,
                            code,
                            data_source=SOURCE,
                            instrument_type=INSTRUMENT_TYPE,
                        )
                cb_factors = repo.upsert_cb_daily_factors(factor_rows, source=SOURCE) if factor_rows else 0
                cb_events = repo.upsert_cb_events(event_rows, source=SOURCE) if event_rows else 0

            stats.add_counts(
                cb_basic=cb_basic,
                cb_factors=cb_factors,
                cb_events=cb_events,
                stock_daily_new=stock_daily_new,
            )
            stats.record_success(code)
            logger.info(
                "[完成] %s basic=%d factors=%d events=%d stock_new=%d",
                code, cb_basic, cb_factors, cb_events, stock_daily_new,
            )
        except Exception as exc:  # noqa: BLE001 - 单只失败不中断整体
            stats.record_failure(code, str(exc))
            logger.error("[失败] %s: %s", code, exc)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(process_bond, bond) for bond in bonds]
        for future in as_completed(futures):
            future.result()  # 异常已在 process_bond 内捕获，此处仅确保全部完成

    result = {
        "bonds_total": len(bonds),
        "bonds_succeeded": len(stats.succeeded),
        "bonds_failed": len(stats.failed),
        "cb_basic_upserted": stats.cb_basic,
        "cb_factor_upserted": stats.cb_factors,
        "cb_event_upserted": stats.cb_events,
        "stock_daily_rows_new": stats.stock_daily_new,
    }
    if stats.failed:
        repo.fail_sync_run(run.id, "; ".join(f"{item['bond_code']}: {item['error']}" for item in stats.failed[:50]))
    else:
        repo.complete_sync_run(run.id, result=result)

    print("\n===== 同步汇总 =====")
    print(f"转债总数: {len(bonds)}  成功: {len(stats.succeeded)}  失败: {len(stats.failed)}")
    print(f"cb_basic 写入: {stats.cb_basic}  因子行: {stats.cb_factors}  事件行: {stats.cb_events}")
    print(f"stock_daily 新增行: {stats.stock_daily_new}")
    if stats.failed:
        print("\n失败清单:")
        for item in stats.failed:
            print(f"  {item['bond_code']}: {item['error']}")
    print(f"\nsync_run #{run.id} 记录于 strategy_lab_sync_runs")


def json_dump(value: Any) -> str:
    import json

    def _convert(obj: Any) -> Any:
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return obj

    return json.dumps(value, ensure_ascii=False, indent=2, default=_convert)


if __name__ == "__main__":
    main()
