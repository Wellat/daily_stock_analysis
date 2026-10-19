# -*- coding: utf-8 -*-
"""ETF 信号实盘通知服务测试：信号评估三态、hfq 折算、状态机、飞书通知与幂等。"""

import json
from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.services.etf_signal_notify_service import EtfSignalNotifyService
from src.services.live_strategy_service import LiveStrategyService
from src.storage import (DatabaseManager, LiveStrategyConfig, LiveStrategyRun,
                         StrategyDecisionRecord, StrategyLabEtfDaily)

TRADE_DATE = date(2026, 10, 15)  # 周四


class _Quote:
    def __init__(self, price, pre_close, change_pct=0.0, name="红利ETF"):
        self.price, self.pre_close, self.change_pct, self.name = price, pre_close, change_pct, name


class _Fetcher:
    def __init__(self, quote):
        self.quote = quote

    def get_realtime_quote(self, code, log_final_failure=True):
        return self.quote


class _Notifier:
    def __init__(self):
        self.sent: list[str] = []
        self.ok = True

    def send_to_feishu(self, content, timeout_seconds=None):
        if not self.ok:
            return False
        self.sent.append(content)
        return True


def _weekdays_before(anchor: date, count: int) -> list[date]:
    days, cur = [], anchor - timedelta(days=1)
    while len(days) < count:
        if cur.weekday() < 5:
            days.append(cur)
        cur -= timedelta(days=1)
    return list(reversed(days))


def _seed_etf_history(db: DatabaseManager, symbol: str, closes: list[float], dates: list[date]) -> None:
    with db.get_session() as session:
        for close, day in zip(closes, dates):
            session.add(StrategyLabEtfDaily(symbol=symbol, trade_date=day, close=close, source="test"))
        session.commit()


def _make_notify_config(db: DatabaseManager, parameters: dict | None = None, strategy_id: str = "signal") -> int:
    service = LiveStrategyService(db)
    payload = service.create_instance({
        "name": "ETF信号通知", "strategy_id": strategy_id, "enabled": True, "symbols": ["510880"],
        "parameters": parameters if parameters is not None else
        {"ma_window": 5, "roc_window": 3, "entry_gap_max": 0, "exit_gap_min": 7},
        "schedule_time": "14:25",
    })
    return payload["id"]


@pytest.fixture()
def notify_env():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def test_notify_evaluates_entry_signal_and_sends_feishu(notify_env):
    """未持仓 + 买入规则成立（乖离率<0）→ 买入信号；消息含现价/因子/结论。"""
    dates = _weekdays_before(TRADE_DATE, 75)  # 预热恒含 MACD 估计 (26+9)*2=70 根
    _seed_etf_history(notify_env, "510880", [100.0] * 75, dates)
    config_id = _make_notify_config(notify_env)

    service = EtfSignalNotifyService(notify_env)
    service.notifier = _Notifier()
    service._fetcher = _Fetcher(_Quote(price=99.0, pre_close=100.0, change_pct=-1.0))

    result = service.run(config_id=config_id, trade_date=TRADE_DATE)

    assert result["status"] == "completed" and result["feishu_sent"] is True
    signals = {s["symbol"]: s for s in result["signals"]}
    assert signals["510880"]["signal"] == "buy"
    assert signals["510880"]["reason"] == "signal_entry"
    # hfq 折算：库内昨收 100 × 99/100 = 99.0，乖离率 (99/99.8-1) ≈ -0.8%
    assert signals["510880"]["hfq_scaled"] is True
    assert signals["510880"]["factors"]["ma_gap_pct"] == pytest.approx(-0.8016, abs=1e-3)
    assert "买入信号" in result["message"] and "未持仓" in result["message"]
    assert len(service.notifier.sent) == 1
    # 决策落库（买入推进状态机）
    with notify_env.get_session() as session:
        rows = session.execute(select(StrategyDecisionRecord).where(
            StrategyDecisionRecord.live_run_id == result["run_id"])).scalars().all()
    assert {(r.symbol, r.action, r.reason) for r in rows} == {("510880", "buy", "signal_entry")}


