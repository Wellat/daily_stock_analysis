# -*- coding: utf-8 -*-
"""Tests for scheduled-task registry and heartbeat recorder."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.config import Config
from src.services.scheduled_task_registry import (
    SCHEDULED_TASK_METADATA,
    build_scheduled_task_overview,
    list_last_runs,
    record_task_run,
    registered_background_task_names,
    resolve_schedule_text,
    task_failed,
    wrap_background_task,
)
from src.storage import DatabaseManager, ScheduledTaskRunRecord


def _raise(_msg=None):
    raise AssertionError("wrapped task must swallow recorder errors")


class ScheduledTaskRecorderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "scheduled_task_test.db"
        os.environ["ENV_FILE"] = str(self.temp_dir.name + "/.env")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def _rows(self):
        with self.db.get_session() as session:
            return session.query(ScheduledTaskRunRecord).order_by(ScheduledTaskRunRecord.id).all()

    def test_record_task_run_writes_row_and_prunes_expired(self) -> None:
        record_task_run("etf_daily_sync", "success", duration_ms=1234)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].task_name, "etf_daily_sync")
        self.assertEqual(rows[0].status, "success")
        self.assertEqual(rows[0].duration_ms, 1234)

        # 过期行在下次写入时被清理（按 created_at 判断，不看 id：
        # SQLite 会复用被删除的 rowid，新行可能拿到与过期行相同的 id）
        with self.db.get_session() as session:
            from datetime import datetime, timedelta

            session.execute(
                ScheduledTaskRunRecord.__table__.update().values(
                    created_at=datetime.now() - timedelta(days=30)
                )
            )
            session.commit()
        record_task_run("etf_daily_sync", "success")
        from datetime import datetime, timedelta

        cutoff = datetime.now() - timedelta(days=7)
        with self.db.get_session() as session:
            expired = (
                session.query(ScheduledTaskRunRecord)
                .filter(ScheduledTaskRunRecord.created_at < cutoff)
                .count()
            )
            total = session.query(ScheduledTaskRunRecord).count()
        self.assertEqual(expired, 0)
        self.assertEqual(total, 1)

    def test_wrap_convention_success_noop_failure_and_raise(self) -> None:
        # 真值返回 → success
        wrap_background_task(lambda: {"status": "completed"}, "task_a")()
        # None 空跳 → 不记录
        wrap_background_task(lambda: None, "task_b")()
        # 失败标记 → failed，detail 落库
        wrap_background_task(lambda: task_failed("boom"), "task_c")()
        # 抛异常 → failed 后原样上抛
        def _raising():
            raise RuntimeError("kaboom")

        wrapped_raise = wrap_background_task(_raising, "task_d")
        with self.assertRaises(RuntimeError):
            wrapped_raise()

        runs = list_last_runs()
        self.assertEqual(runs["task_a"]["status"], "success")
        self.assertEqual(runs["task_a"]["last_success_at"], runs["task_a"]["created_at"])
        self.assertNotIn("task_b", runs)
        self.assertEqual(runs["task_c"]["status"], "failed")
        self.assertEqual(runs["task_c"]["detail"], "boom")
        self.assertEqual(runs["task_d"]["status"], "failed")
        self.assertIn("kaboom", runs["task_d"]["detail"])

    def test_wrap_result_passthrough(self) -> None:
        marker = {"value": 42}
        result = wrap_background_task(lambda: marker, "task_e")()
        self.assertIs(result, marker)

    def test_recorder_failure_never_breaks_task(self) -> None:
        """记录器自身故障（DB 不可用）时任务行为不受影响。"""
        with patch(
            "src.services.scheduled_task_registry.DatabaseManager.get_instance",
            side_effect=RuntimeError("db down"),
        ):
            result = wrap_background_task(lambda: "ok", "task_f")()
        self.assertEqual(result, "ok")

    def test_no_name_returns_task_unwrapped(self) -> None:
        task = lambda: "x"  # noqa: E731
        self.assertIs(wrap_background_task(task, None), task)

    def test_overview_merges_registry_config_and_runs(self) -> None:
        record_task_run("etf_daily_sync", "success", duration_ms=500)
        config = SimpleNamespace(
            agent_event_monitor_enabled=True,
            agent_event_monitor_interval_minutes=5,
            cb_sync_enabled=True,
            cb_intraday_sync_time="14:20",
            cb_after_close_sync_time="20:00",
            portfolio_snapshot_time="20:05",
            live_trade_summary_enabled=True,
            live_trade_summary_time="15:20",
            weekly_review_enabled=True,
            weekly_review_time="01:00",
        )
        items = {item["name"]: item for item in build_scheduled_task_overview(config)}

        self.assertEqual(items["agent_event_monitor"]["schedule"], "每 5 分钟")
        self.assertTrue(items["agent_event_monitor"]["enabled"])
        self.assertEqual(items["cb_after_close_sync"]["schedule"], "每交易日 20:00")
        self.assertTrue(items["etf_daily_sync"]["enabled"])
        self.assertEqual(items["etf_daily_sync"]["last_run_status"], "success")
        self.assertIsNotNone(items["etf_daily_sync"]["last_success_at"])
        # 开关关闭的任务仍列出，但 enabled=False
        config.agent_event_monitor_enabled = False
        items2 = {item["name"]: item for item in build_scheduled_task_overview(config)}
        self.assertFalse(items2["agent_event_monitor"]["enabled"])
        # 从未运行的任务 last_run 为空
        self.assertIsNone(items2["weekly_portfolio_review"]["last_run_at"])


class ScheduledTaskRegistryDriftTestCase(unittest.TestCase):
    def test_registry_covers_all_scheduler_background_task_names(self) -> None:
        """调度器源码里注册的后台任务名必须都在注册表中（新增任务须同步登记）。"""
        project_root = Path(__file__).resolve().parents[1]
        source = (project_root / "src" / "services" / "runtime_scheduler.py").read_text(encoding="utf-8")
        names = set(registered_background_task_names(source))
        self.assertTrue(names, "task name extraction should find entries")
        missing = names - set(SCHEDULED_TASK_METADATA)
        self.assertEqual(missing, set(), f"tasks missing from registry: {missing}")

    def test_registry_entries_have_required_fields(self) -> None:
        for name, meta in SCHEDULED_TASK_METADATA.items():
            with self.subTest(task=name):
                self.assertTrue(meta.get("label"))
                self.assertTrue(meta.get("description"))
                self.assertTrue(meta.get("schedule"))
                self.assertTrue(callable(meta.get("enabled_resolver")))

    def test_resolve_schedule_time_env_override(self) -> None:
        with patch.dict(os.environ, {"ETF_DAILY_SYNC_TIME": "21:00"}):
            self.assertIn("21:00", resolve_schedule_text("etf_daily_sync", SimpleNamespace()))
        # env 未设时回落 config 属性/缺省
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ETF_DAILY_SYNC_TIME", None)
            text = resolve_schedule_text("etf_daily_sync", SimpleNamespace())
            self.assertIn("20:30", text)


if __name__ == "__main__":
    unittest.main()
