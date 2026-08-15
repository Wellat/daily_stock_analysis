# -*- coding: utf-8 -*-
"""Strategy Lab data sync tests."""

from __future__ import annotations

import json
import pytest
import sys
from datetime import date
from types import SimpleNamespace
from sqlalchemy import func, select

from src.services.strategy_lab.data_sync_service import StrategyLabDataSyncService
from src.services.strategy_lab.cb_providers import (
    AkshareConvertibleBondProvider,
    ConvertibleBondOhlcFetcher,
    ConvertibleBondSyncPayload,
    JisiluConvertibleBondProvider,
    OpencliConvertibleBondProvider,
)
import src.services.strategy_lab.cb_providers as cb_providers
from src.storage import DatabaseManager, StrategyLabCbBasic, StrategyLabCbDailyFactor, StrategyLabCbEvent


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


def test_sync_cb_basic_with_symbols_skips_list_and_maps_underlying_stock(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.services.strategy_lab.data_sync_service as dss

    class _DirectOpencliProvider:
        name = "opencli"

        def __init__(self, **kwargs):
            pass

        def fetch_list(self, *, include_delisted=False):
            raise AssertionError("symbol-scoped sync must not call cb-list")

        def fetch_detail_batch(self, bond_codes, workers=3):
            assert bond_codes == ["110081"]
            return {
                "110081": {
                    "bond_code": "110081",
                    "bond_name": "闻泰转债",
                    "stockCode": "600745",
                    "stockName": "闻泰科技",
                    "list_date": "2021-08-20",
                    "maturity_date": "2027-07-28",
                    "remaining_size": "72.068",
                    "convert_price": "18.36",
                    "industry": "电子-半导体-分立器件",
                    "delisted": False,
                    "cb_event_list": [],
                }
            }

        @staticmethod
        def normalize_detail(record):
            return cb_providers._cb_detail_normalize(record)

        @staticmethod
        def normalize_list_row(record):
            return cb_providers._cb_list_basic_row(record)

    monkeypatch.setattr(dss, "OpencliConvertibleBondProvider", _DirectOpencliProvider)
    service = StrategyLabDataSyncService(db_manager)

    result = service.sync_cb_basic(market="cn", symbols=["110081"])

    assert result["bonds_total"] == 1
    assert result["cb_basic_upserted"] == 1
    assert result["bonds_failed"] == []
    with db_manager.get_session() as session:
        row = session.get(StrategyLabCbBasic, "110081")
        assert row is not None
        assert row.stock_code == "600745"
        assert row.stock_name == "闻泰科技"
        assert json.loads(row.terms_json)["industry"] == "电子-半导体-分立器件"


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


def test_opencli_provider_fetches_list_and_details(monkeypatch: pytest.MonkeyPatch) -> None:
    list_stdout = json.dumps([{"bondId": "123001", "bondName": "测试转债", "status": "active"}])
    detail_stdout = json.dumps(
        [
            {
                "bond_code": "123001",
                "bond_name": "测试转债",
                "list_date": "2024-01-02",
                "maturity_date": "2028-01-02",
                "remaining_size": "12.3",
                "convert_price": "10.0",
                "industry": "测试行业",
                "force_redemption_trigger_price": "13.0",
                "adjust_trigger_price": "8.0",
                "put_trigger_price": "7.0",
                "delisted": "false",
                "cb_event_list": [
                    {"event_time": "2024-05-06", "event_type": "down_revise", "detail": "下修底价"},
                    {
                        "event_time": "2024-06-01",
                        "event_type": "bond_rating_change",
                        "detail": "债项评级",
                        "rating_from": "A+",
                        "rating_to": "AA-",
                    },
                ],
            }
        ]
    )

    def _run(command, **kwargs):
        assert kwargs["timeout"] == 5
        if command[2] == "cb-list":
            assert command == ["opencli", "jisilu", "cb-list", "-f", "json"]
            return SimpleNamespace(stdout=list_stdout)
        if command[2] == "cb-detail":
            assert command == ["opencli", "jisilu", "cb-detail", "123001", "-f", "json"]
            return SimpleNamespace(stdout=detail_stdout)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(cb_providers.subprocess, "run", _run)
    payload = OpencliConvertibleBondProvider(timeout=5, workers=1).fetch(market="cn")

    assert payload.cb_basic[0]["bond_code"] == "123001"
    assert payload.cb_basic[0]["status"] == "正常"
    assert payload.cb_basic[0]["list_date"] == date(2024, 1, 2)
    assert payload.cb_basic[0]["remaining_size"] == 12.3
    assert payload.cb_basic[0]["terms"]["industry"] == "测试行业"
    assert payload.cb_terms[0]["redeem_trigger_price"] == 13.0
    assert payload.cb_terms[0]["down_revise_trigger_price"] == 8.0
    assert len(payload.cb_events) == 2
    assert payload.cb_events[0]["event_type"] == "down_revise"
    assert payload.cb_events[0]["event_date"] == date(2024, 5, 6)
    assert payload.cb_events[1]["event_type"] == "bond_rating_change"
    assert "AA-" in payload.cb_events[1]["event_detail"]


def test_opencli_provider_delisted_list_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    def _run(command, **kwargs):
        assert command == ["opencli", "jisilu", "cb-list", "--delisted", "-f", "json"]
        return SimpleNamespace(
            stdout=json.dumps(
                [{"bondId": "110001", "bondName": "旧转债", "status": "delisted", "lastPrice": 105.0, "lastTradeDate": "2026-01-15"}]
            )
        )

    monkeypatch.setattr(cb_providers.subprocess, "run", _run)
    provider = OpencliConvertibleBondProvider(timeout=5)
    rows = provider.fetch_list(include_delisted=True)
    basic = provider.normalize_list_row(rows[0])
    assert basic["status"] == "已退市"
    assert basic["terms"]["last_price"] == 105.0
    assert basic["terms"]["last_trade_date"] == "2026-01-15"


def test_upsert_cb_basic_serializes_non_json_terms(db_manager: DatabaseManager) -> None:
    """terms 元数据里混入 date 等非 JSON 类型时，落库不应抛异常（default=str 兜底）。"""
    service = StrategyLabDataSyncService(db_manager)
    service.repository.upsert_cb_basic(
        [
            {
                "bond_code": "110001",
                "bond_name": "旧转债",
                "stock_code": "",
                "market": "cn",
                "status": "已退市",
                "terms": {"last_trade_date": date(2026, 1, 15), "provider": "opencli"},
            }
        ],
        source="opencli",
    )
    with db_manager.get_session() as session:
        row = session.get(StrategyLabCbBasic, "110001")
        terms = json.loads(row.terms_json)
        assert terms["last_trade_date"] == "2026-01-15"


def test_sync_cb_basic_with_delisted_last_trade_date(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归：已退市列表含 lastTradeDate 时，基础数据同步不应因 terms 序列化失败整体失败。"""
    import src.services.strategy_lab.data_sync_service as dss

    class _FakeProvider:
        name = "opencli"

        def fetch_list(self, *, include_delisted=False):
            assert include_delisted is True
            return [
                {
                    "bondId": "110001",
                    "bondName": "旧转债",
                    "status": "delisted",
                    "lastPrice": 105.0,
                    "lastTradeDate": "2026-01-15",
                }
            ]

        def fetch_detail_batch(self, codes, workers=3):
            return {codes[0]: {"bond_code": "110001", "bond_name": "旧转债", "delisted": "true", "delist_reason": "强赎"}}

        @staticmethod
        def normalize_list_row(record):
            return cb_providers._cb_list_basic_row(record)

        @staticmethod
        def normalize_detail(record):
            return cb_providers._cb_detail_normalize(record)

    monkeypatch.setattr(dss, "OpencliConvertibleBondProvider", lambda **kwargs: _FakeProvider())
    service = StrategyLabDataSyncService(db_manager)

    result = service.sync_cb_basic(market="cn", include_delisted=True)

    assert result["cb_basic_upserted"] == 1
    assert result["bonds_failed"] == []
    assert result["cb_terms_upserted"] == 1
    runs = service.list_sync_runs(page=1, limit=5)
    assert runs["items"][0]["status"] == "completed"


def test_upsert_cb_events_dedupes_by_lowercase_event_type(db_manager: DatabaseManager) -> None:
    """同一 (bond_code, event_date) 的 event_type 大小写变体应去重，只保留一条并更新 detail。"""
    service = StrategyLabDataSyncService(db_manager)
    service.repository.upsert_cb_events(
        [
            {
                "bond_code": "123001",
                "event_date": date(2024, 1, 4),
                "event_type": "Down_Revise",
                "event_detail": "v1",
            }
        ],
        source="opencli",
    )
    service.repository.upsert_cb_events(
        [
            {
                "bond_code": "123001",
                "event_date": date(2024, 1, 4),
                "event_type": "DOWN_REVISE",
                "event_detail": "v2",
            }
        ],
        source="opencli",
    )
    with db_manager.get_session() as session:
        rows = session.execute(select(StrategyLabCbEvent)).scalars().all()
        assert len(rows) == 1
        assert rows[0].event_type == "down_revise"
        assert rows[0].event_detail == "v2"


def test_upsert_cb_events_repairs_preexisting_case_variant_duplicates(
    db_manager: DatabaseManager,
) -> None:
    """历史库中已有大小写重复事件时，重新同步应合并重复行而不是抛 MultipleResultsFound。"""
    service = StrategyLabDataSyncService(db_manager)
    with db_manager.get_session() as session:
        session.add_all(
            [
                StrategyLabCbBasic(
                    bond_code="123001",
                    bond_name="测试转债",
                    stock_code="600001",
                    market="cn",
                ),
                StrategyLabCbEvent(
                    bond_code="123001",
                    event_date=date(2024, 1, 4),
                    event_type="Down_Revise",
                    event_detail="旧记录 1",
                    source="legacy",
                ),
                StrategyLabCbEvent(
                    bond_code="123001",
                    event_date=date(2024, 1, 4),
                    event_type="DOWN_REVISE",
                    event_detail="旧记录 2",
                    source="legacy",
                ),
            ]
        )
        session.commit()

    assert service.repository.upsert_cb_events(
        [
            {
                "bond_code": "123001",
                "event_date": date(2024, 1, 4),
                "event_type": "down_revise",
                "event_detail": "最新记录",
            }
        ],
        source="opencli",
    ) == 1

    with db_manager.get_session() as session:
        rows = session.execute(select(StrategyLabCbEvent)).scalars().all()
        assert len(rows) == 1
        assert rows[0].event_type == "down_revise"
        assert rows[0].event_detail == "最新记录"
        assert rows[0].source == "opencli"


def test_opencli_provider_detail_delisted_and_dash_dates() -> None:
    record = {
        "bond_code": "110001",
        "bond_name": "旧转债",
        "list_date": "-",
        "maturity_date": "2026-01-15",
        "delisted": "true",
        "delist_reason": "强赎",
        "last_trading_date": "2026-01-15",
    }
    normalized = OpencliConvertibleBondProvider(timeout=5).normalize_detail(record)
    assert normalized["basic"]["list_date"] is None
    assert normalized["basic"]["maturity_date"] == date(2026, 1, 15)
    assert normalized["status"] == "已退市"
    assert normalized["meta"]["delist_reason"] == "强赎"


class _NoEastmoneyPatchConfig:
    enable_eastmoney_patch = False


def _em_klines_response() -> dict:
    return {
        "data": {
            "klines": [
                "2026-01-02,100.0,101.0,102.0,99.5,12345,1234567.0",
                "2026-01-03,101.0,102.5,103.0,100.5,23456,2345678.0",
            ]
        }
    }


def test_ohlc_fetcher_prefers_eastmoney(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cb_providers, "get_config", lambda: _NoEastmoneyPatchConfig())
    calls: list[str] = []

    def _get(url, **kwargs):
        calls.append(url)
        assert kwargs["params"]["secid"] == "1.113709"
        assert kwargs["params"]["klt"] == "101"

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return _em_klines_response()

        return _Response()

    monkeypatch.setattr(cb_providers.requests, "get", _get)
    fetcher = ConvertibleBondOhlcFetcher(timeout=5)
    frame = fetcher.fetch_daily("113709", date(2026, 1, 1), date(2026, 1, 31))

    assert len(calls) == 1
    assert list(frame.columns) == ["date", "open", "high", "low", "close", "volume", "amount"]
    assert len(frame) == 2
    assert frame.iloc[1]["close"] == 102.5
    assert fetcher.last_source == "eastmoney"


def test_ohlc_fetcher_falls_back_to_tencent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cb_providers, "get_config", lambda: _NoEastmoneyPatchConfig())
    calls: list[str] = []

    def _get(url, **kwargs):
        calls.append(url)
        if "push2his" in url:
            raise RuntimeError("eastmoney blocked")
        symbol = "sh113709"
        assert kwargs["params"]["param"].startswith(f"{symbol},day,")

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "data": {
                        symbol: {
                            "day": [
                                ["2026-01-02", "100.0", "101.0", "102.0", "99.5", "12345", "1234567.0"],
                                ["2026-01-03", "101.0", "102.5", "103.0", "100.5", "23456", "2345678.0"],
                            ]
                        }
                    }
                }

        return _Response()

    monkeypatch.setattr(cb_providers.requests, "get", _get)
    fetcher = ConvertibleBondOhlcFetcher(timeout=5)
    frame = fetcher.fetch_daily("113709", date(2026, 1, 1), date(2026, 1, 31))

    assert len(calls) == 2
    assert len(frame) == 2
    assert fetcher.last_source == "tencent"


def test_ohlc_fetcher_returns_empty_frame_on_total_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cb_providers, "get_config", lambda: _NoEastmoneyPatchConfig())

    def _get(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(cb_providers.requests, "get", _get)
    fetcher = ConvertibleBondOhlcFetcher(timeout=5)
    frame = fetcher.fetch_daily("113709", date(2026, 1, 1), date(2026, 1, 31))

    assert frame.empty
    assert fetcher.last_source is None
