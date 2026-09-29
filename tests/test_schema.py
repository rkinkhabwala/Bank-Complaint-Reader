import pytest

from ingest.schema import EXPECTED_COLUMNS, column_map, header_diff, to_snake_case


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Date received", "date_received"),
        ("Sub-product", "sub_product"),
        ("ZIP code", "zip_code"),
        ("Timely response?", "timely_response"),
        ("Complaint ID", "complaint_id"),
        ("Consumer consent provided?", "consumer_consent_provided"),  # historical column
        ("  Weird -- header?? ", "weird_header"),
    ],
)
def test_to_snake_case(header, expected):
    assert to_snake_case(header) == expected


def test_to_snake_case_rejects_symbol_only_header():
    with pytest.raises(ValueError):
        to_snake_case("???")


def test_column_map_for_real_header_is_complete_and_unique():
    m = column_map()
    assert list(m) == list(EXPECTED_COLUMNS)
    assert list(m.values()) == [
        "date_received", "product", "sub_product", "issue", "sub_issue",
        "company_public_response", "company", "state", "zip_code", "tags", "submitted_via",
        "date_sent_to_company", "company_response_to_consumer", "timely_response", "complaint_id",
    ]  # fmt: skip


def test_column_map_detects_collisions():
    with pytest.raises(ValueError, match="both map to"):
        column_map(["Sub-product", "Sub product"])


def test_header_diff():
    assert header_diff(list(EXPECTED_COLUMNS)) is None
    missing = header_diff([c for c in EXPECTED_COLUMNS if c != "Tags"])
    assert missing == "missing=['Tags']"
    extra = header_diff([*EXPECTED_COLUMNS, "Consumer complaint narrative"])
    assert extra == "unexpected=['Consumer complaint narrative']"
    swapped = header_diff([EXPECTED_COLUMNS[1], EXPECTED_COLUMNS[0], *EXPECTED_COLUMNS[2:]])
    assert swapped.startswith("same columns, different order")
