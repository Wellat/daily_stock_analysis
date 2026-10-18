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
        if config.instance_id is not None:
            instance = self.repository.get_strategy_instance(config.instance_id)
            if instance is None:
                raise ValueError(f"Strategy instance not found: {config.instance_id}")
            if instance.strategy_id != config.strategy_id:
                raise ValueError(
                    "instance_id does not match strategy_id: "
                    f"instance {config.instance_id} uses {instance.strategy_id}"
                )

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
        if config.instrument_type == "etf":
            return self._resolve_signal_engine(config)
        if config.instrument_type != "convertible_bond":
            return FixtureDoubleLowEngine()
        if config.strategy_id == "rotation":
            return self._resolve_rotation_engine(config)
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
        # low-premium 不再特判到 unified_low_premium_v1（未完成：无持仓跨日、
        # 持仓市值恒 0、永不卖出，现金烧完后停摆，9 月起的所有该策略回测结果
        # 均失真）；回到 database 引擎，真实轮动回测走 rotation + score_preset。
        if config.strategy_id == "ma-crossover":
            return MovingAverageCrossoverEngine(dataset)
        return FixtureDoubleLowEngine(dataset, name="database_double_low_v1")

    def _resolve_signal_engine(self, config: StrategyLabRunConfig) -> Any:
        """signal 策略：纯 Python 信号引擎 + ETF 日线（含均线预热段）+ 可选指数基准。

        无已同步数据时直接报错（ETF 依赖显式 symbols，无 fixture 回退语义）。
        """
        from src.core.strategy_lab.signal_engine import SignalEngine
        from src.core.strategy_lab.signal_params import SignalParams

        if not config.symbols:
            raise ValueError(
                "ETF 回测需要在「标的」中提供 ETF 代码（如 510880），并先完成 etf_daily 数据同步"
            )
        params = SignalParams.from_parameters(config.parameters or {})
        # 时序因子预热（均线/MACD）：按窗口大小回看（交易日×1.6 ≈ 自然日），从库里多取一段
        lookback_days = int(params.warmup_trading_days() * 1.6)
        rows = self.data_repository.load_etf_signal_rows(
            symbols=config.symbols,
            start_date=config.start_date,
            end_date=config.end_date,
            lookback_days=lookback_days,
        )
        benchmark_rows = None
        requested = (config.benchmark_symbol or "").strip().lower()
        if requested in {"000300", "hs300", "csi300", "sh000300", "1.000300"}:
            index_rows = self.data_repository.load_index_daily(
                symbol="000300",
                start_date=config.start_date,
                end_date=config.end_date,
            )
            benchmark_rows = [(row["trade_date"], float(row["close"])) for row in index_rows]
        return SignalEngine(rows, benchmark_rows=benchmark_rows)

    def _resolve_rotation_engine(self, config: StrategyLabRunConfig) -> Any:
        """rotation 策略：backtrader 引擎 + v2 数据行 + 可选指数基准。

        无已同步数据时回退 fixture 引擎（保持与旧策略一致的样本数据警示语义）。
        """
        from src.core.strategy_lab.backtest import RotationBacktestEngine

        rows = self.data_repository.load_cb_backtest_rows_v2(
            market=config.market,
            start_date=config.start_date,
            end_date=config.end_date,
            symbols=config.symbols,
        )
        if not rows:
            # 保持与旧策略一致的样本数据回退与警示语义（engine_name 含 fixture）
            return FixtureDoubleLowEngine(name="fixture_rotation_v1")
        benchmark_rows = None
        requested = (config.benchmark_symbol or "").strip().lower()
        if requested in {"000300", "hs300", "csi300", "sh000300", "1.000300"}:
            index_rows = self.data_repository.load_index_daily(
                symbol="000300",
                start_date=config.start_date,
                end_date=config.end_date,
            )
            benchmark_rows = [(row["trade_date"], float(row["close"])) for row in index_rows]
        return RotationBacktestEngine(rows, benchmark_rows=benchmark_rows)

    def get_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        row = self.repository.get_run_with_metric(run_id)
        if row is None:
            return None
        return self._run_payload(row[0], row[1])

    def list_runs(self, *, page: int, limit: int) -> Dict[str, Any]:
        offset = (page - 1) * limit
        rows, total = self.repository.list_runs(limit=limit, offset=offset)
        metrics_by_run = self.repository.list_run_metrics([row.id for row in rows])
        instance_names = self.repository.strategy_instance_names(
            [getattr(row, "instance_id", None) for row in rows]
        )
        return {
            "total": total,
            "page": page,
            "limit": limit,
            "items": [
                self._run_summary_payload(
                    row,
                    metric=metrics_by_run.get(row.id),
                    with_parameters=True,
                    instance_names=instance_names,
                )
                for row in rows
            ],
        }

    def list_trades(self, run_id: int) -> List[Dict[str, Any]]:
        if self.repository.get_run_with_metric(run_id) is None:
            raise KeyError(f"Strategy Lab run not found: {run_id}")
        rows = self.repository.list_trades(run_id)
        # Trades persist codes only; enrich display names from the CB master table.
        # （ETF/股票暂无名称主数据表，symbol_name 为 None 由前端回退显示代码）
        names: Dict[str, str] = {}
        for market in {row.market for row in rows if row.instrument_type == "convertible_bond"}:
            names.update(
                self.data_repository.get_cb_names(
                    market=market, codes=[row.symbol for row in rows]
                )
            )
        return [self._trade_payload(row, names) for row in rows]

    def _get_strategy(self, strategy_id: str) -> Dict[str, Any]:
        for strategy in list_builtin_strategies():
            if strategy["strategy_id"] == strategy_id:
                return strategy
        raise ValueError(f"Unsupported strategy_id: {strategy_id}")

    def _run_payload(self, run: StrategyLabRun, metric: Optional[StrategyLabRunMetric]) -> Dict[str, Any]:
        instance_id = getattr(run, "instance_id", None)
        payload = self._run_summary_payload(
            run,
            with_parameters=True,
            instance_names=self.repository.strategy_instance_names([instance_id]),
        )
        payload["symbols"] = json_list(run.symbols_json)
        payload["equity_curve"] = json_list(run.equity_curve_json)
        # 详情用完整指标（diagnostics / benchmark_metrics / 盈亏周期），覆盖摘要紧凑版
        payload["metrics"] = self._metric_payload(metric)
        return payload

    def _run_summary_payload(
        self,
        run: StrategyLabRun,
        *,
        metric: Optional[StrategyLabRunMetric] = None,
        with_parameters: bool = False,
        instance_names: Optional[Dict[int, str]] = None,
    ) -> Dict[str, Any]:
        instance_id = getattr(run, "instance_id", None)
        payload = {
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
            "instance_id": instance_id,
            "instance_name": instance_names.get(instance_id) if instance_names and instance_id else None,
            "error_message": run.error_message,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        }
        # 列表摘要带紧凑指标（不含 diagnostics，避免整页膨胀）；详情走 _metric_payload
        if metric is not None:
            payload["metrics"] = self._metric_summary_payload(metric)
        if with_parameters:
            payload["parameters"] = json_dict(run.parameters_json)
        return payload

    @staticmethod
    def _metric_summary_payload(metric: StrategyLabRunMetric) -> Dict[str, Any]:
        return {
            "total_return_pct": metric.total_return_pct,
            "annualized_return_pct": metric.annualized_return_pct,
            "max_drawdown_pct": metric.max_drawdown_pct,
            "sharpe_ratio": metric.sharpe_ratio,
            "sortino_ratio": metric.sortino_ratio,
            "calmar_ratio": metric.calmar_ratio,
            "win_rate_pct": metric.win_rate_pct,
            "trade_count": metric.trade_count,
            "turnover_avg_pct": metric.turnover_avg_pct,
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
            # rotation 引擎扩展指标
            "turnover_avg_pct": metric.turnover_avg_pct,
            "period_count": metric.period_count,
            "profit_periods": metric.profit_periods,
            "loss_periods": metric.loss_periods,
            "benchmark_metrics": json_dict(metric.benchmark_metrics_json),
        }

    @staticmethod
    def _trade_payload(trade: StrategyLabTrade, names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        return {
            "id": trade.id,
            "run_id": trade.run_id,
            "trade_date": trade.trade_date.isoformat() if trade.trade_date else None,
            "canonical_id": trade.canonical_id,
            "symbol": trade.symbol,
            "symbol_name": (names or {}).get(str(trade.symbol or "").lower()),
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
