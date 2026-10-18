# -*- coding: utf-8 -*-
"""实盘调度后台任务测试：多配置分派（order/notify）与触发时刻跟随配置。"""

from datetime import datetime

import pytest

from src.services import runtime_scheduler


class _FakeDatetime(datetime):
    """固定 now() 的 datetime 替身。"""

    _now: datetime = datetime(2026, 10, 15, 14, 30)  # 周四，A 股交易日

    @classmethod
    def now(cls, tz=None):
        return cls._now


class _FakeLiveStrategyService:
    """list_configs / run(trade_date, config_id) 双记录桩。"""

    configs: list[dict] = []
    order_calls: list[tuple] = []

    def list_configs(self):
        return self.configs

    def run(self, *, trade_date, config_id=None, **kwargs):
        self.order_calls.append((config_id, trade_date.isoformat()))


class _FakeNotifyService:
    notify_calls: list[tuple] = []

    def __init__(self, *args, **kwargs):
        pass

    def run(self, *, config_id, trade_date, **kwargs):
        self.notify_calls.append((config_id, trade_date.isoformat()))
        return {"run_id": 1, "status": "completed"}


@pytest.fixture()
def _patched_services(monkeypatch: pytest.MonkeyPatch):
    _FakeLiveStrategyService.configs = []
    _FakeLiveStrategyService.order_calls = []
    _FakeNotifyService.notify_calls = []
    monkeypatch.setattr("src.services.live_strategy_service.LiveStrategyService", _FakeLiveStrategyService)
    monkeypatch.setattr("src.services.etf_signal_notify_service.EtfSignalNotifyService", _FakeNotifyService)
    monkeypatch.setattr(runtime_scheduler, "datetime", _FakeDatetime)
    return _FakeLiveStrategyService


def _first_task():
    tasks = runtime_scheduler.build_live_strategy_background_tasks(config=None)
    assert len(tasks) == 1
    return tasks[0]["task"]


def test_live_task_dispatches_order_config_at_configured_time(monkeypatch: pytest.MonkeyPatch, _patched_services):
    """order 配置在配置时刻触发一次，经 config_id 分派到交易链路；同日 tick 幂等。"""
    monkeypatch.setenv("LIVE_STRATEGY_SCHEDULE_ENABLED", "true")
    _FakeLiveStrategyService.configs = [
        {"id": 1, "enabled": True, "strategy_id": "low-premium", "schedule_time": "14:30"},
        {"id": 2, "enabled": False, "strategy_id": "double-low", "schedule_time": "14:30"},
    ]
    task = _first_task()
    task()
    task()
    assert _FakeLiveStrategyService.order_calls == [(1, "2026-10-15")]
    assert _FakeNotifyService.notify_calls == []


def test_live_task_dispatches_notify_config(_patched_services):
    """notify 配置（ETF 信号）走通知链路，与 order 配置互不挤占（dedup 按 config_id）。"""
    _FakeLiveStrategyService.configs = [
        {"id": 1, "enabled": True, "strategy_id": "low-premium", "schedule_time": "14:30"},
        {"id": 7, "enabled": True, "strategy_id": "signal", "schedule_time": "14:30"},
    ]
    task = _first_task()
    task()
    task()
    assert _FakeLiveStrategyService.order_calls == [(1, "2026-10-15")]
    assert _FakeNotifyService.notify_calls == [(7, "2026-10-15")]


def test_live_task_skips_when_time_mismatches(_patched_services):
    _FakeLiveStrategyService.configs = [{"id": 1, "enabled": True, "strategy_id": "low-premium", "schedule_time": "14:30"}]
    _FakeDatetime._now = datetime(2026, 10, 15, 14, 35)
    try:
        _first_task()()
    finally:
        _FakeDatetime._now = datetime(2026, 10, 15, 14, 30)
    assert _FakeLiveStrategyService.order_calls == []


def test_live_task_falls_back_to_default_1435(_patched_services):
    _FakeLiveStrategyService.configs = [{"id": 1, "enabled": True, "strategy_id": "low-premium"}]
    _FakeDatetime._now = datetime(2026, 10, 15, 14, 35)
    try:
        _first_task()()
    finally:
        _FakeDatetime._now = datetime(2026, 10, 15, 14, 30)
    assert _FakeLiveStrategyService.order_calls == [(1, "2026-10-15")]


def test_live_task_skips_disabled_config(_patched_services):
    _FakeLiveStrategyService.configs = [{"id": 1, "enabled": False, "strategy_id": "low-premium", "schedule_time": "14:30"}]
    _first_task()()
    assert _FakeLiveStrategyService.order_calls == []


def test_live_task_dispatches_multiple_order_instances(_patched_services):
    """多实盘实例并存：各自按 config_id 分派、dedup 互不挤占。"""
    _FakeLiveStrategyService.configs = [
        {"id": 1, "enabled": True, "strategy_id": "low-premium", "schedule_time": "14:30"},
        {"id": 5, "enabled": True, "strategy_id": "double-low", "schedule_time": "14:30"},
    ]
    task = _first_task()
    task()
    task()
    assert sorted(_FakeLiveStrategyService.order_calls) == [(1, "2026-10-15"), (5, "2026-10-15")]


def test_live_task_skips_backtest_only_instance(_patched_services):
    """live_mode=None 实例（rotation/ma-crossover）即使 enabled 也不进任何分派。"""
    _FakeLiveStrategyService.configs = [
        {"id": 1, "enabled": True, "strategy_id": "low-premium", "schedule_time": "14:30"},
        {"id": 9, "enabled": True, "strategy_id": "rotation", "schedule_time": "14:30"},
        {"id": 10, "enabled": True, "strategy_id": "ma-crossover", "schedule_time": "14:30"},
    ]
    _first_task()()
    assert _FakeLiveStrategyService.order_calls == [(1, "2026-10-15")]
    assert _FakeNotifyService.notify_calls == []
