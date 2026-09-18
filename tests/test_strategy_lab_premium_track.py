# -*- coding: utf-8 -*-
"""Strategy Lab lowest-premium top-N membership tracking tests."""

from __future__ import annotations

from datetime import date

import pytest

from src.services.strategy_lab.premium_track_service import StrategyLabPremiumTrackService
from src.storage import DatabaseManager, StrategyLabCbBasic, StrategyLabCbDailyFactor


@pytest.fixture()
def db_manager() -> DatabaseManager:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def _seed(db: DatabaseManager) -> None:
    """三个交易日、top_n=3 的榜单：A/B 常驻，C/D/E 轮换；F 缺溢价率不参与。

    D1(1/5): A=1.0 B=2.0 C=3.0 D=4.0 E=5.0   -> 榜单 A,B,C
    D2(1/6): A=1.1 B=2.1 D=3.9 E=5.1 C=9.0   -> 榜单 A,B,D（C 出、D 进）
    D3(1/7): A=1.2 B=2.2 E=5.2 D=8.0 C=9.1   -> 榜单 A,B,E（D 出、E 进）
    """
    bonds = {
        "A债": ("113001", {"2026-01-05": 1.0, "2026-01-06": 1.1, "2026-01-07": 1.2}),
        "B债": ("113002", {"2026-01-05": 2.0, "2026-01-06": 2.1, "2026-01-07": 2.2}),
        "C债": ("113003", {"2026-01-05": 3.0, "2026-01-06": 9.0, "2026-01-07": 9.1}),
        "D债": ("113004", {"2026-01-05": 4.0, "2026-01-06": 3.9, "2026-01-07": 8.0}),
        "E债": ("113005", {"2026-01-05": 5.0, "2026-01-06": 5.1, "2026-01-07": 5.2}),
        "F债": ("113006", {"2026-01-05": None, "2026-01-06": None, "2026-01-07": None}),  # 溢价率全空
    }
    with db.get_session() as session:
        for name, (code, _) in bonds.items():
            session.add(StrategyLabCbBasic(bond_code=code, bond_name=name, stock_code=f"SH{code}", market="cn", status="active"))
        session.commit()
        for _, (code, premiums) in bonds.items():
            for iso, premium in premiums.items():
                session.add(
                    StrategyLabCbDailyFactor(
                        bond_code=code,
                        trade_date=date.fromisoformat(iso),
                        close=100.0,
                        premium_rate=premium,
                    )
                )
        session.commit()


def test_premium_track_membership_and_turnover(db_manager: DatabaseManager) -> None:
    _seed(db_manager)
    payload = StrategyLabPremiumTrackService(db_manager).premium_top_track(
        start=date(2026, 1, 1), end=date(2026, 1, 31), top_n=3
    )

    assert payload["dates"] == ["2026-01-05", "2026-01-06", "2026-01-07"]
    # F 缺溢价率不参与，A/B 常驻，C/D/E 轮换 → 共 5 只上过榜
    assert payload["stats"] == {"window_days": 3, "distinct_bonds": 5, "avg_overlap": 2.0, "avg_entered": 1.0}

    by_code = {item["bond_code"]: item for item in payload["bonds"]}
    # 按在榜天数降序：A/B 3 天在前
    assert [item["bond_code"] for item in payload["bonds"][:2]] == ["113001", "113002"]
    assert by_code["113001"]["days_count"] == 3
    assert by_code["113001"]["ratio"] == 1.0
    assert by_code["113001"]["first_date"] == "2026-01-05"
    assert by_code["113001"]["last_date"] == "2026-01-07"
    assert by_code["113001"]["avg_premium"] == 1.1
    assert by_code["113001"]["day_indexes"] == [0, 1, 2]
    assert by_code["113003"]["days_count"] == 1  # C 只在首日上榜
    assert by_code["113003"]["day_indexes"] == [0]

    # 首日无对比数据；此后每日与前日重叠 2、进 1、出 1，门槛 = 榜内最高溢价率
    assert payload["turnover"] == [
        {"date": "2026-01-05", "overlap": None, "entered": None, "exited": None, "threshold": 3.0},
        {"date": "2026-01-06", "overlap": 2, "entered": 1, "exited": 1, "threshold": 3.9},
        {"date": "2026-01-07", "overlap": 2, "entered": 1, "exited": 1, "threshold": 5.2},
    ]


