"""Company name normalization v1 (Milestone 2, step 1).

CFPB already keeps one entry per company, so v1 only undoes spelling noise:

  company_key  identity key: a trailing 'National Association' is written as 'N.A.', then letters
               and digits only, upper-cased. Groups complaints. Merges only case/punctuation/spacing
               variants and N.A. / National Association (same legal term) variants
               ('OneMain Finance Corporation' and 'ONEMAIN FINANCE CORPORATION'; 'CashMax LLC' and
               'Cash Max LLC'; 'Flagstar Bank, N.A.' and '..., National Association'). Legal forms
               are NOT stripped: 'USCB, Inc.' and 'USCB Corporation' both file 2013-2026 and stay
               separate. See docs/company/v1/README.md for the evidence.
  match_key    candidate key for FDIC matching only (step 3): tokens, '&' -> AND, trailing legal
               forms stripped. Never used to merge CFPB companies.

COMPANY_KEY_SQL is the Databricks SQL equivalent of company_key. tests/test_company.py checks the
two agree on every company name in the profiled data.
"""

from __future__ import annotations

import re

# Trailing 'National Association' == 'N.A.' ('Flagstar Bank, National Association' and 'Flagstar
# Bank, N.A.'). No backslashes on purpose: the same pattern text works in Python, Databricks SQL
# (Java regex) and DuckDB (RE2), whose string literals treat backslashes differently.
NATIONAL_ASSOCIATION_RE = "(?i)[^A-Za-z0-9]NATIONAL[^A-Za-z0-9]+ASSOCIATION[^A-Za-z0-9]*$"

# Databricks regexp_replace replaces every match. Mirror of company_key().
COMPANY_KEY_SQL = (
    "nullif(upper(regexp_replace(regexp_replace({col}, '"
    + NATIONAL_ASSOCIATION_RE
    + "', 'NA'), '[^A-Za-z0-9]', '')), '')"
)

# Trailing legal-form tokens stripped by match_key (after punctuation became spaces).
LEGAL_SUFFIXES = (
    "NATIONAL ASSOCIATION",
    "N A",
    "NA",
    "INCORPORATED",
    "INC",
    "L L C",
    "LLC",
    "LLP",
    "L P",
    "LP",
    "LIMITED",
    "LTD",
    "CORPORATION",
    "CORP",
    "COMPANY",
    "CO",
    "P C",
    "PC",
    "PLLC",
    "PLC",
)


def company_key(name: str | None) -> str | None:
    """Letters and digits only, upper-cased; None for empty input."""
    if name is None:
        return None
    key = re.sub(r"[^A-Za-z0-9]", "", re.sub(NATIONAL_ASSOCIATION_RE, "NA", name)).upper()
    return key or None


def match_key(name: str | None) -> str | None:
    """Loose key for proposing FDIC matches: 'JPMORGAN CHASE & CO.' -> 'JPMORGAN CHASE'."""
    if name is None:
        return None
    t = " ".join(re.sub(r"[^A-Z0-9&]+", " ", name.upper()).replace("&", " AND ").split())
    # Articles first, so 'X CORPORATION, THE' still loses its legal form below.
    t = t.removeprefix("THE ").removesuffix(" THE")
    changed = True
    while changed:
        changed = False
        for suffix in LEGAL_SUFFIXES:
            if t.endswith(" " + suffix):
                t = t[: -len(suffix) - 1].rstrip()
                changed = True
    # 'JPMORGAN CHASE AND CO' -> 'JPMORGAN CHASE AND' -> 'JPMORGAN CHASE'
    t = t.removesuffix(" AND")
    # 'HUNTINGTON NATIONAL BANK, THE' (CFPB) vs 'The Huntington National Bank' (FDIC)
    t = t.removeprefix("THE ").removesuffix(" THE")
    return t or None