def test_notify_exit_signal_uses_decision_history_state(notify_env):
    """持仓（历史买入决策）+ 卖出规则成立（乖离率>7）→ 卖出信号。"""
    dates = _weekdays_before(TRADE_DATE, 75)  # 预热恒含 MACD 估计 (26+9)*2=70 根
    _seed_etf_history(notify_env, "510880", [100.0] * 75, dates)
    config_id = _make_notify_config(notify_env)

    # 预置更早一次成功通知 run + 买入决策 → 状态机视为持仓
    with notify_env.get_session() as session:
        prior = LiveStrategyRun(run_uid=uuid4().hex, config_id=config_id, qmt_account="-",
                                trade_date=TRADE_DATE - timedelta(days=5), status="completed", mode="notify")
        session.add(prior)
        session.flush()
        decision = StrategyDecisionRecord(decision_uid=uuid4().hex, strategy_id="signal", mode="notify",
                                          trade_date=TRADE_DATE - timedelta(days=5), action="buy",
                                          symbol="510880", live_run_id=prior.id)
        session.add(decision)
        session.commit()

    service = EtfSignalNotifyService(notify_env)
    service.notifier = _Notifier()
    # 现价 110 / 昨收 100 → 合成后复权 bar 110，ma5=102，乖离率 ≈ 7.84% > 7
    service._fetcher = _Fetcher(_Quote(price=110.0, pre_close=100.0, change_pct=10.0))

    result = service.run(config_id=config_id, trade_date=TRADE_DATE)
    signals = {s["symbol"]: s for s in result["signals"]}
    assert signals["510880"]["signal"] == "sell"
    assert signals["510880"]["reason"] == "signal_exit"
    assert signals["510880"]["holding"] is True
    assert "卖出信号" in result["message"] and "持仓中" in result["message"]


def test_notify_no_signal_when_rules_not_met(notify_env):
    """未持仓 + 买入规则不成立 → 无信号（hold 决策）。"""
    dates = _weekdays_before(TRADE_DATE, 75)  # 预热恒含 MACD 估计 (26+9)*2=70 根
    _seed_etf_history(notify_env, "510880", [100.0] * 75, dates)
    config_id = _make_notify_config(notify_env)

    service = EtfSignalNotifyService(notify_env)
    service.notifier = _Notifier()
    # 现价 101：乖离率 +0.2%，不满足 <0 买入，也不满足 >7 卖出
    service._fetcher = _Fetcher(_Quote(price=101.0, pre_close=100.0, change_pct=1.0))

    result = service.run(config_id=config_id, trade_date=TRADE_DATE)
    signals = {s["symbol"]: s for s in result["signals"]}
    assert signals["510880"]["signal"] is None
    assert signals["510880"]["reason"] == "no_signal"
    assert "无信号" in result["message"]
    with notify_env.get_session() as session:
        rows = session.execute(select(StrategyDecisionRecord).where(
            StrategyDecisionRecord.live_run_id == result["run_id"])).scalars().all()
    assert {(r.action, r.reason) for r in rows} == {("hold", "no_signal")}


def test_notify_marks_quote_unavailable_and_insufficient_history(notify_env):
    """行情不可用/历史不足的标的跳过评估但消息照发。"""
    config_id = _make_notify_config(notify_env)

    service = EtfSignalNotifyService(notify_env)
    service.notifier = _Notifier()
    service._fetcher = _Fetcher(None)  # 行情失败；且无历史行

    result = service.run(config_id=config_id, trade_date=TRADE_DATE)
    assert result["feishu_sent"] is True
    assert "行情不可用" in result["message"]

    # 有行情但历史不足
    _seed_etf_history(notify_env, "510880", [100.0] * 3, _weekdays_before(TRADE_DATE, 3))
    service2 = EtfSignalNotifyService(notify_env)
    service2.notifier = _Notifier()
    service2._fetcher = _Fetcher(_Quote(price=99.0, pre_close=100.0))
    # 换一天触发（当日已 completed 的 run 幂等返回）
    result2 = service2.run(config_id=config_id, trade_date=TRADE_DATE + timedelta(days=1))
    assert "历史数据不足" in result2["message"]


