"""Read metadata out of the pipeline SQL, so tests and reconciliation don't duplicate the rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PIPELINES = Path(__file__).resolve().parents[1] / "pipelines"


@dataclass(frozen=True)
class Expectation:
    name: str
    expr: str  # whitespace-normalized SQL boolean expression
    action: str  # 'drop' | 'fail' | 'warn'


def strip_sql_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def parse_expectations(sql: str) -> list[Expectation]:
    """All `CONSTRAINT name EXPECT (expr) [ON VIOLATION ...]` clauses, in file order."""
    body = strip_sql_comments(sql)
    out = []
    for m in re.finditer(r"CONSTRAINT\s+(\w+)\s+EXPECT\s*\(", body):
        depth, i = 1, m.end()
        while depth:  # balanced-paren scan: expressions contain IN (...) lists
            if i >= len(body):
                raise ValueError(f"Unbalanced parentheses in expectation {m.group(1)}")
            depth += {"(": 1, ")": -1}.get(body[i], 0)
            i += 1
        expr = " ".join(body[m.end() : i - 1].split())
        tail = body[i : i + 40].upper()
        action = "drop" if "DROP ROW" in tail else "fail" if "FAIL UPDATE" in tail else "warn"
        out.append(Expectation(m.group(1), expr, action))
    return out


def silver_complaint_expectations() -> list[Expectation]:
    """Expectations on the complaints_typed view (the ones that gate silver.complaints)."""
    sql = (PIPELINES / "silver.sql").read_text()
    body = strip_sql_comments(sql)
    start = body.index("CREATE TEMPORARY VIEW complaints_typed")
    end = body.index("FROM STREAM(bronze.complaints_raw)", start)
    return parse_expectations(body[start:end])
