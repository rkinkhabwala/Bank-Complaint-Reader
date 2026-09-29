import csv
import io
import json
import zipfile
from pathlib import Path

import pytest

from ingest import download_bulk as db
from ingest.schema import EXPECTED_COLUMNS

HEADER = list(EXPECTED_COLUMNS)


def make_row(complaint_id: str, date: str = "2024-01-15", **overrides) -> list[str]:
    row = dict.fromkeys(HEADER, "")
    row.update({"Complaint ID": complaint_id, "Date received": date, "Product": "Mortgage"})
    row.update(overrides)
    return [row[h] for h in HEADER]


def csv_bytes(rows: list[list[str]], header=HEADER) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows([header, *rows])
    return buf.getvalue().encode()


def read_parts(parts_dir: Path) -> list[list[list[str]]]:
    out = []
    for p in sorted(parts_dir.glob("complaints_part_*.csv")):
        with open(p, encoding="utf-8", newline="") as f:
            out.append(list(csv.reader(f)))
    return out


# --- IdTracker ------------------------------------------------------------------------------


def test_id_tracker_counts_duplicates_and_invalid():
    t = db.IdTracker()
    for raw in ["5", "7", "5", " 7 ", "abc", "", "-3", "12345678", "5"]:
        t.add(raw)
    assert t.duplicate_count == 3
    assert t.duplicates == [5, 7, 5]
    assert t.invalid == 3  # "abc", "", "-3"


def test_id_tracker_grows_bitmap_for_large_ids():
    t = db.IdTracker()
    t.add("0")
    t.add("20000000")
    t.add("20000000")
    assert t.duplicate_count == 1


# --- split_rows -----------------------------------------------------------------------------


def test_split_rows_parts_header_and_fidelity(tmp_path):
    rows = [
        make_row("1", "2020-05-01"),
        make_row("2", "2019-01-02", Company='Acme "Bank", N.A.'),  # quotes and commas
        make_row("3", "2024-12-31", Issue="Line one\nline two\r\nline three"),  # embedded newlines
        make_row("4", "2021-07-04", Tags="Older American, Servicemember"),
        make_row("5", "2022-02-02"),
    ]
    stats = db.split_rows(
        iter([HEADER, *rows]), tmp_path, rows_per_part=2, allow_schema_change=False
    )

    parts = read_parts(tmp_path)
    assert [len(p) - 1 for p in parts] == [2, 2, 1]
    assert all(p[0] == HEADER for p in parts), "every part repeats the header"
    assert [r for p in parts for r in p[1:]] == rows, "values round-trip byte-for-byte"
    assert [p.rows for p in stats.parts] == [2, 2, 1]
    assert stats.rows == 5
    assert (stats.min_date_received, stats.max_date_received) == ("2019-01-02", "2024-12-31")
    assert stats.duplicate_complaint_ids == stats.invalid_complaint_ids == stats.ragged_rows == 0
    assert all(len(p.sha256) == 64 and p.bytes > 0 for p in stats.parts)


def test_split_rows_reports_duplicates_invalid_and_ragged(tmp_path):
    rows = [make_row("1"), make_row("1"), make_row(""), ["too", "few", "fields"]]
    stats = db.split_rows(iter([HEADER, *rows]), tmp_path, 100, allow_schema_change=False)
    assert stats.rows == 4
    assert stats.duplicate_complaint_ids == 1 and stats.duplicate_id_sample == [1]
    assert stats.invalid_complaint_ids == 1
    assert stats.ragged_rows == 1
    assert len(read_parts(tmp_path)[0]) == 5, "ragged rows are still written for bronze to rescue"


def test_split_rows_rejects_changed_header(tmp_path):
    bad = [*HEADER, "Consumer complaint narrative"]
    with pytest.raises(ValueError, match="unexpected=\\['Consumer complaint narrative'\\]"):
        db.split_rows(iter([bad]), tmp_path, 10, allow_schema_change=False)


def test_split_rows_empty_file(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        db.split_rows(iter([]), tmp_path, 10, allow_schema_change=False)


def test_iter_csv_rows_strips_bom():
    data = b"\xef\xbb\xbf" + csv_bytes([make_row("9")])
    rows = list(db.iter_csv_rows(io.BytesIO(data)))
    assert rows[0] == HEADER


# --- split_snapshot (zip end-to-end, idempotency) -------------------------------------------

REMOTE = db.RemoteInfo(
    etag="abc-1", last_modified="Tue, 29 Sep 2026 09:15:26 GMT", content_length=0
)


def write_zip(path: Path, rows, member=db.CSV_MEMBER) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, csv_bytes(rows))


def test_snapshot_id_from_last_modified():
    assert REMOTE.snapshot_id == "2026-09-29"


