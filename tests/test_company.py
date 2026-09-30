import csv
from collections import defaultdict
from pathlib import Path

import pytest

from ingest.company import COMPANY_KEY_SQL, company_key, match_key

ROOT = Path(__file__).resolve().parents[1]
COMPANIES = ROOT / "docs" / "profile" / "2026-09-29" / "values_company.csv"


@pytest.fixture(scope="module")
def names() -> list[str]:
    with open(COMPANIES, encoding="utf-8", newline="") as f:
        return [r["value"] for r in csv.DictReader(f) if r["value"]]


# --- company_key: identity ------------------------------------------------------------------


@pytest.mark.parametrize(
    "variants",
    [  # real variant pairs from snapshot 2026-09-29
        ["OneMain Finance Corporation", "ONEMAIN FINANCE CORPORATION"],
        ["Seterus, Inc.", "SETERUS INC"],
        ["Ragan & Ragan, PC", "Ragan & Ragan, P.C."],
        ["CashMax LLC", "Cash Max LLC"],
        ["V.I.P. MORTGAGE, INC.", "VIP Mortgage Inc."],
        ["Flagstar Bank, N.A.", "Flagstar Bank, National Association"],
    ],
)
def test_company_key_merges_spelling_variants(variants):
    assert len({company_key(v) for v in variants}) == 1


@pytest.mark.parametrize(
    "distinct",
    [  # different legal entities that file concurrently: must stay separate
        ["USCB, Inc.", "USCB Corporation"],
        ["FIRST MORTGAGE CORPORATION", "FIRST MORTGAGE COMPANY"],
        ["Credit Control, LLC", "Credit Control Company, Inc."],
    ],
)
def test_company_key_keeps_legal_forms_apart(distinct):
    assert len({company_key(v) for v in distinct}) == len(distinct)


def test_national_association_rule_is_trailing_only():
    assert company_key("Bank X, National Association") == company_key("Bank X, N.A.") == "BANKXNA"
    assert company_key("BANK X NATIONAL  ASSOCIATION.") == "BANKXNA"
    # Mid-name occurrences are left alone (a trade body, not a bank charter suffix).
    assert company_key("National Association of Collectors") == "NATIONALASSOCIATIONOFCOLLECTORS"


def test_company_key_empty():
    assert company_key(None) is None
    assert company_key(" ., ") is None


def test_company_key_merges_only_the_reviewed_groups(names):
    groups = defaultdict(set)
    for n in names:
        groups[company_key(n)].add(n)
    merged = {k: v for k, v in groups.items() if len(v) > 1}
    assert len(names) == 8_133
    assert len(merged) == 13, sorted(merged)  # listed in docs/company/v1/README.md
    assert len(groups) == 8_133 - 13


# --- match_key: FDIC candidates only ---------------------------------------------------------


@pytest.mark.parametrize(
    ("cfpb", "fdic"),
    [  # CFPB company vs FDIC holding-company / institution name
        ("JPMORGAN CHASE & CO.", "JPMORGAN CHASE&CO"),
        ("BANK OF AMERICA, NATIONAL ASSOCIATION", "BANK OF AMERICA CORP"),
        ("CITIBANK, N.A.", "Citibank, N.A."),
        ("PNC Bank N.A.", "PNC BANK, NATIONAL ASSOCIATION"),
        ("HUNTINGTON NATIONAL BANK, THE", "The Huntington National Bank"),
        ("CHARLES SCHWAB CORPORATION, THE", "CHARLES SCHWAB CORP THE"),
        ("GOLDMAN SACHS GROUP, INC.", "GOLDMAN SACHS GROUP INC THE"),
    ],
)
def test_match_key_aligns_cfpb_and_fdic_spellings(cfpb, fdic):
    assert match_key(cfpb) == match_key(fdic)


def test_match_key_examples():
    assert match_key("JPMORGAN CHASE & CO.") == "JPMORGAN CHASE"
    assert match_key("WELLS FARGO & COMPANY") == "WELLS FARGO"
    assert match_key("Navient Solutions, LLC.") == "NAVIENT SOLUTIONS"
    assert match_key("CO") == "CO", "a name that is only a suffix token is kept"
    assert match_key(None) is None


# --- SQL mirror -----------------------------------------------------------------------------


def test_company_key_sql_matches_python_on_every_real_name(names):
    duckdb = pytest.importorskip("duckdb")
    sqlglot = pytest.importorskip("sqlglot")
    expr = COMPANY_KEY_SQL.format(col="company")
    duck_expr = sqlglot.transpile(f"SELECT {expr} AS k FROM t", read="databricks", write="duckdb")[
        0
    ]
    con = duckdb.connect()
    con.execute("CREATE TABLE t (company VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?)", [(n,) for n in [*names, "", " .,", None]])
    got = [
        r[0] for r in con.execute(duck_expr.replace("FROM t", "FROM t ORDER BY rowid")).fetchall()
    ]
    want = [company_key(n) for n in [*names, "", " .,", None]]
    diff = [
        (n, g, w) for n, g, w in zip([*names, "", " .,", None], got, want, strict=True) if g != w
    ]
    assert diff == []