def test_notify_idempotent_same_day_and_retry_after_feishu_failure(notify_env):
    """当日成功重跑幂等；发送失败不落决策，修复后重试复用 run 行并补记。"""
    dates = _weekdays_before(TRADE_DATE, 75)  # 预热恒含 MACD 估计 (26+9)*2=70 根
    _seed_etf_history(notify_env, "510880", [100.0] * 75, dates)
    config_id = _make_notify_config(notify_env)

    service = EtfSignalNotifyService(notify_env)
    notifier = _Notifier()
    notifier.ok = False  # 先失败
    service.notifier = notifier
    service._fetcher = _Fetcher(_Quote(price=99.0, pre_close=100.0))

    failed = service.run(config_id=config_id, trade_date=TRADE_DATE)
    assert failed["status"] == "failed" and failed["feishu_sent"] is False

    notifier.ok = True
    retried = service.run(config_id=config_id, trade_date=TRADE_DATE)
    assert retried["status"] == "completed" and retried["feishu_sent"] is True
    assert retried["run_id"] == failed["run_id"]  # 复用当日 run 行

    again = service.run(config_id=config_id, trade_date=TRADE_DATE)
    assert again["run_id"] == retried["run_id"]
    assert len(notifier.sent) == 1  # 幂等：成功后不再重发

    with notify_env.get_session() as session:
        runs = session.execute(select(LiveStrategyRun).where(LiveStrategyRun.config_id == config_id)).scalars().all()
    assert len(runs) == 1  # 当日仅一行


def test_notify_signal_macd_injects_default_rules(notify_env):
    """signal_macd 配置自动注入 macd_cross 默认规则（金叉买/死叉卖）。"""
    with notify_env.get_session() as session:
        config = LiveStrategyConfig(name="notify-signal_macd", qmt_account="-", strategy_id="signal_macd",
                                    enabled=True, symbols_json='["510880"]',
                                    parameters_json='{"macd_fast": 3, "macd_slow": 6, "macd_signal": 3}')
        session.add(config)
        session.commit()
        config_id = config.id

    sp = EtfSignalNotifyService._signal_params(config)
    assert sp.entry_rules[0].factor == "macd_cross" and sp.entry_rules[0].op == ">"
    assert sp.exit_rules[0].factor == "macd_cross" and sp.exit_rules[0].op == "<"


def test_instance_explicit_rules_survive_into_signal_params(notify_env):
    """实验室「保存为策略实例」的显式规则经 create_instance 落库后被通知评估原样消费。"""
    config_id = _make_notify_config(notify_env, parameters={
        "ma_window": 210,
        "entry_rules": [{"factor": "ma_gap_pct", "op": "<", "value": -1}],
        "exit_rules": [{"factor": "ma_gap_pct", "op": ">", "value": 5}],
    })
    with notify_env.get_session() as session:
        config = session.get(LiveStrategyConfig, config_id)
    sp = EtfSignalNotifyService._signal_params(config)
    assert [(r.factor, r.op, r.value) for r in sp.entry_rules] == [("ma_gap_pct", "<", -1.0)]
    assert [(r.factor, r.op, r.value) for r in sp.exit_rules] == [("ma_gap_pct", ">", 5.0)]
    # 显式规则优先生效，扁平键 entry_gap_max/exit_gap_min 不再构默认规则
    assert sp.ma_window == 210


