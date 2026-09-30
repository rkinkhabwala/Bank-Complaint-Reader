import csv
from pathlib import Path

import pytest

from ingest import company_matching as cm

ROOT = Path(__file__).resolve().parents[1]
FDIC_DIR = ROOT / "docs" / "profile" / "fdic" / "2026-06-30"
PROFILE = ROOT / "docs" / "profile" / "2026-09-29" / "values_company.csv"


def inst(cert, name, hc="", rssdhcr="", asset=1_000, state="OH"):
    return {"CERT": str(cert), "NAME": name, "NAMEHCR": hc, "RSSDHCR": rssdhcr,
            "ASSET": str(asset), "STALP": state}  # fmt: skip


FDIC = [
    inst(
        628,
        "JPMorgan Chase Bank, National Association",
        "JPMORGAN CHASE&CO",
        "1039502",
        4_091_315_000,
    ),
    inst(6560, "The Huntington National Bank", "HUNTINGTON BANCSHARES INC", "1068191", 283_000_000),
    inst(
        1,
        "Citizens Bank, National Association",
        "CITIZENS FINANCIAL GROUP INC",
        "1132449",
        232_000_000,
    ),
    inst(2, "Tiny Citizens Bank", "CITIZENS FINANCIAL GROUP INC", "999", 400_000),
    inst(3, "Prairie Sun Bank", "DAKOTA FINANCIAL INC", "555", 100_000),
    inst(4, "Standalone Savings Bank", "", "", 5_000_000),
    inst(5, "U.S. Bank National Association", "U S BCORP", "1119794", 705_000_000),
]


def test_build_orgs_sums_member_banks_and_keys_standalone_by_cert():
    orgs = cm.build_orgs([*FDIC, inst(7, "Chase Bank USA", "JPMORGAN CHASE&CO", "1039502", 100)])
    assert orgs["1039502"].assets_thousands == 4_091_315_100 and orgs["1039502"].bank_count == 2
    assert orgs["1039502"].largest_bank.startswith("JPMorgan Chase Bank")
    assert orgs["CERT:4"].name == "Standalone Savings Bank"


@pytest.mark.parametrize(
    ("cfpb", "expected"),
    [
        ("JPMORGAN CHASE & CO.", ("1039502", "auto_holding_company")),
        ("HUNTINGTON NATIONAL BANK, THE", ("1068191", "auto_bank_name")),
        ("Standalone Savings Bank", ("CERT:4", "auto_bank_name")),
        # branch-level CFPB name -> bank part before ' - '
        (
            "U.S. BANK NATIONAL ASSOCIATION - SAN FRANCISCO MAIN BRANCH",
            ("1119794", "auto_bank_name"),
        ),
        # two FDIC holding companies share the name -> ambiguous -> no match
        ("CITIZENS FINANCIAL GROUP, INC.", None),
        # LLC vs corporation of the same name -> different legal entities -> no match
        ("Dakota Financial, LLC", None),
        ("Unknown Collections Inc", None),
    ],
)
def test_auto_match_rules(cfpb, expected):
    got = cm.auto_match({"K": cfpb}, FDIC)
    assert got.get("K") == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("NAVY FEDERAL CREDIT UNION", "credit_union"),
        ("State Employees’ Credit Union", "credit_union"),
        ("TCF NATIONAL BANK", "bank"),
        ("NEW YORK COMMUNITY BANCORP INC", "bank"),
        ("FIRST CITIZENS BANCSHARES, INC.", "bank"),
        ("AMERICAN BANCSHARES MORTGAGE, LLC", "non_bank"),  # LLC: not a bank
        ("Proscript media and advertising services, llc DBA Bank Rover", "non_bank"),
        ("BANKERS LIFE", "non_bank"),  # BANKERS is not the word BANK
        ("Portfolio Recovery Associates, LLC", "non_bank"),
    ],
)
def test_default_entity_type(name, expected):
    assert cm.default_entity_type(name) == expected


@pytest.mark.parametrize(
    ("entity", "assets", "product", "expected"),
    [
        ("credit_bureau", None, "Credit reporting…", "Credit bureau"),
        ("bank", 4_091_315_000, None, "Bank >$250B"),
        ("bank", 250_000_000, None, "Bank >$250B"),  # lower bound inclusive
        ("bank", 249_999_999, None, "Bank $100-250B"),
        ("bank", 10_000_000, None, "Bank $10-100B"),
        ("bank", 9_999_999, None, "Bank <$10B"),
        ("bank", None, None, "Bank (no active FDIC record)"),
        ("credit_union", None, "Checking or savings account", "Credit union"),
        ("non_bank", None, "Debt collection", "Non-bank: Debt collection"),
        ("non_bank", None, None, "Non-bank: Unknown"),
    ],
)
def test_peer_group(entity, assets, product, expected):
    assert cm.peer_group(entity, assets, product) == expected


def test_overrides_are_valid_and_point_at_real_fdic_orgs():
    overrides = cm.load_overrides()
    orgs = cm.build_orgs(cm.read_csv(FDIC_DIR / "institutions.csv"))
    bureaus = [k for k, o in overrides.items() if o["entity_type"] == "credit_bureau"]
    assert sorted(bureaus) == [
        "EQUIFAXINC", "EXPERIANINFORMATIONSOLUTIONSINC", "TRANSUNIONINTERMEDIATEHOLDINGSINC"
    ]  # fmt: skip
    for key, o in overrides.items():
        assert o["note"], f"override {key} needs evidence"
        if o["fdic_org_id"]:
            assert o["fdic_org_id"] in orgs, key


def test_committed_seed_is_up_to_date():
    """pipelines/seeds/company_fdic_match.csv == a fresh build from committed inputs."""
    names, _ = cm.company_names(PROFILE)
    rows = cm.build_matches(names, cm.read_csv(FDIC_DIR / "institutions.csv"), cm.load_overrides())
    with open(cm.MATCH_SEED, encoding="utf-8", newline="") as f:
        committed = list(csv.DictReader(f))
    assert committed == rows, "run `python -m ingest.company_matching` and commit the seed"


def test_sql_default_rules_match_python_on_every_company():
    duckdb = pytest.importorskip("duckdb")
    sqlglot = pytest.importorskip("sqlglot")
    names = [r["value"] for r in cm.read_csv(PROFILE) if r["value"]]
    norm = cm.NORMALIZED_NAME_SQL.format(col="company")
    sql = f"""SELECT company, CASE
        WHEN regexp_like({norm}, '{cm.CREDIT_UNION_RE}') THEN 'credit_union'
        WHEN regexp_like({norm}, '{cm.BANK_TOKEN_RE}')
             AND NOT regexp_like({norm}, '{cm.PARTNERSHIP_RE}') THEN 'bank'
        ELSE 'non_bank' END AS et, {norm} AS n FROM t"""
    duck = sqlglot.transpile(sql, read="databricks", write="duckdb")[0]
    con = duckdb.connect()
    con.execute("CREATE TABLE t (company VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?)", [(n,) for n in names])
    diff = [
        (c, et, cm.default_entity_type(c))
        for c, et, n in con.execute(duck).fetchall()
        if et != cm.default_entity_type(c) or n != cm.normalized_name(c)
    ]
    assert diff == []
