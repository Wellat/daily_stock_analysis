"""Authenticated SQLite SQL console endpoints."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from api.deps import get_database_manager
from api.v1.schemas.sql import SqlExecuteRequest, SqlExecuteResponse, SqlTablesResponse
from src.storage import DatabaseManager

router = APIRouter()
_MAX_ROWS = 500
_MAX_UPDATE_ROWS = 50


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, Decimal, bytes)):
        return value.isoformat() if hasattr(value, "isoformat") else value.decode("utf-8", "replace")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _statement_type(sql: str) -> str:
    match = re.match(r"\s*(?:--[^\n]*\n|/\*.*?\*/\s*)*([A-Za-z]+)", sql, re.S)
    return (match.group(1).upper() if match else "UNKNOWN")


def _validate_single_statement(sql: str) -> None:
    # SQLite's execute() rejects most multi-statement input; reject explicit
    # trailing statements consistently before opening a transaction.
    stripped = re.sub(r"(--[^\n]*|/\*.*?\*/)", "", sql, flags=re.S).strip()
    if not stripped:
        raise HTTPException(status_code=400, detail="SQL 不能为空")
    if ";" in stripped.rstrip(";"):
        raise HTTPException(status_code=400, detail="一次只能执行一条 SQL 语句")


def _validate_statement(sql: str, kind: str) -> None:
    if kind not in {"SELECT", "UPDATE"}:
        raise HTTPException(status_code=400, detail="SQL 控制台仅允许执行 SELECT 或 UPDATE 语句")
    if kind == "UPDATE":
        # Comments have already been removed for statement-boundary checks;
        # require WHERE in the executable UPDATE text to prevent full-table writes.
        without_comments = re.sub(r"(--[^\n]*|/\*.*?\*/)", " ", sql, flags=re.S)
        if not re.search(r"\bWHERE\b", without_comments, flags=re.I):
            raise HTTPException(status_code=400, detail="UPDATE 语句必须包含 WHERE 条件")


@router.get("/tables", response_model=SqlTablesResponse)
def list_tables(db_manager: DatabaseManager = Depends(get_database_manager)) -> SqlTablesResponse:
    try:
        tables = inspect(db_manager._engine).get_table_names()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=500, detail="无法读取数据库表列表") from exc
    return SqlTablesResponse(tables=sorted(t for t in tables if not t.startswith("sqlite_")))


@router.post("/execute", response_model=SqlExecuteResponse)
def execute_sql(payload: SqlExecuteRequest, db_manager: DatabaseManager = Depends(get_database_manager)) -> SqlExecuteResponse:
    _validate_single_statement(payload.sql)
    kind = _statement_type(payload.sql)
    _validate_statement(payload.sql, kind)
    try:
        with db_manager._engine.begin() as connection:
            result = connection.execute(text(payload.sql))
            if kind == "UPDATE":
                # SQLite reports the exact number of rows changed through changes().
                # Raising inside the transaction rolls back an oversized update.
                affected_rows = connection.exec_driver_sql("SELECT changes()").scalar_one()
                if affected_rows > _MAX_UPDATE_ROWS:
                    raise HTTPException(status_code=400, detail=f"一次 UPDATE 最多只能影响 {_MAX_UPDATE_ROWS} 行")
                return SqlExecuteResponse(affected_rows=affected_rows, statement_type=kind)
            if result.returns_rows:
                columns = list(result.keys())
                raw_rows = result.fetchmany(_MAX_ROWS + 1)
                truncated = len(raw_rows) > _MAX_ROWS
                rows = [[_json_value(v) for v in row] for row in raw_rows[:_MAX_ROWS]]
                return SqlExecuteResponse(columns=columns, rows=rows, row_count=len(rows), truncated=truncated, statement_type=kind)
            return SqlExecuteResponse(affected_rows=result.rowcount if result.rowcount >= 0 else None, statement_type=kind)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=400, detail=f"SQL 执行失败: {exc}") from exc
