# -*- coding: utf-8 -*-
"""实盘策略 API 契约测试：运行记录 / 批次 / 订单字段完整性。

回归背景：``GET /runs`` 的 response_model 此前缺少 mode/decision_count/order_count
等字段声明，Pydantic 会把这些字段从响应中过滤掉，导致前端运行记录的
“模式”“订单数”两列恒为空。
"""

from __future__ import annotations

import tempfile
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.deps import get_database_manager
from src.storage import (DatabaseManager, LiveRebalanceBatch, LiveStrategyConfig,
                         LiveStrategyRun, TradingOrder)


@pytest.fixture()
def client_and_db():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    app = create_app(static_dir=Path(tempfile.mkdtemp()))
    app.dependency_overrides[get_database_manager] = lambda: db
    try:
        yield TestClient(app), db
    finally:
        DatabaseManager.reset_instance()


def _seed_run_with_orders(db: DatabaseManager) -> int:
    """一条已完成的调仓 run + 1 个批次 + 2 笔订单（1 成交 1 待执行）。"""
    with db.get_session() as session:
        config = LiveStrategyConfig(name="default", qmt_account="testS", strategy_id="double-low")
        session.add(config)
        session.flush()
        run = LiveStrategyRun(run_uid=uuid4().hex, config_id=config.id, qmt_account="testS",
                              trade_date=date(2024, 1, 2), status="completed", mode="rebalance",
                              strategy_id="double-low", strategy_version="v1",
                              decision_count=2, order_count=2,
                              completed_at=datetime(2024, 1, 2, 14, 35, 0))
        session.add(run)
        session.flush()
        batch = LiveRebalanceBatch(batch_uid=uuid4().hex, run_id=run.id, qmt_account="testS",
                                   status="pending", summary_json='{"count": 2}')
        session.add(batch)
        session.flush()
        for i, status in enumerate(("filled", "pending")):
            session.add(TradingOrder(order_uid=f"qmt_{uuid4().hex[:8]}", symbol="113001",
                                     side="buy" if i == 0 else "sell", quantity=100, status=status,
                                     live_run_id=run.id, rebalance_batch_id=batch.id))
        session.commit()
        return int(run.id)


def test_list_runs_keeps_mode_and_counts(client_and_db):
    client, db = client_and_db
    _seed_run_with_orders(db)
    resp = client.get("/api/v1/live-strategy/runs")
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["mode"] == "rebalance"
    assert item["strategy_id"] == "double-low"
    assert item["strategy_version"] == "v1"
    assert item["qmt_account"] == "testS"
    assert item["decision_count"] == 2
    assert item["order_count"] == 2
    assert item["completed_at"]


def test_list_batches_joins_run_and_aggregates_order_status(client_and_db):
    client, db = client_and_db
    _seed_run_with_orders(db)
    resp = client.get("/api/v1/live-strategy/batches")
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["trade_date"] == "2024-01-02"
    assert item["mode"] == "rebalance"
    assert item["orders"] == {"total": 2, "pending": 1, "submitted": 0,
                              "filled": 1, "rejected": 0, "cancelled": 0}


def test_run_orders_include_fill_report_fields(client_and_db):
    client, db = client_and_db
    run_id = _seed_run_with_orders(db)
    resp = client.get(f"/api/v1/live-strategy/runs/{run_id}/orders")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 2
    assert {o["status"] for o in items} == {"filled", "pending"}
    assert all("filled_price" in o and "filled_quantity" in o and "completed_at" in o for o in items)
