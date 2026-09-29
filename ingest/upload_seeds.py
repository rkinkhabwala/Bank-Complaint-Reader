"""Upload pipelines/seeds/*.csv to /Volumes/<catalog>/raw/landing/seeds/ (idempotent).

    python -m ingest.upload_seeds             # validate, then upload changed seeds
    python -m ingest.upload_seeds --dry-run   # validate only

Validation first: a seed the resolver rejects (for example duplicate keys) is never uploaded.
A seed is skipped when the remote file already has the same size. Seeds are small, so an
upload with overwrite=True is otherwise cheap. Run a pipeline refresh afterwards so
silver.taxonomy_map picks the change up.
"""

from __future__ import annotations

import argparse
import os
import sys

from ingest.taxonomy import ISSUE_SEED, PRODUCT_SEED, SEEDS, Taxonomy


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", default=os.environ.get("CR_CATALOG", "complaint_radar"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    Taxonomy.load(PRODUCT_SEED, ISSUE_SEED)  # raises on malformed/duplicate rules
    seeds = sorted(SEEDS.glob("*.csv"))
    print(f"[seeds] validated {len(seeds)} file(s): {', '.join(p.name for p in seeds)}")
    if args.dry_run:
        return 0

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.errors import NotFound

    w = WorkspaceClient()
    remote_dir = f"/Volumes/{args.catalog}/raw/landing/seeds"
    w.files.create_directory(remote_dir)
    for path in seeds:
        remote = f"{remote_dir}/{path.name}"
        try:
            same = w.files.get_metadata(remote).content_length == path.stat().st_size
        except NotFound:
            same = False
        if same:
            print(f"  unchanged {path.name}")
            continue
        with open(path, "rb") as f:
            w.files.upload(remote, f, overwrite=True)
        print(f"  uploaded  {path.name} -> {remote}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
