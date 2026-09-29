from pathlib import Path

import pytest
from run_setup import SQL_FILE, split_sql_statements


def test_splits_on_semicolons_and_drops_comments():
    script = """
    -- header comment; with a semicolon
    CREATE SCHEMA a; -- trailing comment
    CREATE SCHEMA b
      COMMENT 'x';
    """
    assert split_sql_statements(script) == ["CREATE SCHEMA a", "CREATE SCHEMA b\n      COMMENT 'x'"]


def test_semicolons_and_dashes_inside_quotes_are_preserved():
    script = "SELECT 'a;b', \"c;d\", `e;f`, 'no -- comment'; SELECT 1"
    assert split_sql_statements(script) == [
        "SELECT 'a;b', \"c;d\", `e;f`, 'no -- comment'",
        "SELECT 1",
    ]


def test_doubled_quote_escape():
    assert split_sql_statements("SELECT 'it''s;fine'; SELECT 2;") == [
        "SELECT 'it''s;fine'",
        "SELECT 2",
    ]


def test_trailing_statement_without_semicolon_and_empty_statements():
    assert split_sql_statements(";;SELECT 1;;  SELECT 2  ") == ["SELECT 1", "SELECT 2"]


def test_unterminated_quote_raises():
    with pytest.raises(ValueError, match="Unterminated"):
        split_sql_statements("SELECT 'oops;")


def test_real_setup_script_shape():
    stmts = split_sql_statements(Path(SQL_FILE).read_text())
    heads = [" ".join(s.split()[:6]) for s in stmts]
    assert heads == [
        "CREATE CATALOG IF NOT EXISTS complaint_radar",
        "CREATE SCHEMA IF NOT EXISTS complaint_radar.raw",
        "CREATE SCHEMA IF NOT EXISTS complaint_radar.bronze",
        "CREATE SCHEMA IF NOT EXISTS complaint_radar.silver",
        "CREATE SCHEMA IF NOT EXISTS complaint_radar.gold",
        "CREATE SCHEMA IF NOT EXISTS complaint_radar.ml",
        "CREATE VOLUME IF NOT EXISTS complaint_radar.raw.landing",
    ]
    assert all("IF NOT EXISTS" in s for s in stmts), "setup must stay idempotent"
