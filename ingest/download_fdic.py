"""Download active FDIC-insured institutions (BankFind API), validate, upload to the landing Volume.

    python -m ingest.download_fdic                 # download + validate + upload (idempotent)
    python -m ingest.download_fdic --skip-upload   # local only

Used for bank peer groups (holding-company total assets). Source and field definitions, checked
2026-09-30:
  API:    https://api.fdic.gov/banks/institutions   (docs: https://api.fdic.gov/banks/docs)
  Fields: https://api.fdic.gov/banks/docs/institution_properties.yaml
Measured facts (docs/data_notes.md): limit=10000 returns all active banks in one page (20000 returns
nothing); `x-ratelimit-limit: 20`; REPDTE is 'MM/DD/YYYY'.

ASSET unit: the field definition does not state it. We treat it as thousands of dollars (the FDIC
Call Report convention). This is inferred from magnitude and enforced by
check_asset_magnitude(): the largest bank must fall between $1T and $10T.

Output: data/fdic/<snapshot>/institutions.csv + manifest.json, where snapshot = the most common
REPDTE (quarter end) as YYYY-MM-DD. Uploaded to
/Volumes/<catalog>/raw/landing/fdic/<snapshot>/institutions.csv (manifest to _manifests/).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import requests

from ingest.download_bulk import sha256_file

API = "https://api.fdic.gov/banks/institutions"
PAGE_LIMIT = 10_000  # measured maximum; 20000 returns an empty response
FIELDS = (
    "CERT",
    "NAME",
    "ACTIVE",
    "ASSET",
    "DEP",
    "NAMEHCR",
    "RSSDHCR",
    "FED_RSSD",
    "BKCLASS",
    "STALP",
    "CITY",
    "SPECGRPN",
    "REPDTE",
)
# Largest US bank, read as thousands of dollars: must be $1T..$10T (unit sanity check).
MAX_ASSET_THOUSANDS_RANGE = (1_000_000_000, 10_000_000_000)


def repdte_to_iso(value: str | None) -> str | None:
    """'06/30/2026' -> '2026-06-30'; None/'' -> None."""
    if not value:
        return None
    return datetime.strptime(value, "%m/%d/%Y").strftime("%Y-%m-%d")


def fetch_all(session: requests.Session, limit: int = PAGE_LIMIT) -> list[dict]:
    """All active institutions, paging by offset until meta.total rows are collected."""
    rows: list[dict] = []
    total = None
    while total is None or len(rows) < total:
        r = session.get(
            API,
            params={
                "filters": "ACTIVE:1",
                "fields": ",".join(FIELDS),
                "sort_by": "CERT",
                "sort_order": "ASC",
                "limit": limit,
                "offset": len(rows),
            },
            timeout=120,
        )
        r.raise_for_status()
        body = r.json()
        page = [item["data"] for item in body.get("data", [])]
        total = body.get("meta", {}).get("total")
        if total is None:
            raise ValueError(f"FDIC response has no meta.total: {str(body)[:200]}")
        if not page:
            raise ValueError(f"Empty page at offset {len(rows)} of {total}")
        rows.extend(page)
    return rows


def check_asset_magnitude(rows: list[dict]) -> int:
    assets = [r["ASSET"] for r in rows if r.get("ASSET") is not None]
    if not assets:
        raise ValueError("No ASSET values")
    top = max(assets)
    lo, hi = MAX_ASSET_THOUSANDS_RANGE
    if not lo <= top <= hi:
        raise ValueError(
            f"Largest ASSET {top:,} is outside {lo:,}..{hi:,}: the unit is no longer thousands "
            "of dollars, or the data changed. Check FDIC docs before using peer-group thresholds."
        )
    return top


def validate(rows: list[dict]) -> dict:
    """Checks + summary facts. Raises on anything that would corrupt peer groups."""
    if not rows:
        raise ValueError("No institutions returned")
    certs = [r.get("CERT") for r in rows]
    dup = [c for c, n in Counter(certs).items() if n > 1]
    if dup or any(c in (None, "") for c in certs):
        raise ValueError(f"CERT must be unique and non-empty; duplicates: {dup[:5]}")
    if any(str(r.get("ACTIVE")) != "1" for r in rows):
        raise ValueError("Non-active institution returned despite filter ACTIVE:1")
    repdtes = Counter(repdte_to_iso(r.get("REPDTE")) for r in rows)
    snapshot = max((d for d in repdtes if d), key=lambda d: (repdtes[d], d))
    return {
        "rows": len(rows),
        "snapshot_id": snapshot,
        "repdte_counts": {str(k): v for k, v in sorted(repdtes.items(), key=lambda kv: str(kv[0]))},
        "null_asset": sum(1 for r in rows if r.get("ASSET") is None),
        "max_asset_thousands": check_asset_magnitude(rows),
        "with_holding_company": sum(1 for r in rows if r.get("RSSDHCR") not in (None, "", "0", 0)),
    }


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(FIELDS), lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in FIELDS})
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-dir", type=Path, default=Path(os.environ.get("CR_DATA_DIR", "data"))
    )
    parser.add_argument("--catalog", default=os.environ.get("CR_CATALOG", "complaint_radar"))
    parser.add_argument("--skip-upload", action="store_true")
    args = parser.parse_args(argv)

    with requests.Session() as s:
        rows = fetch_all(s)
    facts = validate(rows)
    snap = facts["snapshot_id"]
    out_dir = args.data_dir / "fdic" / snap
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "institutions.csv"
    csv_path.write_text(to_csv(rows), encoding="utf-8")
    manifest = {
        **facts,
        "source": {"url": API, "filters": "ACTIVE:1", "fields": list(FIELDS)},
        "asset_unit": "thousands of USD (inferred; see module docstring)",
        "sha256": sha256_file(csv_path),
        "bytes": csv_path.stat().st_size,
        "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        f"[fdic] {facts['rows']:,} active institutions, snapshot {snap} "
        f"(REPDTE counts {facts['repdte_counts']}), largest ASSET {facts['max_asset_thousands']:,}k"
    )
    if args.skip_upload:
        return 0

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.errors import NotFound

    w = WorkspaceClient()
    landing = f"/Volumes/{args.catalog}/raw/landing"
    remote = f"{landing}/fdic/{snap}/institutions.csv"
    try:
        same = w.files.get_metadata(remote).content_length == manifest["bytes"]
    except NotFound:
        same = False
    if same:
        print(f"[fdic] unchanged: {remote}")
        return 0
    w.files.create_directory(f"{landing}/fdic/{snap}")
    with open(csv_path, "rb") as f:
        w.files.upload(remote, f, overwrite=True)
    w.files.create_directory(f"{landing}/_manifests")
    w.files.upload(
        f"{landing}/_manifests/fdic_{snap}.json",
        io.BytesIO(json.dumps(manifest, indent=2).encode()),
        overwrite=True,
    )
    print(f"[fdic] uploaded {remote}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
