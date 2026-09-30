import csv
import io

import pytest

from ingest import download_fdic as fd


def inst(cert, asset=1_000, repdte="06/30/2026", **kw):
    row = {"CERT": str(cert), "NAME": f"Bank {cert}", "ACTIVE": 1, "ASSET": asset, "REPDTE": repdte}
    row.update(kw)
    return row


BIG = inst(628, 4_091_315_000, NAMEHCR="JPMORGAN CHASE&CO", RSSDHCR="1039502")


def test_repdte_to_iso():
    assert fd.repdte_to_iso("06/30/2026") == "2026-06-30"
    assert fd.repdte_to_iso(None) is None and fd.repdte_to_iso("") is None


def test_validate_summary_and_snapshot_is_most_common_repdte():
    rows = [BIG, inst(1), inst(2), inst(3, repdte="03/31/2026"), inst(4, asset=None, repdte=None)]
    v = fd.validate(rows)
    assert v["snapshot_id"] == "2026-06-30"
    assert v["rows"] == 5 and v["null_asset"] == 1 and v["with_holding_company"] == 1
    assert v["repdte_counts"] == {"2026-03-31": 1, "2026-06-30": 3, "None": 1}


@pytest.mark.parametrize(
    ("rows", "msg"),
    [
        ([BIG, inst(1), inst(1)], "unique"),
        ([BIG, inst("")], "non-empty"),
        ([BIG, inst(2, ACTIVE=0)], "Non-active"),
        ([], "No institutions"),
    ],
)
def test_validate_rejects_bad_data(rows, msg):
    with pytest.raises(ValueError, match=msg):
        fd.validate(rows)


@pytest.mark.parametrize("top", [4_091_315, 4_091_315_000_000])  # dollars-in-millions / in-dollars
def test_asset_unit_sanity_check_fails_loudly_on_unit_change(top):
    with pytest.raises(ValueError, match="unit"):
        fd.check_asset_magnitude([inst(1, top)])


def test_to_csv_roundtrip_keeps_commas_and_nulls():
    rows = [inst(1, NAMEHCR="SMITH, JONES & CO", SPECGRPN=None)]
    parsed = list(csv.DictReader(io.StringIO(fd.to_csv(rows))))
    assert list(parsed[0]) == list(fd.FIELDS)
    assert parsed[0]["NAMEHCR"] == "SMITH, JONES & CO" and parsed[0]["SPECGRPN"] == ""


class FakeSession:
    def __init__(self, total, per_page):
        self.total, self.per_page, self.offsets = total, per_page, []

    def get(self, url, params, timeout):
        self.offsets.append(params["offset"])
        start = params["offset"]
        data = [{"data": inst(i)} for i in range(start, min(start + self.per_page, self.total))]
        body = {"meta": {"total": self.total}, "data": data}

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return body

        return R()


def test_fetch_all_pages_until_total():
    s = FakeSession(total=25, per_page=10)
    rows = fd.fetch_all(s, limit=10)
    assert len(rows) == 25 and s.offsets == [0, 10, 20]


def test_fetch_all_fails_on_short_data():
    s = FakeSession(total=25, per_page=10)
    orig = s.get

    def truncated(url, params, timeout):
        r = orig(url, params, timeout)
        if params["offset"] == 20:
            body = {"meta": {"total": 25}, "data": []}
            r.json = lambda: body
        return r

    s.get = truncated
    with pytest.raises(ValueError, match="Empty page"):
        fd.fetch_all(s, limit=10)
