# -*- coding: utf-8 -*-
"""Strategy Lab data sync tests."""

from __future__ import annotations

import pytest
import sys
from datetime import date
from types import SimpleNamespace
from sqlalchemy import func, select

from src.services.strategy_lab.data_sync_service import StrategyLabDataSyncService
from src.services.strategy_lab.cb_providers import (
    AkshareConvertibleBondProvider,
    ConvertibleBondSyncPayload,
    JisiluConvertibleBondProvider,
    OpencliConvertibleBondProvider,
)
import src.services.strategy_lab.cb_providers as cb_providers
from src.storage import DatabaseManager, StrategyLabCbBasic, StrategyLabCbDailyFactor


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def test_fixture_sync_populates_cb_tables(db_manager: DatabaseManager) -> None:
    service = StrategyLabDataSyncService(db_manager)

    payload = service.sync_fixture_convertible_bonds()

    assert payload["cb_basic_upserted"] == 3
    assert payload["cb_factor_upserted"] == 9

    with db_manager.get_session() as session:
        basics = session.execute(select(func.count(StrategyLabCbBasic.bond_code))).scalar()
        factors = session.execute(select(func.count(StrategyLabCbDailyFactor.id))).scalar()

    assert basics == 3
    assert factors == 9


def test_payload_sync_populates_cb_tables(db_manager: DatabaseManager) -> None:
    service = StrategyLabDataSyncService(db_manager)

    payload = service.sync_payload_convertible_bonds(
        market="cn",
        source="manual",
        cb_basic=[
            {
                "bond_code": "123001",
                "bond_name": "测试转债",
                "stock_code": "600001",
                "stock_name": "测试正股",
                "list_date": "2024-01-02",
                "maturity_date": "2028-01-02",
                "remaining_size": 12.3,
                "current_premium_rate": 18.5,
            }
        ],
        cb_terms=[
            {
                "bond_code": "123001",
                "redeem_clause": "强赎条款",
                "down_revise_clause": "下修条款",
                "put_clause": "回售条款",
            }
        ],
        cb_daily_factors=[
            {
                "bond_code": "123001",
                "trade_date": "2024-01-03",
                "close": 101.2,
                "premium_rate": 17.8,
                "remaining_size": 12.0,
            }
        ],
        cb_events=[
            {
                "bond_code": "123001",
                "event_date": "2024-01-04",
                "event_type": "down_revise",
                "event_detail": "董事会提议下修",
            }
        ],
    )

    assert payload["cb_basic_upserted"] == 1
    assert payload["cb_terms_upserted"] == 1
    assert payload["cb_factor_upserted"] == 1
    assert payload["cb_event_upserted"] == 1


class _FakeCbProvider:
    name = "fake_provider"

    def fetch(self, *, market: str, symbols: list[str] | None = None) -> ConvertibleBondSyncPayload:
        assert market == "cn"
        assert symbols == ["123001"]
        return ConvertibleBondSyncPayload(
            cb_basic=[
                {
                    "bond_code": "123001",
                    "bond_name": "测试转债",
                    "stock_code": "600001",
                    "market": "cn",
                    "remaining_size": 10.0,
                    "current_premium_rate": 20.0,
                }
            ],
            cb_daily_factors=[
                {
                    "bond_code": "123001",
                    "trade_date": "2024-01-03",
                    "close": 101.0,
                    "premium_rate": 19.0,
                }
            ],
        )


class _FailingCbProvider:
    name = "broken_provider"

    def fetch(self, *, market: str, symbols: list[str] | None = None) -> ConvertibleBondSyncPayload:
        raise RuntimeError("upstream unavailable")


def test_provider_sync_populates_cb_tables(db_manager: DatabaseManager) -> None:
    service = StrategyLabDataSyncService(db_manager)

    payload = service.sync_provider_convertible_bonds(
        market="cn",
        source="fake_provider",
        symbols=["123001"],
        provider=_FakeCbProvider(),
    )

    assert payload["cb_basic_upserted"] == 1
    assert payload["cb_factor_upserted"] == 1


def test_provider_sync_records_failed_run(db_manager: DatabaseManager) -> None:
    service = StrategyLabDataSyncService(db_manager)

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        service.sync_provider_convertible_bonds(
            market="cn",
            source="broken_provider",
            provider=_FailingCbProvider(),
        )

    runs = service.list_sync_runs(page=1, limit=10)
    assert runs["items"][0]["status"] == "failed"
    assert "upstream unavailable" in runs["items"][0]["error_message"]


def test_akshare_provider_normalizes_master_terms_daily_and_events(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_akshare = SimpleNamespace(
        bond_zh_cov=lambda: [
            {
                "债券代码": "123001.SZ",
                "债券简称": "测试转债",
                "正股代码": "000001.SZ",
                "剩余规模": "12.3",
                "转股溢价率": "18.5%",
                "下修条款": "下修条款",
            }
        ],
        bond_cb_redeem_jsl=lambda: [{"代码": "123001", "公告日期": "2024/01/04", "强赎状态": "已公告"}],
        bond_zh_hs_cov_daily=lambda symbol: [
            {"date": "2024/01/02", "close": "101.2"},
            {"date": "2024/01/03", "close": "102.5"},
        ],
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)
    monkeypatch.setattr(
        cb_providers,
        "_akshare_call_with_timeout",
        lambda func, **kwargs: func(**({"symbol": kwargs["symbol"]} if "symbol" in kwargs else {})),
    )

    payload = AkshareConvertibleBondProvider().fetch(market="cn", symbols=["123001"])

    assert payload.cb_basic[0]["bond_code"] == "123001"
    assert payload.cb_basic[0]["current_premium_rate"] == 18.5
    assert payload.cb_terms[0]["down_revise_clause"] == "下修条款"
    assert [row["trade_date"] for row in payload.cb_daily_factors] == [date(2024, 1, 2), date(2024, 1, 3)]
    assert payload.cb_events[0]["event_date"] == date(2024, 1, 4)


def test_jisilu_provider_normalizes_snapshot_envelope() -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "rows": [
                    {
                        "id": "123001",
                        "cell": {
                            "bond_id": "123001",
                            "bond_nm": "测试转债",
                            "stock_id": "600001",
                            "stock_nm": "测试正股",
                            "price": "101.2",
                            "premium_rt": "18.5%",
                            "remain_size": "12.3",
                            "redeem_dt": "2024-01-04",
                        },
                    }
                ]
            }

    class _Session:
        def get(self, *args, **kwargs):
            assert kwargs["timeout"] == 5
            return _Response()

    payload = JisiluConvertibleBondProvider(session=_Session(), timeout=5).fetch(market="cn")

    assert payload.cb_basic[0]["bond_code"] == "123001"
    assert payload.cb_basic[0]["current_premium_rate"] == 18.5
    assert payload.cb_daily_factors[0]["close"] == 101.2
    assert payload.cb_events[0]["event_type"] == "strong_redeem"


def test_opencli_provider_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Completed:
        stdout = '[{"bondId": "123001", "bondName": "测试转债", "price": 101.0, "premiumRate": 12.0}]'

    def _run(command, **kwargs):
        assert command[:3] == ["opencli", "jisilu", "cb"]
        assert kwargs["timeout"] == 5
        return _Completed()

    monkeypatch.setattr(cb_providers.subprocess, "run", _run)
    payload = OpencliConvertibleBondProvider(timeout=5).fetch(market="cn")

    assert payload.cb_basic[0]["bond_code"] == "123001"
    assert payload.cb_daily_factors[0]["close"] == 101.0
