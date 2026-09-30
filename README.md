# Complaint Radar

An early-warning system for consumer-finance complaints, built on the Databricks lakehouse
(Free Edition). It turns the public CFPB Consumer Complaint Database into governed analytics
tables, spike alerts by company × product × issue × state, and a grounded assistant that
explains *why* a spike is happening, with cited complaint IDs.

> Status: Milestone 1 done (bulk load → bronze/silver, taxonomy v1, reconciliation). See [spec.md](spec.md).

## Architecture

_TODO: diagram (spec §3)._

Local download → UC Volume `complaint_radar.raw.landing` → Lakeflow pipeline
(Auto Loader bronze → AUTO CDC silver → gold) → spike detection, enrichment, agent, App.

## How to run

Tested on Databricks Free Edition (serverless) from macOS, 2026-09-30.

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/), the
[Databricks CLI](https://docs.databricks.com/aws/en/dev-tools/cli/install) v1.x, a Databricks
Free Edition workspace, and about 6 GB free disk space.

```bash
# 1. Databricks CLI + login (opens a browser). Use the bare workspace host, quoted.
brew tap databricks/tap && brew trust --formula databricks/tap/databricks && brew install databricks
databricks auth login --host "https://<your-workspace>.cloud.databricks.com" --profile complaint-radar

# 2. Local environment
uv venv --python 3.12 && source .venv/bin/activate && uv pip install -e ".[dev]"
cp .env.example .env
pytest                                   # 87 tests, no workspace needed

# 3. Unity Catalog objects: catalog, 5 schemas, landing Volume (idempotent)
python setup/run_setup.py

# 4. Data: download the CFPB bulk file (~350 MB zip, 5.5 GB CSV), validate, split into 37 parts,
#    upload to the Volume (~5.1 GB; resumable, skips parts already uploaded), then the taxonomy seeds
python -m ingest.download_bulk
python -m ingest.upload_seeds

# 5. Deploy and run the Lakeflow pipeline (triggered; ~4 min for the full history)
databricks bundle deploy -t dev
databricks bundle run -t dev complaint_radar_pipeline

# 6. Reconcile source -> bronze -> silver and the expectation metrics; exits 1 on any failed check
python -m ingest.reconcile
```

Optional: `python -m ingest.profile_bulk` regenerates the data profile in `docs/data_notes.md`.

**Troubleshooting**
- `zsh: no matches found` on login: quote the host and drop any `?o=...` suffix.
- `ModuleNotFoundError: No module named 'ingest'` in an iCloud-synced folder: macOS marks the venv's
  `.pth` files hidden and Python 3.12.13+ ignores them. Run `chflags nohidden .venv/lib/python*/site-packages/*.pth`,
  or run the modules from the repo root (`python -m ingest.…`).
- `CREATE CATALOG` fails with "Metastore storage root URL does not exist": create `complaint_radar` in
  Catalog Explorer with *Use default storage*, then re-run step 3.

## Data quality

Full history, snapshot 2026-09-29 (details: [docs/data_notes.md](docs/data_notes.md#reconciliation)):

| Stage | Rows |
|---|---|
| CFPB bulk CSV | 18,062,308 |
| `bronze.complaints_raw` | 18,062,308 (37/37 part files reconcile; 0 rescued rows) |
| dropped by expectations | 0 |
| `silver.complaints` | 18,062,308 (0 duplicate `complaint_id`s after two runs) |
| warned: missing `state` | 62,969 (0.35%) |

13/13 reconciliation checks pass. The event-log expectation counts equal direct counts in silver.
Taxonomy v1 maps all 1,031 observed product/sub-product/issue combinations to the 11 current CFPB
products ([docs/taxonomy/v1](docs/taxonomy/v1/README.md)).

**Known source change:** CFPB removed consumer narratives from the database in September 2026
(Release 24), so the bulk file has 15 columns, not the 18 in spec §2. See
[docs/data_notes.md](docs/data_notes.md).

## Repository layout

See [CLAUDE.md](CLAUDE.md#folder-layout-spec-6-repo-root--this-folder).

## Data source

[CFPB Consumer Complaint Database](https://www.consumerfinance.gov/data-research/consumer-complaints/).
Public data; narratives are published only with consumer consent and are scrubbed of PII by the CFPB.
