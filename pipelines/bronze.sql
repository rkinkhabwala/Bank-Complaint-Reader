-- =============================================================================================
-- Bronze: complaints exactly as delivered, one row per source record, never deduplicated.
--
-- Syntax verified 2026-09-29 against:
--   Load data in pipelines:  https://docs.databricks.com/aws/en/ldp/load
--   read_files (options, _metadata, globs):
--                            https://docs.databricks.com/aws/en/sql/language-manual/functions/read_files
--   Auto Loader schema / rescued data:
--                            https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/schema
--   Auto Loader CSV option defaults (escape = '\', multiLine = false):
--                            https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/options
--   Append flows (CREATE FLOW ... INSERT INTO ... BY NAME):
--                            https://docs.databricks.com/aws/en/ldp/flow-examples
--   ${key} substitution from pipeline `configuration`:
--                            https://docs.databricks.com/aws/en/ldp/parameters
--
-- Design:
-- * One target table fed by one append flow per source. The bulk CSV flow is below; the daily
--   API JSON flow (spec §4.1) gets its own CREATE FLOW in Milestone 2 without a full refresh.
-- * Columns are renamed to snake_case here (a lossless rename; values stay untouched strings or
--   hinted types). Source headers contain spaces and '?', and the API delta JSON already uses
--   snake_case, so both flows land on the same names. The mapping is tested against
--   ingest/schema.py in tests/test_pipeline_sql.py.
-- * schemaEvolutionMode 'rescue': unknown or renamed source columns go into _rescued_data and the
--   stream never fails on a schema change (in dev mode a failed update isn't retried).
-- * Type hints on dates and ID (spec §4.1): a value that doesn't parse becomes NULL, and the raw
--   text is kept in _rescued_data, so bronze row count still equals source row count.
-- =============================================================================================

CREATE OR REFRESH STREAMING TABLE bronze.complaints_raw
  COMMENT 'CFPB complaints as delivered (bulk CSV parts; daily API deltas from M2). One row per source record, not deduplicated. Snake_case names; _rescued_data holds values that did not fit the schema.'
  TBLPROPERTIES ('quality' = 'bronze');

CREATE FLOW bronze_bulk_csv
AS INSERT INTO bronze.complaints_raw BY NAME
SELECT
  `Date received`                AS date_received,
  `Product`                      AS product,
  `Sub-product`                  AS sub_product,
  `Issue`                        AS issue,
  `Sub-issue`                    AS sub_issue,
  `Company public response`      AS company_public_response,
  `Company`                      AS company,
  `State`                        AS state,
  `ZIP code`                     AS zip_code,
  `Tags`                         AS tags,
  `Submitted via`                AS submitted_via,
  `Date sent to company`         AS date_sent_to_company,
  `Company response to consumer` AS company_response_to_consumer,
  `Timely response?`             AS timely_response,
  `Complaint ID`                 AS complaint_id,
  _rescued_data,
  -- Lineage. _source_file_mtime is the AUTO CDC sequencing key in silver: a later file wins.
  _metadata.file_path                                             AS _source_file,
  _metadata.file_modification_time                                AS _source_file_mtime,
  'bulk_csv'                                                      AS _source_kind,
  regexp_extract(_metadata.file_path, '/bulk/([0-9]{4}-[0-9]{2}-[0-9]{2})/', 1) AS _snapshot_id,
  current_timestamp()                                             AS _ingested_at
FROM STREAM read_files(
  -- Glob limits the read to part files, so a stray file in the Volume isn't ingested.
  '${landing_path}/bulk/*/complaints_part_*.csv',
  format              => 'csv',
  header              => true,
  -- The 2026-09-29 file has no embedded newlines, but multiLine keeps a future multi-line field
  -- as one record. It only prevents splitting within a file, and we already have ~37 parts.
  multiLine           => true,
  quote               => '"',
  escape              => '"',   -- RFC 4180 doubled quotes; Spark's default escape is '\'
  mode                => 'PERMISSIVE',
  inferColumnTypes    => false,  -- everything is a string unless hinted below
  -- Backtick-quoted names with spaces work in schemaHints: verified on the first run
  -- (2026-09-30; bronze columns typed DATE/DATE/BIGINT, 0 rows in _rescued_data).
  schemaHints         => '`Date received` DATE, `Date sent to company` DATE, `Complaint ID` BIGINT',
  dateFormat          => 'yyyy-MM-dd',
  schemaEvolutionMode => 'rescue',
  rescuedDataColumn   => '_rescued_data'
);
