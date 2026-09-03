from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel, Field


class SqlExecuteRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=100_000)


class SqlExecuteResponse(BaseModel):
    columns: List[str] = Field(default_factory=list)
    rows: List[List[Optional[Any]]] = Field(default_factory=list)
    row_count: int = 0
    affected_rows: Optional[int] = None
    truncated: bool = False
    statement_type: str


class SqlTablesResponse(BaseModel):
    tables: List[str]
