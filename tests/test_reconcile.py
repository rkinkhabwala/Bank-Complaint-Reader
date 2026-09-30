import copy

import pytest

from ingest.reconcile import Facts, evaluate, render
from ingest.sqlmeta import Expectation, parse_expectations, silver_complaint_expectations

EXPS = silver_complaint_expectations()
N = 18_062_308


def clean_facts() -> Facts:
    """A run that matches the predictions in docs/data_notes.md (snapshot 2026-09-29)."""
    parts = [{"name": f"complaints_part_{i:04d}.csv", "rows": 500_000} for i in range(1, 37)]
    parts.append({"name": "complaints_part_0037.csv", "rows": N - 36 * 500_000})
    failed = {e.name: 0 for e in EXPS}
    failed["state_present"] = 62_969
    return Facts(
        catalog="complaint_radar",
        pipeline_id="p-123",
        manifests={"2026-09-29": {"rows": N, "parts": parts}},
        bronze_by_file={f"2026-09-29/{p['name']}": p["rows"] for p in parts},
        bronze_rows=N,
        bronze_distinct_ids=N,
        bronze_rescued_rows=0,
        silver_rows=N,
        silver_distinct_ids=N,
        expectations=EXPS,
        event_log_failed=dict(failed),
        silver_failing=dict(failed),
        bronze_distinct_ids_passing_drops=N,
        updates=[{"update_id": "u1", "timestamp": "2026-09-30 10:00:00"}],
        taxonomy_combinations=2_645,
        taxonomy_unmapped=0,
        taxonomy_rows=N,
        gathered_at="2026-09-30 10:30 UTC",
    )


def failing(checks):
    return [c.name for c in checks if not c.ok]


def test_clean_run_passes_every_check():
    checks = evaluate(clean_facts())
    assert failing(checks) == []
    assert len(checks) >= 10


def test_missing_rows_in_one_part_are_pinpointed():
    f = clean_facts()
    f.bronze_by_file["2026-09-29/complaints_part_0007.csv"] -= 3
    f.bronze_rows -= 3
    checks = evaluate(f)
    assert failing(checks) == [
        "source rows = bronze rows (snapshot 2026-09-29)",
        "per-part row counts match manifest (snapshot 2026-09-29)",
    ]
    note = next(c.note for c in checks if c.name.startswith("per-part"))
    assert "complaints_part_0007.csv" in note


def test_part_missing_from_bronze_and_unknown_snapshot():
    f = clean_facts()
    del f.bronze_by_file["2026-09-29/complaints_part_0037.csv"]
    f.bronze_by_file["2027-01-01/complaints_part_0001.csv"] = 10
    names = failing(evaluate(f))
    assert "per-part row counts match manifest (snapshot 2026-09-29)" in names
    assert "bronze snapshots have a manifest" in names


def test_duplicates_and_silver_mismatch():
    f = clean_facts()
    f.silver_rows += 5  # e.g. a broken CDC key
    names = failing(evaluate(f))
    assert "duplicate complaint_id in silver" in names
    assert "silver rows = distinct bronze IDs passing drop expectations" in names


def test_event_log_disagreeing_with_silver_is_flagged():
    f = clean_facts()
    f.event_log_failed["state_present"] = 60_000
    assert failing(evaluate(f)) == ["`state_present` (warn): event log = silver count"]


def test_expectation_missing_from_event_log_is_flagged():
    """Guards against view expectations silently not being reported in the event log."""
    f = clean_facts()
    del f.event_log_failed["valid_complaint_id"]
    assert failing(evaluate(f)) == ["expectation `valid_complaint_id` reported in event log"]


def test_drop_expectation_rows_left_in_silver_is_flagged():
    f = clean_facts()
    f.silver_failing["valid_date_received"] = 2
    assert failing(evaluate(f)) == ["`valid_date_received` (drop): failing rows left in silver"]


def test_warn_comparison_skipped_when_bronze_has_repeat_ids():
    """With several snapshots bronze holds several rows per ID, so row-level warn counts differ."""
    f = clean_facts()
    f.bronze_rows += 100
    f.event_log_failed["state_present"] += 7
    names = failing(evaluate(f))
    assert not any("state_present" in n for n in names)


def test_taxonomy_checks():
    f = clean_facts()
    f.taxonomy_unmapped = 1
    f.taxonomy_rows -= 4
    assert failing(evaluate(f)) == [
        "unmapped taxonomy combinations",
        "taxonomy_map complaint_count total = silver rows",
    ]


def test_render_contains_funnel_and_verdicts():
    f = clean_facts()
    bad = copy.deepcopy(f)
    bad.silver_rows += 1
    md_ok, md_bad = render(f, evaluate(f)), render(bad, evaluate(bad))
    assert "| `bronze.complaints_raw` | 18,062,308 | 18,062,308 |" in md_ok
    assert "| `state_present` | warn | 62,969 | 62,969 |" in md_ok
    assert "❌" not in md_ok and "❌" in md_bad
    assert md_ok.count("✅") == len(evaluate(f))


# --- sqlmeta ------------------------------------------------------------------------------


def test_parse_expectations_actions_and_nested_parens():
    sql = """
      CONSTRAINT a EXPECT (x IS NOT NULL) ON VIOLATION DROP ROW,
      CONSTRAINT b EXPECT (y IS NULL OR y IN ('A', 'B')), -- comment with ) paren
      CONSTRAINT c EXPECT (f(z) > 0) ON VIOLATION FAIL UPDATE
    """
    assert parse_expectations(sql) == [
        Expectation("a", "x IS NOT NULL", "drop"),
        Expectation("b", "y IS NULL OR y IN ('A', 'B')", "warn"),
        Expectation("c", "f(z) > 0", "fail"),
    ]


def test_parse_expectations_unbalanced():
    with pytest.raises(ValueError, match="Unbalanced"):
        parse_expectations("CONSTRAINT a EXPECT (x IN (1, 2)")


def test_silver_complaint_expectations_excludes_taxonomy_constraint():
    names = [e.name for e in EXPS]
    assert "all_combinations_mapped" not in names
    assert names[:2] == ["valid_complaint_id", "valid_date_received"]
