# -*- coding: utf-8 -*-
"""Tests for the signal backtest engine (ETF time-series entry/exit rules)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.core.strategy_lab.models import StrategyLabRunConfig
from src.core.strategy_lab.signal_engine import SignalEngine
from src.core.strategy_lab.signal_params import SignalParams

START = date(2026, 1, 1)


def _dates(n: int) -> list:
    out = []
    current = START
    while len(out) < n:
        if current.weekday() < 5:
            out.append(current)
        current += timedelta(days=1)
    return out


def _rows(closes: list, *, symbol: str = "510880", dividend_yields=None) -> list:
    dates = _dates(len(closes))
    rows = []
    for index, close in enumerate(closes):
        row = {"symbol": symbol, "trade_date": dates[index], "close": float(close)}
        if dividend_yields is not None:
            row["dividend_yield"] = dividend_yields[index]
        rows.append(row)
    return rows


def _config(parameters=None, *, start=None, end=None, cash=100000.0, benchmark=None, symbols=None):
    return StrategyLabRunConfig(
        strategy_id="signal",
        market="cn",
        instrument_type="etf",
        start_date=start or dates_all[0],
        end_date=end or dates_all[-1],
        initial_cash=cash,
        benchmark_symbol=benchmark,
        symbols=symbols or ["510880"],
        parameters=parameters or {},
    )


# 60 根日线：MA 窗口 10。前 30 根 5.0 横盘；31-45 缓跌到 4.5（低于均线）；46-60 拉升到 5.8（涨超均线 7%）
dates_all = _dates(60)
Closes = [5.0] * 30 + [5.0 - 0.033 * i for i in range(1, 16)] + [4.5 + 0.087 * i for i in range(1, 16)]
ROWS = _rows(Closes)


class TestSignalSemantics:
    def test_entry_below_ma_exit_above_ma_threshold(self):
        # 默认规则：乖离率 < 0 买入，> 7 卖出
        result = SignalEngine(ROWS).run(_config({"ma_window": 10}))
        buys = [(t.trade_date, t.quantity) for t in result.trades if t.side == "buy"]
        sells = [(t.trade_date, t.quantity) for t in result.trades if t.side == "sell"]
        assert len(buys) == 1 and len(sells) == 1
        assert buys[0][1] == sells[0][1]  # 全仓进出
        assert sells[0][0] > buys[0][0]
        # 卖出价高于买入价（低买高卖）
        buy_price = next(t.price for t in result.trades if t.side == "buy")
        sell_price = next(t.price for t in result.trades if t.side == "sell")
        assert sell_price > buy_price
        assert result.metrics.win_rate_pct == 100.0

    def test_warmup_period_does_not_trigger(self):
        # 回测窗口从第 1 根开始（MA10 未就绪），信号必须等到第 10 根之后
        result = SignalEngine(ROWS).run(_config({"ma_window": 10}))
        first_buy = next(t for t in result.trades if t.side == "buy")
        ready_index = next(
            i for i, d in enumerate(dates_all)
            if d >= first_buy.trade_date
        )
        assert ready_index >= 10

    def test_missing_factor_blocks_entry(self):
        # dividend_yield 规则引用缺失数据（列恒 None）→ 永不买入
        result = SignalEngine(ROWS).run(_config({
            "ma_window": 10,
            "dividend_yield_min": 3.0,
        }))
        assert result.trades == []
        assert result.metrics.diagnostics["factor_coverage_pct"]["dividend_yield"] == 0.0
        assert result.final_equity == pytest.approx(100000.0)

    def test_dividend_filter_disabled_at_zero(self):
        # 0=关闭（与本仓库 stop_loss_pct 等口径一致）：默认买入规则不含股息率条件，
        # 无数据源也不堵死买入；正数阈值才激活过滤
        sp = SignalParams.from_parameters({"dividend_yield_min": 0})
        assert [r.factor for r in sp.entry_rules] == ["ma_gap_pct"]
        sp_active = SignalParams.from_parameters({"dividend_yield_min": 3.0})
        assert [r.factor for r in sp_active.entry_rules] == ["ma_gap_pct", "dividend_yield"]
        result = SignalEngine(ROWS).run(_config({"ma_window": 10, "dividend_yield_min": 0}))
        assert result.trades, "dividend_yield_min=0 不应堵死买入"

    def test_dividend_yield_rule_fires_when_data_present(self):
        # 数据落库后规则即刻生效：前半段股息率 2%（不满足），后半段 4%（满足）
        yields = [2.0] * 35 + [4.0] * 25
        rows = _rows(Closes, dividend_yields=yields)
        result = SignalEngine(rows).run(_config({
            "ma_window": 10,
            "dividend_yield_min": 3.0,
        }))
        first_buy = next((t for t in result.trades if t.side == "buy"), None)
        assert first_buy is not None
        assert first_buy.trade_date >= dates_all[35]
        assert result.metrics.diagnostics["factor_coverage_pct"]["dividend_yield"] == 100.0

    def test_explicit_rules_override_scalar_defaults(self):
        # 显式规则存在时，扁平标量 entry_gap_max 不参与规则构建（解析级断言）
        params = SignalParams.from_parameters({
            "entry_gap_max": 0.0,
            "entry_rules": [{"factor": "ma_gap_pct", "op": "<", "value": -5.0}],
            "exit_rules": [{"factor": "ma_gap_pct", "op": ">", "value": 9.0}],
        })
        assert [r.to_dict() for r in params.entry_rules] == [{"factor": "ma_gap_pct", "op": "<", "value": -5.0}]
        assert [r.to_dict() for r in params.exit_rules] == [{"factor": "ma_gap_pct", "op": ">", "value": 9.0}]
        # 引擎级：更严的入场阈值（<-2，数据最低乖离 -3.19%）晚于默认（<0）触发
        by_scalar = SignalEngine(ROWS).run(_config({"ma_window": 10, "entry_gap_max": -2.0}))
        by_default = SignalEngine(ROWS).run(_config({"ma_window": 10}))
        strict_buy = next((t for t in by_scalar.trades if t.side == "buy"), None)
        default_buy = next(t for t in by_default.trades if t.side == "buy")
        assert strict_buy is not None and strict_buy.trade_date > default_buy.trade_date

    def test_scalar_and_default_rules_equivalent(self):
        # 扁平标量（缺省值）与显式默认规则构建逐笔一致（双轨参数等价性）
        by_scalar = SignalEngine(ROWS).run(_config({"ma_window": 10}))
        by_rules = SignalEngine(ROWS).run(_config({
            "ma_window": 10,
            "entry_rules": [{"factor": "ma_gap_pct", "op": "<", "value": 0.0}],
            "exit_rules": [{"factor": "ma_gap_pct", "op": ">", "value": 7.0}],
        }))
        assert [(t.trade_date, t.side, t.quantity) for t in by_scalar.trades] == [
            (t.trade_date, t.side, t.quantity) for t in by_rules.trades
        ]
        assert [p.equity for p in by_scalar.equity_curve] == [p.equity for p in by_rules.equity_curve]

    def test_multi_symbol_independent_state_machines(self):
        # 两个标的节奏不同：AAA（510880）正常进出；BBB 先跌破均线买入、
        # 温和反弹不触及 +7% 出场阈值，持有到期末
        rows_a = _rows(Closes, symbol="510880")
        bbb = [4.0] * 20 + [4.0 - 0.02 * i for i in range(1, 21)] + [3.6 + 0.04 * i for i in range(1, 21)]
        rows_b = _rows(bbb, symbol="159915")
        result = SignalEngine(rows_a + rows_b).run(_config(
            {"ma_window": 10}, symbols=["510880", "159915"], benchmark=None,
        ))
        assert {t.symbol for t in result.trades if t.side == "sell"} == {"510880"}
        assert result.equity_curve[-1].holdings == ["159915"]
        # 预算 = 权益/2（两个标的）
        buy = next(t for t in result.trades if t.side == "buy" and t.symbol == "510880")
        assert buy.amount <= 100000 / 2 + 1e-6


class TestExecutionAccounting:
    def test_commission_charged_both_sides(self):
        result = SignalEngine(ROWS).run(_config({"ma_window": 10, "commission": 0.001}))
        buy = next(t for t in result.trades if t.side == "buy")
        sell = next(t for t in result.trades if t.side == "sell")
        assert buy.fee == pytest.approx(buy.amount * 0.001)
        assert sell.fee == pytest.approx(sell.amount * 0.001)

    def test_lot_size_rounding_reserves_commission(self):
        result = SignalEngine(ROWS).run(_config({"ma_window": 10, "lot_size": 1000, "commission": 0.001}))
        buy = next(t for t in result.trades if t.side == "buy")
        assert buy.quantity % 1000 == 0
        assert buy.amount * 1.001 <= 100000.0 + 1e-6

    def test_max_position_pct_caps_allocation(self):
        result = SignalEngine(ROWS).run(_config({"ma_window": 10, "max_position_pct": 50}))
        buy = next(t for t in result.trades if t.side == "buy")
        assert buy.amount <= 50000 + 1e-6

    def test_equity_equals_cash_plus_positions(self):
        result = SignalEngine(ROWS).run(_config({"ma_window": 10}))
        for point in result.equity_curve:
            assert point.equity == pytest.approx(point.cash + point.positions_value, abs=0.01)

    def test_open_position_marked_to_market_at_end(self):
        # 期末不强平（与轮动引擎一致）：持仓按最后市值计入 final_equity
        result = SignalEngine(ROWS).run(_config({
            "ma_window": 10,
            "exit_rules": [{"factor": "ma_gap_pct", "op": ">", "value": 50.0}],  # 永不触发出场
        }))
        assert [t for t in result.trades if t.side == "sell"] == []
        assert result.final_equity == pytest.approx(result.equity_curve[-1].equity)
        assert result.metrics.diagnostics["final_holdings"] == ["510880"]


class TestBenchmarksAndMetrics:
    def test_symbol_benchmark_is_buy_and_hold(self):
        # 指定标的自身做基准 = 买入持有收益
        result = SignalEngine(ROWS).run(_config({"ma_window": 10}, benchmark="510880"))
        assert result.metrics.benchmark_metrics["mode"] == "symbol:510880"
        first, last = Closes[0], Closes[-1]
        assert result.benchmark_return_pct == pytest.approx((last / first - 1) * 100, abs=1e-4)

    def test_index_benchmark_uses_rows(self):
        index_rows = [(d, 4000.0 + i * 10.0) for i, d in enumerate(dates_all)]
        result = SignalEngine(ROWS, benchmark_rows=index_rows).run(
            _config({"ma_window": 10}, benchmark="000300")
        )
        assert result.metrics.benchmark_metrics["mode"] == "index:000300"
        expected = (index_rows[-1][1] / index_rows[0][1] - 1) * 100
        assert result.benchmark_return_pct == pytest.approx(expected, abs=1e-4)

    def test_buy_hold_benchmark_fixed_shares(self):
        # 长持 = 期初等权买入固定份额：AAA 100→50→100、BBB 恒 100，
        # 各投 50 → day2 市值 75、day3 回到 100（收益 0%）；
        # 每日再平衡等权池同期会到 112.5（+12.5%），两者语义不同
        rows = _rows([100.0, 50.0, 100.0], symbol="510880") + _rows([100.0] * 3, symbol="159915")
        config = _config(
            {"ma_window": 10},  # 无预热数据 → 无信号成交，纯基准对比
            start=dates_all[0], end=dates_all[2],
            symbols=["510880", "159915"], benchmark="buy_hold",
        )
        result = SignalEngine(rows).run(config)
        assert result.metrics.benchmark_metrics["mode"] == "buy_hold"
        assert result.benchmark_return_pct == pytest.approx(0.0, abs=1e-4)

        equal_weight = SignalEngine(rows).run(_config(
            {"ma_window": 10}, start=dates_all[0], end=dates_all[2],
            symbols=["510880", "159915"], benchmark=None,
        ))
        assert equal_weight.benchmark_return_pct == pytest.approx(12.5, abs=1e-4)

    def test_buy_hold_single_symbol_equals_own_return(self):
        # 单标的长持 = 自身区间收益
        result = SignalEngine(ROWS).run(_config({"ma_window": 10}, benchmark="buy_hold"))
        assert result.metrics.benchmark_metrics["mode"] == "buy_hold"
        expected = (Closes[-1] / Closes[0] - 1) * 100
        assert result.benchmark_return_pct == pytest.approx(expected, abs=1e-4)

    def test_equal_weight_benchmark_default(self):
        result = SignalEngine(ROWS).run(_config({"ma_window": 10}))
        assert result.metrics.benchmark_metrics["mode"] == "equal_weight_pool"
        # 单标的等权基准 = 自身买入持有
        expected = (Closes[-1] / Closes[0] - 1) * 100
        assert result.benchmark_return_pct == pytest.approx(expected, abs=1e-4)

    def test_extended_metrics_present(self):
        result = SignalEngine(ROWS).run(_config({"ma_window": 10}))
        metrics = result.metrics
        assert metrics.period_count == len(dates_all)
        point = result.equity_curve[-1]
        assert point.drawdown_pct is not None
        assert point.turnover_pct is not None or point.turnover_pct is None  # 字段存在
        assert point.holdings_count == len(point.holdings)
        assert metrics.turnover_avg_pct is not None


class TestValidation:
    def test_no_data_raises(self):
        engine = SignalEngine([])
        with pytest.raises(ValueError):
            engine.run(_config({}))

    def test_start_after_end_raises(self):
        engine = SignalEngine(ROWS)
        with pytest.raises(ValueError):
            engine.run(_config({}, start=date(2026, 12, 1), end=date(2026, 1, 1)))

    def test_unknown_rule_factor_rejected(self):
        with pytest.raises(ValueError):
            SignalParams.from_parameters({
                "entry_rules": [{"factor": "no_such_factor", "op": "<", "value": 1}],
            })

    def test_bad_comparator_rejected(self):
        with pytest.raises(ValueError):
            SignalParams.from_parameters({
                "entry_rules": [{"factor": "ma_gap_pct", "op": "~", "value": 1}],
                "exit_rules": [{"factor": "ma_gap_pct", "op": ">", "value": 7}],
            })

    def test_empty_explicit_rules_fall_back_to_defaults(self):
        params = SignalParams.from_parameters({"entry_rules": [], "exit_rules": []})
        assert params.entry_rules and params.exit_rules

    def test_ma_window_minimum_enforced(self):
        with pytest.raises(ValueError):
            SignalParams.from_parameters({"ma_window": 1})


class TestMacdStrategy:
    """MACD 因子与金叉/死叉信号策略。"""

    MACD = {"macd_fast": 5, "macd_slow": 12, "macd_signal": 4}

    # 横盘 → 深跌 → 拉升 → 回落：产生一次金叉与一次死叉
    WAVE = (
        [5.0] * 10
        + [5.0 - 0.05 * i for i in range(1, 21)]
        + [4.0 + 0.09 * i for i in range(1, 26)]
        + [6.25 - 0.07 * i for i in range(1, 26)]
    )

    @classmethod
    def _crosses(cls, closes):
        from src.core.strategy_lab.series_factors import SERIES_FACTOR_REGISTRY
        rows = _rows(closes)
        spec = SERIES_FACTOR_REGISTRY["macd_cross"]
        return spec.compute(rows, dict(cls.MACD))

    def test_cross_detection_golden_then_dead(self):
        cross = self._crosses(self.WAVE)
        events = [(i, v) for i, v in enumerate(cross) if v not in (None, 0.0)]
        assert events, "波浪序列应产生交叉事件"
        golden = next((i, v) for i, v in events if v == 1.0)
        dead = next((i, v) for i, v in events if v == -1.0 and i > golden[0])
        assert dead[0] > golden[0]
        # 预热期（slow+signal 根内）无值，非事件日为 0
        warm = self.MACD["macd_slow"] + self.MACD["macd_signal"]
        assert all(v is None for v in cross[:warm])
        assert set(v for i, v in enumerate(cross) if i >= warm) <= {0.0, 1.0, -1.0}

    def test_default_macd_rules_roundtrip(self):
        # default_rules=macd_cross 构建金叉买/死叉卖；买入日=金叉日、卖出日=死叉日
        wave_dates = _dates(len(self.WAVE))
        result = SignalEngine(_rows(self.WAVE)).run(_config({
            "default_rules": "macd_cross", **self.MACD,
        }))
        cross = self._crosses(self.WAVE)
        golden_idx = next(i for i, v in enumerate(cross) if v == 1.0)
        dead_idx = next(i for i, v in enumerate(cross) if v == -1.0 and i > golden_idx)
        golden_day, dead_day = wave_dates[golden_idx], wave_dates[dead_idx]
        buy = next(t for t in result.trades if t.side == "buy")
        sell = next(t for t in result.trades if t.side == "sell")
        assert buy.trade_date == golden_day
        assert sell.trade_date == dead_day
        assert sell.trade_date > buy.trade_date
        assert result.metrics.win_rate_pct == 100.0

    def test_zero_line_filter_blocks_below_zero_golden_cross(self):
        # 长跌后小幅反弹：金叉发生在零轴下方，DIF>0 过滤应阻止入场
        drift = [6.0 - 0.06 * i for i in range(0, 45)] + [3.3 + 0.03 * i for i in range(1, 16)]
        cross = self._crosses(drift)
        assert any(v == 1.0 for v in cross), "构造序列应存在金叉"
        result = SignalEngine(_rows(drift)).run(_config({
            **self.MACD,
            "entry_rules": [
                {"factor": "macd_cross", "op": ">", "value": 0},
                {"factor": "macd_dif", "op": ">", "value": 0},
            ],
            "exit_rules": [{"factor": "macd_cross", "op": "<", "value": 0}],
        }))
        assert result.trades == []
        assert result.final_equity == pytest.approx(100000.0)

    def test_macd_default_vs_explicit_rules_equivalent(self):
        by_default = SignalEngine(_rows(self.WAVE)).run(_config({
            "default_rules": "macd_cross", **self.MACD,
        }))
        by_explicit = SignalEngine(_rows(self.WAVE)).run(_config({
            **self.MACD,
            "entry_rules": [{"factor": "macd_cross", "op": ">", "value": 0.0}],
            "exit_rules": [{"factor": "macd_cross", "op": "<", "value": 0.0}],
        }))
        assert [(t.trade_date, t.side, t.quantity) for t in by_default.trades] == [
            (t.trade_date, t.side, t.quantity) for t in by_explicit.trades
        ]
        assert [p.equity for p in by_default.equity_curve] == [p.equity for p in by_explicit.equity_curve]

    def test_macd_param_validation(self):
        with pytest.raises(ValueError, match="macd_slow"):
            SignalParams.from_parameters({"macd_fast": 26, "macd_slow": 12})
        with pytest.raises(ValueError):
            SignalParams.from_parameters({"macd_fast": 1})

    def test_warmup_covers_macd_ema_convergence(self):
        params = SignalParams.from_parameters({
            "ma_window": 10, "roc_window": 5, "macd_slow": 120, "macd_signal": 40,
        })
        assert params.warmup_trading_days() == (120 + 40) * 2
        assert SignalParams.from_parameters({}).warmup_trading_days() == 182
