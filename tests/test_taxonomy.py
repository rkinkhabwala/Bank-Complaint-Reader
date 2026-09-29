"""Taxonomy v1: seed integrity, coverage of profiled data, and SQL/Python equivalence."""

import csv
import re
from pathlib import Path

import pytest

from ingest.taxonomy import (
    ISSUE_GROUPS,
    ISSUE_SEED,
    MAPPINGS,
    PRODUCT_SEED,
    WILDCARD,
    Resolved,
    Taxonomy,
)

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "docs" / "profile" / "2026-09-29" / "taxonomy_combinations.csv"


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def tax() -> Taxonomy:
    return Taxonomy.load()


@pytest.fixture(scope="module")
def observed() -> list[tuple[str | None, str | None, str | None, int]]:
    """Distinct (product, sub_product, issue) with row counts; empty CSV field = NULL."""
    agg: dict[tuple, int] = {}
    for r in read(PROFILE):
        key = (r["product"] or None, r["sub_product"] or None, r["issue"] or None)
        agg[key] = agg.get(key, 0) + int(r["rows"])
    return [(*k, n) for k, n in agg.items()]


# --- seed integrity -------------------------------------------------------------------------


def test_product_seed_integrity():
    rows = read(PRODUCT_SEED)
    canon = {r["canonical_product"] for r in rows}
    assert len(canon) == 11, "v1 targets the 11 current CFPB products"
    code_of = {}
    for r in rows:
        assert (
            code_of.setdefault(r["canonical_product"], r["canonical_product_code"])
            == r["canonical_product_code"]
        ), "one code per canonical product"
        assert re.fullmatch(r"[a-z_]+", r["canonical_product_code"])
    assert len(set(code_of.values())) == 11
    # Every source product has exactly one '*' default; every canonical product maps to itself.
    sources = {r["source_product"] for r in rows}
    defaults = [r["source_product"] for r in rows if r["source_sub_product"] == WILDCARD]
    assert sorted(defaults) == sorted(sources)
    for c in canon:
        assert (c, WILDCARD) in {(r["source_product"], r["source_sub_product"]) for r in rows}


def test_issue_seed_integrity():
    rows = read(ISSUE_SEED)
    canon_products = {r["canonical_product"] for r in read(PRODUCT_SEED)}
    for r in rows:
        assert r["canonical_product"] in canon_products
        assert r["issue_group"] in ISSUE_GROUPS
        assert r["mapping"] in MAPPINGS
        if r["mapping"] == "current":
            assert r["canonical_issue"] == r["source_issue"]
    # Renames must point at a label that is itself 'current' in the same product.
    current = {
        (r["canonical_product"], r["source_issue"]) for r in rows if r["mapping"] == "current"
    }
    for r in rows:
        if r["mapping"] == "renamed":
            assert (r["canonical_product"], r["canonical_issue"]) in current, r


def test_duplicate_rules_are_rejected():
    p = [{"source_product": "A", "source_sub_product": "*", "canonical_product": "X",
          "canonical_product_code": "x"}] * 2  # fmt: skip
    with pytest.raises(ValueError, match="Duplicate product rule"):
        Taxonomy(p, [])


# --- resolver semantics ---------------------------------------------------------------------


def test_sub_product_override_beats_default(tax):
    assert tax.resolve_product("Credit card or prepaid card", "Gift card")[0] == "Prepaid card"
    assert (
        tax.resolve_product("Credit card or prepaid card", "Store credit card")[0] == "Credit card"
    )
    assert tax.resolve_product("Credit card or prepaid card", None)[0] == "Credit card"
    assert tax.resolve_product("Consumer Loan", "Vehicle lease")[0] == "Vehicle loan or lease"
    assert tax.resolve_product("Consumer Loan", "Installment loan")[1] == "payday_personal_loan"


