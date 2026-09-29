-- Complaint Radar: one-time Unity Catalog setup. Idempotent (IF NOT EXISTS everywhere).
-- Run with `python setup/run_setup.py`, or paste into the SQL editor.
--
-- Syntax verified 2026-09-29:
--   CREATE CATALOG: https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-catalog
--   Default storage: https://docs.databricks.com/aws/en/storage/default-storage
--   CREATE VOLUME: https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-volume
--
-- Free Edition has no metastore root storage. With no MANAGED LOCATION, the catalog goes on
-- Default Storage (docs above). If this fails with "Metastore storage root URL does not exist",
-- create the catalog in Catalog Explorer -> Create catalog -> "Use default storage" and re-run
-- the script; the remaining statements are IF NOT EXISTS.
-- (Known Free Edition issue: https://github.com/databricks/cli/issues/4513)

CREATE CATALOG IF NOT EXISTS complaint_radar
  COMMENT 'Complaint Radar: CFPB consumer complaint lakehouse (see spec.md)';

CREATE SCHEMA IF NOT EXISTS complaint_radar.raw
  COMMENT 'Landing zone: files exactly as downloaded (bulk CSV parts, daily API JSON, seeds)';

CREATE SCHEMA IF NOT EXISTS complaint_radar.bronze
  COMMENT 'Auto Loader streaming tables: source columns as strings, plus rescued data and file metadata';

CREATE SCHEMA IF NOT EXISTS complaint_radar.silver
  COMMENT 'Typed, deduplicated (AUTO CDC on complaint_id), quality-checked complaints and dimensions';

CREATE SCHEMA IF NOT EXISTS complaint_radar.gold
  COMMENT 'Aggregates, peer benchmarks, spike alerts and LLM enrichment';

CREATE SCHEMA IF NOT EXISTS complaint_radar.ml
  COMMENT 'Agent models, eval results and AI Search artifacts';

-- Managed Volume: files live in UC-governed storage. Subfolders (bulk/, delta/, seeds/) are
-- created by run_setup.py via the Files API because SQL has no mkdir.
CREATE VOLUME IF NOT EXISTS complaint_radar.raw.landing
  COMMENT 'Landing files: bulk/<snapshot_date>/*.csv, delta/*.json, seeds/*.csv';
