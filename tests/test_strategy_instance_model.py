# -*- coding: utf-8 -*-
"""策略模板 / 策略实例两层产品模型的落地测试。

覆盖：presets → 实例的一次性迁移（幂等 / 损坏行保留）、实例 CRUD 校验
（order/notify/仅回测三分支）、save_config 兼容层语义、holdings 默认账户
解析、实验室回测的实例血缘（instance_id 落库 + 列表回带实例名）。
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from src.services.live_strategy_service import LiveStrategyService
from src.storage import (DatabaseManager, LiveStrategyConfig,
                         StrategyLabStrategyConfig)


@pytest.fixture()
def instance_db():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


# ------------------------------------------------------------------
# presets → 策略实例迁移
# ------------------------------------------------------------------

def _seed_presets(db: DatabaseManager) -> None:
    with db.get_session() as session:
        session.add(StrategyLabStrategyConfig(
            config_uid="p1", name="红利ETF回归", description="182日线低吸",
            strategy_id="signal", market="cn", instrument_type="etf",
            parameters_json='{"ma_window": 182}', symbols_json='["510880"]'))
        session.add(StrategyLabStrategyConfig(
            config_uid="p2", name="双低小仓", strategy_id="double-low",
            market="cn", instrument_type="convertible_bond",
            parameters_json='{"max_positions": 2}', symbols_json="null"))
        session.commit()


def _manager(db: DatabaseManager) -> DatabaseManager:
    """reset 后旧引用失效，统一回落到当前单例。"""
    return db if db._initialized else DatabaseManager.get_instance()


def _instances(db: DatabaseManager) -> list[LiveStrategyConfig]:
    with _manager(db).get_session() as session:
        return list(session.execute(select(LiveStrategyConfig).order_by(LiveStrategyConfig.id)).scalars())


def test_migration_copies_presets_once(tmp_path):
    """init 时 presets 拷贝为未启用实例（copy 不删、幂等、旧表保留）。"""
    db_file = tmp_path / "migrate.db"
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{db_file}")
    _seed_presets(db)
    DatabaseManager.reset_instance()

    # 再次 init 同一文件库 → 迁移执行
    DatabaseManager(db_url=f"sqlite:///{db_file}")
    rows = _instances(db)
    by_name = {r.name: r for r in rows}
    assert set(by_name) == {"红利ETF回归", "双低小仓"}
    inst = by_name["红利ETF回归"]
    assert inst.enabled is False and inst.qmt_account == "-"
    assert inst.strategy_id == "signal"
    assert json.loads(inst.parameters_json) == {"ma_window": 182}
    assert json.loads(inst.symbols_json) == ["510880"]
    assert inst.description == "182日线低吸"

    # 幂等：再 init 一遍不产生重复实例，旧 presets 行保留
    DatabaseManager.reset_instance()
    DatabaseManager(db_url=f"sqlite:///{db_file}")
    assert len(_instances(db)) == 2
    with _manager(db).get_session() as session:
        assert len(session.execute(select(StrategyLabStrategyConfig)).scalars().all()) == 2

    # 一次性标记：用户删除实例后重启不再复活（回归：按同名去重的旧语义
    # 会让迁移器在每次重启时用旧 preset 重建被删实例）
    with _manager(db).get_session() as session:
        victim = next(r for r in session.execute(select(LiveStrategyConfig)).scalars()
                      if r.name == "红利ETF回归")
        session.delete(victim)
        session.commit()
    DatabaseManager.reset_instance()
    DatabaseManager(db_url=f"sqlite:///{db_file}")
    assert [r.name for r in _instances(db)] == ["双低小仓"]
    DatabaseManager.reset_instance()


def test_migration_flags_presets_with_existing_instance_without_copy(tmp_path):
    """同名同模板实例已存在的 preset 只回填标记、不重复拷贝（存量库升级路径）。"""
    db_file = tmp_path / "backfill.db"
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{db_file}")
    with db.get_session() as session:
        session.add(StrategyLabStrategyConfig(
            config_uid="preset-1", name="红利ETF·MA210", strategy_id="signal",
            parameters_json='{"ma_window": 210}', symbols_json='["510880"]'))
        session.add(LiveStrategyConfig(name="红利ETF·MA210", strategy_id="signal",
                                       qmt_account="-", enabled=False,
                                       symbols_json='["510880"]',
                                       parameters_json='{"ma_window": 210}'))
        session.commit()
    DatabaseManager.reset_instance()

    DatabaseManager(db_url=f"sqlite:///{db_file}")
    # 只回填标记：不新建、不重复
    assert len(_instances(db)) == 1
    # 删除实例后重启同样不复活
    with _manager(db).get_session() as session:
        session.delete(session.execute(select(LiveStrategyConfig)).scalars().first())
        session.commit()
    DatabaseManager.reset_instance()
    DatabaseManager(db_url=f"sqlite:///{db_file}")
    assert _instances(db) == []
    DatabaseManager.reset_instance()


def test_migration_skips_corrupt_preset_rows(tmp_path):
    """parameters_json 损坏的 preset 留在旧表，不影响其余行迁移。"""
    db_file = tmp_path / "corrupt.db"
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{db_file}")
    with db.get_session() as session:
        session.add(StrategyLabStrategyConfig(
            config_uid="bad", name="损坏", strategy_id="signal",
            parameters_json='{"ma_window": ', symbols_json="null"))
        session.add(StrategyLabStrategyConfig(
            config_uid="good", name="完好", strategy_id="signal",
            parameters_json='{}', symbols_json='["510880"]'))
        session.commit()
    DatabaseManager.reset_instance()

    DatabaseManager(db_url=f"sqlite:///{db_file}")
    rows = _instances(db)
    assert [r.name for r in rows] == ["完好"]
    with _manager(db).get_session() as session:
        remaining = session.execute(select(StrategyLabStrategyConfig)).scalars().all()
    # copy 语义：旧表两行都保留，仅损坏行未迁移
    assert {r.name for r in remaining} == {"损坏", "完好"}
    DatabaseManager.reset_instance()


# ------------------------------------------------------------------
# 实例 CRUD 校验矩阵
# ------------------------------------------------------------------

def test_create_order_instance_requires_valid_account(instance_db):
    service = LiveStrategyService(instance_db)
    with pytest.raises(ValueError, match="qmt_account"):
        service.create_instance({"name": "低溢价", "strategy_id": "low-premium", "parameters": {}})
    created = service.create_instance({
        "name": "低溢价", "strategy_id": "low-premium", "qmt_account": "135129739",
        "enabled": True, "parameters": {"max_positions": 3, "exclude_event_blocked": 0},
        "schedule_time": "14:40", "rebalance_frequency_days": 2,
    })
    # 布尔参数归一 + order 专属字段落库 + 调度时刻
    assert created["parameters"]["exclude_event_blocked"] is False
    assert created["rebalance_frequency_days"] == 2
    assert created["schedule_time"] == "14:40" and created["qmt_account"] == "135129739"


def test_signal_instance_accepts_and_normalizes_explicit_rules(instance_db):
    """实验室「保存为策略实例」带显式规则（entry/exit_rules、default_rules）可保存并归一。"""
    service = LiveStrategyService(instance_db)
    created = service.create_instance({
        "name": "红利信号", "strategy_id": "signal", "symbols": ["510880"],
        "parameters": {"ma_window": 210, "default_rules": "ma_gap",
                       "entry_rules": [{"factor": "ma_gap_pct", "op": "<", "value": 0}],
                       "exit_rules": [{"factor": "ma_gap_pct", "op": ">", "value": 7}]},
    })
    assert created["parameters"]["entry_rules"] == [{"factor": "ma_gap_pct", "op": "<", "value": 0.0}]
    assert created["parameters"]["exit_rules"] == [{"factor": "ma_gap_pct", "op": ">", "value": 7.0}]
    assert created["parameters"]["default_rules"] == "ma_gap"


def test_signal_instance_rejects_malformed_rules(instance_db):
    """结构化规则键结构非法时拒绝；非 notify 模板不放开白名单。"""
    service = LiveStrategyService(instance_db)
    base_params = {"ma_window": 210}

    def _signal_with(extra: dict):
        return service.create_instance({"name": "n", "strategy_id": "signal",
                                        "symbols": ["510880"], "parameters": {**base_params, **extra}})

    with pytest.raises(ValueError, match="list of rules"):
        _signal_with({"entry_rules": {"factor": "ma_gap_pct"}})
    with pytest.raises(ValueError, match="factor"):
        _signal_with({"entry_rules": [{"op": "<", "value": 0}]})
    with pytest.raises(ValueError, match="comparator"):
        _signal_with({"exit_rules": [{"factor": "ma_gap_pct", "op": "~", "value": 0}]})
    with pytest.raises(ValueError, match="number"):
        _signal_with({"entry_rules": [{"factor": "ma_gap_pct", "op": "<", "value": "abc"}]})
    with pytest.raises(ValueError, match="default_rules"):
        _signal_with({"default_rules": "rsi"})
    # order / 仅回测模板仍按白名单严格拒绝规则键
    with pytest.raises(ValueError, match="unknown strategy parameters"):
        service.create_instance({"name": "低溢价", "strategy_id": "low-premium", "qmt_account": "testS",
                                 "parameters": {"entry_rules": []}})
    with pytest.raises(ValueError, match="unknown strategy parameters"):
        service.create_instance({"name": "轮动", "strategy_id": "rotation",
                                 "parameters": {"exit_rules": []}})


def test_update_instance_keeps_omitted_fields(instance_db):
    """更新省略的字段沿用现值；描述可清空；非法时刻拒绝。"""
    service = LiveStrategyService(instance_db)
    created = service.create_instance({
        "name": "低溢价", "strategy_id": "low-premium", "qmt_account": "testS",
        "symbols": ["113050"], "parameters": {"max_positions": 3}, "description": "备注",
    })
    patched = service.update_instance(created["id"], {"name": "低溢价改", "parameters": {"max_positions": 5}})
    assert patched["symbols"] == ["113050"] and patched["qmt_account"] == "testS"
    assert patched["parameters"] == {"max_positions": 5}
    cleared = service.update_instance(created["id"], {"name": "低溢价改", "description": ""})
    assert cleared["description"] is None
    with pytest.raises(ValueError, match="HH:MM"):
        service.update_instance(created["id"], {"name": "x", "schedule_time": "9:5"})


def test_delete_instance_hard_deletes(instance_db):
    service = LiveStrategyService(instance_db)
    created = service.create_instance({"name": "轮动", "strategy_id": "rotation"})
    assert service.delete_instance(created["id"])["deleted"] is True
    with pytest.raises(ValueError, match="not found"):
        service.delete_instance(created["id"])


def test_save_config_compat_targets_latest_order_instance_only(instance_db):
    """兼容层：只作用于最新 order 实例；notify 行不被抢占；notify 策略被拒。"""
    service = LiveStrategyService(instance_db)
    notify = service.create_instance({"name": "ETF信号", "strategy_id": "signal",
                                      "symbols": ["510880"], "enabled": True})
    # 无 order 行时：save_config 新建 order 实例（name=default）
    created = service.save_config({"qmt_account": "testS", "enabled": True,
                                   "parameters": {"max_positions": 1}})
    assert created["strategy_id"] == "double-low" and created["name"] == "default"
    # 再保存 → 更新同一 order 行（含 order 模板内切换），notify 行原样
    switched = service.save_config({"qmt_account": "testS", "strategy_id": "low-premium",
                                    "parameters": {"max_positions": 2}})
    assert switched["strategy_id"] == "low-premium"
    rows = _instances(instance_db)
    assert len(rows) == 2
    notify_row = next(r for r in rows if r.id == notify["id"])
    assert notify_row.strategy_id == "signal" and notify_row.enabled is True
    # notify 策略直接走兼容层被拒（不能把交易实例改成通知实例）
    with pytest.raises(ValueError, match="order-mode"):
        service.save_config({"qmt_account": "testS", "strategy_id": "signal"})


# ------------------------------------------------------------------
# holdings 默认账户解析
# ------------------------------------------------------------------

def test_holdings_default_account_prefers_enabled_order_instance(instance_db):
    from src.services.qmt_cb_holdings_service import QmtCbHoldingsService
    with instance_db.get_session() as session:
        session.add(LiveStrategyConfig(name="停用低溢价", qmt_account="testS",
                                       strategy_id="low-premium", enabled=False))
        session.add(LiveStrategyConfig(name="启用双低", qmt_account="135129739",
                                       strategy_id="double-low", enabled=True))
        session.commit()
    service = QmtCbHoldingsService(instance_db)
    assert service._resolve_account(None) == "135129739"
    assert service._resolve_account("testS") == "testS"  # 显式账户优先


# ------------------------------------------------------------------
# 实验室回测的实例血缘
# ------------------------------------------------------------------

def _lab_config(instance_id=None, strategy_id="signal"):
    from src.core.strategy_lab.models import StrategyLabRunConfig
    return StrategyLabRunConfig(
        strategy_id=strategy_id, market="cn", instrument_type="etf",
        start_date=date(2024, 1, 2), end_date=date(2024, 6, 28),
        initial_cash=100000.0, benchmark_symbol="buy_hold", symbols=["510880"],
        parameters={"ma_window": 5, "roc_window": 3}, instance_id=instance_id,
    )


class _StubSignalEngine:
    name = "signal_v1"

    def run(self, config):
        from src.core.strategy_lab.models import (StrategyLabEquityPoint,
                                                  StrategyLabMetric, StrategyLabRunResult)
        curve = [StrategyLabEquityPoint(trade_date=config.start_date + timedelta(days=i),
                                        equity=100000.0, cash=100000.0,
                                        positions_value=0.0, holdings=[], holdings_count=0)
                 for i in range(3)]
        return StrategyLabRunResult(
            final_equity=100000.0, benchmark_return_pct=1.0,
            metrics=StrategyLabMetric(total_return_pct=0.0, annualized_return_pct=0.0,
                                      max_drawdown_pct=0.0, sharpe_ratio=None, sortino_ratio=None,
                                      calmar_ratio=None, win_rate_pct=None, trade_count=0,
                                      exposure_days=0, diagnostics={}, turnover_avg_pct=None,
                                      period_count=3, profit_periods=0, loss_periods=0,
                                      benchmark_metrics={}),
            trades=[], equity_curve=curve,
        )


def test_lab_run_records_instance_lineage(instance_db):
    """从实例发起的回测：instance_id 落库、列表回带实例名；模板不一致被拒。"""
    from src.services.strategy_lab.service import StrategyLabService
    service = LiveStrategyService(instance_db)
    inst = service.create_instance({"name": "红利ETF回归", "strategy_id": "signal",
                                    "symbols": ["510880"], "parameters": {"ma_window": 5}})
    lab = StrategyLabService(instance_db, engine=_StubSignalEngine())

    result = lab.create_run(_lab_config(instance_id=inst["id"]))
    assert result["instance_id"] == inst["id"] and result["instance_name"] == "红利ETF回归"
    listing = lab.list_runs(page=1, limit=10)
    assert listing["items"][0]["instance_name"] == "红利ETF回归"

    # 模板不一致 / 实例不存在 → ValueError
    with pytest.raises(ValueError, match="does not match"):
        lab.create_run(_lab_config(instance_id=inst["id"], strategy_id="signal_macd"))
    with pytest.raises(ValueError, match="not found"):
        lab.create_run(_lab_config(instance_id=9999))

    # 实例删除后：血缘仍可展示（instance_name 为 None，不报错）
    service.delete_instance(inst["id"])
    listing = lab.list_runs(page=1, limit=10)
    assert listing["items"][0]["instance_id"] == inst["id"]
    assert listing["items"][0]["instance_name"] is None
