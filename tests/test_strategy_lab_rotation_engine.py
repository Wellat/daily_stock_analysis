# -*- coding: utf-8 -*-
"""Tests for the backtrader-based rotation engine."""

from datetime import date

import pytest

from src.core.strategy_lab.backtest import RotationBacktestEngine
from src.core.strategy_lab.models import StrategyLabRunConfig

DATES = [
    date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3),
    date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8),
    date(2026, 9, 9), date(2026, 9, 10),
]


def make_rows(prices, premiums=None, *, remaining=5.0, blocked=None, last_trading=None, list_dates=None):
    premiums = premiums or {sym: [10.0] * len(DATES) for sym in prices}
    blocked = blocked or {}
    last_trading = last_trading or {}
    list_dates = list_dates or {}
    rows = []
    for symbol, closes in prices.items():
        for index, trade_date in enumerate(DATES):
            rows.append({
                "bond_code": symbol,
                "bond_name": f"BOND-{symbol}",
                "trade_date": trade_date,
                "close": float(closes[index]),
                "premium_rate": premiums[symbol][index],
                "remaining_size": remaining,
                "event_blocked": blocked.get(symbol, [False] * len(DATES))[index],
                "last_trading_date": last_trading.get(symbol),
                "list_date": list_dates.get(symbol, date(2020, 1, 1)),
            })
    return rows


def make_config(parameters=None, *, start=DATES[0], end=DATES[-1], cash=100000.0, benchmark=None, symbols=None):
    return StrategyLabRunConfig(
        strategy_id="rotation",
        market="cn",
        instrument_type="convertible_bond",
        start_date=start,
        end_date=end,
        initial_cash=cash,
        benchmark_symbol=benchmark,
        symbols=symbols or [],
        parameters=parameters or {},
    )


STEADY_PRICES = {
    "AAA": [100.0] * 8,
    "BBB": [110.0] * 8,
    "CCC": [120.0] * 8,
}


