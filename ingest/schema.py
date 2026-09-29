"""CFPB bulk CSV column contract.

EXPECTED_COLUMNS is copied from the header of the real file (complaints.csv in complaints.csv.zip,
Last-Modified 2026-09-29) and matches https://cfpb.github.io/api/ccdb/fields.html. As of CFPB
Release 22/24 (Jun/Sep 2026) the narrative, consent and disputed columns no longer exist; see
docs/data_notes.md.
"""

from __future__ import annotations

import re

EXPECTED_COLUMNS: tuple[str, ...] = (
    "Date received",
    "Product",
    "Sub-product",
    "Issue",
    "Sub-issue",
    "Company public response",
    "Company",
    "State",
    "ZIP code",
    "Tags",
    "Submitted via",
    "Date sent to company",
    "Company response to consumer",
    "Timely response?",
    "Complaint ID",
)

COMPLAINT_ID_COLUMN = "Complaint ID"
DATE_RECEIVED_COLUMN = "Date received"


def to_snake_case(header: str) -> str:
    """Normalize a CSV header: 'Timely response?' -> 'timely_response', 'ZIP code' -> 'zip_code'."""
    s = re.sub(r"[^0-9a-zA-Z]+", "_", header.strip()).strip("_").lower()
    if not s:
        raise ValueError(f"Header {header!r} has no alphanumeric characters")
    return s


def column_map(headers: tuple[str, ...] | list[str] = EXPECTED_COLUMNS) -> dict[str, str]:
    """Source header -> snake_case name. Raises if two headers collapse to the same name."""
    mapping = {h: to_snake_case(h) for h in headers}
    seen: dict[str, str] = {}
    for src, dst in mapping.items():
        if dst in seen:
            raise ValueError(f"Headers {seen[dst]!r} and {src!r} both map to {dst!r}")
        seen[dst] = src
    return mapping


def header_diff(actual: list[str]) -> str | None:
    """Human-readable difference between `actual` and EXPECTED_COLUMNS, or None if identical."""
    if tuple(actual) == EXPECTED_COLUMNS:
        return None
    missing = [c for c in EXPECTED_COLUMNS if c not in actual]
    extra = [c for c in actual if c not in EXPECTED_COLUMNS]
    parts = []
    if missing:
        parts.append(f"missing={missing}")
    if extra:
        parts.append(f"unexpected={extra}")
    if not parts:
        parts.append(f"same columns, different order: {actual}")
    return "; ".join(parts)