def test_known_renames(tax):
    old_product = "Credit reporting, credit repair services, or other personal consumer reports"
    old_issue = "Problem with a credit reporting company's investigation into an existing problem"
    r = tax.resolve(old_product, "Credit reporting", old_issue)
    assert r.canonical_product_code == "credit_reporting"
    assert r.canonical_issue == "Problem with a company's investigation into an existing problem"
    assert r.issue_group == "dispute_investigation"
    r = tax.resolve("Debt collection", "I do not know", "Cont'd attempts collect debt not owed")
    assert (r.canonical_issue, r.issue_group) == (
        "Attempts to collect debt not owed",
        "debt_collection_conduct",
    )


def test_null_issue_is_mapped_to_other(tax):
    r = tax.resolve("Checking or savings account", None, None)
    assert r == Resolved("Checking or savings account", "checking_savings", None, "other", True)


def test_unknown_labels_are_unmapped_not_dropped(tax):
    assert tax.resolve("Brand new product", None, "x").is_mapped is False
    r = tax.resolve("Mortgage", "FHA mortgage", "A label CFPB invents next year")
    assert (r.canonical_product, r.canonical_issue, r.issue_group, r.is_mapped) == (
        "Mortgage", None, "other", False
    )  # fmt: skip


# --- coverage of the real data --------------------------------------------------------------


def test_every_profiled_combination_is_mapped(tax, observed):
    unmapped = [(p, s, i, n) for p, s, i, n in observed if not tax.resolve(p, s, i).is_mapped]
    assert unmapped == []


def test_mapping_covers_all_profiled_rows_and_11_products(tax, observed):
    by_code: dict[str, int] = {}
    for p, s, i, n in observed:
        code = tax.resolve(p, s, i).canonical_product_code
        by_code[code] = by_code.get(code, 0) + n
    assert sum(by_code.values()) == 18_062_308  # snapshot 2026-09-29 (docs/data_notes.md)
    assert len(by_code) == 11


# --- SQL (silver.taxonomy_map) == Python resolver -------------------------------------------


def taxonomy_map_select() -> str:
    sql = re.sub(r"--[^\n]*", "", (ROOT / "pipelines" / "silver.sql").read_text())
    start = sql.index("AS WITH combos AS")
    return sql[start + len("AS ") : sql.index(";", start)]


def test_sql_taxonomy_map_matches_python_resolver(tax, observed):
    duckdb = pytest.importorskip("duckdb")
    sqlglot = pytest.importorskip("sqlglot")

    con = duckdb.connect()
    con.execute("CREATE SCHEMA silver")
    con.execute(
        "CREATE TABLE silver.complaints "
        "(product VARCHAR, sub_product VARCHAR, issue VARCHAR, date_received DATE)"
    )
    con.executemany(
        "INSERT INTO silver.complaints VALUES (?, ?, ?, DATE '2024-01-01')",
        [(p, s, i) for p, s, i, _ in observed]
        + [("Brand new product", None, "x"), ("Mortgage", "FHA mortgage", "New label")],
    )
    for view, path in (
        ("taxonomy_product_seed", PRODUCT_SEED),
        ("taxonomy_issue_seed", ISSUE_SEED),
    ):
        con.execute(
            f"CREATE TABLE {view} AS SELECT * FROM read_csv(?, header=true, all_varchar=true)",
            [str(path)],
        )

    duck_sql = sqlglot.transpile(taxonomy_map_select(), read="databricks", write="duckdb")[0]
    got = {
        (r[0], r[1], r[2]): Resolved(r[3], r[4], r[5], r[6], r[7])
        for r in con.execute(
            f"SELECT product, sub_product, issue, canonical_product, canonical_product_code, "
            f"canonical_issue, issue_group, is_mapped FROM ({duck_sql})"
        ).fetchall()
    }
    keys = [(p, s, i) for p, s, i, _ in observed] + [
        ("Brand new product", None, "x"),
        ("Mortgage", "FHA mortgage", "New label"),
    ]
    assert len(got) == len(keys)
    mismatches = [(k, got[k], tax.resolve(*k)) for k in keys if got[k] != tax.resolve(*k)]
    assert mismatches == []
