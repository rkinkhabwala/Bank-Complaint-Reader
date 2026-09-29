# Complaint Radar

An early-warning system for consumer-finance complaints, built on the Databricks lakehouse
(Free Edition). It turns the public CFPB Consumer Complaint Database into governed analytics
tables, spike alerts by company × product × issue × state, and a grounded assistant that
explains *why* a spike is happening, with cited complaint IDs.

> Status: Milestone 1 in progress (bulk load → bronze/silver). See [spec.md](spec.md).

## Architecture

_TODO: diagram (spec §3)._

Local download → UC Volume `complaint_radar.raw.landing` → Lakeflow pipeline
(Auto Loader bronze → AUTO CDC silver → gold) → spike detection, enrichment, agent, App.

## How to run

_TODO: complete at the end of Milestone 1. Planned outline:_

1. Prerequisites: Python ≥ 3.11, [Databricks CLI](https://docs.databricks.com/aws/en/dev-tools/cli/install), a Databricks Free Edition workspace.
2. Authenticate: `databricks auth login --host <workspace-url> --profile complaint-radar`
3. Local env: `uv venv && uv pip install -e ".[dev]"` then `cp .env.example .env`
4. Create UC objects (idempotent): `python setup/run_setup.py`. If catalog creation fails with
   "Metastore storage root URL does not exist", create `complaint_radar` in Catalog Explorer with
   *Use default storage* and re-run.
5. Load data and seeds: `python -m ingest.download_bulk && python -m ingest.upload_seeds`
6. Deploy and run: `databricks bundle deploy -t dev && databricks bundle run -t dev complaint_radar_pipeline`
7. Verify: `python -m ingest.reconcile` (exits non-zero if any check fails) writes the results to
   [docs/data_notes.md](docs/data_notes.md#reconciliation)

## Data quality

_TODO: reconciliation numbers (source vs bronze vs silver, rows dropped by expectations)._

## Repository layout

See [CLAUDE.md](CLAUDE.md#folder-layout-spec-6-repo-root--this-folder).

## Data source

[CFPB Consumer Complaint Database](https://www.consumerfinance.gov/data-research/consumer-complaints/).
Public data; narratives are published only with consumer consent and are scrubbed of PII by the CFPB.
