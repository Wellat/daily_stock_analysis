# -*- coding: utf-8 -*-
"""Strategy Lab signal service."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from src.repositories.portfolio_repo import PortfolioRepository
from src.repositories.strategy_lab.signal_repo import StrategyLabSignalRepository
from src.services.portfolio_service import PortfolioService
from src.services.strategy_lab.service import StrategyLabService
from src.storage import DatabaseManager


class StrategyLabSignalService:
    """Create and list Strategy Lab signals."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.repository = StrategyLabSignalRepository(db_manager)
        self.run_service = StrategyLabService(db_manager)
        self.portfolio_repository = PortfolioRepository(db_manager)
        self.portfolio_service = PortfolioService(
            self.portfolio_repository
        )

    def create_from_run(
        self,
        *,
        run_id: int,
        portfolio_account_id: Optional[int] = None,
        suggested_action: str = "hold",
        signal_type: str = "strategy_recommendation",
        confidence: float | None = None,
        reason: str | None = None,
    ) -> Dict[str, Any]:
        run = self.run_service.get_run(run_id)
        if run is None:
            raise ValueError(f"Strategy Lab run not found: {run_id}")
        symbols = run.get("symbols") or []
        if not symbols:
            raise ValueError("Strategy Lab run has no selected symbols")
        first_symbol = symbols[0]
        if portfolio_account_id is not None:
            self._require_matching_portfolio_account(portfolio_account_id, run["market"])
        canonical_id = (
            first_symbol
            if first_symbol.startswith(("cn.", "hk.", "us."))
            else f"{run['market']}.{run['instrument_type']}.{first_symbol}"
        )
        payload = self.repository.create_signal(
            run_id=run_id,
            portfolio_account_id=portfolio_account_id,
            canonical_id=canonical_id,
            symbol=first_symbol,
            market=run["market"],
            instrument_type=run["instrument_type"],
            signal_type=signal_type,
            suggested_action=suggested_action,
            confidence=confidence,
            reason=reason or "Strategy Lab generated signal",
            status="active",
        )
        return self.repository._payload(payload)

    def list_signals(self, *, page: int, limit: int) -> Dict[str, Any]:
        offset = (page - 1) * limit
        payload = self.repository.list_signals(limit=limit, offset=offset)
        return {"page": page, "limit": limit, **payload}

    def confirm_signal_trade(
        self,
        *,
        signal_id: int,
        portfolio_account_id: int,
        trade_date: date,
        quantity: float,
        price: float,
        side: Optional[str] = None,
        fee: float = 0.0,
        tax: float = 0.0,
    ) -> Dict[str, Any]:
        signal = self.repository.get_signal(signal_id)
        if signal is None:
            raise ValueError(f"Strategy Lab signal not found: {signal_id}")
        if signal.portfolio_trade_id is not None and signal.status == "confirmed":
            return self.repository._payload(signal)
        self._require_matching_portfolio_account(portfolio_account_id, signal.market)
        if signal.portfolio_account_id is not None and signal.portfolio_account_id != portfolio_account_id:
            raise ValueError("signal is linked to a different Portfolio account")

        action_side = (side or self._side_from_action(signal.suggested_action)).lower()
        trade = self.portfolio_service.record_trade(
            account_id=portfolio_account_id,
            symbol=signal.symbol,
            trade_date=trade_date,
            side=action_side,
            quantity=quantity,
            price=price,
            fee=fee,
            tax=tax,
            market=signal.market,
            currency="CNY" if signal.market == "cn" else None,
            trade_uid=f"strategy_lab_signal_{signal_id}",
            note=f"Strategy Lab signal #{signal_id}: {signal.reason or signal.signal_type}",
        )
        confirmed = self.repository.mark_confirmed(
            signal_id=signal_id,
            portfolio_account_id=portfolio_account_id,
            portfolio_trade_id=int(trade["id"]),
        )
        return self.repository._payload(confirmed)

    @staticmethod
    def _side_from_action(action: str) -> str:
        action_norm = (action or "").strip().lower()
        if action_norm in {"sell", "reduce"}:
            return "sell"
        return "buy"

    def _require_matching_portfolio_account(self, account_id: int, market: str) -> None:
        account = self.portfolio_repository.get_account(account_id)
        if account is None:
            raise ValueError(f"Portfolio account not found or inactive: {account_id}")
        if account.market != market:
            raise ValueError(
                f"Portfolio account market {account.market} does not match signal market {market}"
            )
