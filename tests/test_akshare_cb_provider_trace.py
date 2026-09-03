# -*- coding: utf-8 -*-
"""AkshareConvertibleBondProvider 输入/输出追踪测试。

用途：关注可转债数据获取的及时性与完整性，通过日志观察每个方法的
输入参数与返回结果（含字段数量、耗时、异常）。

运行方式（需要真实网络，标记为 ``network``）：

    # 全量列表 + 逐只因子（较慢）
    python -m pytest tests/test_akshare_cb_provider_trace.py -m network -s

    # 只测单只可转债（推荐，快速验证及时性/完整性）
    BOND_CODE=113709 python -m pytest tests/test_akshare_cb_provider_trace.py -m network -s

    # 指定多只
    BOND_CODES=113709,123001 python -m pytest tests/test_akshare_cb_provider_trace.py -m network -s

日志会打印到 stdout（``-s``），同时写入 ``logs/akshare_cb_provider_trace.log``。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import pytest

from src.services.strategy_lab.cb_providers import (
    AkshareConvertibleBondProvider,
    ConvertibleBondSyncPayload,
)

logger = logging.getLogger("akshare_cb_provider_trace")

pytestmark = pytest.mark.network


def _setup_logging() -> None:
    """配置控制台 + 文件双输出，便于观察每个方法的输入输出。"""
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    console.setFormatter(fmt)
    logger.addHandler(console)

    log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.FileHandler(
        os.path.join(log_dir, "akshare_cb_provider_trace.log"),
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)


def _resolve_bond_codes() -> Optional[List[str]]:
    """从环境变量解析单只/多只可转债代码，未设置则返回 None（全量）。"""
    raw = os.getenv("BOND_CODES") or os.getenv("BOND_CODE")
    if not raw:
        return None
    codes = [c.strip() for c in raw.split(",") if c.strip()]
    return codes or None


def _summarize_payload(payload: ConvertibleBondSyncPayload) -> Dict[str, Any]:
    """汇总 payload 各字段数量，便于观察完整性。"""
    return {
        "cb_basic": len(payload.cb_basic),
        "cb_terms": len(payload.cb_terms),
        "cb_daily_factors": len(payload.cb_daily_factors),
        "cb_events": len(payload.cb_events),
    }


@pytest.fixture(scope="module", autouse=True)
def _trace_logging() -> None:
    _setup_logging()


@pytest.fixture(scope="module")
def provider() -> AkshareConvertibleBondProvider:
    return AkshareConvertibleBondProvider()


@pytest.fixture(scope="module")
def bond_codes() -> Optional[List[str]]:
    return _resolve_bond_codes()


def test_fetch_list_input_output(provider: AkshareConvertibleBondProvider, bond_codes: Optional[List[str]]) -> None:
    """观察 fetch_list 的输入（market/symbols）与输出（basics/terms 数量）。"""
    logger.info("=== fetch_list 开始 ===")
    logger.info("输入 market=%r symbols=%r", "cn", bond_codes)
    start = time.time()
    basics, terms = provider.fetch_list(market="cn", symbols=bond_codes)
    elapsed = time.time() - start
    logger.info("输出 basics=%d terms=%d 耗时=%.2fs", len(basics), len(terms), elapsed)
    if basics:
        logger.info("basics[0]=%s", basics[0])
    if terms:
        logger.info("terms[0]=%s", terms[0])
    logger.info("=== fetch_list 结束 ===\n")


def test_fetch_factors_input_output(provider: AkshareConvertibleBondProvider, bond_codes: Optional[List[str]]) -> None:
    """观察单只 fetch_factors 的输入（bond_code）与输出（因子行数）。"""
    codes = bond_codes or ["113709"]
    for code in codes:
        logger.info("=== fetch_factors 开始 ===")
        logger.info("输入 bond_code=%r", code)
        start = time.time()
        rows = provider.fetch_factors(code)
        elapsed = time.time() - start
        logger.info("输出 因子行数=%d 耗时=%.2fs", len(rows), elapsed)
        if rows:
            logger.info("最新一行=%s", rows[-1])
            logger.info("最早一行=%s", rows[0])
        logger.info("=== fetch_factors 结束 ===\n")


def test_fetch_events_input_output(provider: AkshareConvertibleBondProvider, bond_codes: Optional[List[str]]) -> None:
    """观察 fetch_events 的输入（symbols）与输出（事件行数）。"""
    logger.info("=== fetch_events 开始 ===")
    logger.info("输入 symbols=%r", bond_codes)
    start = time.time()
    events = provider.fetch_events(symbols=bond_codes)
    elapsed = time.time() - start
    logger.info("输出 事件数=%d 耗时=%.2fs", len(events), elapsed)
    if events:
        logger.info("events[0]=%s", events[0])
    logger.info("=== fetch_events 结束 ===\n")


def test_fetch_full_payload_input_output(provider: AkshareConvertibleBondProvider, bond_codes: Optional[List[str]]) -> None:
    """观察 fetch 全流程的输入（market/symbols）与输出（四类字段数量汇总）。"""
    logger.info("=== fetch 全流程开始 ===")
    logger.info("输入 market=%r symbols=%r", "cn", bond_codes)
    start = time.time()
    payload = provider.fetch(market="cn", symbols=bond_codes)
    elapsed = time.time() - start
    summary = _summarize_payload(payload)
    logger.info("输出 %s 耗时=%.2fs", summary, elapsed)
    logger.info("=== fetch 全流程结束 ===\n")
