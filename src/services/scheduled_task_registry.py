# -*- coding: utf-8 -*-
"""Scheduled background-task registry and heartbeat recorder.

定时任务总览（Web 行情数据-数据同步页）的数据源：

1. ``SCHEDULED_TASK_METADATA``：调度器后台任务的静态注册表（名称、中文名、
   同步内容、频率文案解析、开关来源）。新增后台任务时应同步登记，测试里
   有注册表与 ``runtime_scheduler`` 实际任务名的漂移守护。
2. 心跳记录：``wrap_background_task`` 包装任务函数后由调度器统一执行，
   按返回值约定落 ``scheduled_task_runs``：

   - 返回真值 → 本次实际做了事，记 ``success``
   - 返回 ``task_failed(detail)`` → 业务失败（任务内部已捕获异常），记 ``failed``
   - 返回 ``None`` → 时刻未到的空 tick，不记录
   - 抛异常 → 记 ``failed`` 后原样上抛（保持既有调度器行为）

   记录动作全程吞异常：总览是观测能力，绝不影响任务本身执行。
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from src.storage import DatabaseManager, ScheduledTaskRunRecord
from src.utils.sanitize import sanitize_diagnostic_text

logger = logging.getLogger(__name__)

TASK_RETENTION_DAYS = 7
_FAILED_MARKER = "__task_failed__"


def task_failed(detail: Any = None) -> Dict[str, Any]:
    """任务内部捕获异常后返回的失败标记（不再上抛，由记录器落库）。"""
    return {"_marker": _FAILED_MARKER, "detail": detail}


def _is_failed_marker(value: Any) -> bool:
    return isinstance(value, dict) and value.get("_marker") == _FAILED_MARKER


def record_task_run(
    task_name: str,
    status: str,
    *,
    detail: Optional[str] = None,
    duration_ms: Optional[int] = None,
    db_manager: Optional[DatabaseManager] = None,
) -> None:
    """Best-effort 写入一条任务心跳并清理过期行；任何异常只记日志。"""
    try:
        db = db_manager or DatabaseManager.get_instance()
        now = datetime.now()
        with db.get_session() as session:
            session.add(
                ScheduledTaskRunRecord(
                    task_name=str(task_name or "unknown")[:64],
                    status=status,
                    detail=sanitize_diagnostic_text(detail) if detail else None,
                    duration_ms=duration_ms,
                )
            )
            session.execute(
                ScheduledTaskRunRecord.__table__.delete().where(
                    ScheduledTaskRunRecord.created_at < now - timedelta(days=TASK_RETENTION_DAYS)
                )
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001 - 观测路径绝不影响任务执行
        logger.debug("[ScheduledTaskRuns] record failed for %s: %s", task_name, exc)


def wrap_background_task(task: Callable[[], Any], name: Optional[str]) -> Callable[[], Any]:
    """按返回值约定包装后台任务，统一记录心跳（见模块 docstring）。

    幂等：同一任务函数重复包装（调度器 reconcile 重建）返回同一个包装，
    保持任务闭包跨重建的同一性。
    """
    if not name:
        return task
    existing = getattr(task, "__scheduled_task_wrap__", None)
    if existing is not None:
        return existing

    def wrapped() -> Any:
        started = time.monotonic()
        try:
            result = task()
        except Exception as exc:
            record_task_run(
                name,
                "failed",
                detail=str(exc) or "task raised",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            raise
        if result is None:
            return None
        if _is_failed_marker(result):
            record_task_run(
                name,
                "failed",
                detail=str(result.get("detail") or "task reported failure"),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return None
        record_task_run(
            name,
            "success",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    try:
        task.__scheduled_task_wrap__ = wrapped  # type: ignore[attr-defined]
    except (AttributeError, TypeError):  # pragma: no cover - 不可设属性的对象直接返回包装
        return wrapped
    return wrapped


def list_last_runs(db_manager: Optional[DatabaseManager] = None) -> Dict[str, Dict[str, Any]]:
    """每个任务最近一次运行 + 最近一次成功时间（用于总览合并）。"""
    from sqlalchemy import desc, select

    db = db_manager or DatabaseManager.get_instance()
    with db.get_session() as session:
        rows = (
            session.execute(
                select(ScheduledTaskRunRecord)
                .order_by(desc(ScheduledTaskRunRecord.id))
                .limit(2000)
            )
            .scalars()
            .all()
        )
    last_run: Dict[str, Dict[str, Any]] = {}
    last_success: Dict[str, datetime] = {}
    for row in rows:  # id 倒序，首个即最新
        if row.task_name not in last_run:
            last_run[row.task_name] = {
                "status": row.status,
                "detail": row.detail,
                "duration_ms": row.duration_ms,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        if row.status == "success" and row.task_name not in last_success:
            last_success[row.task_name] = row.created_at
    return {
        name: {**run, "last_success_at": (last_success.get(name) or datetime.min).isoformat() if last_success.get(name) else None}
        for name, run in last_run.items()
    }


# ---------------------------------------------------------------------------
# 静态注册表：name → 元数据
#   schedule: 频率文案（可含 {time}/{interval} 占位，resolve 时按 config 填充）
#   enabled_resolver: (config) -> bool；开关与任务构建器读取同一来源
# ---------------------------------------------------------------------------

def _env_flag(key: str, default: bool = True) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


SCHEDULED_TASK_METADATA: Dict[str, Dict[str, Any]] = {
    "agent_event_monitor": {
        "label": "告警巡检",
        "description": "评估启用的告警规则并推送触发通知（盘中时段）",
        "schedule": "每 {interval} 分钟",
        "enabled_resolver": lambda config: bool(getattr(config, "agent_event_monitor_enabled", False)),
    },
    "live_strategy_schedule": {
        "label": "策略实例调度",
        "description": "order 实例实盘调仓、notify 实例 ETF 信号通知（按各自 schedule_time）",
        "schedule": "每 30 秒检查，按实例时刻触发",
        "enabled_resolver": lambda _config: _env_flag("LIVE_STRATEGY_SCHEDULE_ENABLED", True),
    },
    "cb_intraday_sync": {
        "label": "可转债盘中同步",
        "description": "可转债基础数据、行情与当日因子（盘中口径）",
        "schedule": "每交易日 {time}",
        "time_attr": "cb_intraday_sync_time",
        "time_default": "14:25",
        "enabled_resolver": lambda config: bool(getattr(config, "cb_sync_enabled", True)),
    },
    "cb_after_close_sync": {
        "label": "可转债盘后同步",
        "description": "可转债基础数据、行情、因子与持仓行情（盘后调度链路）",
        "schedule": "每交易日 {time}",
        "time_attr": "cb_after_close_sync_time",
        "time_default": "20:00",
        "enabled_resolver": lambda config: bool(getattr(config, "cb_sync_enabled", True)),
    },
    "etf_daily_sync": {
        "label": "ETF 日线同步",
        "description": "同步启用中 ETF 信号实例标的的后复权日线（增量）",
        "schedule": "每交易日 {time}",
        "time_env": "ETF_DAILY_SYNC_TIME",
        "time_default": "20:30",
        "enabled_resolver": lambda _config: _env_flag("ETF_DAILY_SYNC_ENABLED", True),
    },
    "portfolio_daily_snapshot": {
        "label": "持仓每日快照",
        "description": "全部活跃账户的当日持仓快照（持仓页趋势图数据源）",
        "schedule": "每交易日 {time}",
        "time_attr": "portfolio_snapshot_time",
        "time_default": "20:05",
        "enabled_resolver": lambda _config: True,
    },
    "live_trade_summary": {
        "label": "盘后成交汇总",
        "description": "汇总当日实盘成交并推送飞书（无成交也发一条）",
        "schedule": "每交易日 {time}",
        "time_attr": "live_trade_summary_time",
        "time_default": "15:20",
        "enabled_resolver": lambda config: bool(getattr(config, "live_trade_summary_enabled", True)),
    },
    "weekly_portfolio_review": {
        "label": "周度组合复盘",
        "description": "生成上一自然周的持仓复盘并推送飞书",
        "schedule": "每周六 {time}",
        "time_attr": "weekly_review_time",
        "time_default": "01:00",
        "enabled_resolver": lambda config: bool(getattr(config, "weekly_review_enabled", True)),
    },
}


def resolve_schedule_text(name: str, config: Any) -> str:
    meta = SCHEDULED_TASK_METADATA.get(name)
    if meta is None:
        return ""
    template = str(meta.get("schedule") or "")
    if "{time}" in template:
        time_value = None
        if meta.get("time_env"):
            time_value = os.getenv(meta["time_env"])
        if not time_value and meta.get("time_attr"):
            time_value = getattr(config, meta["time_attr"], None)
        template = template.replace("{time}", str(time_value or meta.get("time_default") or "--"))
    if "{interval}" in template:
        interval = getattr(config, "agent_event_monitor_interval_minutes", 5) or 5
        template = template.replace("{interval}", str(interval))
    return template


def build_scheduled_task_overview(config: Any) -> List[Dict[str, Any]]:
    """注册表 + 开关状态 + 最近运行 合并为总览列表（API 响应体）。"""
    last_runs = list_last_runs()
    items: List[Dict[str, Any]] = []
    for name, meta in SCHEDULED_TASK_METADATA.items():
        run = last_runs.get(name)
        items.append(
            {
                "name": name,
                "label": meta.get("label") or name,
                "description": meta.get("description") or "",
                "schedule": resolve_schedule_text(name, config),
                "enabled": bool(meta["enabled_resolver"](config)),
                "last_run_status": (run or {}).get("status"),
                "last_run_detail": (run or {}).get("detail"),
                "last_run_at": (run or {}).get("created_at"),
                "last_run_duration_ms": (run or {}).get("duration_ms"),
                "last_success_at": (run or {}).get("last_success_at"),
            }
        )
    return items


_BACKGROUND_TASK_NAME_PATTERN = re.compile(r'"name":\s*"([a-z_]+)"')


def registered_background_task_names(source_text: str) -> List[str]:
    """从调度器源码文本中提取后台任务名（测试漂移守护用）。"""
    return _BACKGROUND_TASK_NAME_PATTERN.findall(source_text)
