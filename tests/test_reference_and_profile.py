import pytest

from ingest.profile_bulk import MARK_END, MARK_START, md_table, pct, replace_marked_block
from ingest.reference import (
    MILITARY,
    STATE_NAME_TO_CODE,
    TERRITORIES_AND_FREELY_ASSOCIATED,
    US_STATES,
    USPS_CODES,
    classify_state,
)

# --- reference ------------------------------------------------------------------------------


def test_usps_code_counts_match_pub28():
    assert len(US_STATES) == 50
    assert len(TERRITORIES_AND_FREELY_ASSOCIATED) == 8
    assert len(MILITARY) == 3
    assert len(USPS_CODES) == 62  # 50 + DC + 8 + 3
    assert all(len(c) == 2 and c.isupper() for c in USPS_CODES)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("FL", "usps"),
        ("fl ", "usps"),
        ("AE", "usps"),
        ("PW", "usps"),
        ("UNITED STATES MINOR OUTLYING ISLANDS", "normalizable"),
        ("UM", "invalid"),  # ISO code, not USPS: only the full name is a known alias
        ("XX", "invalid"),
        ("", "missing"),
        ("   ", "missing"),
        (None, "missing"),
    ],
)
def test_classify_state(value, expected):
    assert classify_state(value) == expected


def test_state_aliases_are_not_usps_codes():
    assert not set(STATE_NAME_TO_CODE.values()) & USPS_CODES


# --- markdown helpers -----------------------------------------------------------------------


def test_replace_marked_block_replaces_only_between_markers():
    text = f"# Notes\nbefore\n{MARK_START}\nold stuff\n{MARK_END}\nafter\n"
    out = replace_marked_block(text, "new stuff\n")
    assert out == f"# Notes\nbefore\n{MARK_START}\nnew stuff\n{MARK_END}\nafter\n"
    assert replace_marked_block(out, "new stuff") == out, "idempotent"


def test_replace_marked_block_appends_when_absent():
    out = replace_marked_block("# Notes\n", "block")
    assert out == f"# Notes\n\n{MARK_START}\nblock\n{MARK_END}\n"


@pytest.mark.parametrize(
    "text", [f"{MARK_START} only", f"{MARK_END} only", f"{MARK_END}\n{MARK_START}"]
)
def test_replace_marked_block_rejects_unbalanced_markers(text):
    with pytest.raises(ValueError):
        replace_marked_block(text, "x")


def test_md_table_and_pct():
    assert md_table(["a", "b"], [[1, 2]]) == "| a | b |\n|---|---|\n| 1 | 2 |"
    assert pct(1, 3) == "33.33%"
    assert pct(1, 0) == "-"
