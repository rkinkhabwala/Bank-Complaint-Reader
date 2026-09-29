"""Profile a local bulk snapshot (the split parts) with DuckDB and publish the results.

Writes:
  docs/profile/<snapshot>/*.csv   value sets with counts and first/last date_received; the
                                  taxonomy input for step 8, small enough to commit
  docs/data_notes.md              the block between the profile markers is regenerated

    python -m ingest.profile_bulk                      # latest snapshot under data/bulk/
    python -m ingest.profile_bulk --snapshot 2026-09-29

Local tooling only: DuckDB reads the same CSV parts we upload, with every column as VARCHAR, so
the profile shows the raw source values, not anything Spark infers.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

from ingest.reference import STATE_NAME_TO_CODE, USPS_CODES, classify_state
from ingest.schema import EXPECTED_COLUMNS

MARK_START = "<!-- profile:start -->"
MARK_END = "<!-- profile:end -->"

# Categorical columns whose full value set goes in the profile CSVs.
VALUE_SET_COLUMNS = (
    "Product",
    "Submitted via",
    "Company response to consumer",
    "Company public response",
    "Tags",
    "Timely response?",
    "State",
)


def replace_marked_block(
    text: str, block: str, start: str = MARK_START, end: str = MARK_END
) -> str:
    """Replace the content between the `start`/`end` markers (inclusive) or append it if absent."""
    new = f"{start}\n{block.rstrip()}\n{end}"
    i, j = text.find(start), text.find(end)
    if i == -1 and j == -1:
        return text.rstrip() + "\n\n" + new + "\n"
    if i == -1 or j == -1 or j < i:
        raise ValueError(f"Markers {start!r}/{end!r} are unbalanced")
    return text[:i] + new + text[j + len(end) :]


def fmt_int(n: int | None) -> str:
    return "-" if n is None else f"{n:,}"


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.2f}%" if d else "-"


def md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def latest_snapshot(data_dir: Path) -> str:
    snaps = sorted(p.name for p in (data_dir / "bulk").iterdir() if (p / "manifest.json").exists())
    if not snaps:
        sys.exit("No split snapshot found; run `python -m ingest.download_bulk --skip-upload`.")
    return snaps[-1]


def write_csv(path: Path, header: list[str], rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def profile(snapshot_dir: Path, out_dir: Path) -> str:
    import duckdb

    manifest = json.loads((snapshot_dir / "manifest.json").read_text())
    con = duckdb.connect()
    parts = str(snapshot_dir / "parts" / "*.csv")
    # Empty fields read as NULL. strict_mode fails on malformed quoting instead of guessing.
    con.execute(
        "CREATE TABLE t AS SELECT * FROM read_csv(?, header=true, all_varchar=true, "
        "quote='\"', escape='\"', strict_mode=true)",
        [parts],
    )
    one = lambda sql: con.sql(sql).fetchone()  # noqa: E731
    rows = lambda sql: con.sql(sql).fetchall()  # noqa: E731
    total = one("SELECT count(*) FROM t")[0]
    if total != manifest["rows"]:
        raise AssertionError(f"DuckDB counted {total:,} rows, manifest says {manifest['rows']:,}")

    # --- per-column null/distinct -----------------------------------------------------------
    col_stats = []
    for c in EXPECTED_COLUMNS:
        nulls, distinct = one(f'SELECT count(*) - count("{c}"), count(DISTINCT "{c}") FROM t')
        col_stats.append((c, nulls, distinct))
    write_csv(out_dir / "column_stats.csv", ["column", "null_count", "distinct_count"], col_stats)

    # --- value sets with eras ---------------------------------------------------------------
    for c in VALUE_SET_COLUMNS:
        vs = rows(
            f'SELECT "{c}", count(*), min("Date received"), max("Date received") '
            f"FROM t GROUP BY 1 ORDER BY 3, 1 NULLS FIRST"
        )
        slug = c.lower().replace("?", "").replace(" ", "_")
        write_csv(out_dir / f"values_{slug}.csv", ["value", "rows", "first_seen", "last_seen"], vs)

    taxonomy_sql = """
        SELECT "Product", "Sub-product", "Issue", "Sub-issue",
               count(*) AS rows,
               min("Date received") AS first_seen, max("Date received") AS last_seen
        FROM t GROUP BY ALL ORDER BY 1, 2 NULLS FIRST, 3 NULLS FIRST, 4 NULLS FIRST"""
    tax = rows(taxonomy_sql)
    write_csv(
        out_dir / "taxonomy_combinations.csv",
        ["product", "sub_product", "issue", "sub_issue", "rows", "first_seen", "last_seen"],
        tax,
    )

    # --- facts for the markdown summary -----------------------------------------------------
    ids_distinct, id_min, id_max, id_nonnumeric = one(
        'SELECT count(DISTINCT "Complaint ID"), min(try_cast("Complaint ID" AS BIGINT)), '
        'max(try_cast("Complaint ID" AS BIGINT)), '
        "count(*) FILTER (WHERE NOT regexp_matches(\"Complaint ID\", '^[0-9]+$')) FROM t"
    )
    bad_recv, bad_sent = one(
        "SELECT count(*) FILTER (WHERE try_strptime(\"Date received\", '%Y-%m-%d') IS NULL), "
        "count(*) FILTER (WHERE try_strptime(\"Date sent to company\", '%Y-%m-%d') IS NULL) FROM t"
    )
    snap_date = manifest["snapshot_id"]
    future = one(f"SELECT count(*) FROM t WHERE \"Date received\"::DATE > DATE '{snap_date}'")[0]
    sent_before = one(
        'SELECT count(*) FROM t WHERE "Date sent to company"::DATE < "Date received"::DATE'
    )[0]
    per_year = rows('SELECT year("Date received"::DATE), count(*) FROM t GROUP BY 1 ORDER BY 1')
    recent_months = rows(
        "SELECT strftime(\"Date received\"::DATE, '%Y-%m'), count(*) FROM t "
        # Whole months only: start on the 1st, six months before the snapshot's month.
        f'WHERE "Date received"::DATE >= '
        f"date_trunc('month', DATE '{snap_date}') - INTERVAL 6 MONTH "
        "GROUP BY 1 ORDER BY 1"
    )
    in_progress = one(
        "SELECT count(*) FROM t WHERE \"Company response to consumer\" = 'In progress'"
    )[0]
    zip_patterns = rows(
        "SELECT CASE WHEN \"ZIP code\" IS NULL THEN '(null)' "
        "WHEN regexp_matches(\"ZIP code\", '^[0-9]{5}$') THEN '5 digits' "
        "WHEN regexp_matches(\"ZIP code\", '^[0-9]{3}XX$') THEN '3 digits + XX (masked)' "
        "WHEN \"ZIP code\" = 'XXXXX' THEN 'XXXXX (fully masked)' ELSE 'other/malformed' END, "
        "count(*) FROM t GROUP BY 1 ORDER BY 2 DESC"
    )
    top_companies = rows('SELECT "Company", count(*) FROM t GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10')
    products = rows(
        'SELECT "Product", count(*), min("Date received"), max("Date received") '
        "FROM t GROUP BY 1 ORDER BY 3, 1"
    )
    state_counts = rows('SELECT "State", count(*) FROM t GROUP BY 1')
    state_classes: dict[str, int] = {}
    for value, n in state_counts:
        k = classify_state(value)
        state_classes[k] = state_classes.get(k, 0) + n
    seen_codes = {v for v, _ in state_counts if v in USPS_CODES}
    unused_codes = sorted(USPS_CODES - seen_codes)
    non_usps = sorted(
        (v, n) for v, n in state_counts if classify_state(v) in ("normalizable", "invalid")
    )
    pair_counts = one(
        'SELECT (SELECT count(*) FROM (SELECT DISTINCT "Product", "Sub-product" FROM t)), '
        '(SELECT count(*) FROM (SELECT DISTINCT "Product", "Issue" FROM t))'
    )

    # --- markdown ---------------------------------------------------------------------------
    by_col = {c: (n, d) for c, n, d in col_stats}
    md = [
        f"### Profile of bulk snapshot {snap_date}",
        "",
        f"Generated by `python -m ingest.profile_bulk` over `data/bulk/{snap_date}/parts/*.csv` "
        f"(DuckDB, all columns VARCHAR, empty field = NULL). Full value sets: "
        f"[docs/profile/{snap_date}/](profile/{snap_date}/).",
        "",
        "**Integrity**",
        "",
        md_table(
            ["Check", "Result"],
            [
                ["Rows (DuckDB) = manifest rows", f"{total:,} = {manifest['rows']:,} ✅"],
                [
                    "Distinct `Complaint ID`",
                    f"{ids_distinct:,} (duplicates: {total - ids_distinct:,})",
                ],
                ["Non-numeric `Complaint ID`", fmt_int(id_nonnumeric)],
                ["`Complaint ID` range", f"{id_min:,} .. {id_max:,} (not dense)"],
                [
                    "Unparseable `Date received` / `Date sent to company`",
                    f"{bad_recv:,} / {bad_sent:,}",
                ],
                [f"`Date received` after snapshot date ({snap_date})", fmt_int(future)],
                [
                    "`Date sent to company` < `Date received`",
                    f"{sent_before:,} ({pct(sent_before, total)})",
                ],
                [
                    "`Company response` = 'In progress' (will change later)",
                    f"{in_progress:,} ({pct(in_progress, total)})",
                ],
            ],
        ),
        "",
        "**Nulls per column** (expectation-relevant: `Product` / `Complaint ID` / `State`)",
        "",
        md_table(
            ["Column", "Nulls", "% null", "Distinct"],
            [
                [f"`{c}`", fmt_int(by_col[c][0]), pct(by_col[c][0], total), fmt_int(by_col[c][1])]
                for c in EXPECTED_COLUMNS
            ],
        ),
        "",
        "**State** (valid set = USPS Pub 28 Appendix B, 62 codes; `ingest/reference.py`)",
        "",
        md_table(
            ["Class", "Rows", "%"],
            [
                [k, fmt_int(state_classes.get(k, 0)), pct(state_classes.get(k, 0), total)]
                for k in ("usps", "normalizable", "invalid", "missing")
            ],
        ),
        "",
        "Non-USPS values: "
        + (
            ", ".join(
                f"`{v}` ({n:,}; maps to `{STATE_NAME_TO_CODE.get(v, '?')}`)" for v, n in non_usps
            )
            or "none"
        )
        + f". USPS codes never seen: {', '.join(unused_codes) or 'none'}.",
        "",
        "**Products and their eras** (three taxonomy generations are visible from the dates)",
        "",
        md_table(
            ["Product", "Rows", "First seen", "Last seen"],
            [[p, f"{n:,}", a, b] for p, n, a, b in products],
        ),
        "",
        f"Distinct (product, sub-product) pairs: {pair_counts[0]:,}; (product, issue) pairs: "
        f"{pair_counts[1]:,}; (product, sub-product, issue, sub-issue) combinations: {len(tax):,}.",
        "",
        "**Volume per year** (note the jump from 2023)",
        "",
        md_table(["Year", "Rows"], [[y, f"{n:,}"] for y, n in per_year]),
        "",
        "**Last 6 full months + snapshot month** (the last row is partial: snapshot date and "
        "reporting lag)",
        "",
        md_table(["Month", "Rows"], [[m, f"{n:,}"] for m, n in recent_months]),
        "",
        "**ZIP code masking**",
        "",
        md_table(["Pattern", "Rows", "%"], [[p, f"{n:,}", pct(n, total)] for p, n in zip_patterns]),
        "",
        "**Top 10 companies**",
        "",
        md_table(
            ["Company", "Rows", "% of all"],
            [[c, f"{n:,}", pct(n, total)] for c, n in top_companies],
        ),
    ]
    return "\n".join(md)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-dir", type=Path, default=Path(os.environ.get("CR_DATA_DIR", "data"))
    )
    parser.add_argument("--snapshot", help="snapshot id (YYYY-MM-DD); default: latest")
    parser.add_argument("--notes", type=Path, default=Path("docs/data_notes.md"))
    args = parser.parse_args(argv)

    snap = args.snapshot or latest_snapshot(args.data_dir)
    out_dir = Path("docs/profile") / snap
    block = profile(args.data_dir / "bulk" / snap, out_dir)
    args.notes.write_text(replace_marked_block(args.notes.read_text(), block))
    print(f"[profile] wrote {out_dir}/ and updated {args.notes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
