# -*- coding: utf-8 -*-
"""Strategy Lab signal tests."""

from __future__ import annotations

from datetime import date

import pytest

from src.core.strategy_lab.models import StrategyLabRunConfig
from src.repositories.portfolio_repo import PortfolioRepository
from src.services.portfolio_service import PortfolioService
from src.services.strategy_lab.signal_service import StrategyLabSignalService
from src.services.strategy_lab.service import StrategyLabService
from src.storage import DatabaseManager


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def test_signal_service_links_run_and_portfolio_account(db_manager: DatabaseManager) -> None:
    run_service = StrategyLabService(db_manager)
    run = run_service.create_run(
        StrategyLabRunConfig(
            strategy_id="double-low",
            market="cn",
            instrument_type="convertible_bond",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            initial_cash=100000,
            parameters={"max_positions": 2},
        )
    )

    signal = StrategyLabSignalService(db_manager).create_from_run(
        run_id=run["id"],
        portfolio_account_id=None,
        suggested_action="buy",
        confidence=0.88,
    )

    assert signal["run_id"] == run["id"]
    assert signal["suggested_action"] == "buy"
    assert signal["symbol"] == "113002"


def test_signal_confirmation_writes_portfolio_trade(db_manager: DatabaseManager) -> None:
    account = PortfolioService(PortfolioRepository(db_manager)).create_account(
        name="Strategy Lab",
        broker="paper",
        market="cn",
        base_currency="CNY",
    )
    run = StrategyLabService(db_manager).create_run(
        StrategyLabRunConfig(
            strategy_id="double-low",
            market="cn",
            instrument_type="convertible_bond",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            initial_cash=100000,
            parameters={"max_positions": 2},
        )
    )
    service = StrategyLabSignalService(db_manager)
    signal = service.create_from_run(run_id=run["id"], suggested_action="buy")

    confirmed = service.confirm_signal_trade(
        signal_id=signal["id"],
        portfolio_account_id=account["id"],
        trade_date=date(2024, 1, 5),
        quantity=10,
        price=100,
    )

    assert confirmed["status"] == "confirmed"
    assert confirmed["portfolio_account_id"] == account["id"]
    assert confirmed["portfolio_trade_id"] is not None


def test_signal_requires_active_matching_portfolio_account_when_linked(db_manager: DatabaseManager) -> None:
    account = PortfolioService(PortfolioRepository(db_manager)).create_account(
        name="Hong Kong account",
        broker="paper",
        market="hk",
        base_currency="HKD",
    )
    run = StrategyLabService(db_manager).create_run(
        StrategyLabRunConfig(
            strategy_id="double-low",
            market="cn",
            instrument_type="convertible_bond",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            initial_cash=100000,
        )
    )

    with pytest.raises(ValueError, match="does not match signal market"):
        StrategyLabSignalService(db_manager).create_from_run(
            run_id=run["id"],
            portfolio_account_id=account["id"],
        )


def test_signal_cannot_be_confirmed_into_different_linked_account(db_manager: DatabaseManager) -> None:
    portfolio_service = PortfolioService(PortfolioRepository(db_manager))
    first_account = portfolio_service.create_account(
        name="Strategy one", broker="paper", market="cn", base_currency="CNY"
    )
    second_account = portfolio_service.create_account(
        name="Strategy two", broker="paper", market="cn", base_currency="CNY"
    )
    run = StrategyLabService(db_manager).create_run(
        StrategyLabRunConfig(
            strategy_id="double-low",
            market="cn",
            instrument_type="convertible_bond",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            initial_cash=100000,
        )
    )
    service = StrategyLabSignalService(db_manager)
    signal = service.create_from_run(run_id=run["id"], portfolio_account_id=first_account["id"])

    with pytest.raises(ValueError, match="different Portfolio account"):
        service.confirm_signal_trade(
            signal_id=signal["id"],
            portfolio_account_id=second_account["id"],
            trade_date=date(2024, 1, 5),
            quantity=10,
            price=100,
        )