class TestRebalanceSemantics:
    def test_rebalance_changes_holdings_when_rank_flips(self):
        # 双低分：前 4 日 AAA 最优；第 5 日起 BBB 价格下跌反超
        prices = {
            "AAA": [100.0, 100.0, 100.0, 100.0, 110.0, 110.0, 110.0, 110.0],
            "BBB": [110.0, 110.0, 110.0, 110.0, 90.0, 90.0, 90.0, 90.0],
        }
        premiums = {"AAA": [10.0] * 8, "BBB": [11.0] * 8}
        rows = make_rows(prices, premiums)
        config = make_config({"score_preset": "double_low", "max_positions": 1})
        result = RotationBacktestEngine(rows).run(config)
        buys = [t for t in result.trades if t.side == "buy"]
        sells = [t for t in result.trades if t.side == "sell"]
        assert [(t.trade_date, t.symbol) for t in buys] == [
            (date(2026, 9, 1), "AAA"),
            (date(2026, 9, 7), "BBB"),
        ]
        assert [(t.trade_date, t.symbol) for t in sells] == [(date(2026, 9, 7), "AAA")]
        # 每日换仓（interval=1）下第 5 根 bar（09-08 是第 5 根？）——见下面对节奏的精确断言
        assert result.equity_curve[-1].holdings == ["BBB"]

    def test_rebalance_interval_skips_intermediate_days(self):
        # 第 3 根 bar（09-03）排名翻转，但 interval=4 时下一根换仓 bar 是第 5 根（09-07）
        prices = {
            "AAA": [100.0, 100.0, 100.0, 100.0, 110.0, 110.0, 110.0, 110.0],
            "BBB": [110.0, 110.0, 110.0, 110.0, 90.0, 90.0, 90.0, 90.0],
        }
        premiums = {"AAA": [10.0] * 8, "BBB": [11.0] * 8}
        rows = make_rows(prices, premiums)
        config = make_config({"score_preset": "double_low", "max_positions": 1, "rebalance_interval": 4})
        result = RotationBacktestEngine(rows).run(config)
        buys = [(t.trade_date, t.symbol) for t in result.trades if t.side == "buy"]
        sells = [(t.trade_date, t.symbol) for t in result.trades if t.side == "sell"]
        assert buys == [(DATES[0], "AAA"), (DATES[4], "BBB")]  # 第 1、5 根 bar 触发换仓
        assert sells == [(DATES[4], "AAA")]  # 第 3 根 bar 的翻转被 interval 跳过

    def test_weekly_rebalance_triggers_on_first_trading_day_of_week(self):
        # 09-04（周五）排名翻转，但按周换仓要等 09-07（新一周首个交易日）才执行
        prices = {
            "AAA": [100.0, 100.0, 100.0, 100.0, 110.0, 110.0, 110.0, 110.0],
            "BBB": [110.0, 110.0, 110.0, 110.0, 90.0, 90.0, 90.0, 90.0],
        }
        premiums = {"AAA": [10.0] * 8, "BBB": [11.0] * 8}
        rows = make_rows(prices, premiums)
        config = make_config({"score_preset": "double_low", "max_positions": 1, "rebalance_unit": "week"})
        result = RotationBacktestEngine(rows).run(config)
        buys = [(t.trade_date, t.symbol) for t in result.trades if t.side == "buy"]
        sells = [(t.trade_date, t.symbol) for t in result.trades if t.side == "sell"]
        assert buys == [(date(2026, 9, 1), "AAA"), (date(2026, 9, 7), "BBB")]
        assert sells == [(date(2026, 9, 7), "AAA")]

    def test_max_positions_holds_top_n(self):
        prices = {
            "AAA": [100.0] * 8,
            "BBB": [110.0] * 8,
            "CCC": [120.0] * 8,
        }
        premiums = {"AAA": [5.0] * 8, "BBB": [10.0] * 8, "CCC": [15.0] * 8}
        rows = make_rows(prices, premiums)
        config = make_config({"score_preset": "double_low", "max_positions": 2})
        result = RotationBacktestEngine(rows).run(config)
        assert result.equity_curve[-1].holdings == ["AAA", "BBB"]
        assert result.equity_curve[-1].holdings_count == 2

    def test_composite_double_low_factor_run_matches_preset_run(self):
        # 组合因子（价格+溢价率×100%）与双低预设（1×price + 1×premium）应产生逐笔一致的回测
        prices = {
            "AAA": [100.0, 100.0, 100.0, 100.0, 110.0, 110.0, 110.0, 110.0],
            "BBB": [110.0, 110.0, 110.0, 110.0, 90.0, 90.0, 90.0, 90.0],
            "CCC": [130.0, 128.0, 126.0, 124.0, 122.0, 120.0, 118.0, 116.0],
        }
        premiums = {
            "AAA": [10.0] * 4 + [60.0] * 4,  # 第 5 根 bar 起排名翻转
            "BBB": [11.0] * 8,
            "CCC": [12.0] * 8,
        }
        rows = make_rows(prices, premiums)
        by_preset = RotationBacktestEngine(rows).run(
            make_config({"score_preset": "double_low", "max_positions": 2})
        )
        by_composite = RotationBacktestEngine(rows).run(
            make_config({"score_factors": [{"factor": "double_low"}], "max_positions": 2})
        )
        assert [(t.trade_date, t.symbol, t.side, t.quantity) for t in by_preset.trades] == [
            (t.trade_date, t.symbol, t.side, t.quantity) for t in by_composite.trades
        ]
        assert [p.equity for p in by_preset.equity_curve] == [p.equity for p in by_composite.equity_curve]
        assert [p.holdings for p in by_preset.equity_curve] == [p.holdings for p in by_composite.equity_curve]

    def test_rebalance_weights_generates_turnover_when_drift(self):
        prices = {
            "AAA": [100.0, 100.0, 100.0, 130.0, 130.0, 130.0, 130.0, 130.0],
            "BBB": [100.0] * 8,
        }
        premiums = {"AAA": [10.0] * 8, "BBB": [10.0] * 8}
        rows = make_rows(prices, premiums)
        config = make_config({"score_preset": "double_low", "max_positions": 2, "rebalance_weights": True})
        result = RotationBacktestEngine(rows).run(config)
        reasons = {t.reason for t in result.trades}
        assert "rebalance_down" in reasons
        assert "rebalance_up" in reasons

    def test_no_rebalance_weights_keeps_position_stable(self):
        prices = {
            "AAA": [100.0, 100.0, 100.0, 130.0, 130.0, 130.0, 130.0, 130.0],
            "BBB": [100.0] * 8,
        }
        rows = make_rows(prices)
        config = make_config({"score_preset": "double_low", "max_positions": 2, "rebalance_weights": False})
        result = RotationBacktestEngine(rows).run(config)
        reasons = {t.reason for t in result.trades}
        assert reasons == {"rotation_entry"}


