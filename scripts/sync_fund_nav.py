#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""场外基金净值同步脚本（供持仓快照估值）。

背景：
    持仓账户（portfolio_trades）中的场外基金以 ``XXXXXX.OF`` 代码记录，
    行情源不覆盖该类代码，快照估值（现价/市值/盈亏）会退化为 missing。
    本脚本把基金单位净值写入 ``stock_daily``（instrument_type='fund'，
    close=单位净值），使快照按既有 history_close 路径完成估值。

数据源：akshare ``fund_open_fund_info_em``（东财「单位净值走势」，复用
``FundNavFetcher``，与盘后 ``portfolio_holdings`` 同步链路同一实现）。

默认自动发现 ``portfolio_trades`` 中所有 ``.OF`` 代码并做增量同步
（从库内该代码最后日期的次日开始，无历史则全量）；也可用 --symbols
指定、--start-date 重设起点、--full 强制全量。

用法：
    python scripts/sync_fund_nav.py                          # 自动发现 + 增量
    python scripts/sync_fund_nav.py --symbols 021583.OF,161725.OF
    python scripts/sync_fund_nav.py --start-date 2025-10-01  # 指定起点重刷
    python scripts/sync_fund_nav.py --full                   # 从净值序列最早开始
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.strategy_lab.cb_providers import FundNavFetcher  # noqa: E402
from src.storage import DatabaseManager  # noqa: E402

logger = logging.getLogger("sync_fund_nav")

FUND_CODE_SUFFIX = ".OF"
INSTRUMENT_TYPE = "fund"
FULL_HISTORY_START = date(1990, 1, 1)


def _parse_date_text(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"invalid date: {value!r} (expected YYYY-MM-DD)")


def _parse_symbols(value: str) -> List[str]:
    symbols = [item.strip().upper() for item in (value or "").split(",") if item.strip()]
    if not symbols:
        raise argparse.ArgumentTypeError("symbols must not be empty")
    for symbol in symbols:
        if not symbol.endswith(FUND_CODE_SUFFIX):
            raise argparse.ArgumentTypeError(
                f"fund symbol must end with {FUND_CODE_SUFFIX}: {symbol}"
            )
    return symbols


def _discover_fund_symbols(db: DatabaseManager) -> List[str]:
    """发现 portfolio_trades 中所有 .OF 代码（含已清仓，便于历史回放）。"""
    from sqlalchemy import text

    with db.get_session() as session:
        rows = session.execute(
            text("SELECT DISTINCT symbol FROM portfolio_trades WHERE symbol LIKE '%.OF'")
        ).fetchall()
    return sorted(row[0].strip().upper() for row in rows if (row[0] or "").strip())


def _last_stored_date(db: DatabaseManager, symbol: str) -> Optional[date]:
    from sqlalchemy import text

    with db.get_session() as session:
        row = session.execute(
            text("SELECT MAX(date) FROM stock_daily WHERE code = :code"),
            {"code": symbol},
        ).fetchone()
    raw = row[0] if row and row[0] else None
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    return datetime.strptime(str(raw), "%Y-%m-%d").date()


def sync_fund(
    db: DatabaseManager,
    symbol: str,
    *,
    start_date: Optional[date],
) -> int:
    fetcher = FundNavFetcher()
    effective_start = start_date
    if effective_start is None:
        last_stored = _last_stored_date(db, symbol)
        if last_stored is not None:
            effective_start = last_stored + timedelta(days=1)
    frame = fetcher.fetch_daily(symbol, effective_start or FULL_HISTORY_START, date.today())
    if frame.empty:
        logger.info("%s: no new NAV rows (start %s)", symbol, effective_start or "full history")
        return 0
    added = db.save_daily_data(
        frame,
        code=symbol,
        data_source=fetcher.last_source or "fund_nav",
        instrument_type=INSTRUMENT_TYPE,
    )
    latest = frame.iloc[-1]
    logger.info(
        "%s: stored %d rows (new=%d), latest NAV %s = %s",
        symbol,
        len(frame),
        added,
        latest["date"],
        latest["close"],
    )
    return len(frame)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sync OTC fund NAV into stock_daily")
    parser.add_argument(
        "--symbols",
        type=_parse_symbols,
        default=None,
        help="comma-separated fund symbols ending with .OF (default: auto-discover from portfolio_trades)",
    )
    parser.add_argument(
        "--start-date",
        type=_parse_date_text,
        default=None,
        help="sync start date YYYY-MM-DD (default: incremental from last stored date)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="sync the full NAV history regardless of stored dates",
    )
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    load_dotenv()
    if args.full and args.start_date:
        parser.error("--full and --start-date are mutually exclusive")
    start_date = FULL_HISTORY_START if args.full else args.start_date

    db = DatabaseManager()
    symbols = args.symbols or _discover_fund_symbols(db)
    if not symbols:
        logger.warning("no .OF fund symbols found; nothing to sync")
        return 0
    logger.info("syncing %d fund(s): %s", len(symbols), ", ".join(symbols))

    failed: List[str] = []
    for symbol in symbols:
        try:
            sync_fund(db, symbol, start_date=start_date)
        except Exception as exc:  # noqa: BLE001 - 单只失败不阻断其余基金
            logger.error("sync %s failed: %s", symbol, exc)
            failed.append(symbol)

    if failed:
        logger.error("failed funds: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