def test_premium_track_top_n_cuts_daily_list(db_manager: DatabaseManager) -> None:
    _seed(db_manager)
    payload = StrategyLabPremiumTrackService(db_manager).premium_top_track(
        start=date(2026, 1, 1), end=date(2026, 1, 31), top_n=1
    )
    by_code = {item["bond_code"]: item for item in payload["bonds"]}
    assert set(by_code) == {"113001"}  # 每日只取最低 1 只，A 三天全占
    assert payload["stats"]["distinct_bonds"] == 1
    # 门槛即榜首溢价率
    assert [item["threshold"] for item in payload["turnover"]] == [1.0, 1.1, 1.2]


def test_premium_track_rejects_invalid_range(db_manager: DatabaseManager) -> None:
    with pytest.raises(ValueError, match="start must not be after end"):
        StrategyLabPremiumTrackService(db_manager).premium_top_track(
            start=date(2026, 2, 1), end=date(2026, 1, 1)
        )


def test_premium_track_empty_window(db_manager: DatabaseManager) -> None:
    _seed(db_manager)
    payload = StrategyLabPremiumTrackService(db_manager).premium_top_track(
        start=date(2025, 1, 1), end=date(2025, 1, 31), top_n=10
    )
    assert payload["dates"] == []
    assert payload["bonds"] == []
    assert payload["stats"] == {"window_days": 0, "distinct_bonds": 0, "avg_overlap": None, "avg_entered": None}


def test_premium_track_endpoint() -> None:
    """GET /cb/premium-track 端点契约：参数透传、载荷结构正确。"""
    import os
    import sys
    import tempfile
    import unittest
    from pathlib import Path
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    try:
        import litellm  # noqa: F401
    except ModuleNotFoundError:
        sys.modules["litellm"] = MagicMock()

    import src.auth as auth
    from api.app import create_app
    from src.config import Config

    class _EndpointCase(unittest.TestCase):
        def setUp(self) -> None:
            auth._auth_enabled = None
            auth._session_secret = None
            auth._password_hash_salt = None
            auth._password_hash_stored = None
            auth._rate_limit = {}
            self.temp_dir = tempfile.TemporaryDirectory()
            data_dir = Path(self.temp_dir.name)
            db_path = data_dir / "premium_track_api.db"
            env_path = data_dir / ".env"
            env_path.write_text(
                f"STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\nDATABASE_PATH={db_path}\n",
                encoding="utf-8",
            )
            os.environ["ENV_FILE"] = str(env_path)
            os.environ["DATABASE_PATH"] = str(db_path)
            Config.reset_instance()
            DatabaseManager.reset_instance()
            self.db = DatabaseManager(db_url=f"sqlite:///{db_path}")
            _seed(self.db)
            self.client = TestClient(create_app(static_dir=data_dir / "empty-static"))

        def tearDown(self) -> None:
            DatabaseManager.reset_instance()
            Config.reset_instance()
            os.environ.pop("ENV_FILE", None)
            os.environ.pop("DATABASE_PATH", None)
            self.temp_dir.cleanup()

        def test_payload(self) -> None:
            resp = self.client.get(
                "/api/v1/strategy-lab/cb/premium-track",
                params={"start": "2026-01-01", "end": "2026-01-31", "top_n": 3},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["top_n"], 3)
            self.assertEqual(body["dates"], ["2026-01-05", "2026-01-06", "2026-01-07"])
            self.assertEqual(body["stats"]["distinct_bonds"], 5)
            self.assertEqual([b["bond_code"] for b in body["bonds"][:2]], ["113001", "113002"])

        def test_invalid_range(self) -> None:
            resp = self.client.get(
                "/api/v1/strategy-lab/cb/premium-track",
                params={"start": "2026-02-01", "end": "2026-01-01"},
            )
            self.assertEqual(resp.status_code, 400, resp.text)
            self.assertEqual(resp.json()["error"], "invalid_params")

    suite = unittest.TestLoader().loadTestsFromTestCase(_EndpointCase)
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    assert result.wasSuccessful(), "premium-track endpoint tests failed"
