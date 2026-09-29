"""Reconcile source -> bronze -> silver after a pipeline run and publish the results.

    python -m ingest.reconcile                 # query the workspace, update docs/data_notes.md
    python -m ingest.reconcile --pipeline-id <id>

Every number comes from the workspace, and every check compares two independently obtained numbers:
  * source rows (upload manifest in the Volume)   vs  bronze rows, per snapshot and per part file
  * bronze distinct complaint IDs, minus drops    vs  silver rows
  * expectation failures in the pipeline event log vs the same predicate counted in silver
    (the predicates are parsed from pipelines/silver.sql, not re-typed here)
  * silver.taxonomy_map coverage

Exit code 1 if any check fails, so it can gate a job later.

Event log querying: https://docs.databricks.com/aws/en/ldp/monitor-event-logs (checked 2026-09-29).
Only the pipeline's run-as user can read event_log(); run this as that user.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ingest.sqlmeta import Expectation, silver_complaint_expectations

MARK_START = "<!-- reconcile:start -->"
MARK_END = "<!-- reconcile:end -->"
PIPELINE_NAME = "complaint_radar_pipeline"


@dataclass
class Facts:
    """Everything the checks need, gathered from the workspace (or built by hand in tests)."""

    catalog: str
    pipeline_id: str
    manifests: dict[str, dict]  # snapshot_id -> upload manifest
    bronze_by_file: dict[str, int]  # "<snapshot>/<part file name>" -> rows (bulk_csv only)
    bronze_rows: int
    bronze_distinct_ids: int
    bronze_rescued_rows: int
    silver_rows: int
    silver_distinct_ids: int
    expectations: list[Expectation]
    event_log_failed: dict[str, int]  # expectation name -> failed_records summed over updates
    silver_failing: dict[str, int]  # expectation name -> rows in silver failing the predicate
    bronze_distinct_ids_passing_drops: int
    updates: list[dict] = field(default_factory=list)  # [{update_id, timestamp}]
    taxonomy_combinations: int = 0
    taxonomy_unmapped: int = 0
    taxonomy_rows: int = 0
    gathered_at: str = ""


@dataclass(frozen=True)
class Check:
    name: str
    expected: str
    actual: str
    ok: bool
    note: str = ""


def _fmt(n: int | None) -> str:
    return "n/a" if n is None else f"{n:,}"


def evaluate(f: Facts) -> list[Check]:
    checks: list[Check] = []

    def add(name: str, expected: int, actual: int, note: str = "") -> None:
        checks.append(Check(name, _fmt(expected), _fmt(actual), expected == actual, note))

    # 1. Source -> bronze, per snapshot and per part
    for snap, m in sorted(f.manifests.items()):
        bronze_snap = sum(n for k, n in f.bronze_by_file.items() if k.startswith(f"{snap}/"))
        add(f"source rows = bronze rows (snapshot {snap})", m["rows"], bronze_snap)
        expected_parts = {f"{snap}/{p['name']}": p["rows"] for p in m["parts"]}
        loaded_parts = {k: n for k, n in f.bronze_by_file.items() if k.startswith(f"{snap}/")}
        bad = sorted(
            k
            for k in expected_parts.keys() | loaded_parts.keys()
            if expected_parts.get(k) != loaded_parts.get(k)
        )
        checks.append(
            Check(
                f"per-part row counts match manifest (snapshot {snap})",
                f"{len(expected_parts)} parts",
                f"{len(expected_parts) - len(bad)} match",
                not bad,
                ("mismatched: " + ", ".join(bad[:5]) + (" …" if len(bad) > 5 else ""))
                if bad
                else "",
            )
        )
    unknown = sorted({k.split("/", 1)[0] for k in f.bronze_by_file} - set(f.manifests))
    if unknown:
        checks.append(
            Check("bronze snapshots have a manifest", "all", "missing", False, str(unknown))
        )

    # 2. Bronze -> silver
    add(
        "rows with _rescued_data in bronze",
        0,
        f.bronze_rescued_rows,
        "values that didn't fit the schema",
    )
    add(
        "silver rows = distinct bronze IDs passing drop expectations",
        f.bronze_distinct_ids_passing_drops,
        f.silver_rows,
        "AUTO CDC keeps one row per complaint_id",
    )
    add("duplicate complaint_id in silver", 0, f.silver_rows - f.silver_distinct_ids)

    # 3. Event log vs direct counts. Drop expectations: silver must contain no failing rows.
    #    Warn expectations: event-log failures = failing silver rows when bronze has 1 row per ID.
    one_row_per_id = f.bronze_rows == f.bronze_distinct_ids
    for e in f.expectations:
        if e.name not in f.event_log_failed:
            checks.append(
                Check(f"expectation `{e.name}` reported in event log", "present", "missing", False)
            )
            continue
        if e.action == "drop":
            add(
                f"`{e.name}` ({e.action}): failing rows left in silver", 0, f.silver_failing[e.name]
            )
        elif one_row_per_id:
            add(
                f"`{e.name}` ({e.action}): event log = silver count",
                f.event_log_failed[e.name],
                f.silver_failing[e.name],
            )

    # 4. Taxonomy
    add("unmapped taxonomy combinations", 0, f.taxonomy_unmapped, "new CFPB labels -> update seeds")
    add("taxonomy_map complaint_count total = silver rows", f.silver_rows, f.taxonomy_rows)
    return checks


def render(f: Facts, checks: list[Check]) -> str:
    total_dropped = sum(
        f.event_log_failed.get(e.name, 0) for e in f.expectations if e.action == "drop"
    )
    src = sum(m["rows"] for m in f.manifests.values())
    lines = [
        f"### Reconciliation run {f.gathered_at}",
        "",
        f"Generated by `python -m ingest.reconcile` against catalog `{f.catalog}`, pipeline "
        f"`{f.pipeline_id}` ({len(f.updates)} update(s) in the event log).",
        "",
        "**Funnel**",
        "",
        "| Stage | Rows | Distinct complaint_id |",
        "|---|---|---|",
        f"| Source (manifests: {', '.join(sorted(f.manifests)) or 'none'}) | {_fmt(src)} | |",
        f"| `bronze.complaints_raw` | {_fmt(f.bronze_rows)} | {_fmt(f.bronze_distinct_ids)} |",
        f"| dropped by expectations (event log) | {_fmt(total_dropped)} | |",
        f"| `silver.complaints` | {_fmt(f.silver_rows)} | {_fmt(f.silver_distinct_ids)} |",
        "",
        "**Expectations** (event log `flow_progress.data_quality`, summed over all updates)",
        "",
        "| Expectation | Action | Failed (event log) | Failing rows in silver |",
        "|---|---|---|---|",
    ]
    for e in f.expectations:
        lines.append(
            f"| `{e.name}` | {e.action} | {_fmt(f.event_log_failed.get(e.name))} | "
            f"{_fmt(f.silver_failing.get(e.name))} |"
        )
    passed = sum(c.ok for c in checks)
    lines += [
        "",
        f"**Checks: {passed}/{len(checks)} passed**",
        "",
        "| | Check | Expected | Actual | Note |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {'✅' if c.ok else '❌'} | {c.name} | {c.expected} | {c.actual} | {c.note} |"
        for c in checks
    ]
    lines += [
        "",
        f"Taxonomy: {_fmt(f.taxonomy_combinations)} observed (product, sub_product, issue) "
        f"combinations, {_fmt(f.taxonomy_unmapped)} unmapped.",
        "",
        # TODO(verify): the event-log docs don't show how to tell a full refresh apart; after one,
        # summing failed_records over all updates would double count. Updates listed for audit.
        "Updates: "
        + (", ".join(f"`{u['update_id']}` ({u['timestamp']})" for u in f.updates) or "none"),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# Workspace access
# ---------------------------------------------------------------------------------------------


def find_pipeline_id(w, name: str = PIPELINE_NAME) -> str:
    hits = list(w.pipelines.list_pipelines(filter=f"name LIKE '%{name}%'"))
    if len(hits) != 1:
        names = [h.name for h in hits]
        sys.exit(f"Expected one pipeline matching '{name}', found {names}. Pass --pipeline-id.")
    return hits[0].pipeline_id


def gather(w, warehouse_id: str, catalog: str, pipeline_id: str) -> Facts:
    from ingest.dbsql import query

    def one(sql: str) -> dict:
        return query(w, warehouse_id, sql)[0]

    def i(v) -> int:
        return int(v or 0)

    exps = silver_complaint_expectations()
    bronze = f"{catalog}.bronze.complaints_raw"
    silver = f"{catalog}.silver.complaints"

    by_file = {
        f"{r['snap']}/{r['name']}": i(r["n"])
        for r in query(
            w,
            warehouse_id,
            f"SELECT _snapshot_id AS snap, regexp_extract(_source_file, '[^/]+$', 0) AS name, "
            f"count(*) AS n FROM {bronze} WHERE _source_kind = 'bulk_csv' GROUP BY 1, 2",
        )
    }
    manifests = {}
    for snap in sorted({k.split("/", 1)[0] for k in by_file}):
        path = f"/Volumes/{catalog}/raw/landing/_manifests/bulk_{snap}.json"
        manifests[snap] = json.loads(w.files.download(path).contents.read())

    b = one(
        f"SELECT count(*) AS n, count(DISTINCT complaint_id) AS d, "
        f"count_if(_rescued_data IS NOT NULL) AS r FROM {bronze}"
    )
    drop_pred = " AND ".join(f"({e.expr})" for e in exps if e.action == "drop") or "true"
    passing = one(
        f"SELECT count(DISTINCT complaint_id) AS d FROM {bronze} WHERE {drop_pred}"
    )  # bronze columns carry the same names/types as the typed view for these predicates
    s = one(f"SELECT count(*) AS n, count(DISTINCT complaint_id) AS d FROM {silver}")
    fail_cols = ", ".join(f"count_if(NOT coalesce({e.expr}, false)) AS `{e.name}`" for e in exps)
    silver_failing = {k: i(v) for k, v in one(f"SELECT {fail_cols} FROM {silver}").items()}

    names = ", ".join(f"'{e.name}'" for e in exps)
    # Schema string from the docs example, with bigint instead of int for 18M-row counts.
    dq_schema = (
        "array<struct<name: string, dataset: string, "
        "passed_records: bigint, failed_records: bigint>>"
    )
    event_rows = query(
        w,
        warehouse_id,
        f"""SELECT x.name AS name, sum(x.failed_records) AS failed
            FROM (SELECT explode(from_json(details:flow_progress:data_quality:expectations,
                    '{dq_schema}')) x
                  FROM event_log('{pipeline_id}') WHERE event_type = 'flow_progress')
            WHERE x.name IN ({names}) GROUP BY x.name""",
    )
    updates = query(
        w,
        warehouse_id,
        f"SELECT origin.update_id AS update_id, CAST(timestamp AS STRING) AS timestamp "
        f"FROM event_log('{pipeline_id}') WHERE event_type = 'create_update' ORDER BY timestamp",
    )
    t = one(
        f"SELECT count(*) AS c, count_if(NOT is_mapped) AS u, sum(complaint_count) AS n "
        f"FROM {catalog}.silver.taxonomy_map"
    )
    return Facts(
        catalog=catalog,
        pipeline_id=pipeline_id,
        manifests=manifests,
        bronze_by_file=by_file,
        bronze_rows=i(b["n"]),
        bronze_distinct_ids=i(b["d"]),
        bronze_rescued_rows=i(b["r"]),
        silver_rows=i(s["n"]),
        silver_distinct_ids=i(s["d"]),
        expectations=exps,
        event_log_failed={r["name"]: i(r["failed"]) for r in event_rows},
        silver_failing=silver_failing,
        bronze_distinct_ids_passing_drops=i(passing["d"]),
        updates=updates,
        taxonomy_combinations=i(t["c"]),
        taxonomy_unmapped=i(t["u"]),
        taxonomy_rows=i(t["n"]),
        gathered_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    from ingest.profile_bulk import replace_marked_block

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", default=os.environ.get("CR_CATALOG", "complaint_radar"))
    parser.add_argument("--pipeline-id")
    parser.add_argument("--notes", type=Path, default=Path("docs/data_notes.md"))
    args = parser.parse_args(argv)

    from databricks.sdk import WorkspaceClient

    from ingest.dbsql import pick_warehouse

    w = WorkspaceClient()
    pipeline_id = args.pipeline_id or find_pipeline_id(w)
    facts = gather(w, pick_warehouse(w), args.catalog, pipeline_id)
    checks = evaluate(facts)
    block = render(facts, checks)
    args.notes.write_text(
        replace_marked_block(args.notes.read_text(), block, start=MARK_START, end=MARK_END)
    )
    print(block)
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
