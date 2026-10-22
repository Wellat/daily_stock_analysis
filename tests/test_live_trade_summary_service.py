# -*- coding: utf-8 -*-
"""盘后实盘成交汇总（LiveTradeSummaryService）测试。"""

from __future__ import annotations

from datetime import date
from unittest import mock

import pytest

from src.services.live_trade_summary_service import LiveTradeSummaryService
from src.storage import (
    DatabaseManager,
    PortfolioAccount,
    PortfolioTrade,
    StrategyLabCbBasic,
    TradingOrder,
)

TRADE_DATE = date(2026, 10, 22)


class _NoQuoteFetcher:
    """行情不可用桩：名称兜底降级为仅代码。"""

    def get_realtime_quote(self, symbol, log_final_failure=True):
        return None


class _NamedQuoteFetcher:
    """行情桩：按代码返回固定名称。"""

    def __init__(self, names: dict[str, str]):
        self.names = names

    def get_realtime_quote(self, symbol, log_final_failure=True):
        from types import SimpleNamespace
        return SimpleNamespace(name=self.names.get(symbol))


@pytest.fixture()
def db() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def _seed_account(db: DatabaseManager, name: str = "主账户") -> int:
    with db.get_session() as session:
        account = PortfolioAccount(name=name, market="cn", base_currency="CNY")
        session.add(account)
        session.commit()
        return int(account.id)


def _seed_trade(db: DatabaseManager, account_id: int, *, symbol: str, side: str,
                quantity: float, price: float, fee: float = 0.0,
                symbol_name: str | None = None, trade_date: date = TRADE_DATE) -> None:
    with db.get_session() as session:
        session.add(PortfolioTrade(
            account_id=account_id, symbol=symbol, symbol_name=symbol_name,
            market="cn", currency="CNY", trade_date=trade_date, side=side,
            quantity=quantity, price=price, fee=fee,
        ))
        session.commit()


def test_summary_message_groups_by_side_with_amounts(db: DatabaseManager) -> None:
    account_id = _seed_account(db)
    _seed_trade(db, account_id, symbol="510880", side="buy", quantity=100, price=3.475,
                fee=0.1, symbol_name="红利ETF华泰柏瑞")
    _seed_trade(db, account_id, symbol="113046", side="sell", quantity=70, price=101.376, fee=2.13)

    service = LiveTradeSummaryService(db)
    service._fetcher = _NoQuoteFetcher()
    payload = service.summarize(TRADE_DATE, service._load_trades(TRADE_DATE))
    message = service.build_message(payload)

    assert payload["buy_count"] == 1 and payload["sell_count"] == 1
    assert payload["buy_amount"] == 347.5 and payload["sell_amount"] == 7096.32
    assert "实盘成交汇总 · 2026-10-22（买入 1 ｜卖出 1）" in message
    assert "🔴 买入" in message and "🟢 卖出" in message
    assert "510880 红利ETF华泰柏瑞 100份 @ 3.475＝347.50元" in message
    assert "113046 70张 @ 101.376＝7096.32元" in message
    assert "净卖出 6748.82" in message
    assert "费用 2.23" in message


def test_run_skips_notification_when_no_trades(db: DatabaseManager) -> None:
    _seed_account(db)

    with mock.patch.object(LiveTradeSummaryService, "_send_feishu") as send:
        result = LiveTradeSummaryService(db).run(trade_date=TRADE_DATE)

    assert result is None
    send.assert_not_called()


def test_run_sends_feishu_with_trade_summary_title(db: DatabaseManager) -> None:
    account_id = _seed_account(db)
    _seed_trade(db, account_id, symbol="113046", side="sell", quantity=70, price=101.376,
                symbol_name="金田转债")

    with mock.patch("src.notification.NotificationService.send_to_feishu", return_value=True) as send:
        service = LiveTradeSummaryService(db)
        service._fetcher = _NoQuoteFetcher()
        result = service.run(trade_date=TRADE_DATE)

    assert result is not None and result["feishu_sent"] is True
    send.assert_called_once()
    assert send.call_args.args[0].startswith("实盘成交汇总 · 2026-10-22")
    assert "113046 金田转债" in send.call_args.args[0]
    assert send.call_args.kwargs.get("title") == "成交汇总"


def test_multi_account_labels_each_trade_line(db: DatabaseManager) -> None:
    a1 = _seed_account(db, "账户A")
    a2 = _seed_account(db, "账户B")
    _seed_trade(db, a1, symbol="113046", side="buy", quantity=10, price=100.0)
    _seed_trade(db, a2, symbol="513630", side="buy", quantity=100, price=1.5)

    service = LiveTradeSummaryService(db)
    service._fetcher = _NoQuoteFetcher()
    payload = service.summarize(TRADE_DATE, service._load_trades(TRADE_DATE))
    message = service.build_message(payload)

    assert "[账户A] 113046" in message
    assert "[账户B] 513630" in message


def test_symbol_names_fallback_orders_cb_basic_and_quote(db: DatabaseManager) -> None:
    """成交行缺名称时按 订单记录 → 转债基础表 → 实时行情 兜底补全。"""
    account_id = _seed_account(db)
    # 113046：订单记录带名称；113605：仅 cb_basic 有 bond_name；510880：只能靠行情
    with db.get_session() as session:
        session.add(TradingOrder(order_uid="ord-1", symbol="113046", market="cn", side="sell",
                                 quantity=70, order_type="limit", status="filled",
                                 symbol_name="金田转债"))
        session.add(StrategyLabCbBasic(bond_code="113605", bond_name="赛意转债", stock_code="300687"))
        session.commit()
    _seed_trade(db, account_id, symbol="113046", side="sell", quantity=70, price=101.376)
    _seed_trade(db, account_id, symbol="113605", side="sell", quantity=10, price=110.055)
    _seed_trade(db, account_id, symbol="510880", side="buy", quantity=100, price=3.475)

    service = LiveTradeSummaryService(db)
    service._fetcher = _NamedQuoteFetcher({"510880": "红利ETF华泰柏瑞"})
    payload = service.summarize(TRADE_DATE, service._load_trades(TRADE_DATE))
    message = service.build_message(payload)

    assert "113046 金田转债" in message
    assert "113605 赛意转债" in message
    assert "510880 红利ETF华泰柏瑞" in message


def test_symbol_name_fallback_tolerates_quote_failure(db: DatabaseManager) -> None:
    """行情取名称失败时降级为仅代码，不影响汇总发送。"""
    account_id = _seed_account(db)
    _seed_trade(db, account_id, symbol="510880", side="buy", quantity=100, price=3.475)

    service = LiveTradeSummaryService(db)
    service._fetcher = _NoQuoteFetcher()
    payload = service.summarize(TRADE_DATE, service._load_trades(TRADE_DATE))
    message = service.build_message(payload)

    assert "• 510880 100份 @ 3.475" in message