def test_dividend_zero_does_not_block_notify_entry(notify_env):
    """dividend_yield_min=0=关闭：管理页按模板默认创建的信号实例（带 0 值）
    买入信号不再被无数据源的股息率条件永久堵死。"""
    dates = _weekdays_before(TRADE_DATE, 75)
    _seed_etf_history(notify_env, "510880", [100.0] * 75, dates)
    config_id = _make_notify_config(notify_env, parameters={
        "ma_window": 5, "roc_window": 3, "entry_gap_max": 0, "exit_gap_min": 7,
        "dividend_yield_min": 0,
    })

    service = EtfSignalNotifyService(notify_env)
    service.notifier = _Notifier()
    service._fetcher = _Fetcher(_Quote(price=99.0, pre_close=100.0, change_pct=-1.0))

    result = service.run(config_id=config_id, trade_date=TRADE_DATE)
    signals = {s["symbol"]: s for s in result["signals"]}
    assert signals["510880"]["signal"] == "buy"


def test_notify_message_lists_only_effective_params(notify_env):
    """通知「参数」行只显示当前规则实际引用的参数，不再罗列 macd_* / roc_window 等无关字段。"""
    with notify_env.get_session() as session:
        cfg_signal = LiveStrategyConfig(name="均线信号", qmt_account="-", strategy_id="signal", enabled=True,
                                        symbols_json='["510880"]',
                                        parameters_json='{"ma_window": 210, "roc_window": 20}')
        cfg_macd = LiveStrategyConfig(name="MACD信号", qmt_account="-", strategy_id="signal_macd", enabled=True,
                                      symbols_json='["510880"]',
                                      parameters_json='{"macd_fast": 12, "macd_slow": 34, "macd_signal": 5}')
        session.add_all([cfg_signal, cfg_macd])
        session.commit()
        signal_id, macd_id = cfg_signal.id, cfg_macd.id
    service = EtfSignalNotifyService(notify_env)
    with notify_env.get_session() as session:
        cfg_signal = session.get(LiveStrategyConfig, signal_id)
        cfg_macd = session.get(LiveStrategyConfig, macd_id)
        msg_signal = service._build_message(cfg_signal, EtfSignalNotifyService._signal_params(cfg_signal), TRADE_DATE, [])
        msg_macd = service._build_message(cfg_macd, EtfSignalNotifyService._signal_params(cfg_macd), TRADE_DATE, [])

    assert "ma_window=210" in msg_signal
    assert "macd_slow" not in msg_signal and "roc_window" not in msg_signal
    assert "macd_slow=34" in msg_macd and "macd_signal=5" in msg_macd
    assert "ma_window" not in msg_macd


def test_instance_validations_and_order_semantics(notify_env):
    """实例校验 + order 语义：get_config 不被 notify 行抢占。"""
    service = LiveStrategyService(notify_env)
    # 先建 order 实例（save_config 兼容层）
    service.save_config({"qmt_account": "testS", "enabled": True, "strategy_id": "low-premium",
                         "parameters": {"max_positions": 1}})
    # notify 实例空标的拒绝
    with pytest.raises(ValueError, match="symbol"):
        service.create_instance({"name": "n", "strategy_id": "signal", "symbols": [], "parameters": {}})
    # 未知参数键拒绝
    with pytest.raises(ValueError, match="unknown strategy parameters"):
        service.create_instance({"name": "n", "strategy_id": "signal", "symbols": ["510880"], "parameters": {"foo": 1}})
    # 实例名必填
    with pytest.raises(ValueError, match="name is required"):
        service.create_instance({"strategy_id": "signal", "symbols": ["510880"]})
    # 合法 notify 实例：qmt_account 占位 '-'；更新复用同一行且不可换模板
    first = service.create_instance({"name": "ETF信号", "strategy_id": "signal", "enabled": True,
                                     "symbols": ["510880.SH"], "parameters": {"ma_window": 20},
                                     "schedule_time": "14:25"})
    second = service.update_instance(first["id"], {"name": "ETF信号", "strategy_id": "signal",
                                                   "enabled": False, "symbols": ["510880"]})
    assert first["id"] == second["id"] and first["qmt_account"] == "-"
    with pytest.raises(ValueError, match="cannot be changed"):
        service.update_instance(first["id"], {"name": "x", "strategy_id": "low-premium"})
    # 仅回测实例：enabled 强制 False、不参与调度
    backtest = service.create_instance({"name": "轮动", "strategy_id": "rotation", "enabled": True})
    assert backtest["enabled"] is False
    # order 语义：get_config 仍返回低溢价交易配置，list_configs 三行
    assert service.get_config()["strategy_id"] == "low-premium"
    assert {c["strategy_id"] for c in service.list_configs()} == {"low-premium", "signal", "rotation"}


