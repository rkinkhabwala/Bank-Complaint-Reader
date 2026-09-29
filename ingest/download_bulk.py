"""Download the CFPB bulk complaints zip, validate and split it, upload the parts to a UC Volume.

Stages (each one idempotent and resumable):
  1. download: HTTP Range resume into data/downloads/; restarts if the remote ETag changes
  2. split:    stream-parse the CSV inside the zip (no 5+ GB extraction to disk), check the header,
               count records, detect duplicate Complaint IDs, write N-row parts plus manifest.json.
               Reading each zip member to EOF makes zipfile verify its CRC-32, so a corrupt
               download fails here.
  3. upload:   parts -> /Volumes/<catalog>/raw/landing/bulk/<snapshot>/, skipping parts already
               uploaded with the same size; the manifest goes last, to .../landing/_manifests/

    python -m ingest.download_bulk                   # all stages
    python -m ingest.download_bulk --skip-upload     # local only (no workspace needed)

Why split? 5.5 GB as ~150 MB parts makes the upload resumable per file and gives Auto Loader
many files to read in parallel. The 2026-09-29 file has no embedded newlines (physical lines ==
records + 1), but the parser stays RFC 4180-correct in case CFPB adds free-text fields again.
"""

from __future__ import annotations

import argparse
import csv
import email.utils
import hashlib
import io
import json
import os
import shutil
import sys
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import requests

from ingest.schema import COMPLAINT_ID_COLUMN, DATE_RECEIVED_COLUMN, header_diff

DEFAULT_URL = "https://files.consumerfinance.gov/ccdb/complaints.csv.zip"
CSV_MEMBER = "complaints.csv"
DEFAULT_ROWS_PER_PART = 500_000
CHUNK = 1 << 20  # 1 MiB
MANIFEST_VERSION = 1


# ---------------------------------------------------------------------------------------------
# 1. download
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteInfo:
    etag: str
    last_modified: str  # RFC 7231 date, as sent by the server
    content_length: int

    @property
    def snapshot_id(self) -> str:
        """YYYY-MM-DD (UTC) of the server's Last-Modified: names the snapshot folder."""
        return email.utils.parsedate_to_datetime(self.last_modified).strftime("%Y-%m-%d")


def head_remote(url: str, session: requests.Session) -> RemoteInfo:
    r = session.head(url, timeout=30, allow_redirects=True)
    r.raise_for_status()
    return RemoteInfo(
        etag=r.headers["ETag"].strip('"'),
        last_modified=r.headers["Last-Modified"],
        content_length=int(r.headers["Content-Length"]),
    )


def download(url: str, dest: Path, session: requests.Session) -> RemoteInfo:
    """Download `url` to `dest`, resuming a partial `.part` file if the remote is unchanged."""
    remote = head_remote(url, session)
    meta_path = dest.with_suffix(dest.suffix + ".meta.json")
    part = dest.with_suffix(dest.suffix + ".part")
    stored = json.loads(meta_path.read_text()) if meta_path.exists() else None
    same_remote = stored is not None and stored.get("etag") == remote.etag

    if dest.exists() and same_remote and dest.stat().st_size == remote.content_length:
        print(f"[download] up to date: {dest.name} (etag {remote.etag})")
        return remote
    if not same_remote:  # new or changed remote: throw away stale partials
        part.unlink(missing_ok=True)
        dest.unlink(missing_ok=True)

    dest.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(asdict(remote), indent=2))
    offset = part.stat().st_size if part.exists() else 0
    # If-Range: the server sends 206 only if the ETag still matches, otherwise the full body (200).
    headers = {"Range": f"bytes={offset}-", "If-Range": f'"{remote.etag}"'} if offset else {}

    with session.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        if offset and r.status_code != 206:
            print("[download] server did not honour Range; restarting from 0")
            offset = 0
        state = f"resuming at {offset:,}" if offset else "starting"
        print(f"[download] {state} -> {remote.content_length:,} bytes")
        with open(part, "ab" if offset else "wb") as f:
            for chunk in r.iter_content(CHUNK):
                f.write(chunk)
    size = part.stat().st_size
    if size != remote.content_length:
        raise OSError(f"Incomplete download: {size:,} of {remote.content_length:,} bytes; re-run")
    part.rename(dest)
    print(f"[download] complete: {dest} ({size:,} bytes)")
    return remote


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------
# 2. validate + split
# ---------------------------------------------------------------------------------------------


class IdTracker:
    """Duplicate detector for non-negative integer IDs using a growable bitmap.

    Uses about max_id/8 bytes (~2 MB for IDs up to 16M), far less than a set of millions of ints.
    """

    def __init__(self) -> None:
        self._bits = bytearray()
        self.duplicates: list[int] = []
        self.duplicate_count = 0
        self.invalid = 0

    def add(self, raw: str) -> None:
        raw = raw.strip()
        if not raw.isdigit():
            self.invalid += 1
            return
        n = int(raw)
        byte, bit = divmod(n, 8)
        if byte >= len(self._bits):
            self._bits.extend(bytes(max(byte + 1 - len(self._bits), len(self._bits))))
        if self._bits[byte] >> bit & 1:
            self.duplicate_count += 1
            if len(self.duplicates) < 100:  # keep a sample for data_notes
                self.duplicates.append(n)
        else:
            self._bits[byte] |= 1 << bit