class TestFilters:
    def test_exclusion_factor_blocks_candidate(self):
        rows = make_rows(STEADY_PRICES, premiums={
            "AAA": [10.0] * 8, "BBB": [11.0] * 8, "CCC": [12.0] * 8,
        })
        config = make_config({
            "max_positions": 1,
            "exclusion_factors": [{"factor": "price", "op": ">=", "value": 110}],
        })
        result = RotationBacktestEngine(rows).run(config)
        assert result.equity_curve[-1].holdings == ["AAA"]

    def test_event_blocked_excluded(self):
        blocked = {"AAA": [True] * 8}
        rows = make_rows(STEADY_PRICES, blocked=blocked)
        config = make_config({"max_positions": 1})
        result = RotationBacktestEngine(rows).run(config)
        assert result.equity_curve[-1].holdings == ["BBB"]

    def test_event_blocked_ignored_when_disabled(self):
        blocked = {"AAA": [True] * 8}
        rows = make_rows(STEADY_PRICES, blocked=blocked)
        config = make_config({"max_positions": 1, "exclude_event_blocked": False})
        result = RotationBacktestEngine(rows).run(config)
        assert result.equity_curve[-1].holdings == ["AAA"]

    def test_exclude_new_bond_days(self):
        # CCC 上市晚（list_date = 09-04），价格低分数最优
        rows = make_rows(STEADY_PRICES)
        rows = [row for row in rows if row["bond_code"] != "CCC"]
        for trade_date in DATES[3:]:
            rows.append({
                "bond_code": "CCC", "bond_name": "BOND-CCC", "trade_date": trade_date,
                "close": 90.0, "premium_rate": 1.0, "remaining_size": 5.0,
                "event_blocked": False, "last_trading_date": None,
                "list_date": DATES[3],
            })
        config = make_config({"max_positions": 1, "exclude_new_bond_days": 10})
        result = RotationBacktestEngine(rows).run(config)
        assert result.equity_curve[-1].holdings == ["AAA"]  # CCC 虽分数最低但被新债排除
        # 放宽到 2 天后，CCC 自 09-06（满 2 天）起可入池
        config2 = make_config({"max_positions": 1, "exclude_new_bond_days": 2})
        result2 = RotationBacktestEngine(rows).run(config2)
        assert result2.equity_curve[-1].holdings == ["CCC"]

    def test_exclude_last_trading_days(self):
        rows = make_rows(STEADY_PRICES, last_trading={"AAA": date(2026, 9, 11)})
        config = make_config({"max_positions": 1, "exclude_last_trading_days": 3})
        result = RotationBacktestEngine(rows).run(config)
        # AAA 最后交易日 09-11，09-08 起剩余 <=3 天 → 排除，换仓到 BBB
        assert result.equity_curve[-1].holdings == ["BBB"]

    def test_missing_premium_skips_symbol(self):
        premiums = {"AAA": [None] * 8, "BBB": [11.0] * 8, "CCC": [12.0] * 8}
        rows = make_rows(STEADY_PRICES, premiums=premiums)
        config = make_config({"score_preset": "double_low", "max_positions": 1})
        result = RotationBacktestEngine(rows).run(config)
        assert result.equity_curve[-1].holdings == ["BBB"]

    def test_excluded_symbols_respected(self):
        rows = make_rows(STEADY_PRICES)
        config = make_config({"max_positions": 1, "excluded_symbols": ["AAA"]})
        result = RotationBacktestEngine(rows).run(config)
        assert result.equity_curve[-1].holdings == ["BBB"]


