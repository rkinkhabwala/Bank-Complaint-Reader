"""Company classification v1: FDIC matching and peer groups (Milestone 2, step 3).

    python -m ingest.company_matching     # rebuild the generated seed + org snapshot + review stats

Inputs
  docs/profile/<snap>/values_company.csv        CFPB company names with counts (profile)
  data/fdic/<quarter>/institutions.csv          FDIC active institutions (ingest.download_fdic)
  pipelines/seeds/company_overrides.csv         hand-curated: bureaus, evidence-backed FDIC
                                                matches, banks without an active FDIC record
Outputs
  pipelines/seeds/company_fdic_match.csv        generated: company_key -> entity_type, fdic_org_id
  docs/profile/fdic/<quarter>/institutions.csv  committed copy of the FDIC input, and
  docs/profile/fdic/<quarter>/organizations.csv FDIC organizations (holding company or standalone
                                                bank) with summed assets; with these the seed is
                                                reproducible and testable without data/

Matching rules
  1. An override row wins.
  2. Automatic: CFPB match_key equals the match_key of exactly ONE FDIC organization's holding
     company name (NAMEHCR) or, failing that, exactly one bank name (NAME). Several candidates
     means no automatic match (e.g. three 'CITIZENS FINANCIAL GROUP' holding companies); curate.
  3. Never automatic across legal-entity classes: an LLC/partnership-type name (LLC, LP, LLP,
     PLLC, PC) doesn't match a corporation of the same name ('Dakota Financial, LLC' is not FDIC
     holding company 'DAKOTA FINANCIAL INC'), and vice versa.
  4. No link based on outside corporate knowledge (e.g. a fintech owning a small bank); those
     companies stay non-banks, grouped by their main product.

The pipeline (silver.company_dim) reads only the generated seed plus the live FDIC snapshot (for
assets), and applies default_entity_type() to companies without a seed row: 'CREDIT UNION' in the
name -> credit_union; a BANK/BANCORP/BANCSHARES word (not an LLC-type name) -> bank without FDIC
assets; else non_bank. peer_group() mirrors the SQL.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ingest.company import company_key, match_key

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "pipelines" / "seeds"
OVERRIDES = SEEDS / "company_overrides.csv"
MATCH_SEED = SEEDS / "company_fdic_match.csv"
ENTITY_TYPES = ("credit_bureau", "bank", "credit_union", "non_bank")

# Holding-company total assets (USD thousands) tier boundaries, lower bound inclusive.
TIERS = (
    (250_000_000, "Bank >$250B"),
    (100_000_000, "Bank $100-250B"),
    (10_000_000, "Bank $10-100B"),
    (0, "Bank <$10B"),
)


@dataclass(frozen=True)
class Org:
    org_id: str  # RSSDHCR, or 'CERT:<cert>' for a bank without a holding company
    name: str
    assets_thousands: int
    bank_count: int
    largest_bank: str


def org_id(row: dict) -> str:
    return row["RSSDHCR"] if row["RSSDHCR"] not in ("", "0") else f"CERT:{row['CERT']}"


def build_orgs(institutions: list[dict]) -> dict[str, Org]:
    members: dict[str, list[dict]] = defaultdict(list)
    for r in institutions:
        members[org_id(r)].append(r)
    orgs = {}
    for oid, rs in members.items():
        rs.sort(key=lambda r: -int(r["ASSET"] or 0))
        name = rs[0]["NAMEHCR"] or rs[0]["NAME"]
        orgs[oid] = Org(oid, name, sum(int(r["ASSET"] or 0) for r in rs), len(rs), rs[0]["NAME"])
    return orgs


# Default-rule regexes, applied to normalized_name() (upper case, runs of non-alphanumerics -> one
# space, trimmed). No backslashes, so the same text works in Python, Databricks SQL and DuckDB.
# LLC/LLP/PLLC as a word anywhere ('X, LLC DBA Y'); the short LP/PC forms only at the end.
PARTNERSHIP_RE = "(^| )(LLC|L L C|LLP|PLLC)( |$)|(^| )(LP|L P|PC|P C)$"
CREDIT_UNION_RE = "(^| )CREDIT UNION( |$)"
BANK_TOKEN_RE = "(^| )(BANK|BANKS|BANCORP|BANCORPORATION|BANCSHARES|BANKSHARES)( |$)"
NORMALIZED_NAME_SQL = "trim(regexp_replace(upper({col}), '[^A-Z0-9]+', ' '))"


def normalized_name(name: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", name.upper()).split())


def is_partnership_form(name: str) -> bool:
    """True if the name ends in an LLC/partnership-type legal form."""
    return re.search(PARTNERSHIP_RE, normalized_name(name)) is not None


def default_entity_type(name: str) -> str:
    """Entity type for a company without a seed row (mirror of the CASE in silver.company_dim)."""
    n = normalized_name(name)
    if re.search(CREDIT_UNION_RE, n):
        return "credit_union"
    if re.search(BANK_TOKEN_RE, n) and not re.search(PARTNERSHIP_RE, n):
        return "bank"  # bank name without an active FDIC match (merged/closed, branch, foreign)
    return "non_bank"


def auto_match(names: dict[str, str], institutions: list[dict]) -> dict[str, tuple[str, str]]:
    """company_key -> (org_id, method) for unambiguous name matches; see module docstring."""
    # index: match_key -> {(org_id, is_partnership_form of the FDIC name)}
    by_hc: dict[str, set[tuple[str, bool]]] = defaultdict(set)
    by_bank: dict[str, set[tuple[str, bool]]] = defaultdict(set)
    for r in institutions:
        if r["NAMEHCR"]:
            by_hc[match_key(r["NAMEHCR"])].add((org_id(r), is_partnership_form(r["NAMEHCR"])))
        by_bank[match_key(r["NAME"])].add((org_id(r), is_partnership_form(r["NAME"])))
    out = {}
    for key, full_name in names.items():
        # 'U.S. BANK NATIONAL ASSOCIATION - SAN FRANCISCO MAIN BRANCH' -> match the bank part.
        name = full_name
        if " - " in full_name and normalized_name(full_name).endswith("BRANCH"):
            name = full_name.split(" - ", 1)[0]
        mk, partnership = match_key(name), is_partnership_form(name)
        for index, method in ((by_hc, "auto_holding_company"), (by_bank, "auto_bank_name")):
            hits = index.get(mk, set())
            orgs = {oid for oid, _ in hits}
            if len(orgs) > 1:
                break  # ambiguous: never fall through to a weaker index
            if len(orgs) == 1:
                if all(p == partnership for _, p in hits):
                    out[key] = (next(iter(orgs)), method)
                break  # same name, different legal-entity class: no match
    return out


def tier(assets_thousands: int | None) -> str:
    if assets_thousands is None:
        return "Bank (no active FDIC record)"
    return next(label for floor, label in TIERS if assets_thousands >= floor)


def peer_group(entity_type: str, assets_thousands: int | None, main_product: str | None) -> str:
    """Mirror of the peer_group CASE in silver.company_dim."""
    if entity_type == "credit_bureau":
        return "Credit bureau"
    if entity_type == "bank":
        return tier(assets_thousands)
    if entity_type == "credit_union":
        return "Credit union"
    return f"Non-bank: {main_product or 'Unknown'}"


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_overrides(path: Path = OVERRIDES) -> dict[str, dict]:
    rows = read_csv(path)
    out = {}
    for r in rows:
        if r["entity_type"] not in ENTITY_TYPES:
            raise ValueError(f"Bad entity_type {r['entity_type']!r} for {r['company_key']}")
        if r["company_key"] in out:
            raise ValueError(f"Duplicate override {r['company_key']}")
        out[r["company_key"]] = r
    return out


def build_matches(
    names: dict[str, str], institutions: list[dict], overrides: dict[str, dict]
) -> list[dict]:
    """Rows for the generated seed: every company with a non-default classification."""
    orgs = build_orgs(institutions)
    auto = auto_match(names, institutions)
    rows = []
    for key in sorted(set(auto) | set(overrides)):
        if key in overrides:
            o = overrides[key]
            oid = o["fdic_org_id"] or None
            if oid and oid not in orgs:
                raise ValueError(f"Override {key} points at unknown FDIC org {oid}")
            rows.append(
                {
                    "company_key": key,
                    "entity_type": o["entity_type"],
                    "fdic_org_id": oid or "",
                    "method": "curated",
                    "evidence": o["note"],
                }  # fmt: skip
            )
        else:
            oid, method = auto[key]
            org = orgs[oid]
            rows.append(
                {
                    "company_key": key,
                    "entity_type": "bank",
                    "fdic_org_id": oid,
                    "method": method,
                    "evidence": f"CFPB '{names[key]}' ~ FDIC '{org.name}' / '{org.largest_bank}'",
                }  # fmt: skip
            )
    return rows


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def company_names(profile_csv: Path) -> tuple[dict[str, str], dict[str, int]]:
    """company_key -> display name (most complaints), and company_key -> complaints."""
    best: dict[str, tuple[int, str]] = {}
    counts: dict[str, int] = defaultdict(int)
    for r in read_csv(profile_csv):
        if not r["value"]:
            continue
        k, n = company_key(r["value"]), int(r["rows"])
        counts[k] += n
        if k not in best or n > best[k][0]:
            best[k] = (n, r["value"])
    return {k: v[1] for k, v in best.items()}, dict(counts)


def main() -> int:
    snap_profile = ROOT / "docs" / "profile" / "2026-09-29" / "values_company.csv"
    fdic_dirs = sorted((ROOT / "data" / "fdic").glob("*/institutions.csv"))
    if not fdic_dirs:
        raise SystemExit("No FDIC snapshot; run `python -m ingest.download_fdic --skip-upload`.")
    inst_path = fdic_dirs[-1]
    quarter = inst_path.parent.name
    institutions = read_csv(inst_path)
    # Committed copy, so the seed can be rebuilt and tested without data/ (public FDIC data).
    snapshot_dir = ROOT / "docs" / "profile" / "fdic" / quarter
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "institutions.csv").write_bytes(inst_path.read_bytes())
    names, _counts = company_names(snap_profile)
    rows = build_matches(names, institutions, load_overrides())
    write_csv(MATCH_SEED, rows, ["company_key", "entity_type", "fdic_org_id", "method", "evidence"])
    orgs = build_orgs(institutions)
    org_path = ROOT / "docs" / "profile" / "fdic" / quarter / "organizations.csv"
    write_csv(
        org_path,
        [o.__dict__ for o in sorted(orgs.values(), key=lambda o: (-o.assets_thousands, o.org_id))],
        ["org_id", "name", "assets_thousands", "bank_count", "largest_bank"],
    )
    by_method: dict[str, int] = defaultdict(int)
    for r in rows:
        by_method[r["method"]] += 1
    print(json.dumps({"seed_rows": len(rows), "by_method": by_method, "orgs": len(orgs)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
