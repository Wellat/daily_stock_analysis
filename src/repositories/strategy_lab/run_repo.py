# -*- coding: utf-8 -*-
"""Repository for Strategy Lab run persistence."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, select

from src.core.strategy_lab.models import StrategyLabRunConfig, StrategyLabRunResult
from src.storage import (
    DatabaseManager,
    PortfolioAccount,
    StrategyLabRun,
    StrategyLabRunMetric,
    StrategyLabTrade,
)


class StrategyLabRepository:
    """DB access layer for Strategy Lab."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def portfolio_account_exists(self, account_id: int) -> bool:
        with self.db.get_session() as session:
            row = session.execute(
                select(PortfolioAccount.id)
                .where(PortfolioAccount.id == account_id, PortfolioAccount.is_active.is_(True))
                .limit(1)
            ).scalar_one_or_none()
            return row is not None

    def create_pending_run(
        self,
        *,
        run_uid: str,
        config: StrategyLabRunConfig,
        strategy_name: str,
        engine_name: str,
    ) -> StrategyLabRun:
        now = datetime.now()
        with self.db.get_session() as session:
            row = StrategyLabRun(
                run_uid=run_uid,
                strategy_id=config.strategy_id,
                strategy_name=strategy_name,
                engine_name=engine_name,
                status="running",
                market=config.market,
                instrument_type=config.instrument_type,
                start_date=config.start_date,
                end_date=config.end_date,
                initial_cash=config.initial_cash,
                benchmark_symbol=config.benchmark_symbol,
                portfolio_account_id=config.portfolio_account_id,
                parameters_json=json.dumps(config.parameters, ensure_ascii=False, sort_keys=True),
                symbols_json=json.dumps(config.symbols, ensure_ascii=False),
                created_at=now,
                started_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def mark_run_failed(self, run_id: int, message: str) -> None:
        with self.db.get_session() as session:
            row = session.get(StrategyLabRun, run_id)
            if row is None:
                return
            row.status = "failed"
            row.error_message = message
            row.completed_at = datetime.now()
            row.updated_at = datetime.now()
            session.commit()

    def save_completed_result(self, run_id: int, result: StrategyLabRunResult) -> StrategyLabRun:
        now = datetime.now()
        with self.db.get_session() as session:
            run = session.get(StrategyLabRun, run_id)
            if run is None:
                raise ValueError(f"Strategy Lab run not found: {run_id}")

            run.status = "completed"
            run.final_equity = result.final_equity
            run.benchmark_return_pct = result.benchmark_return_pct
            selected_symbols = (
                result.metrics.diagnostics.get("selected_symbols")
                if result.metrics.diagnostics
                else None
            )
            if isinstance(selected_symbols, list) and selected_symbols:
                run.symbols_json = json.dumps(selected_symbols, ensure_ascii=False)
            run.equity_curve_json = json.dumps(
                [
                    {
                        "trade_date": point.trade_date.isoformat(),
                        "equity": point.equity,
                        "cash": point.cash,
                        "positions_value": point.positions_value,
                        # rotation 引擎扩展字段（旧引擎为 None 时省略，节省载荷）
                        **({"benchmark_equity": point.benchmark_equity} if point.benchmark_equity is not None else {}),
                        **({"drawdown_pct": point.drawdown_pct} if point.drawdown_pct is not None else {}),
                        **({"daily_return_pct": point.daily_return_pct} if point.daily_return_pct is not None else {}),
                        **({"turnover_pct": point.turnover_pct} if point.turnover_pct is not None else {}),
                        **({"holdings": point.holdings} if point.holdings is not None else {}),
                        **({"holdings_count": point.holdings_count} if point.holdings_count is not None else {}),
                    }
                    for point in result.equity_curve
                ],
                ensure_ascii=False,
            )
            run.completed_at = now
            run.updated_at = now

            metric = StrategyLabRunMetric(
                run_id=run_id,
                total_return_pct=result.metrics.total_return_pct,
                annualized_return_pct=result.metrics.annualized_return_pct,
                max_drawdown_pct=result.metrics.max_drawdown_pct,
                sharpe_ratio=result.metrics.sharpe_ratio,
                sortino_ratio=result.metrics.sortino_ratio,
                calmar_ratio=result.metrics.calmar_ratio,
                win_rate_pct=result.metrics.win_rate_pct,
                trade_count=result.metrics.trade_count,
                exposure_days=result.metrics.exposure_days,
                turnover_avg_pct=result.metrics.turnover_avg_pct,
                period_count=result.metrics.period_count,
                profit_periods=result.metrics.profit_periods,
                loss_periods=result.metrics.loss_periods,
                benchmark_metrics_json=(
                    json.dumps(result.metrics.benchmark_metrics, ensure_ascii=False, sort_keys=True)
                    if result.metrics.benchmark_metrics
                    else None
                ),
                diagnostics_json=json.dumps(result.metrics.diagnostics, ensure_ascii=False, sort_keys=True),
            )
            session.add(metric)
            for trade in result.trades:
                session.add(
                    StrategyLabTrade(
                        run_id=run_id,
                        trade_date=trade.trade_date,
                        canonical_id=trade.canonical_id,
                        symbol=trade.symbol,
                        market=trade.market,
                        instrument_type=trade.instrument_type,
                        side=trade.side,
                        quantity=trade.quantity,
                        price=trade.price,
                        amount=trade.amount,
                        fee=trade.fee,
                        reason=trade.reason,
                        portfolio_trade_id=trade.portfolio_trade_id,
                    )
                )
            session.commit()
            session.refresh(run)
            session.expunge(run)
            return run

    def get_run_with_metric(self, run_id: int) -> Optional[Tuple[StrategyLabRun, Optional[StrategyLabRunMetric]]]:
        with self.db.get_session() as session:
            row = session.get(StrategyLabRun, run_id)
            if row is None:
                return None
            metric = session.execute(
                select(StrategyLabRunMetric).where(StrategyLabRunMetric.run_id == run_id).limit(1)
            ).scalar_one_or_none()
            session.expunge(row)
            if metric is not None:
                session.expunge(metric)
            return row, metric

    def list_runs(self, *, limit: int, offset: int) -> Tuple[List[StrategyLabRun], int]:
        with self.db.get_session() as session:
            total = session.execute(select(StrategyLabRun.id)).scalars().all()
            rows = session.execute(
                select(StrategyLabRun)
                .order_by(desc(StrategyLabRun.created_at), desc(StrategyLabRun.id))
                .offset(offset)
                .limit(limit)
            ).scalars().all()
            for row in rows:
                session.expunge(row)
            return list(rows), len(total)

    def list_run_metrics(self, run_ids: List[int]) -> Dict[int, StrategyLabRunMetric]:
        """批量取一页 run 的指标行（列表摘要用）。"""
        if not run_ids:
            return {}
        with self.db.get_session() as session:
            metric_rows = session.execute(
                select(StrategyLabRunMetric).where(StrategyLabRunMetric.run_id.in_(run_ids))
            ).scalars().all()
            result: Dict[int, StrategyLabRunMetric] = {}
            for metric in metric_rows:
                session.expunge(metric)
                result[metric.run_id] = metric
            return result

    def list_trades(self, run_id: int) -> List[StrategyLabTrade]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(StrategyLabTrade)
                .where(StrategyLabTrade.run_id == run_id)
                .order_by(StrategyLabTrade.trade_date.asc(), StrategyLabTrade.id.asc())
            ).scalars().all()
            for row in rows:
                session.expunge(row)
            return list(rows)


def json_dict(value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def json_list(value: Optional[str]) -> List[Any]:
    if not value:
        return []
    parsed = json.loads(value)
    return parsed if isinstance(parsed, list) else []
