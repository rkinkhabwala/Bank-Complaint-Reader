-- =============================================================================================
-- Silver: one row per complaint_id, typed, with data-quality expectations; upserted with AUTO CDC.
--
-- Syntax verified 2026-09-29 against:
--   AUTO CDC INTO:          https://docs.databricks.com/aws/en/ldp/developer/ldp-sql-ref-apply-changes-into
--   AUTO CDC concepts:      https://docs.databricks.com/aws/en/ldp/cdc
--   CREATE TEMPORARY VIEW:  https://docs.databricks.com/aws/en/ldp/developer/ldp-sql-ref-create-temporary-view
--   Expectations:           https://docs.databricks.com/aws/en/ldp/expectations
--
-- Shape: bronze.complaints_raw -> complaints_typed (temporary view: typing + expectations)
--        -> AUTO CDC (SCD1, key complaint_id) -> silver.complaints
--
-- Why expectations sit on a temporary view, not the CDC target: the view isn't persisted (no
-- second 18M-row write), and DROP ROW takes effect before the merge, so a dropped row can never
-- overwrite a good version of the same complaint. Per-expectation pass/fail counts are reported
-- for the view in the event log (flow_progress.data_quality).
-- TODO(verify): the temporary-view page doesn't show a streaming query inside the view; the
-- AUTO CDC page lists STREAM(view) as a valid source. If the first run rejects this, change the
-- view to CREATE PRIVATE STREAMING TABLE complaints_typed (same constraints).
--
-- NULL semantics: the docs don't say how an expectation that evaluates to NULL is counted, so
-- every constraint below is written to be TRUE/FALSE, never NULL.
-- =============================================================================================

