"""Taxonomy v1 resolver: the reference implementation of the lookup in silver.taxonomy_map.

The seeds in pipelines/seeds/ are the source of truth (hand-curated; review record in
docs/taxonomy/v1/README.md). This module applies the same precedence as pipelines/silver.sql, so
tests can check seed coverage against the profiled data and check that the SQL gives the same
answers.

Precedence
  product: exact (source_product, source_sub_product) override, else (source_product, '*') default
  issue:   (canonical_product, source_issue) exact match; a NULL issue maps to issue_group 'other'
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

SEEDS = Path(__file__).resolve().parents[1] / "pipelines" / "seeds"
PRODUCT_SEED = SEEDS / "taxonomy_product_map.csv"
ISSUE_SEED = SEEDS / "taxonomy_issue_map.csv"
WILDCARD = "*"
ISSUE_GROUPS = frozenset(
    {
        "account_management_and_servicing",
        "credit_report_access_and_use",
        "credit_report_accuracy",
        "debt_collection_conduct",
        "dispute_investigation",
        "fees_and_interest",
        "fraud_and_unauthorized",
        "hardship_and_default",
        "marketing_and_disclosures",
        "origination",
        "payments_and_billing",
        "transactions_and_funds",
        "other",
    }
)
MAPPINGS = frozenset({"current", "renamed", "legacy_kept"})


@dataclass(frozen=True)
class Resolved:
    canonical_product: str | None
    canonical_product_code: str | None
    canonical_issue: str | None
    issue_group: str
    is_mapped: bool


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class Taxonomy:
    def __init__(self, product_rows: list[dict[str, str]], issue_rows: list[dict[str, str]]):
        self.products: dict[tuple[str, str], tuple[str, str]] = {}
        for r in product_rows:
            key = (r["source_product"], r["source_sub_product"])
            if key in self.products:
                raise ValueError(f"Duplicate product rule {key}")
            self.products[key] = (r["canonical_product"], r["canonical_product_code"])
        self.issues: dict[tuple[str, str], tuple[str, str]] = {}
        for r in issue_rows:
            key = (r["canonical_product"], r["source_issue"])
            if key in self.issues:
                raise ValueError(f"Duplicate issue rule {key}")
            self.issues[key] = (r["canonical_issue"], r["issue_group"])

    @classmethod
    def load(cls, product_seed: Path = PRODUCT_SEED, issue_seed: Path = ISSUE_SEED) -> Taxonomy:
        return cls(_read(product_seed), _read(issue_seed))

    def resolve_product(
        self, product: str | None, sub_product: str | None
    ) -> tuple[str, str] | None:
        if product is None:
            return None
        if sub_product is not None and (product, sub_product) in self.products:
            return self.products[(product, sub_product)]
        return self.products.get((product, WILDCARD))

    def resolve(self, product: str | None, sub_product: str | None, issue: str | None) -> Resolved:
        prod = self.resolve_product(product, sub_product)
        if prod is None:
            return Resolved(None, None, None, "other", False)
        canonical_product, code = prod
        if issue is None:
            return Resolved(canonical_product, code, None, "other", True)
        hit = self.issues.get((canonical_product, issue))
        if hit is None:
            return Resolved(canonical_product, code, None, "other", False)
        return Resolved(canonical_product, code, hit[0], hit[1], True)