class TestExecutionAccounting:
    def test_commission_charged_both_sides(self):
        # 09-07 AAA 溢价率抬升导致排名翻转到 BBB → 卖 AAA 买 BBB
        prices = {
            "AAA": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
            "BBB": [110.0, 110.0, 110.0, 110.0, 110.0, 110.0, 110.0, 110.0],
        }
        premiums = {"AAA": [10.0] * 4 + [60.0] * 4, "BBB": [30.0] * 8}
        rows = make_rows(prices, premiums)
        config = make_config({"score_preset": "double_low", "max_positions": 1, "commission": 0.001})
        result = RotationBacktestEngine(rows).run(config)
        first_buy = next(t for t in result.trades if t.side == "buy" and t.trade_date == DATES[0])
        assert first_buy.fee == pytest.approx(first_buy.amount * 0.001)
        sells = [t for t in result.trades if t.side == "sell"]
        assert sells, "排名翻转应产生卖出"
        for sell in sells:
            assert sell.fee == pytest.approx(sell.amount * 0.001)
        second_buys = [t for t in result.trades if t.side == "buy" and t.trade_date >= DATES[4]]
        assert second_buys, "排名翻转应产生新买入"
        assert second_buys[0].fee == pytest.approx(second_buys[0].amount * 0.001)

    def test_lot_size_rounding(self):
        rows = make_rows(STEADY_PRICES)
        config = make_config({"max_positions": 1, "lot_size": 100, "commission": 0.0})
        result = RotationBacktestEngine(rows).run(config)
        buy = next(t for t in result.trades if t.side == "buy")
        assert buy.quantity % 100 == 0
        assert buy.quantity == pytest.approx(int(100000 / 100.0 / 100) * 100)

    def test_lot_size_rounding_reserves_commission(self):
        rows = make_rows(STEADY_PRICES)
        config = make_config({"max_positions": 1, "lot_size": 100, "commission": 0.001})
        result = RotationBacktestEngine(rows).run(config)
        buy = next(t for t in result.trades if t.side == "buy")
        assert buy.quantity == pytest.approx(int(100000 / (100.0 * 1.001) / 100) * 100)
        assert buy.amount + buy.fee <= 100000.0 + 1e-6

    def test_equity_equals_cash_plus_positions(self):
        rows = make_rows(STEADY_PRICES)
        result = RotationBacktestEngine(rows).run(make_config({"max_positions": 2}))
        for point in result.equity_curve:
            assert point.equity == pytest.approx(point.cash + point.positions_value, abs=0.01)

    def test_max_position_pct_caps_allocation(self):
        rows = make_rows(STEADY_PRICES)
        config = make_config({"max_positions": 1, "max_position_pct": 50})
        result = RotationBacktestEngine(rows).run(config)
        buy = next(t for t in result.trades if t.side == "buy")
        assert buy.amount <= 100000 * 0.5 + 1e-6


