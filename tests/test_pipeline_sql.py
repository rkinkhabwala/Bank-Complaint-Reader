"""Contract tests between the Python column contract (ingest/schema.py) and the pipeline SQL."""

import re
from pathlib import Path

import pytest
import yaml

from ingest.schema import EXPECTED_COLUMNS, column_map
from ingest.sqlmeta import silver_complaint_expectations, strip_sql_comments

ROOT = Path(__file__).resolve().parents[1]
BRONZE = (ROOT / "pipelines" / "bronze.sql").read_text()


def bronze_aliases() -> dict[str, str]:
    """`Source header` AS snake_name pairs in the bulk CSV flow, in order."""
    return dict(re.findall(r"`([^`]+)`\s+AS\s+([a-z_][a-z0-9_]*)", strip_sql_comments(BRONZE)))


def test_bronze_maps_every_source_column_to_its_snake_case_name():
    assert bronze_aliases() == column_map()


def test_bronze_alias_order_matches_source_order():
    assert list(bronze_aliases()) == list(EXPECTED_COLUMNS)


def test_bronze_schema_hints_reference_real_columns():
    hints = re.search(r"schemaHints\s*=>\s*'([^']*)'", BRONZE).group(1)
    hinted = re.findall(r"`([^`]+)`\s+(\w+)", hints)
    assert hinted, "expected type hints for dates and ID (spec §4.1)"
    for col, _type in hinted:
        assert col in EXPECTED_COLUMNS, f"schemaHints names unknown column {col!r}"


def test_bronze_keeps_rescued_data_and_lineage_columns():
    body = strip_sql_comments(BRONZE)
    for col in (
        "_rescued_data",
        "_source_file",
        "_source_file_mtime",
        "_source_kind",
        "_ingested_at",
    ):
        assert re.search(rf"\b{col}\b", body), col
    assert re.search(r"schemaEvolutionMode\s*=>\s*'rescue'", body)
    assert re.search(r"escape\s*=>\s*'\"'", body), "RFC 4180 escape must be explicit"


@pytest.mark.parametrize(
    "sql_file", sorted((ROOT / "pipelines").glob("*.sql")), ids=lambda p: p.name
)
def test_pipeline_sql_uses_only_configured_paths(sql_file):
    body = strip_sql_comments(sql_file.read_text())
    assert "/Volumes/" not in body, "paths must come from ${landing_path}, not be hard-coded"
    used = set(re.findall(r"\$\{(\w+)\}", body))
    configured = yaml.safe_load((ROOT / "databricks.yml").read_text())["resources"]["pipelines"][
        "complaint_radar_pipeline"
    ]["configuration"]
    assert used <= set(configured), f"undeclared pipeline config keys: {used - set(configured)}"


def test_bundle_includes_every_pipeline_sql_file():
    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text())
    libs = bundle["resources"]["pipelines"]["complaint_radar_pipeline"]["libraries"]
    included = {lib["glob"]["include"] for lib in libs}
    on_disk = {f"pipelines/{p.name}" for p in (ROOT / "pipelines").glob("*.sql")}
    assert on_disk <= included, f"SQL files not in databricks.yml: {on_disk - included}"


# --- silver ---------------------------------------------------------------------------------

SILVER = (ROOT / "pipelines" / "silver.sql").read_text()


def silver_expectations() -> dict[str, tuple[str, str]]:
    """name -> (expression, action) for the complaints_typed view."""
    return {e.name: (e.expr, e.action) for e in silver_complaint_expectations()}


def test_silver_expectations_cover_spec_4_2_with_right_actions():
    exp = silver_expectations()
    assert exp["valid_complaint_id"] == ("complaint_id IS NOT NULL", "drop")
    assert exp["valid_date_received"][1] == "drop"
    assert "date_received <= current_date()" in exp["valid_date_received"][0]
    assert exp["product_present"] == ("product IS NOT NULL", "warn")
    assert exp["state_present"][1] == exp["state_valid_code"][1] == "warn"
    assert exp["no_rescued_data"] == ("_rescued_data IS NULL", "warn")


def test_silver_expectations_are_null_safe():
    """Comparisons must be guarded so no constraint evaluates to NULL (behaviour undocumented)."""
    exp = silver_expectations()
    assert exp["valid_date_received"][0].startswith("date_received IS NOT NULL AND")
    assert exp["state_valid_code"][0].startswith("state IS NULL OR")


def test_silver_valid_state_list_matches_reference():
    from ingest.reference import STATE_NAME_TO_CODE, USPS_CODES

    in_list = set(re.findall(r"'([A-Z]{2})'", silver_expectations()["state_valid_code"][0]))
    assert in_list == USPS_CODES | set(STATE_NAME_TO_CODE.values())


def test_silver_state_normalization_mirrors_reference():
    from ingest.reference import STATE_NAME_TO_CODE

    pairs = dict(re.findall(r"WHEN '([A-Z ]{3,})' THEN '([A-Z]{2})'", strip_sql_comments(SILVER)))
    assert pairs == STATE_NAME_TO_CODE


def test_silver_selects_every_bronze_business_column():
    body = strip_sql_comments(SILVER)
    select = body[body.index("AS SELECT") : body.index("FROM STREAM(bronze.complaints_raw)")]
    for col in column_map().values():
        assert re.search(rf"\b{col}\b", select), f"silver drops bronze column {col}"


def test_silver_auto_cdc_contract():
    body = " ".join(strip_sql_comments(SILVER).split())
    assert "AUTO CDC INTO silver.complaints FROM STREAM(complaints_typed)" in body
    assert "KEYS (complaint_id)" in body
    assert "STORED AS SCD TYPE 1" in body
    seq = re.search(r"SEQUENCE BY STRUCT\(([^)]*)\)", body).group(1)
    assert [s.strip() for s in seq.split(",")] == [
        "_source_version",
        "_source_file_mtime",
        "_source_file",
    ]
    # Sequencing columns must be non-NULL: _source_version is coalesced to the file mtime.
    assert re.search(r"coalesce\(nullif\(_snapshot_id, ''\), date_format\(_source_file_mtime", body)
