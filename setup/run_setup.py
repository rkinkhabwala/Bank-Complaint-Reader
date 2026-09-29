"""Run setup/create_uc_objects.sql on the SQL warehouse, then create the Volume subfolders.

Idempotent: every statement is IF NOT EXISTS and Files API create_directory is mkdir -p.

    python setup/run_setup.py                  # profile from DATABRICKS_CONFIG_PROFILE / .env
    python setup/run_setup.py --dry-run        # print the statements, touch nothing

Uses the Statement Execution API (databricks-sdk; signatures checked against SDK 0.143.0):
https://docs.databricks.com/api/workspace/statementexecution/executestatement
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SQL_FILE = Path(__file__).with_name("create_uc_objects.sql")
LANDING_SUBDIRS = ("bulk", "delta", "seeds")


def split_sql_statements(script: str) -> list[str]:
    """Split a SQL script into statements on `;`, ignoring `--` comments and quoted `;`.

    Handles single/double/backtick quotes with doubled-quote escaping. That covers our DDL
    scripts; it doesn't handle block comments or procedural SQL.
    """
    statements: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i, n = 0, len(script)
    while i < n:
        ch = script[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                if i + 1 < n and script[i + 1] == quote:  # escaped quote: '' or ""
                    buf.append(script[i + 1])
                    i += 1
                else:
                    quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
        elif ch == "-" and script.startswith("--", i):
            nl = script.find("\n", i)
            i = n if nl == -1 else nl  # keep the newline itself as whitespace
            continue
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    if quote:
        raise ValueError(f"Unterminated {quote} quote in SQL script")
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def landing_root(catalog: str) -> str:
    return f"/Volumes/{catalog}/raw/landing"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print statements and exit")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv()
    catalog = os.environ.get("CR_CATALOG", "complaint_radar")
    statements = split_sql_statements(SQL_FILE.read_text())
    if catalog != "complaint_radar":
        # Allow a catalog override (e.g. a personal sandbox) without templating the SQL.
        statements = [s.replace("complaint_radar", catalog) for s in statements]

    if args.dry_run:
        for s in statements:
            print(s + ";\n")
        print("mkdir -p", *(f"{landing_root(catalog)}/{d}" for d in LANDING_SUBDIRS))
        return 0

    from databricks.sdk import WorkspaceClient

    from ingest.dbsql import execute, pick_warehouse

    w = WorkspaceClient()  # honours DATABRICKS_CONFIG_PROFILE
    print(f"Workspace: {w.config.host} (profile: {w.config.profile})")
    warehouse_id = pick_warehouse(w)

    for s in statements:
        first_line = s.splitlines()[0]
        print(f"  running: {first_line}")
        try:
            execute(w, warehouse_id, s)
        except RuntimeError as e:
            if first_line.upper().startswith("CREATE CATALOG") and "storage root" in str(e).lower():
                print(
                    "\nCatalog creation failed: Free Edition Default Storage issue.\n"
                    "Create it in Catalog Explorer -> Create catalog -> 'Use default storage',\n"
                    f"name '{catalog}', then re-run this script.\n",
                    file=sys.stderr,
                )
            raise

    for d in LANDING_SUBDIRS:
        path = f"{landing_root(catalog)}/{d}"
        w.files.create_directory(path)
        print(f"  mkdir -p {path}")

    print("UC setup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
