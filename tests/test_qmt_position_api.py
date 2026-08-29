# -*- coding: utf-8 -*-
"""QMT 上报持仓 API 端点测试。"""

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


def _payload(**overrides) -> dict:
    payload = {
        "account": "testS",
        "positions": [
            {
                "symbol": "000858",
                "name": "五粮液",
                "volume": 900,
                "can_use_volume": 900,
                "open_price": 103.657,
                "float_profit": 123.4,
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_report_positions_success(client: TestClient) -> None:
    resp = client.post("/api/v1/trading/qmt/positions", json=_payload())
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"account": "testS", "reported": 1}


def test_report_positions_upsert(client: TestClient) -> None:
    client.post("/api/v1/trading/qmt/positions", json=_payload())
    client.post(
        "/api/v1/trading/qmt/positions",
        json=_payload(positions=[{"symbol": "000858", "volume": 800, "can_use_volume": 800}]),
    )

    listed = client.get("/api/v1/trading/positions")
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["volume"] == 800


def test_report_positions_full_replace(client: TestClient) -> None:
    client.post(
        "/api/v1/trading/qmt/positions",
        json=_payload(positions=[
            {"symbol": "000858", "volume": 900, "can_use_volume": 900},
            {"symbol": "113002", "volume": 10, "can_use_volume": 10},
        ]),
    )

    # 第二次只上报 000858，113002 应被清理
    client.post(
        "/api/v1/trading/qmt/positions",
        json=_payload(positions=[{"symbol": "000858", "volume": 800, "can_use_volume": 800}]),
    )

    listed = client.get("/api/v1/trading/positions")
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["symbol"] == "000858"


def test_report_positions_invalid_symbol(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/trading/qmt/positions",
        json=_payload(positions=[{"symbol": "abc", "volume": 1, "can_use_volume": 1}]),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "invalid_params"


def test_report_positions_requires_token_when_configured(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("QMT_API_TOKEN", "secret")

    resp = client.post("/api/v1/trading/qmt/positions", json=_payload())
    assert resp.status_code == 401, resp.text

    resp = client.post(
        "/api/v1/trading/qmt/positions",
        json=_payload(),
        headers={"X-QMT-Token": "secret"},
    )
    assert resp.status_code == 200, resp.text