def test_split_snapshot_writes_manifest_and_is_idempotent(tmp_path, capsys):
    zp = tmp_path / "c.zip"
    write_zip(zp, [make_row(str(i)) for i in range(1, 6)])
    snap = tmp_path / "snap"

    m1 = db.split_snapshot(zp, snap, REMOTE, "http://x", rows_per_part=2)
    assert m1["rows"] == 5 and len(m1["parts"]) == 3
    assert m1["source"]["zip_sha256"] == db.sha256_file(zp)
    assert json.loads((snap / "manifest.json").read_text()) == m1
    assert not (snap / "parts.tmp").exists()

    mtimes = {p.name: p.stat().st_mtime_ns for p in (snap / "parts").iterdir()}
    m2 = db.split_snapshot(zp, snap, REMOTE, "http://x", rows_per_part=2)
    assert m2 == m1
    assert "up to date" in capsys.readouterr().out
    assert {p.name: p.stat().st_mtime_ns for p in (snap / "parts").iterdir()} == mtimes

    m3 = db.split_snapshot(zp, snap, REMOTE, "http://x", rows_per_part=10)  # new part size
    assert len(m3["parts"]) == 1 and len(list((snap / "parts").iterdir())) == 1


def test_split_snapshot_rejects_unexpected_zip_members(tmp_path):
    zp = tmp_path / "c.zip"
    write_zip(zp, [make_row("1")], member="other.csv")
    with pytest.raises(ValueError, match="Expected exactly"):
        db.split_snapshot(zp, tmp_path / "snap", REMOTE, "http://x", 10)


# --- upload planning ------------------------------------------------------------------------


def test_plan_uploads_skips_only_matching_sizes():
    parts = [{"name": "a", "bytes": 10}, {"name": "b", "bytes": 20}, {"name": "c", "bytes": 30}]
    todo, skipped = db.plan_uploads(parts, {"a": 10, "b": 19, "c": None})
    assert [p["name"] for p in todo] == ["b", "c"]
    assert [p["name"] for p in skipped] == ["a"]


def test_other_snapshots():
    assert db.other_snapshots(["2026-09-29", "2026-01-01"], "2026-09-29") == ["2026-01-01"]
    assert db.other_snapshots(["2026-09-29"], "2026-09-29") == []


# --- download resume (fake HTTP session) ----------------------------------------------------

PAYLOAD = bytes(range(256)) * 40


class FakeResponse:
    def __init__(self, status: int, body: bytes, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i : i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSession:
    def __init__(self, etag="e1", honour_range=True):
        self.etag, self.honour_range, self.gets = etag, honour_range, []

    def head(self, url, **kw):
        return FakeResponse(200, b"", {
            "ETag": f'"{self.etag}"',
            "Last-Modified": "Tue, 29 Sep 2026 09:15:26 GMT",
            "Content-Length": str(len(PAYLOAD)),
        })  # fmt: skip

    def get(self, url, headers=None, **kw):
        self.gets.append(headers or {})
        rng = (headers or {}).get("Range")
        if rng and self.honour_range and headers.get("If-Range") == f'"{self.etag}"':
            start = int(rng.split("=")[1].rstrip("-"))
            return FakeResponse(206, PAYLOAD[start:])
        return FakeResponse(200, PAYLOAD)


def test_download_fresh_then_up_to_date(tmp_path):
    dest = tmp_path / "c.zip"
    s = FakeSession()
    db.download("u", dest, s)
    assert dest.read_bytes() == PAYLOAD
    db.download("u", dest, s)
    assert len(s.gets) == 1, "second call must not re-download"


@pytest.mark.parametrize("honour_range", [True, False])
def test_download_resumes_partial(tmp_path, honour_range):
    dest = tmp_path / "c.zip"
    s = FakeSession(honour_range=honour_range)
    db.download("u", dest, s)  # writes meta
    dest.rename(dest.with_suffix(".zip.part"))
    part = dest.with_suffix(".zip.part")
    part.write_bytes(PAYLOAD[:1000])  # simulate interruption

    db.download("u", dest, s)
    assert dest.read_bytes() == PAYLOAD
    assert s.gets[-1].get("Range") == "bytes=1000-"


def test_download_restarts_when_remote_changed(tmp_path):
    dest = tmp_path / "c.zip"
    db.download("u", dest, FakeSession(etag="old"))
    dest.rename(dest.with_suffix(".zip.part"))
    dest.with_suffix(".zip.part").write_bytes(b"stale bytes")

    s = FakeSession(etag="new")
    db.download("u", dest, s)
    assert dest.read_bytes() == PAYLOAD
    assert s.gets == [{}], "no Range header: stale partial discarded"
