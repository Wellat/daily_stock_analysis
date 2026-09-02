# -*- coding: utf-8 -*-
"""Service layer for Strategy Lab."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from src.core.strategy_lab.engine import list_builtin_strategies
from src.core.strategy_lab.fixture_engine import FixtureDoubleLowEngine
from src.core.strategy_lab.ma_engine import MovingAverageCrossoverEngine
from src.core.strategy_lab.models import StrategyLabBar, StrategyLabDataSet, StrategyLabInstrument, StrategyLabRunConfig
from src.repositories.strategy_lab.data_repo import StrategyLabDataRepository
from src.repositories.strategy_lab.run_repo import StrategyLabRepository, json_dict, json_list
from src.storage import DatabaseManager, StrategyLabRun, StrategyLabRunMetric, StrategyLabTrade


class StrategyLabService:
    """Coordinate Strategy Lab run execution and persistence."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None, engine: Optional[Any] = None):
        self.repository = StrategyLabRepository(db_manager)
        self.data_repository = StrategyLabDataRepository(db_manager)
        self.engine = engine

    def list_strategies(self) -> List[Dict[str, Any]]:
        return list_builtin_strategies()

    def create_run(self, config: StrategyLabRunConfig) -> Dict[str, Any]:
        strategy = self._get_strategy(config.strategy_id)
        if (
            config.portfolio_account_id is not None
            and not self.repository.portfolio_account_exists(config.portfolio_account_id)
        ):
            raise ValueError(f"Portfolio account not found: {config.portfolio_account_id}")

        engine = self.engine or self._resolve_engine(config)
        run = self.repository.create_pending_run(
            run_uid=uuid4().hex,
            config=config,
            strategy_name=strategy["name"],
            engine_name=engine.name,
        )
        try:
            result = engine.run(config)
        except Exception as exc:
            self.repository.mark_run_failed(run.id, str(exc))
            raise

        completed = self.repository.save_completed_result(run.id, result)
        saved = self.repository.get_run_with_metric(completed.id)
        if saved is None:
            raise ValueError(f"Strategy Lab run not found after save: {completed.id}")
        return self._run_payload(saved[0], saved[1])

    def _resolve_engine(self, config: StrategyLabRunConfig) -> Any:
        """Prefer synchronized domain data while retaining fixture-only developer samples."""
        if config.instrument_type != "convertible_bond":
            return FixtureDoubleLowEngine()
        rows = self.data_repository.load_cb_backtest_rows(
            market=config.market,
            start_date=config.start_date,
            end_date=config.end_date,
            symbols=config.symbols,
        )
        if not rows:
            dataset = FixtureDoubleLowEngine().dataset
            return MovingAverageCrossoverEngine(dataset) if config.strategy_id == "ma-crossover" else FixtureDoubleLowEngine(dataset)
        instruments: Dict[str, StrategyLabInstrument] = {}
        bars: Dict[str, List[StrategyLabBar]] = {}
        for row in rows:
            bond_code = str(row["bond_code"])
            canonical_id = f"{config.market}.convertible_bond.{bond_code}"
            instruments.setdefault(
                canonical_id,
                StrategyLabInstrument(
                    canonical_id=canonical_id,
                    symbol=bond_code,
                    market=config.market,
                    instrument_type="convertible_bond",
                    name=row.get("bond_name") or bond_code,
                ),
            )
            bars.setdefault(canonical_id, []).append(
                StrategyLabBar(
                    trade_date=row["trade_date"],
                    close=float(row["close"]),
                    cb_premium_rate=float(row["premium_rate"]) if row["premium_rate"] is not None else None,
                    remaining_size=float(row["remaining_size"]) if row["remaining_size"] is not None else None,
                    event_blocked=bool(row.get("event_blocked")),
                )
            )
        dataset = StrategyLabDataSet(instruments=list(instruments.values()), bars=bars)
        if config.strategy_id == "low-premium":
            from src.core.strategy_lab.unified_engine import UnifiedLowPremiumEngine
            return UnifiedLowPremiumEngine(dataset)
        if config.strategy_id == "ma-crossover":
            return MovingAverageCrossoverEngine(dataset)
        return FixtureDoubleLowEngine(dataset, name="database_double_low_v1")

    def get_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        row = self.repository.get_run_with_metric(run_id)
        if row is None:
            return None
        return self._run_payload(row[0], row[1])

    def list_runs(self, *, page: int, limit: int) -> Dict[str, Any]:
        offset = (page - 1) * limit
        rows, total = self.repository.list_runs(limit=limit, offset=offset)
        return {
            "total": total,
            "page": page,
            "limit": limit,
            "items": [self._run_summary_payload(row) for row in rows],
        }

    def list_trades(self, run_id: int) -> List[Dict[str, Any]]:
        if self.repository.get_run_with_metric(run_id) is None:
            raise KeyError(f"Strategy Lab run not found: {run_id}")
        return [self._trade_payload(row) for row in self.repository.list_trades(run_id)]

    def _get_strategy(self, strategy_id: str) -> Dict[str, Any]:
        for strategy in list_builtin_strategies():
            if strategy["strategy_id"] == strategy_id:
                return strategy
        raise ValueError(f"Unsupported strategy_id: {strategy_id}")

    def _run_payload(self, run: StrategyLabRun, metric: Optional[StrategyLabRunMetric]) -> Dict[str, Any]:
        payload = self._run_summary_payload(run)
        payload["parameters"] = json_dict(run.parameters_json)
        payload["symbols"] = json_list(run.symbols_json)
        payload["equity_curve"] = json_list(run.equity_curve_json)
        payload["metrics"] = self._metric_payload(metric)
        return payload

    @staticmethod
    def _run_summary_payload(run: StrategyLabRun) -> Dict[str, Any]:
        return {
            "id": run.id,
            "run_uid": run.run_uid,
            "strategy_id": run.strategy_id,
            "strategy_name": run.strategy_name,
            "engine_name": run.engine_name,
            "status": run.status,
            "market": run.market,
            "instrument_type": run.instrument_type,
            "start_date": run.start_date.isoformat() if run.start_date else None,
            "end_date": run.end_date.isoformat() if run.end_date else None,
            "initial_cash": run.initial_cash,
            "final_equity": run.final_equity,
            "benchmark_symbol": run.benchmark_symbol,
            "benchmark_return_pct": run.benchmark_return_pct,
            "portfolio_account_id": run.portfolio_account_id,
            "error_message": run.error_message,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        }

    @staticmethod
    def _metric_payload(metric: Optional[StrategyLabRunMetric]) -> Optional[Dict[str, Any]]:
        if metric is None:
            return None
        return {
            "total_return_pct": metric.total_return_pct,
            "annualized_return_pct": metric.annualized_return_pct,
            "max_drawdown_pct": metric.max_drawdown_pct,
            "sharpe_ratio": metric.sharpe_ratio,
            "sortino_ratio": metric.sortino_ratio,
            "calmar_ratio": metric.calmar_ratio,
            "win_rate_pct": metric.win_rate_pct,
            "trade_count": metric.trade_count,
            "exposure_days": metric.exposure_days,
            "diagnostics": json_dict(metric.diagnostics_json),
        }

    @staticmethod
    def _trade_payload(trade: StrategyLabTrade) -> Dict[str, Any]:
        return {
            "id": trade.id,
            "run_id": trade.run_id,
            "trade_date": trade.trade_date.isoformat() if trade.trade_date else None,
            "canonical_id": trade.canonical_id,
            "symbol": trade.symbol,
            "market": trade.market,
            "instrument_type": trade.instrument_type,
            "side": trade.side,
            "quantity": trade.quantity,
            "price": trade.price,
            "amount": trade.amount,
            "fee": trade.fee,
            "reason": trade.reason,
            "portfolio_trade_id": trade.portfolio_trade_id,
        }
