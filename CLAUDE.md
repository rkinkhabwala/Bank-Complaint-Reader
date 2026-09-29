# Complaint Radar — project conventions

Early-warning system for CFPB consumer-finance complaints on Databricks. Full spec: [spec.md](spec.md).
Portfolio project for Forward Deployed Engineer roles: code quality, README clarity and honest
data-quality reporting matter as much as features.

## Target platform: Databricks Free Edition

Every design choice must fit within these limits (source: https://docs.databricks.com/aws/en/getting-started/free-edition-limitations, last updated 2026-09-25):

- Serverless compute only; 1 SQL warehouse (2X-Small); max 5 concurrent job tasks.
- **One active pipeline per pipeline type**, so bronze, silver and gold all live in ONE Lakeflow pipeline.
- 1 AI Search endpoint / 1 search unit; no Direct Vector Access.
- Up to 3 Apps, each stops 24 h after start or redeploy.
- Limited model serving; no provisioned throughput or batch inference.
- Outbound internet restricted, so data is downloaded **locally** and uploaded to a UC Volume.
- Exceeding a usage quota shuts compute down for the rest of the day or month. Pipelines run as
  *triggered*, never continuous. Avoid full refreshes unless they're needed.

## Stack

- Python 3.11+ for local tooling (`ingest/`, `tests/`); `databricks-sdk` for Volume uploads.
- Databricks CLI v1.x (v1.18.0 current as of 2026-09-29) with profile `complaint-radar`; never hard-coded tokens.
- Declarative Automation Bundle (`databricks.yml`; formerly "Asset Bundles") for pipeline, jobs, app,
  dashboard. UC catalog/schemas/Volume are created by `setup/` SQL, NOT bundle resources, so data
  outlives `bundle destroy`. Validate structure offline against the CLI JSON schema when the CLI is unavailable.
- Lakeflow Spark Declarative Pipelines in **SQL** (`pipelines/*.sql`): Auto Loader, AUTO CDC, expectations.
- pytest for tests.

## Unity Catalog naming

- Catalog: `complaint_radar`
- Schemas: `raw`, `bronze`, `silver`, `gold`, `ml`
- Landing Volume: `/Volumes/complaint_radar/raw/landing/`
  - `bulk/`: unzipped bulk CSV part files, one folder per snapshot
  - `delta/`: daily API JSON files (Milestone 2+)
  - `seeds/`: seed CSVs (for example `taxonomy_map.csv`) uploaded from `pipelines/seeds/`
  - `_manifests/`: `bulk_<snapshot>.json` (row counts, hashes), kept outside `bulk/` so Auto
    Loader never reads it as data
- Tables: `bronze.complaints_raw`, `silver.complaints`, `silver.taxonomy_map`, `silver.company_dim`,
  `gold.weekly_metrics`, `gold.peer_benchmarks`, `gold.spike_alerts`, `gold.complaint_enriched`.
- Columns are snake_case. CFPB headers map deterministically (see `docs/data_notes.md`).

## Folder layout (spec §6; repo root = this folder)

```
databricks.yml        README.md        spec.md        CLAUDE.md
.env.example          pyproject.toml
setup/                # catalog/schema/volume DDL (create_uc_objects.sql)
ingest/               # download_bulk.py, pull_daily_delta.py
pipelines/            # bronze.sql, silver.sql, gold.sql, seeds/taxonomy_{product,issue}_map.csv
jobs/                 # spike_detection.py, enrich_complaints.sql
agent/                # agent.py, tools/, prompts/, evals/
app/                  # app.py, app.yaml
dashboards/           # complaint_radar.lvdash.json
docs/                 # data_notes.md (verified CFPB facts + reconciliation results)
tests/                # pytest
```

## Commands

```bash
# local env
uv venv --python 3.12 && source .venv/bin/activate && uv pip install -e ".[dev]"
cp .env.example .env            # set DATABRICKS_CONFIG_PROFILE; no secrets in git

# auth (once)
databricks auth login --host <workspace-url> --profile complaint-radar

# tests
pytest -q

# one-time UC setup (idempotent): catalog, 5 schemas, landing Volume + subfolders
python setup/run_setup.py --dry-run         # preview
python setup/run_setup.py

# data
python -m ingest.download_bulk              # download, validate, split, upload (idempotent)
python -m ingest.profile_bulk               # DuckDB profile -> docs/profile/<snap>/ + data_notes.md block
python -m ingest.upload_seeds               # validate + upload pipelines/seeds/*.csv (taxonomy v1)

# deploy + run
databricks bundle validate -t dev
databricks bundle deploy   -t dev
databricks bundle run      -t dev complaint_radar_pipeline
python -m ingest.reconcile                  # source vs bronze vs silver + event log -> data_notes.md (exit 1 on failure)
```

## Working rules

1. **Verify Databricks syntax against current docs, never memory.** APIs change often
   (DLT → Lakeflow, Vector Search → AI Search, APPLY CHANGES → AUTO CDC). Cite the docs page in a
   code comment above pipeline, bundle and `ai_query` code. If you can't verify something, mark it
   `TODO(verify): <what>`.
2. **Never invent CFPB facts** (field names, URLs, row counts, value sets). Verify them from the
   CFPB site or the downloaded file and record them in `docs/data_notes.md` with the source and date.
3. **Small, reviewable steps.** After each step, show what changed and how to run or verify it,
   then wait for approval.
4. **pytest for non-trivial logic:** parsers, header→snake_case mapping, taxonomy mapping, spike
   math, SQL allow-lists.
5. **No secrets in git.** Use `.env.example` and Databricks CLI profiles. `.env`, `.databricks/` and
   `data/` are gitignored.
6. Report data quality honestly: dropped and warned row counts come from the pipeline event log,
   not estimates.