def test_notify_synthetic_bar_prefers_pre_close_then_change_pct(notify_env):
    """当日 bar 折算：优先 pre_close，缺省用 change_pct，两者皆缺跳过评估。"""
    from src.services.etf_signal_notify_service import EtfSignalNotifyService as S
    entry: dict = {}

    # pre_close 优先：100 × 110/100 = 110
    class Q1: price = 110.0; pre_close = 100.0; change_pct = 5.0
    bar = S._synthetic_today_bar("x", TRADE_DATE, 100.0, Q1(), entry)
    assert bar["close"] == pytest.approx(110.0) and entry["hfq_scaled"] is True

    # 无 pre_close 用涨跌幅：100 × (1 - 1%) = 99
    entry = {}
    class Q2: price = 99.0; pre_close = None; change_pct = -1.0
    bar = S._synthetic_today_bar("x", TRADE_DATE, 100.0, Q2(), entry)
    assert bar["close"] == pytest.approx(99.0) and entry["hfq_scaled"] is True

    # 两者皆缺：返回 None（不硬拼未复权现价）
    entry = {}
    class Q3: price = 3.43; pre_close = None; change_pct = None
    assert S._synthetic_today_bar("x", TRADE_DATE, 5.9, Q3(), entry) is None


def test_notify_skips_stale_history(notify_env):
    """库内最后行情距评估日过远（etf_daily 未同步）→ 标注跳过，不产出信号。"""
    dates = _weekdays_before(TRADE_DATE - timedelta(days=30), 200)
    _seed_etf_history(notify_env, "510880", [100.0] * 200, dates)
    # signal 策略参数白名单不含 macd 键，用 signal_macd 缩 MACD 预热；
    # 其 warmup 仍受 ma_window 默认 182 主导，故补足 200 根历史
    config_id = _make_notify_config(notify_env, {"macd_fast": 2, "macd_slow": 3, "macd_signal": 2},
                                    strategy_id="signal_macd")

    service = EtfSignalNotifyService(notify_env)
    service.notifier = _Notifier()
    service._fetcher = _Fetcher(_Quote(price=99.0, pre_close=100.0))

    result = service.run(config_id=config_id, trade_date=TRADE_DATE)
    assert result["signals"][0]["status"] == "stale_history"
    assert result["signals"][0]["signal"] is None
    assert "历史数据过期" in result["message"]


def test_notify_end_to_end_with_change_pct_only_quote(notify_env):
    """行情只给涨跌幅（实测主源对 ETF 不回 pre_close）：折算正确并产出信号。"""
    dates = _weekdays_before(TRADE_DATE, 75)
    _seed_etf_history(notify_env, "510880", [100.0] * 75, dates)
    config_id = _make_notify_config(notify_env)

    service = EtfSignalNotifyService(notify_env)
    service.notifier = _Notifier()
    # 现价 99 / 昨收 100 → 当日 -1%；折算 bar = 100 × 0.99 = 99，乖离率 -0.8% < 0 → 买入
    service._fetcher = _Fetcher(_Quote(price=99.0, pre_close=None, change_pct=-1.0))

    result = service.run(config_id=config_id, trade_date=TRADE_DATE)
    signals = {s["symbol"]: s for s in result["signals"]}
    assert signals["510880"]["hfq_scaled"] is True
    assert signals["510880"]["factors"]["ma_gap_pct"] == pytest.approx(-0.8016, abs=1e-3)
    assert signals["510880"]["signal"] == "buy"
