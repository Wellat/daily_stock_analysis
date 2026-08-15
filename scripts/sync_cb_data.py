#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可转债数据同步脚本（基础数据 + OHLC 行情）。

能力 1：同步可转债基础数据
    数据源：本机 opencli（`opencli jisilu cb-list` + `opencli jisilu cb-detail`）。
    流程：默认先拉列表，再对每只转债逐个填充详情；传入 --bond/--symbols
    时直接按指定标的拉详情，跳过列表抓取。
    落库：strategy_lab_cb_basic / strategy_lab_cb_terms / strategy_lab_cb_events。

能力 2：同步可转债行情数据（OHLC）
    数据源：东财优先，腾讯兜底。
    落库：stock_daily（instrument_type='convertible_bond'），close 回填
    strategy_lab_cb_daily_factors 供回测引擎读取。
    支持 --start-date 增量同步，不每次全量。

两个能力统一支持：
    --include-delisted  是否包含已退市可转债（默认仅活跃）
    --start-date/--end-date  同步起止日期（仅行情同步有效）

用法：
    python scripts/sync_cb_data.py --basic                     # 基础数据（活跃）
    python scripts/sync_cb_data.py --basic --include-delisted  # 基础数据（含已退市）
    python scripts/sync_cb_data.py --ohlc                      # 行情，增量（本地最后日期起）
    python scripts/sync_cb_data.py --ohlc --start-date 2026-01-01 --end-date 2026-12-31
    python scripts/sync_cb_data.py --all --include-delisted    # 基础 + 行情
    python scripts/sync_cb_data.py --basic --dry-run           # 只打印映射，不落库
    python scripts/sync_cb_data.py --basic --bond 113709       # 单只
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.repositories.strategy_lab.data_repo import StrategyLabDataRepository  # noqa: E402
from src.services.strategy_lab.cb_providers import OpencliConvertibleBondProvider  # noqa: E402
from src.services.strategy_lab.data_sync_service import StrategyLabDataSyncService  # noqa: E402
from src.storage import DatabaseManager  # noqa: E402

logger = logging.getLogger("sync_cb_data")


def _parse_date_text(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"invalid date: {value!r} (expected YYYY-MM-DD)")


def _parse_symbols(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return str(obj)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="可转债数据同步（基础数据 + OHLC 行情）")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--basic", action="store_true", help="同步可转债基础数据（默认）")
    mode.add_argument("--ohlc", action="store_true", help="同步可转债 OHLC 行情")
    mode.add_argument("--all", action="store_true", help="基础数据 + 行情")
    parser.add_argument("--include-delisted", action="store_true", help="包含已退市可转债（默认仅活跃）")
    parser.add_argument("--start-date", type=_parse_date_text, default=None, help="行情同步起始日期 YYYY-MM-DD（缺省时增量）")
    parser.add_argument("--end-date", type=_parse_date_text, default=None, help="行情同步结束日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--bond", default="", help="只处理单只转债代码")
    parser.add_argument("--symbols", default="", help="逗号分隔的转债代码筛选")
    parser.add_argument("--market", default="cn", help="市场（默认 cn）")
    parser.add_argument("--workers", type=int, default=1, help="opencli 详情并发数（默认 1）")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（调试用）")
    parser.add_argument("--dry-run", action="store_true", help="只打印映射与请求计划，不落库")
    return parser


def dry_run(args: argparse.Namespace) -> None:
    """打印 cb-list 解析结果与若干 cb-detail 映射样例，不落库。"""
    symbols = _parse_symbols(args.symbols)
    if args.bond:
        symbols = [args.bond]
    provider = OpencliConvertibleBondProvider(workers=args.workers)
    print(f"== cb-list (include_delisted={args.include_delisted}) ==")
    rows = provider.fetch_list(include_delisted=args.include_delisted)
    basics: List[Dict[str, Any]] = []
    for row in rows:
        basic = provider.normalize_list_row(row)
        if not basic:
            continue
        if symbols and basic["bond_code"] not in symbols:
            continue
        basics.append(basic)
    if args.limit > 0:
        basics = basics[: args.limit]
    print(f"列表命中 {len(basics)} 只，样例：")
    for basic in basics[:3]:
        print(json.dumps(basic, ensure_ascii=False, indent=2, default=_json_default))
    print("\n== cb-detail 映射样例 ==")
    for basic in basics[:3]:
        code = basic["bond_code"]
        detail = provider.fetch_detail(code)
        if not detail:
            print(f"{code}: 无详情返回")
            continue
        normalized = provider.normalize_detail(detail)
        print(json.dumps(normalized, ensure_ascii=False, indent=2, default=_json_default))


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()

    do_basic = args.basic or (not args.ohlc and not args.all)
    do_ohlc = args.ohlc or args.all

    if args.dry_run:
        dry_run(args)
        return

    db = DatabaseManager()
    service = StrategyLabDataSyncService(db)
    symbols = _parse_symbols(args.symbols)
    if args.bond:
        symbols = [args.bond]

    if do_basic:
        print("===== 同步可转债基础数据 =====")
        result = service.sync_cb_basic(
            market=args.market,
            include_delisted=args.include_delisted,
            symbols=symbols or None,
            workers=args.workers,
        )
        print(f"sync_run #{result['sync_run_id']} 基础数据："
              f"total={result.get('bonds_total')} basic={result.get('cb_basic_upserted')} "
              f"terms={result.get('cb_terms_upserted')} events={result.get('cb_event_upserted')}")
        if result.get("bonds_failed"):
            print(f"失败清单: {result['bonds_failed']}")

    if do_ohlc:
        print("===== 同步可转债 OHLC 行情 =====")
        result = service.sync_cb_ohlc(
            market=args.market,
            include_delisted=args.include_delisted,
            start_date=args.start_date,
            end_date=args.end_date,
            symbols=symbols or None,
            workers=args.workers,
        )
        print(f"sync_run #{result['sync_run_id']} 行情："
              f"total={result.get('bonds_total')} stock_daily新增={result.get('stock_daily_rows_new')} "
              f"factors={result.get('cb_factor_upserted')} 跳过={result.get('bonds_skipped')}")
        if result.get("bonds_failed"):
            print(f"失败清单: {result['bonds_failed']}")


if __name__ == "__main__":
    main()
