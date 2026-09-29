"""Static reference data used by profiling, silver expectations and tests."""

from __future__ import annotations

# USPS Publication 28, Appendix B "Two-Letter State and Possession Abbreviations"
# https://pe.usps.com/text/pub28/28apb.htm (checked 2026-09-29): 50 states, DC,
# 8 territories/freely associated states, 3 military "states". UM is NOT a USPS code.
US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
DISTRICT = frozenset({"DC"})
TERRITORIES_AND_FREELY_ASSOCIATED = frozenset({"AS", "GU", "MP", "PR", "VI", "FM", "MH", "PW"})
MILITARY = frozenset({"AA", "AE", "AP"})
USPS_CODES = US_STATES | DISTRICT | TERRITORIES_AND_FREELY_ASSOCIATED | MILITARY

# Full names seen in the CFPB State column instead of a code (profile of snapshot 2026-09-29).
# 'UM' is the ISO 3166-2 subdivision code (US-UM); USPS has no code for it.
STATE_NAME_TO_CODE = {"UNITED STATES MINOR OUTLYING ISLANDS": "UM"}


def classify_state(value: str | None) -> str:
    """'missing' | 'usps' | 'normalizable' (known full name) | 'invalid'."""
    if value is None or not value.strip():
        return "missing"
    v = value.strip().upper()
    if v in USPS_CODES:
        return "usps"
    if v in STATE_NAME_TO_CODE:
        return "normalizable"
    return "invalid"