CREATE TEMPORARY VIEW complaints_typed (
  -- spec §4.2: drop rows without an ID (they can't be upserted).
  CONSTRAINT valid_complaint_id  EXPECT (complaint_id IS NOT NULL) ON VIOLATION DROP ROW,
  -- spec §4.2: drop future-dated rows. A NULL (unparseable) date is deliberately also dropped: a
  -- complaint without a date can't be placed in any time series.
  CONSTRAINT valid_date_received EXPECT (date_received IS NOT NULL AND date_received <= current_date())
    ON VIOLATION DROP ROW,
  -- spec §4.2 warnings (row kept, violation counted).
  CONSTRAINT product_present     EXPECT (product IS NOT NULL),
  -- spec §4.2 "state in valid codes" split in two so the event log separates missing from wrong.
  -- Valid = USPS Pub 28 Appendix B (62 codes) + 'UM'; must equal ingest/reference.py (tested).
  CONSTRAINT state_present       EXPECT (state IS NOT NULL),
  CONSTRAINT state_valid_code    EXPECT (state IS NULL OR state IN (
      'AA', 'AE', 'AK', 'AL', 'AP', 'AR', 'AS', 'AZ', 'CA', 'CO', 'CT', 'DC', 'DE', 'FL', 'FM', 'GA',
      'GU', 'HI', 'IA', 'ID', 'IL', 'IN', 'KS', 'KY', 'LA', 'MA', 'MD', 'ME', 'MH', 'MI', 'MN', 'MO',
      'MP', 'MS', 'MT', 'NC', 'ND', 'NE', 'NH', 'NJ', 'NM', 'NV', 'NY', 'OH', 'OK', 'OR', 'PA', 'PR',
      'PW', 'RI', 'SC', 'SD', 'TN', 'TX', 'UM', 'UT', 'VA', 'VI', 'VT', 'WA', 'WI', 'WV', 'WY')),
  -- Not in spec: makes rows with values that didn't fit the bronze schema visible in DQ metrics.
  CONSTRAINT no_rescued_data     EXPECT (_rescued_data IS NULL)
)
COMMENT 'Typed complaints with expectations; source of the AUTO CDC flow into silver.complaints.'
AS SELECT
  -- try_cast works whether bronze delivered a hinted type or a string (the schemaHints fallback).
  try_cast(complaint_id AS BIGINT)          AS complaint_id,
  try_cast(date_received AS DATE)           AS date_received,
  try_cast(date_sent_to_company AS DATE)    AS date_sent_to_company,
  product,
  sub_product,
  issue,
  sub_issue,
  company,
  company_public_response,
  company_response_to_consumer,
  -- CFPB writes one full name instead of a code (1,516 rows in snapshot 2026-09-29); map it to the
  -- ISO 3166-2 code. Must mirror STATE_NAME_TO_CODE in ingest/reference.py (tested).
  CASE upper(trim(state))
    WHEN 'UNITED STATES MINOR OUTLYING ISLANDS' THEN 'UM'
    ELSE nullif(upper(trim(state)), '')
  END                                       AS state,
  zip_code,
  submitted_via,
  CASE upper(trim(timely_response)) WHEN 'YES' THEN true WHEN 'NO' THEN false END AS timely_response,
  tags,
  -- Tags values seen: 'Servicemember', 'Older American', 'Older American, Servicemember'.
  coalesce(tags LIKE '%Servicemember%', false)  AS is_servicemember,
  coalesce(tags LIKE '%Older American%', false) AS is_older_american,
  -- Lineage
  _rescued_data,
  _source_kind,
  _source_file,
  _source_file_mtime,
  -- Data version of the source: bulk snapshot date from the path, else the file's mtime date.
  -- Never NULL (AUTO CDC doesn't support NULL sequencing values).
  coalesce(nullif(_snapshot_id, ''), date_format(_source_file_mtime, 'yyyy-MM-dd')) AS _source_version,
  _ingested_at
FROM STREAM(bronze.complaints_raw);

CREATE OR REFRESH STREAMING TABLE silver.complaints
  COMMENT 'One row per CFPB complaint_id, latest version wins (AUTO CDC, SCD type 1). Typed and snake_cased. State normalized to USPS codes + UM. DQ metrics: see complaints_typed in the pipeline event log.'
  TBLPROPERTIES ('quality' = 'silver');

-- Sequencing: the newest *data version* wins (bulk snapshot date / delta pull date), then the newest
-- file, then the path as a deterministic tie-breaker. Re-uploading an old snapshot therefore can't
-- overwrite newer data, and re-processing the same files is a no-op: same key and same sequence.
-- Known limitation: complaints CFPB removes from a later bulk snapshot are NOT deleted here
-- (appends can't express absence); handled with the daily-delta design in Milestone 2.
CREATE FLOW silver_complaints_cdc AS AUTO CDC INTO silver.complaints
FROM STREAM(complaints_typed)
KEYS (complaint_id)
SEQUENCE BY STRUCT(_source_version, _source_file_mtime, _source_file)
STORED AS SCD TYPE 1;


-- =============================================================================================
-- Taxonomy v1: silver.taxonomy_map
--
-- Syntax verified 2026-09-29 against:
--   CREATE MATERIALIZED VIEW (constraints, read_files):
--     https://docs.databricks.com/aws/en/ldp/developer/ldp-sql-ref-create-materialized-view
--   CREATE TEMPORARY VIEW: https://docs.databricks.com/aws/en/ldp/developer/ldp-sql-ref-create-temporary-view
--
-- Seeds (source of truth) live in pipelines/seeds/ and are uploaded to ${landing_path}/seeds/ by
-- `python -m ingest.upload_seeds`. Review record: docs/taxonomy/v1/README.md.
-- Lookup precedence must match ingest/taxonomy.py; tests/test_taxonomy.py runs this SELECT in
-- DuckDB and compares it with the Python resolver on every profiled combination.
-- =============================================================================================

CREATE TEMPORARY VIEW taxonomy_product_seed
COMMENT 'pipelines/seeds/taxonomy_product_map.csv'
AS SELECT * FROM read_files(
  '${landing_path}/seeds/taxonomy_product_map.csv',
  format => 'csv', header => true, quote => '"', escape => '"',
  schema => 'source_product STRING, source_sub_product STRING, canonical_product STRING, canonical_product_code STRING'
);

CREATE TEMPORARY VIEW taxonomy_issue_seed
COMMENT 'pipelines/seeds/taxonomy_issue_map.csv'
AS SELECT * FROM read_files(
  '${landing_path}/seeds/taxonomy_issue_map.csv',
  format => 'csv', header => true, quote => '"', escape => '"',
  schema => 'canonical_product STRING, source_issue STRING, canonical_issue STRING, issue_group STRING, mapping STRING'
);

-- One row per observed (product, sub_product, issue). Gold joins silver.complaints to it with
-- null-safe equality (<=>) on those three columns. is_mapped = false means a new CFPB label that
-- the seeds don't cover yet: counted by the expectation below, never silently dropped.
CREATE OR REFRESH MATERIALIZED VIEW silver.taxonomy_map (
  CONSTRAINT all_combinations_mapped EXPECT (is_mapped)
)
COMMENT 'Observed CFPB (product, sub_product, issue) combinations resolved to taxonomy v1: canonical_product (11 current CFPB products), canonical_issue (current CFPB label), issue_group (12 themes + other).'
TBLPROPERTIES ('quality' = 'silver')
AS WITH combos AS (
  SELECT product, sub_product, issue,
         count(*)           AS complaint_count,
         min(date_received) AS first_seen,
         max(date_received) AS last_seen
  FROM silver.complaints
  GROUP BY product, sub_product, issue
),
with_product AS (
  SELECT c.*,
         -- exact sub-product override first, then the product's '*' default
         coalesce(o.canonical_product, d.canonical_product)           AS canonical_product,
         coalesce(o.canonical_product_code, d.canonical_product_code) AS canonical_product_code
  FROM combos c
  LEFT JOIN taxonomy_product_seed o
    ON o.source_product = c.product AND o.source_sub_product = c.sub_product
  LEFT JOIN taxonomy_product_seed d
    ON d.source_product = c.product AND d.source_sub_product = '*'
)
SELECT p.product, p.sub_product, p.issue,
       p.canonical_product, p.canonical_product_code,
       i.canonical_issue,
       coalesce(i.issue_group, 'other') AS issue_group,
       i.mapping                        AS issue_mapping,
       -- A NULL issue is mapped by definition (issue_group 'other'); a non-NULL one needs a seed row.
       coalesce(p.canonical_product IS NOT NULL
                AND (p.issue IS NULL OR i.canonical_issue IS NOT NULL), false) AS is_mapped,
       p.complaint_count, p.first_seen, p.last_seen
FROM with_product p
LEFT JOIN taxonomy_issue_seed i
  ON i.canonical_product = p.canonical_product AND i.source_issue = p.issue;
