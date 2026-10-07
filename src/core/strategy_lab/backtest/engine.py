# -*- coding: utf-8 -*-
"""Rotation backtest engine (backtrader adapter).

实现现有 :class:`StrategyLabEngine` 契约：输入 ``StrategyLabRunConfig`` +
增强数据行（v2 loader 提供的转债日线因子 + 主数据元信息），输出
:class:`StrategyLabRunResult`（含基准曲线与扩展指标）。

基准解析顺序：
1. ``benchmark_symbol`` 为指数别名（000300/hs300/csi300）→ 用引擎构造时
   传入的 ``benchmark_rows``（指数日线，来自 ``strategy_lab_index_daily``）；
2. 与候选池中的转债代码匹配 → 该转债收盘序列；
3. 其余（含空值）→ 标的池等权基准（每日等权收益累计，无新增数据依赖）。

基准与策略共用同一交易日历，从同一批行派生，保证口径一致。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import backtrader as bt

from src.core.strategy_lab.backtest.analyzers import (
    MetricsAnalyzer,
    compute_series_metrics,
    compute_turnover_series,
    compute_win_rate_pct,
    daily_returns,
)
from src.core.strategy_lab.backtest.feeds import CbDailyFeed, build_feed_rows
from src.core.strategy_lab.backtest.params import (
    BENCHMARK_EQUAL_WEIGHT,
    BENCHMARK_INDEX_ALIASES,
    RotationParams,
)
from src.core.strategy_lab.backtest.strategy import (
    CbRotationStrategy,
    InstrumentMeta,
)
from src.core.strategy_lab.engine import StrategyLabEngine
from src.core.strategy_lab.models import (
    StrategyLabEquityPoint,
    StrategyLabMetric,
    StrategyLabRunConfig,
    StrategyLabRunResult,
    StrategyLabTradeResult,
)


class RotationBacktestEngine(StrategyLabEngine):
    """Periodic factor-rotation backtest on backtrader."""

    name = "cb_rotation_v1"

    def __init__(
        self,
        rows: Optional[Sequence[Dict[str, Any]]] = None,
        *,
        benchmark_rows: Optional[Sequence[Tuple[date, float]]] = None,
    ):
        self.rows: List[Dict[str, Any]] = list(rows or [])
        self.benchmark_rows: List[Tuple[date, float]] = list(benchmark_rows or [])

    # ------------------------------------------------------------------

    def run(self, config: StrategyLabRunConfig) -> StrategyLabRunResult:
        if config.instrument_type != "convertible_bond":
            raise ValueError("rotation engine supports convertible_bond only")
        if config.start_date > config.end_date:
            raise ValueError("start_date cannot be after end_date")
        if config.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        params = RotationParams.from_parameters(config.parameters)

        symbols_filter = {
            str(symbol).strip().lower().split(".")[-1]
            for symbol in config.symbols
            if str(symbol).strip()
        }
        scoped_rows = [
            row for row in self.rows
            if config.start_date <= row["trade_date"] <= config.end_date
            and row.get("close") is not None
            and (not symbols_filter or str(row["bond_code"]).lower() in symbols_filter)
        ]
        if not scoped_rows:
            raise ValueError(
                "回测区间内没有已同步的转债行情因子数据，请先在「行情数据-数据同步」完成同步"
            )

        calendar = sorted({row["trade_date"] for row in scoped_rows})
        per_symbol: Dict[str, Dict[date, Dict[str, Any]]] = {}
        meta_rows: Dict[str, Dict[str, Any]] = {}
        for row in scoped_rows:
            symbol = str(row["bond_code"])
            per_symbol.setdefault(symbol, {})[row["trade_date"]] = row
            meta_rows.setdefault(symbol, row)

        datenums = self._datenums(calendar)
        metas: Dict[str, InstrumentMeta] = {}
        feed_rows: Dict[str, list] = {}
        for symbol, values_by_date in per_symbol.items():
            master = meta_rows[symbol]
            metas[symbol] = InstrumentMeta(
                symbol=symbol,
                name=str(master.get("bond_name") or symbol),
                canonical_id=f"{config.market}.convertible_bond.{symbol}",
                first_bar_date=min(values_by_date),
                last_trading_date=self._parse_date(master.get("last_trading_date")),
                list_date=self._parse_date(master.get("list_date")),
                final_data_date=max(values_by_date),
                terminal_exit_date=self._terminal_exit_date(
                    last_trading_date=self._parse_date(master.get("last_trading_date")),
                    delist_date=self._parse_date(master.get("delist_date")),
                    maturity_date=self._parse_date(master.get("maturity_date")),
                    calendar=calendar,
                ),
            )
            feed_rows[symbol] = build_feed_rows(
                calendar,
                datenums=datenums,
                values_by_date=values_by_date,
                first_index=calendar.index(min(values_by_date)),
            )

        cerebro = bt.Cerebro(stdstats=False)
        cerebro.broker.setcash(float(config.initial_cash))
        cerebro.broker.set_coc(True)
        cerebro.broker.setcommission(commission=params.commission)
        for symbol in sorted(feed_rows):
            cerebro.adddata(CbDailyFeed(rows=feed_rows[symbol], symbol=symbol), name=symbol)
        cerebro.addstrategy(CbRotationStrategy, cfg=params, metas=metas)
        cerebro.addanalyzer(MetricsAnalyzer, _name="metrics")
        results = cerebro.run()
        analysis = results[0].analyzers.metrics.get_analysis()
        records = analysis["records"]
        executions = analysis["executions"]
        if not records:
            raise ValueError("回测未产生任何交易日快照，请检查数据区间")

        return self._build_result(config, params, calendar, per_symbol, records, executions)

    # ------------------------------------------------------------------

    @staticmethod
    def _parse_date(value: Any) -> Optional[date]:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str) and value:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return None

    @staticmethod
    def _terminal_exit_date(
        *,
        last_trading_date: Optional[date],
        delist_date: Optional[date],
        maturity_date: Optional[date],
        calendar: Sequence[date],
    ) -> Optional[date]:
        """推导终止交易日前最后一个交易日（T-1 强平日），无终止信息时返回 None。

        终止交易日优先取公告 ``last_trading_date``；缺失时用
        ``min(到期日, 摘牌日-1自然日)`` 近似——自然到期时摘牌日远晚于最后交易日
        （兑付结算周期），min 落在到期日；强赎提前终止时摘牌日紧随最后交易日，
        min 落在摘牌日前一天。终止日在回测日历末尾之后（T-1 在区间外）不强平。
        """
        terminal = last_trading_date
        if terminal is None:
            approximations = [maturity_date]
            if delist_date is not None:
                approximations.append(delist_date - timedelta(days=1))
            approximations = [d for d in approximations if d is not None]
            terminal = min(approximations) if approximations else None
        if terminal is None or not calendar or terminal > calendar[-1]:
            return None
        prior_days = [day for day in calendar if day < terminal]
        return prior_days[-1] if prior_days else None

    @staticmethod
    def _datenums(calendar: Sequence[date]) -> List[float]:
        return [bt.date2num(datetime(d.year, d.month, d.day, 16, 0)) for d in calendar]

    def _benchmark_symbol(
        self,
        config: StrategyLabRunConfig,
        per_symbol: Dict[str, Dict[date, Dict[str, Any]]],
    ) -> Tuple[str, Optional[str]]:
        """返回 (基准模式, 实际标的)；模式见模块 docstring。"""
        requested = (config.benchmark_symbol or "").strip()
        if not requested or requested.lower() in ("equal_weight", "equal_weight_pool"):
            return BENCHMARK_EQUAL_WEIGHT, None
        if requested.lower() in BENCHMARK_INDEX_ALIASES:
            return "index:000300", "000300"
        normalized = requested.lower().split(".")[-1]
        if normalized in {str(symbol).lower() for symbol in per_symbol}:
            return f"bond:{normalized}", normalized
        return BENCHMARK_EQUAL_WEIGHT, None

    def _benchmark_equity(
        self,
        *,
        mode: str,
        target_symbol: Optional[str],
        calendar: Sequence[date],
        per_symbol: Dict[str, Dict[date, Dict[str, Any]]],
        initial_cash: float,
    ) -> List[float]:
        if mode.startswith("index:"):
            return self._normalized_series(
                self._ffill_closes(calendar, dict(self.benchmark_rows)),
                initial_cash,
            )
        if mode.startswith("bond:") and target_symbol:
            values = per_symbol.get(target_symbol, {})
            closes = self._ffill_closes(
                calendar,
                {trade_date: float(row["close"]) for trade_date, row in values.items() if row.get("close") is not None},
            )
            return self._normalized_series(closes, initial_cash)
        # 等权池：ffill 收价下每日等权收益累计
        closes_by_symbol: Dict[str, List[Optional[float]]] = {
            symbol: self._ffill_closes(
                calendar,
                {trade_date: float(row["close"]) for trade_date, row in values.items() if row.get("close") is not None},
            )
            for symbol, values in per_symbol.items()
        }
        equity = [initial_cash]
        value = initial_cash
        for index in range(1, len(calendar)):
            daily_returns_pool: List[float] = []
            for series in closes_by_symbol.values():
                previous, current = series[index - 1], series[index]
                if previous is not None and current is not None and previous > 0:
                    daily_returns_pool.append(current / previous - 1.0)
            value *= 1.0 + (sum(daily_returns_pool) / len(daily_returns_pool) if daily_returns_pool else 0.0)
            equity.append(value)
        return equity

    @staticmethod
    def _ffill_closes(calendar: Sequence[date], closes_by_date: Dict[date, float]) -> List[Optional[float]]:
        series: List[Optional[float]] = []
        last: Optional[float] = None
        for trade_date in calendar:
            if trade_date in closes_by_date:
                last = closes_by_date[trade_date]
            series.append(last)
        return series

    @staticmethod
    def _normalized_series(closes: Sequence[Optional[float]], initial_cash: float) -> List[float]:
        """把收盘序列按首个有效值归一到 initial_cash。"""
        equity: List[float] = []
        base: Optional[float] = None
        for close in closes:
            if close is None:
                equity.append(initial_cash)
                continue
            if base is None:
                base = close
            equity.append(initial_cash * close / base)
        return equity

    # ------------------------------------------------------------------

    def _build_result(
        self,
        config: StrategyLabRunConfig,
        params: RotationParams,
        calendar: Sequence[date],
        per_symbol: Dict[str, Dict[date, Dict[str, Any]]],
        records: List[Any],
        executions: List[Any],
    ) -> StrategyLabRunResult:
        dates = [record.trade_date for record in records]
        equity = [record.equity for record in records]
        initial_cash = float(config.initial_cash)

        mode, target_symbol = self._benchmark_symbol(config, per_symbol)
        benchmark_equity = self._benchmark_equity(
            mode=mode,
            target_symbol=target_symbol,
            calendar=calendar,
            per_symbol=per_symbol,
            initial_cash=initial_cash,
        )
        benchmark_by_date = dict(zip(calendar, benchmark_equity))
        benchmark_series = [benchmark_by_date.get(d, initial_cash) for d in dates]

        drawdowns = self._drawdown_series(equity)
        returns = daily_returns(equity, initial_cash)
        benchmark_returns = daily_returns(benchmark_series, initial_cash)
        excess_returns = [r - b for r, b in zip(returns, benchmark_returns)]
        excess_equity: List[float] = []
        excess_value = initial_cash
        for excess in excess_returns:
            excess_value *= 1.0 + excess
            excess_equity.append(excess_value)

        amounts_by_date = {
            record.trade_date: {"buy": record.buy_amount, "sell": record.sell_amount}
            for record in records
        }
        turnover_series, turnover_avg = compute_turnover_series(
            dates=dates,
            equity=equity,
            amounts_by_date=amounts_by_date,
            initial_cash=initial_cash,
        )

        equity_points: List[StrategyLabEquityPoint] = []
        for index, record in enumerate(records):
            equity_points.append(
                StrategyLabEquityPoint(
                    trade_date=record.trade_date,
                    equity=record.equity,
                    cash=record.cash,
                    positions_value=record.positions_value,
                    benchmark_equity=round(benchmark_series[index], 4) if index < len(benchmark_series) else None,
                    drawdown_pct=drawdowns[index],
                    daily_return_pct=round(returns[index] * 100, 4) if index < len(returns) else None,
                    turnover_pct=turnover_series[index],
                    holdings=list(record.holdings),
                    holdings_count=len(record.holdings),
                )
            )

        strategy_metrics = compute_series_metrics(dates=dates, equity=equity, initial_cash=initial_cash)
        benchmark_metrics = compute_series_metrics(dates=dates, equity=benchmark_series, initial_cash=initial_cash)
        excess_metrics = compute_series_metrics(dates=dates, equity=excess_equity, initial_cash=initial_cash)
        benchmark_metrics["relative_excess"] = excess_metrics
        benchmark_metrics["mode"] = mode

        win_rate = compute_win_rate_pct(executions)
        trades = [
            StrategyLabTradeResult(
                trade_date=record.trade_date,
                canonical_id=f"{config.market}.convertible_bond.{record.symbol}",
                symbol=record.symbol,
                market=config.market,
                instrument_type="convertible_bond",
                side=record.side,
                quantity=record.quantity,
                price=record.price,
                amount=record.amount,
                fee=record.fee,
                reason=record.reason,
            )
            for record in executions
        ]

        total_rows = len(self.rows)
        premium_rows = sum(1 for row in self.rows if row.get("premium_rate") is not None)
        diagnostics = {
            "engine": self.name,
            **params.summary(),
            "benchmark_mode": mode,
            "benchmark_symbol": target_symbol,
            "rebalance_days": len({record.trade_date for record in executions}),
            "calendar_days": len(calendar),
            "symbols_count": len(per_symbol),
            "premium_coverage_pct": round(premium_rows / total_rows * 100, 2) if total_rows else None,
        }
        metrics = StrategyLabMetric(
            total_return_pct=strategy_metrics["total_return_pct"] or 0.0,
            annualized_return_pct=strategy_metrics["annualized_return_pct"],
            max_drawdown_pct=strategy_metrics["max_drawdown_pct"] or 0.0,
            sharpe_ratio=strategy_metrics["sharpe_ratio"],
            sortino_ratio=strategy_metrics["sortino_ratio"],
            calmar_ratio=strategy_metrics["calmar_ratio"],
            win_rate_pct=win_rate,
            trade_count=len(trades),
            exposure_days=(dates[-1] - dates[0]).days if len(dates) > 1 else 0,
            diagnostics=diagnostics,
            turnover_avg_pct=turnover_avg,
            period_count=strategy_metrics["period_count"],
            profit_periods=strategy_metrics["profit_periods"],
            loss_periods=strategy_metrics["loss_periods"],
            benchmark_metrics=benchmark_metrics,
        )
        return StrategyLabRunResult(
            final_equity=round(equity[-1], 4),
            benchmark_return_pct=benchmark_metrics.get("total_return_pct"),
            metrics=metrics,
            trades=trades,
            equity_curve=equity_points,
        )

    @staticmethod
    def _drawdown_series(equity: Sequence[float]) -> List[Optional[float]]:
        peak: Optional[float] = None
        drawdowns: List[Optional[float]] = []
        for value in equity:
            peak = value if peak is None else max(peak, value)
            drawdowns.append(round((value / peak - 1.0) * 100, 4) if peak else None)
        return drawdowns