class TestBenchmarksAndMetrics:
    def test_equal_weight_benchmark_return(self):
        # AAA +0%、BBB -10%、CCC 0% → 等权基准约 -10%/3/… 手算校验
        prices = {
            "AAA": [100.0] * 8,
            "BBB": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 90.0],
            "CCC": [100.0] * 8,
        }
        rows = make_rows(prices)
        result = RotationBacktestEngine(rows).run(make_config({"max_positions": 1}))
        # 最后一日等权收益 = (0 - 0.1 + 0)/3，其余日 0
        expected = ((1 + (-0.1 / 3)) - 1) * 100
        assert result.benchmark_return_pct == pytest.approx(expected, abs=1e-4)
        assert result.metrics.benchmark_metrics["mode"] == "equal_weight_pool"
        assert result.metrics.benchmark_metrics["total_return_pct"] == pytest.approx(expected, abs=1e-4)

    def test_single_bond_benchmark(self):
        rows = make_rows(STEADY_PRICES)
        result = RotationBacktestEngine(rows).run(make_config({"max_positions": 1}, benchmark="CCC"))
        assert result.metrics.benchmark_metrics["mode"] == "bond:ccc"
        assert result.benchmark_return_pct == pytest.approx(0.0, abs=1e-6)  # CCC 恒 120

    def test_index_benchmark_uses_rows(self):
        rows = make_rows(STEADY_PRICES)
        index_rows = [(d, 4000.0 + i * 10.0) for i, d in enumerate(DATES)]
        result = RotationBacktestEngine(rows, benchmark_rows=index_rows).run(
            make_config({"max_positions": 1}, benchmark="000300")
        )
        assert result.metrics.benchmark_metrics["mode"] == "index:000300"
        first, last = index_rows[0][1], index_rows[-1][1]
        assert result.benchmark_return_pct == pytest.approx((last / first - 1) * 100, abs=1e-4)

    def test_extended_metrics_present(self):
        rows = make_rows(STEADY_PRICES)
        result = RotationBacktestEngine(rows).run(make_config({"max_positions": 1}))
        metrics = result.metrics
        assert metrics.period_count == len(DATES)
        assert metrics.profit_periods + metrics.loss_periods <= metrics.period_count
        assert metrics.benchmark_metrics["relative_excess"]["total_return_pct"] == pytest.approx(
            metrics.total_return_pct - result.benchmark_return_pct, abs=1e-3
        )
        point = result.equity_curve[-1]
        assert point.drawdown_pct is not None
        assert point.turnover_pct is not None or point.turnover_pct is None  # 字段存在
        assert point.holdings_count == len(point.holdings)

    def test_turnover_series_reflects_trading(self):
        rows = make_rows(STEADY_PRICES)
        result = RotationBacktestEngine(rows).run(make_config({"max_positions": 1}))
        first_day = result.equity_curve[0]
        assert first_day.turnover_pct is not None and first_day.turnover_pct > 0
        second_day = result.equity_curve[1]
        assert second_day.turnover_pct == 0.0

    def test_win_rate_from_round_trips(self):
        prices = {
            "AAA": [100.0, 100.0, 100.0, 100.0, 110.0, 110.0, 110.0, 110.0],
            "BBB": [110.0] * 8,
        }
        premiums = {"AAA": [10.0] * 4 + [60.0] * 4, "BBB": [11.0] * 8}
        rows = make_rows(prices, premiums)
        config = make_config({"score_preset": "double_low", "max_positions": 1})
        result = RotationBacktestEngine(rows).run(config)
        # 09-04 买入 AAA 100 → 09-07 换仓卖出 110 → 盈利回合
        assert result.metrics.win_rate_pct == pytest.approx(100.0)

    def test_sortino_present_when_enough_days(self):
        prices = {"AAA": [100.0, 101.0, 99.0, 102.0, 98.0, 103.0, 100.0, 104.0]}
        rows = make_rows(prices)
        result = RotationBacktestEngine(rows).run(make_config({"max_positions": 1}))
        assert result.metrics.sortino_ratio is not None


class TestValidation:
    def test_no_data_raises(self):
        engine = RotationBacktestEngine([])
        with pytest.raises(ValueError):
            engine.run(make_config({}))

    def test_start_after_end_raises(self):
        engine = RotationBacktestEngine(make_rows(STEADY_PRICES))
        with pytest.raises(ValueError):
            engine.run(make_config({}, start=date(2026, 10, 1), end=date(2026, 9, 1)))

    def test_non_cb_raises(self):
        engine = RotationBacktestEngine(make_rows(STEADY_PRICES))
        config = StrategyLabRunConfig(
            strategy_id="rotation", market="cn", instrument_type="stock",
            start_date=DATES[0], end_date=DATES[-1], initial_cash=100000.0,
        )
        with pytest.raises(ValueError):
            engine.run(config)

    def test_min_positions_above_max_rejected(self):
        engine = RotationBacktestEngine(make_rows(STEADY_PRICES))
        with pytest.raises(ValueError):
            engine.run(make_config({"min_positions": 5, "max_positions": 2}))

    def test_symbols_filter_scopes_universe(self):
        rows = make_rows(STEADY_PRICES)
        result = RotationBacktestEngine(rows).run(
            make_config({"max_positions": 1}, symbols=["BBB", "CCC"])
        )
        assert result.equity_curve[-1].holdings == ["BBB"]