@dataclass
class PartInfo:
    name: str
    rows: int
    bytes: int
    sha256: str


@dataclass
class SplitStats:
    header: list[str]
    rows: int = 0
    ragged_rows: int = 0  # field count != header length; written as-is so bronze can rescue them
    invalid_complaint_ids: int = 0
    duplicate_complaint_ids: int = 0
    duplicate_id_sample: list[int] = field(default_factory=list)
    min_date_received: str | None = None
    max_date_received: str | None = None
    parts: list[PartInfo] = field(default_factory=list)


def iter_csv_rows(binary: io.BufferedIOBase) -> Iterator[list[str]]:
    """RFC 4180 records (embedded newlines inside quotes preserved). BOM-tolerant."""
    return csv.reader(io.TextIOWrapper(binary, encoding="utf-8-sig", newline=""))


def split_rows(
    rows: Iterable[list[str]], out_dir: Path, rows_per_part: int, *, allow_schema_change: bool
) -> SplitStats:
    """Write header + up to `rows_per_part` records per part file, collecting validation stats."""
    it = iter(rows)
    header = next(it, None)
    if header is None:
        raise ValueError("CSV is empty (no header row)")
    diff = header_diff(header)
    if diff and not allow_schema_change:
        raise ValueError(f"CSV header differs from ingest/schema.py EXPECTED_COLUMNS: {diff}")

    id_idx = header.index(COMPLAINT_ID_COLUMN)
    date_idx = header.index(DATE_RECEIVED_COLUMN)
    stats = SplitStats(header=header)
    ids = IdTracker()
    out_dir.mkdir(parents=True, exist_ok=True)

    part_no, part_rows, fh, writer = 0, 0, None, None

    def close_part() -> None:
        nonlocal fh
        if fh is None:
            return
        fh.close()
        path = Path(fh.name)
        stats.parts.append(PartInfo(path.name, part_rows, path.stat().st_size, sha256_file(path)))
        fh = None

    for row in it:
        if fh is None or part_rows >= rows_per_part:
            close_part()
            part_no += 1
            part_rows = 0
            fh = open(
                out_dir / f"complaints_part_{part_no:04d}.csv", "w", encoding="utf-8", newline=""
            )
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(header)
        writer.writerow(row)
        part_rows += 1
        stats.rows += 1
        if len(row) != len(header):
            stats.ragged_rows += 1
            continue
        ids.add(row[id_idx])
        d = row[date_idx]
        if d:  # ISO yyyy-mm-dd sorts lexically; malformed values surface in bronze
            if stats.min_date_received is None or d < stats.min_date_received:
                stats.min_date_received = d
            if stats.max_date_received is None or d > stats.max_date_received:
                stats.max_date_received = d
    close_part()

    stats.invalid_complaint_ids = ids.invalid
    stats.duplicate_complaint_ids = ids.duplicate_count
    stats.duplicate_id_sample = ids.duplicates
    return stats


def split_snapshot(
    zip_path: Path,
    snapshot_dir: Path,
    remote: RemoteInfo,
    url: str,
    rows_per_part: int,
    *,
    allow_schema_change: bool = False,
) -> dict:
    """Validate and split the zip into snapshot_dir/parts. Skips if the manifest matches the zip."""
    manifest_path = snapshot_dir / "manifest.json"
    zip_sha = sha256_file(zip_path)
    if manifest_path.exists():
        m = json.loads(manifest_path.read_text())
        if (
            m.get("source", {}).get("zip_sha256") == zip_sha
            and m.get("rows_per_part") == rows_per_part
        ):
            print(f"[split] up to date: {manifest_path}")
            return m

    tmp = snapshot_dir / "parts.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"[split] parsing {zip_path.name} -> {rows_per_part:,} rows/part")
    with zipfile.ZipFile(zip_path) as z:
        members = z.namelist()
        if members != [CSV_MEMBER]:
            raise ValueError(f"Expected exactly [{CSV_MEMBER!r}] in zip, found {members}")
        info = z.getinfo(CSV_MEMBER)
        with z.open(CSV_MEMBER) as raw:  # CRC-32 verified by zipfile when read to EOF
            stats = split_rows(
                iter_csv_rows(raw), tmp, rows_per_part, allow_schema_change=allow_schema_change
            )

    final = snapshot_dir / "parts"
    shutil.rmtree(final, ignore_errors=True)
    tmp.rename(final)

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "snapshot_id": remote.snapshot_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": {
            "url": url,
            "etag": remote.etag,
            "last_modified": remote.last_modified,
            "zip_bytes": zip_path.stat().st_size,
            "zip_sha256": zip_sha,
            "csv_member": CSV_MEMBER,
            "csv_uncompressed_bytes": info.file_size,
            "csv_crc32": f"{info.CRC:08x}",
        },
        "rows_per_part": rows_per_part,
        **{k: v for k, v in asdict(stats).items() if k != "parts"},
        "parts": [asdict(p) for p in stats.parts],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        f"[split] {stats.rows:,} rows -> {len(stats.parts)} parts; "
        f"duplicate ids={stats.duplicate_complaint_ids:,}, "
        f"invalid ids={stats.invalid_complaint_ids:,}, ragged rows={stats.ragged_rows:,}, "
        f"date_received {stats.min_date_received}..{stats.max_date_received}"
    )
    return manifest


