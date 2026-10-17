# -*- coding: utf-8 -*-
"""Integration tests: ETF daily store + StrategyLabService signal runs against a seeded DB."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.core.strategy_lab.models import StrategyLabRunConfig
from src.repositories.strategy_lab.data_repo import StrategyLabDataRepository
from src.services.strategy_lab.service import StrategyLabService
from src.storage import DatabaseManager

START = date(2026, 1, 1)


def _dates(n: int) -> list:
    out = []
    current = START
    while len(out) < n:
        if current.weekday() < 5:
            out.append(current)
        current += timedelta(days=1)
    return out


DATES = _dates(60)
# 前 30 根 5.0 横盘；31-45 缓跌；46-60 拉升（对 MA10 触发 <0 买入、>7 卖出）
CLOSES = [5.0] * 30 + [5.0 - 0.033 * i for i in range(1, 16)] + [4.5 + 0.087 * i for i in range(1, 16)]


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def _seed(db_manager: DatabaseManager) -> None:
    repo = StrategyLabDataRepository(db_manager)
    repo.upsert_etf_daily([
        {"symbol": "510880", "trade_date": d, "close": close, "source": "sample"}
        for d, close in zip(DATES, CLOSES)
    ])


def test_etf_daily_upsert_and_load_roundtrip(db_manager: DatabaseManager) -> None:
    repo = StrategyLabDataRepository(db_manager)
    _seed(db_manager)
    rows = repo.load_etf_daily(symbols=["510880"], start_date=DATES[0], end_date=DATES[-1])
    assert len(rows) == len(DATES)
    assert rows[0] == {
        "symbol": "510880", "trade_date": DATES[0], "close": 5.0,
        "dividend_yield": None, "source": "sample",
    }
    # 幂等重写 + 追加 dividend_yield 不被日线 upsert 清空
    repo.upsert_etf_daily([
        {"symbol": "510880", "trade_date": DATES[0], "close": 5.01, "dividend_yield": 4.5, "source": "sample"},
    ])
    repo.upsert_etf_daily([
        {"symbol": "510880", "trade_date": DATES[0], "close": 5.02, "source": "sample"},
    ])
    rows = repo.load_etf_daily(symbols=["510880"], start_date=DATES[0], end_date=DATES[0])
    assert rows[0]["close"] == 5.02
    assert rows[0]["dividend_yield"] == 4.5


def test_load_etf_signal_rows_includes_lookback(db_manager: DatabaseManager) -> None:
    repo = StrategyLabDataRepository(db_manager)
    _seed(db_manager)
    window_start = DATES[40]
    rows = repo.load_etf_signal_rows(
        symbols=["510880"], start_date=window_start, end_date=DATES[-1], lookback_days=14,
    )
    assert rows[0]["trade_date"] < window_start
    assert rows[0]["trade_date"] >= window_start - timedelta(days=14)
    # symbols 归一化（带后缀形态）
    rows_suffixed = repo.load_etf_signal_rows(
        symbols=["sh.510880"], start_date=window_start, end_date=DATES[-1],
    )
    assert len(rows_suffixed) == len(DATES) - 40
    # 空 symbols 不做全量兜底（ETF 无默认全集）
    assert repo.load_etf_signal_rows(symbols=[], start_date=DATES[0], end_date=DATES[-1]) == []


def test_signal_run_persists_result(db_manager: DatabaseManager) -> None:
    _seed(db_manager)
    service = StrategyLabService(db_manager)
    payload = service.create_run(StrategyLabRunConfig(
        strategy_id="signal",
        market="cn",
        instrument_type="etf",
        start_date=DATES[30],
        end_date=DATES[-1],
        initial_cash=100000.0,
        benchmark_symbol="510880",
        symbols=["510880"],
        parameters={"ma_window": 10, "commission": 0.0005},
    ))
    assert payload["status"] == "completed"
    assert payload["engine_name"] == "signal_v1"
    assert payload["metrics"]["diagnostics"]["engine"] == "signal_v1"
    assert payload["metrics"]["benchmark_metrics"]["mode"] == "symbol:510880"
    trades = service.list_trades(payload["id"])
    sides = {t["side"] for t in trades}
    assert sides == {"buy", "sell"}
    assert trades[0]["canonical_id"] == "cn.etf.510880"
    # 预热段不产生快照：曲线首日 = 回测窗口首日
    assert payload["equity_curve"][0]["trade_date"] == DATES[30].isoformat()


def test_signal_run_requires_symbols(db_manager: DatabaseManager) -> None:
    service = StrategyLabService(db_manager)
    with pytest.raises(ValueError, match="ETF 代码"):
        service.create_run(StrategyLabRunConfig(
            strategy_id="signal",
            market="cn",
            instrument_type="etf",
            start_date=DATES[0],
            end_date=DATES[-1],
            initial_cash=100000.0,
            symbols=[],
            parameters={},
        ))


def test_signal_strategy_registered_in_metadata(db_manager: DatabaseManager) -> None:
    service = StrategyLabService(db_manager)
    strategies = {item["strategy_id"]: item for item in service.list_strategies()}
    signal = strategies.get("signal")
    assert signal is not None
    assert signal["instrument_types"] == ["etf"]
    factor_ids = {factor["factor"] for factor in signal["factors"]}
    assert {"close", "ma_gap_pct", "roc_pct", "dividend_yield"} <= factor_ids


def test_signal_macd_strategy_registered_in_metadata(db_manager: DatabaseManager) -> None:
    service = StrategyLabService(db_manager)
    strategies = {item["strategy_id"]: item for item in service.list_strategies()}
    macd = strategies.get("signal_macd")
    assert macd is not None
    assert macd["instrument_types"] == ["etf"]
    factor_ids = {factor["factor"] for factor in macd["factors"]}
    assert {"macd_cross", "macd_dif", "macd_dea", "macd_hist", "close"} <= factor_ids
    assert "ma_gap_pct" not in factor_ids  # MACD 策略因子下拉只暴露 MACD 族
    param_keys = {item["key"] for item in macd["parameters"]}
    assert {"macd_fast", "macd_slow", "macd_signal"} <= param_keys
