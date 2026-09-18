# -*- coding: utf-8 -*-
"""可转债实盘交易指令 API 端点测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.deps import get_database_manager
from src.storage import DatabaseManager


@pytest.fixture()
def client():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    app = create_app(static_dir=Path(tempfile.mkdtemp()))
    app.dependency_overrides[get_database_manager] = lambda: db
    try:
        yield TestClient(app)
    finally:
        DatabaseManager.reset_instance()


def _create_payload(**overrides) -> dict:
    payload = {
        "symbol": "113002",
        "side": "buy",
        "quantity": 10,
        "order_type": "limit",
        "limit_price": 120.5,
    }
    payload.update(overrides)
    return payload


def test_create_and_list_orders(client: TestClient) -> None:
    created = client.post("/api/v1/trading/orders", json=_create_payload())
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "pending"

    listed = client.get("/api/v1/trading/orders")
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["symbol"] == "113002"


def test_create_validation_error(client: TestClient) -> None:
    resp = client.post("/api/v1/trading/orders", json=_create_payload(symbol="abc"))
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "invalid_params"


def test_cancel_order(client: TestClient) -> None:
    created = client.post("/api/v1/trading/orders", json=_create_payload()).json()
    cancelled = client.post(f"/api/v1/trading/orders/{created['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"


def test_qmt_pending_defaults_to_today_and_include_all_returns_stale(client: TestClient) -> None:
    """pending 拉取默认只返回当天创建的指令；include_all=true 兜底返回隔日遗留。"""
    from datetime import datetime, timedelta

    from sqlalchemy import update

    from src.storage import TradingOrder

    stale = client.post("/api/v1/trading/orders", json=_create_payload(symbol="113001")).json()
    fresh = client.post("/api/v1/trading/orders", json=_create_payload(symbol="113003")).json()

    # 把第一笔单改成昨天创建（模拟隔日未认领的遗留 pending 单）
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        session.execute(
            update(TradingOrder)
            .where(TradingOrder.id == stale["id"])
            .values(created_at=datetime.now() - timedelta(days=1))
        )
        session.commit()

    pending = client.get("/api/v1/trading/qmt/pending")
    assert pending.status_code == 200, pending.text
    assert [item["id"] for item in pending.json()["items"]] == [fresh["id"]]

    everything = client.get("/api/v1/trading/qmt/pending", params={"include_all": "true"})
    assert everything.status_code == 200, everything.text
    assert {item["id"] for item in everything.json()["items"]} == {stale["id"], fresh["id"]}


def test_qmt_pending_and_callback_no_token(client: TestClient) -> None:
    created = client.post("/api/v1/trading/orders", json=_create_payload()).json()

    pending = client.get("/api/v1/trading/qmt/pending")
    assert pending.status_code == 200, pending.text
    assert len(pending.json()["items"]) == 1
    assert pending.json()["items"][0]["id"] == created["id"]

    callback = client.post(
        f"/api/v1/trading/qmt/orders/{created['id']}/callback",
        json={"status": "filled", "qmt_order_id": "q123", "filled_quantity": 10, "filled_price": 120.4},
    )
    assert callback.status_code == 200, callback.text
    assert callback.json()["status"] == "filled"
    assert callback.json()["qmt_order_id"] == "q123"


def test_qmt_requires_token_when_configured(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("QMT_API_TOKEN", "secret")

    pending = client.get("/api/v1/trading/qmt/pending")
    assert pending.status_code == 401, pending.text

    pending = client.get("/api/v1/trading/qmt/pending", headers={"X-QMT-Token": "secret"})
    assert pending.status_code == 200, pending.text
