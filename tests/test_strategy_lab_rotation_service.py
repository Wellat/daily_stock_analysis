# -*- coding: utf-8 -*-
"""Integration tests: StrategyLabService rotation runs against a seeded DB."""

from __future__ import annotations

from datetime import date

import pytest

from src.core.strategy_lab.models import StrategyLabRunConfig
from src.services.strategy_lab.data_sync_service import StrategyLabDataSyncService
from src.services.strategy_lab.service import StrategyLabService
from src.storage import DatabaseManager

DATES = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)]


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def _seed(db_manager: DatabaseManager) -> None:
    repo = StrategyLabDataSyncService(db_manager).repository
    repo.upsert_cb_basic(
        [
            {
                "bond_code": "111AAA", "bond_name": "样例甲", "stock_code": "600001",
                "list_date": date(2020, 1, 1),
                "terms": {"last_trading_date": "2030-01-01"},
            },
            {
                "bond_code": "111BBB", "bond_name": "样例乙", "stock_code": "600002",
                "list_date": date(2020, 6, 1),
                "terms_json": "{}",
            },
        ],
        source="sample",
    )
    repo.upsert_cb_daily_factors(
        [
            {"bond_code": "111AAA", "trade_date": d, "close": 100 + i, "premium_rate": 10, "remaining_size": 5}
            for i, d in enumerate(DATES)
        ]
        + [
            {"bond_code": "111BBB", "trade_date": d, "close": 120 + i, "premium_rate": 12, "remaining_size": 5}
            for i, d in enumerate(DATES)
        ],
        source="sample",
    )


def test_rotation_run_persists_extended_result(db_manager: DatabaseManager) -> None:
    _seed(db_manager)
    service = StrategyLabService(db_manager)
    payload = service.create_run(
        StrategyLabRunConfig(
            strategy_id="rotation",
            market="cn",
            instrument_type="convertible_bond",
            start_date=DATES[0],
            end_date=DATES[-1],
            initial_cash=100000,
            benchmark_symbol="",
            parameters={"score_preset": "double_low", "max_positions": 1},
        )
    )

    assert payload["status"] == "completed"
    assert payload["engine_name"] == "cb_rotation_v1"
    metrics = payload["metrics"]
    assert metrics["period_count"] == len(DATES)
    assert metrics["turnover_avg_pct"] is not None
    assert metrics["benchmark_metrics"]["mode"] == "equal_weight_pool"
    assert metrics["benchmark_metrics"]["relative_excess"]["total_return_pct"] is not None
    assert metrics["diagnostics"]["engine"] == "cb_rotation_v1"

    curve = payload["equity_curve"]
    assert len(curve) == len(DATES)
    last_point = curve[-1]
    assert last_point["holdings"] == ["111AAA"]  # 双低分最低
    assert last_point["holdings_count"] == 1
    assert last_point["benchmark_equity"] is not None
    assert last_point["drawdown_pct"] is not None
    assert last_point["daily_return_pct"] is not None

    trades = service.list_trades(payload["id"])
    assert trades
    assert trades[0]["symbol_name"] == "样例甲"


def test_rotation_run_without_data_falls_back_to_fixture(db_manager: DatabaseManager) -> None:
    service = StrategyLabService(db_manager)
    payload = service.create_run(
        StrategyLabRunConfig(
            strategy_id="rotation",
            market="cn",
            instrument_type="convertible_bond",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            initial_cash=100000,
            parameters={"max_positions": 1},
        )
    )
    assert payload["status"] == "completed"
    assert "fixture" in payload["engine_name"]  # 保持样本数据回退与警示语义


def test_rotation_run_with_index_benchmark(db_manager: DatabaseManager) -> None:
    _seed(db_manager)
    repo = StrategyLabDataSyncService(db_manager).repository
    repo.upsert_index_daily(
        [{"symbol": "000300", "trade_date": d, "close": 4000.0 + i * 10} for i, d in enumerate(DATES)]
    )
    payload = StrategyLabService(db_manager).create_run(
        StrategyLabRunConfig(
            strategy_id="rotation",
            market="cn",
            instrument_type="convertible_bond",
            start_date=DATES[0],
            end_date=DATES[-1],
            initial_cash=100000,
            benchmark_symbol="000300",
            parameters={"max_positions": 1},
        )
    )
    assert payload["metrics"]["benchmark_metrics"]["mode"] == "index:000300"
    expected = (4030.0 / 4000.0 - 1) * 100
    assert payload["benchmark_return_pct"] == pytest.approx(expected, abs=1e-4)


def test_load_cb_backtest_rows_v2_exposes_metadata(db_manager: DatabaseManager) -> None:
    _seed(db_manager)
    repo = StrategyLabDataSyncService(db_manager).repository
    rows = repo.load_cb_backtest_rows_v2(
        market="cn",
        start_date=DATES[0],
        end_date=DATES[-1],
        symbols=[],
    )
    assert rows
    by_code = {row["bond_code"]: row for row in rows}
    assert by_code["111AAA"]["list_date"] == date(2020, 1, 1)
    assert by_code["111AAA"]["last_trading_date"] == "2030-01-01"
    assert by_code["111BBB"]["status"] is None  # 未提供状态时为 None（不排除）
    assert "close" in by_code["111AAA"] and "premium_rate" in by_code["111AAA"]


def test_index_daily_upsert_and_load_roundtrip(db_manager: DatabaseManager) -> None:
    repo = StrategyLabDataSyncService(db_manager).repository
    upserted = repo.upsert_index_daily(
        [{"symbol": "000300", "trade_date": DATES[0], "close": 4000.0}]
    )
    assert upserted == 1
    # 幂等重写
    repo.upsert_index_daily([{"symbol": "000300", "trade_date": DATES[0], "close": 4010.0}])
    rows = repo.load_index_daily(symbol="000300", start_date=DATES[0], end_date=DATES[-1])
    assert len(rows) == 1
    assert rows[0]["close"] == 4010.0