# ---------------------------------------------------------------------------------------------
# 3. upload
# ---------------------------------------------------------------------------------------------


def plan_uploads(
    parts: list[dict], remote_sizes: dict[str, int | None]
) -> tuple[list[dict], list[dict]]:
    """Split parts into (to_upload, skipped). A part is skipped only if the remote size matches.

    `remote_sizes` maps part name -> remote content_length, or None if the file is missing.
    """
    todo, skipped = [], []
    for p in parts:
        (skipped if remote_sizes.get(p["name"]) == p["bytes"] else todo).append(p)
    return todo, skipped


def other_snapshots(existing_dirs: Iterable[str], snapshot_id: str) -> list[str]:
    return sorted(d for d in existing_dirs if d != snapshot_id)


def upload_snapshot(
    manifest: dict, snapshot_dir: Path, catalog: str, *, allow_new_snapshot: bool
) -> None:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.errors import NotFound

    w = WorkspaceClient()  # honours DATABRICKS_CONFIG_PROFILE
    landing = f"/Volumes/{catalog}/raw/landing"
    bulk_root = f"{landing}/bulk"
    snap = manifest["snapshot_id"]
    remote_dir = f"{bulk_root}/{snap}"
    print(f"[upload] {w.config.host} -> {remote_dir}")

    # Guard: every bulk snapshot is FULL history. A second one doubles bronze (silver is still
    # correct, since AUTO CDC upserts on complaint_id) and costs a full re-parse of quota.
    try:
        existing = [e.name for e in w.files.list_directory_contents(bulk_root) if e.is_directory]
    except NotFound:
        sys.exit(f"{bulk_root} not found. Run `python setup/run_setup.py` first.")
    others = other_snapshots(existing, snap)
    if others and not allow_new_snapshot:
        sys.exit(
            f"Bulk snapshot(s) {others} already uploaded. Uploading {snap} re-ingests full "
            "history into bronze. Use daily deltas instead, or pass --allow-new-snapshot "
            "if you really mean it."
        )

    def remote_size(name: str) -> int | None:
        try:
            return w.files.get_metadata(f"{remote_dir}/{name}").content_length
        except NotFound:
            return None

    parts = manifest["parts"]
    todo, skipped = plan_uploads(parts, {p["name"]: remote_size(p["name"]) for p in parts})
    print(f"[upload] {len(skipped)} part(s) already present, {len(todo)} to upload")
    w.files.create_directory(remote_dir)
    for i, p in enumerate(todo, 1):
        with open(snapshot_dir / "parts" / p["name"], "rb") as f:
            w.files.upload(f"{remote_dir}/{p['name']}", f, overwrite=True)
        print(f"  [{i}/{len(todo)}] {p['name']} ({p['bytes']:,} bytes)")

    # Manifest last, and outside bulk/ so Auto Loader never reads it as data.
    w.files.create_directory(f"{landing}/_manifests")
    body = io.BytesIO(json.dumps(manifest, indent=2).encode())
    w.files.upload(f"{landing}/_manifests/bulk_{snap}.json", body, overwrite=True)
    print(f"[upload] done; manifest at {landing}/_manifests/bulk_{snap}.json")


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-dir", type=Path, default=Path(os.environ.get("CR_DATA_DIR", "data"))
    )
    parser.add_argument("--url", default=os.environ.get("CR_BULK_URL", DEFAULT_URL))
    parser.add_argument("--catalog", default=os.environ.get("CR_CATALOG", "complaint_radar"))
    parser.add_argument("--rows-per-part", type=int, default=DEFAULT_ROWS_PER_PART)
    parser.add_argument("--skip-upload", action="store_true", help="download + split only")
    parser.add_argument(
        "--allow-new-snapshot",
        action="store_true",
        help="upload even if another bulk snapshot is already in the Volume",
    )
    parser.add_argument(
        "--allow-schema-change",
        action="store_true",
        help="continue when the CSV header differs from EXPECTED_COLUMNS",
    )
    args = parser.parse_args(argv)

    zip_path = args.data_dir / "downloads" / "complaints.csv.zip"
    with requests.Session() as s:
        remote = download(args.url, zip_path, s)
    snapshot_dir = args.data_dir / "bulk" / remote.snapshot_id
    manifest = split_snapshot(
        zip_path,
        snapshot_dir,
        remote,
        args.url,
        args.rows_per_part,
        allow_schema_change=args.allow_schema_change,
    )
    if args.skip_upload:
        print("[upload] skipped (--skip-upload)")
        return 0
    upload_snapshot(
        manifest, snapshot_dir, args.catalog, allow_new_snapshot=args.allow_new_snapshot
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
