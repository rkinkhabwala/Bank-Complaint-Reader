"""Minimal Databricks SQL warehouse helpers (Statement Execution API via databricks-sdk).

Signatures checked against databricks-sdk 0.143.0. API reference:
https://docs.databricks.com/api/workspace/statementexecution/executestatement
"""

from __future__ import annotations

import sys
import time
from typing import Any

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}


def pick_warehouse(w) -> str:
    whs = list(w.warehouses.list())
    if not whs:
        sys.exit("No SQL warehouse found. Free Edition includes one; check the workspace.")
    if len(whs) > 1:
        print(f"  note: {len(whs)} warehouses found, using the first: {whs[0].name}")
    return whs[0].id


def execute(w, warehouse_id: str, statement: str, timeout_s: int = 900):
    """Run one statement to completion; return the final StatementResponse or raise."""
    from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout

    resp = w.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout="50s",  # API allows 0s or 5s–50s; a stopped warehouse may take longer to start
        on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
    )
    deadline = time.monotonic() + timeout_s
    while resp.status.state.value not in TERMINAL_STATES:
        if time.monotonic() > deadline:
            w.statement_execution.cancel_execution(resp.statement_id)
            raise TimeoutError(f"Statement timed out after {timeout_s}s: {statement[:80]}")
        time.sleep(3)
        resp = w.statement_execution.get_statement(resp.statement_id)
    if resp.status.state.value != "SUCCEEDED":
        err = resp.status.error
        raise RuntimeError(f"{resp.status.state.value}: {err.message if err else 'unknown error'}")
    return resp


def query(w, warehouse_id: str, statement: str) -> list[dict[str, Any]]:
    """Run a SELECT and return rows as dicts (INLINE / JSON_ARRAY; values arrive as strings)."""
    resp = execute(w, warehouse_id, statement)
    cols = [c.name for c in resp.manifest.schema.columns]
    rows: list[list[Any]] = list(resp.result.data_array or []) if resp.result else []
    chunk = resp.result.next_chunk_index if resp.result else None
    while chunk is not None:
        part = w.statement_execution.get_statement_result_chunk_n(resp.statement_id, chunk)
        rows.extend(part.data_array or [])
        chunk = part.next_chunk_index
    return [dict(zip(cols, r, strict=True)) for r in rows]
