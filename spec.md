# Complaint Radar — Specification

> An early-warning system for consumer-finance complaints, built on the Databricks lakehouse.
> Status: Draft v1 · Owner: RK · Target platform: Databricks Free Edition (serverless)

---

## 1. Problem

The CFPB publishes millions of consumer complaints against banks, card issuers, lenders, servicers and fintechs, many with the consumer's own free-text narrative, and adds new ones daily. Today:

- **Bank compliance / complaint-management teams** triage these largely by hand, and usually only for their own institution. They spot emerging problems (a new fee, a broken dispute flow, a servicing change) weeks late.
- **Peer benchmarking is hard.** "Are we worse than similar banks on overdraft complaints this quarter?" requires normalizing volumes and taxonomies across companies.
- **The richest signal is unstructured.** Root cause lives in the narrative, not in the CFPB's `Product` / `Issue` dropdowns.

**Complaint Radar** turns the raw feed into (a) clean, governed analytics tables, (b) automatic spike alerts by company × product × issue × geography, and (c) a grounded assistant that explains *why* a spike is happening, with cited complaint IDs.

### Primary user story
> *As a complaint-management analyst at a mid-size bank, I open Complaint Radar on Monday and see that "fees charged on closed accounts" complaints against us rose 3× week-over-week in Florida, well above our peer group. I ask the assistant what's driving it, and it summarizes the top three root causes with example complaint IDs I can escalate.*

### Secondary users
- Product / risk leaders: peer benchmarking and trend dashboards.
- Fintech founders / researchers: explore the complaint landscape by product.

### Non-goals
- No private or customer-internal complaint data. Public CFPB data only.
- Not a regulatory filing tool; the output is decision support.
- No real-time streaming (the source publishes daily); incremental batch is enough.

---

## 2. Data sources

| Source | Content | Access |
|---|---|---|
| CFPB Consumer Complaint Database (bulk) | Full history, one row per complaint | CSV/JSON zip download (`files.consumerfinance.gov/ccdb/complaints.csv.zip` — verify current URL on the CFPB data page) |
| CFPB complaint search API | Incremental pulls by `date_received` | JSON API (see `cfpb.github.io/ccdb5-api/documentation/`) |
| Census ZCTA / state population (optional) | Denominators for per-capita rates | Static CSV |
| FDIC institution directory (optional) | Bank asset size for peer groups | Static CSV |

**Key CFPB fields:** Date received, Product, Sub-product, Issue, Sub-issue, Consumer complaint narrative, Company public response, Company, State, ZIP code, Tags, Consumer consent provided?, Submitted via, Date sent to company, Company response to consumer, Timely response?, Consumer disputed?, Complaint ID.

**Data realities to design for (this is the "forward-deployed" part):**
- Narratives exist only where the consumer consented, and are already scrubbed (PII replaced with `XXXX`). Many rows have no narrative.
- Complaints are published after the company responds or after 15 days, so **recent weeks are under-counted**. Spike detection must account for reporting lag.
- The product/issue taxonomy **changed over time** (e.g. product names consolidated). A mapping table is needed for consistent trends.
- Company names are not perfectly consistent; normalization is required for peer comparisons.
- The ZIP code field is partially masked for privacy.
- Free Edition has restricted outbound internet, so **the bulk file is downloaded locally and uploaded to a Unity Catalog Volume**. Incremental API pulls run from a local script or a GitHub Action that drops files into the Volume.

---

## 3. Architecture

```
          ┌──────────────── local / GitHub Action ────────────────┐
          │  download bulk zip  +  daily API delta → JSON files    │
          └───────────────────────────┬───────────────────────────┘
                                      ▼
  Unity Catalog:  complaint_radar.raw  (Volume: /landing/...)
                                      │  Auto Loader
                                      ▼
  ┌─────────────── Lakeflow pipeline (declarative) ────────────────┐
  │ bronze.complaints_raw      streaming table, schema evolution   │
  │ silver.complaints          typed, deduped (AUTO CDC on          │
  │                            complaint_id), expectations          │
  │ silver.taxonomy_map        historical product/issue mapping     │
  │ silver.company_dim         normalized company + peer group      │
  │ gold.weekly_metrics        company×product×issue×state×week     │
  │ gold.peer_benchmarks       rates vs peer group                  │
  └────────────────────────────────────────────────────────────────┘
                                      │
       ┌──────────────────────────────┼─────────────────────────────┐
       ▼                              ▼                             ▼
  gold.spike_alerts           gold.complaint_enriched        AI/BI dashboard
  (anomaly job)               (ai_query: root cause,         + Genie space
                              severity, fee amount, etc.)
                                      │
                                      ▼
                          AI Search index (narratives)
                                      │
                                      ▼
            Agent (Python, MLflow-traced): SQL tool + retrieval tool
                                      │
                                      ▼
                   Databricks App (alerts feed · drill-down · chat)
```

**Unity Catalog layout:** catalog `complaint_radar`; schemas `raw`, `bronze`, `silver`, `gold`, `ml`. Everything is governed and has lineage in UC.

**Deployment:** Databricks Asset Bundle (`databricks.yml`) defines the pipeline, jobs, app and dashboards so the whole project deploys with one `databricks bundle deploy`.

---

## 4. Phase 1 — Data platform & spike detection

**Goal:** a reliable, incremental lakehouse over the full complaint history, plus statistically sound spike alerts and a dashboard. Demo-able on its own.

### 4.1 Ingestion
- Auto Loader (`cloudFiles`) over the landing Volume; supports both the bulk CSV and daily JSON deltas.
- Rescued-data column kept for malformed rows; schema hints for dates and IDs.

### 4.2 Bronze → Silver
- Snake-case column names; parse dates; cast booleans (`timely_response`, `consumer_disputed`).
- **Dedup / upsert on `complaint_id`** using the pipeline's AUTO CDC flow (later deltas can revise earlier complaints, e.g. company response updates).
- **Expectations** (data quality):
  - `complaint_id IS NOT NULL` → drop
  - `date_received <= current_date()` → drop
  - `state` in valid US state/territory codes → warn
  - `product IS NOT NULL` → warn
- `silver.taxonomy_map`: map historical product/sub-product/issue labels to a current canonical taxonomy (hand-curated seed CSV + rules).
- `silver.company_dim`: normalized company name, optional FDIC asset-size tier → **peer group** (e.g. "Regional bank $10–100B").

### 4.3 Gold
- `gold.weekly_metrics`: counts, % with narrative, % timely response, % "closed with monetary relief", by company × canonical_product × canonical_issue × state × ISO week.
- `gold.peer_benchmarks`: each company's share of complaints vs its peer-group median for the same product/issue.
- Materialized views, refreshed incrementally.

### 4.4 Spike detection (`gold.spike_alerts`)
- For each series (company × product × issue, optionally × state), compare the current week with a trailing baseline (e.g. 12 weeks):
  - robust z-score using median and MAD, **and**
  - a minimum-volume floor (e.g. ≥ 10 complaints) to avoid alerting on noise.
- **Lag correction:** exclude or down-weight the most recent 2 weeks, or scale them with an empirically estimated publication-lag curve (computed from `date_received` vs first-seen date in bronze).
- Output: `alert_id, series keys, week, observed, expected, z_score, severity_band, first_detected_at`.
- Scheduled Job: runs after the pipeline refresh each day.

### 4.5 Dashboard
- AI/BI dashboard: national trend, top movers this week, company vs peer group, geographic map, alert table.
- Genie space over the gold tables with curated instructions and example questions.

### Phase 1 acceptance criteria
- [ ] Full history loaded; bronze row count reconciles with the source file (± rows rejected by expectations, which are reported).
- [ ] Re-running the pipeline on the same files is idempotent (no duplicate `complaint_id`s in silver).
- [ ] A daily delta file updates silver/gold incrementally without a full recompute.
- [ ] Expectation metrics are visible in the pipeline event log.
- [ ] Spike alerts are reproducible, and a backtest on at least 2 known historical events shows the detector would have flagged them (document which events you chose and why).
- [ ] Dashboard and Genie space answer the 5 example questions in §7.

---

## 5. Phase 2 — AI enrichment, assistant & app

**Goal:** explain *why* spikes happen, using the narratives, through a grounded, evaluated assistant in a usable app.

### 5.1 LLM enrichment (`gold.complaint_enriched`)
- Use `ai_query` (SQL) against a Foundation Model API endpoint available in the workspace, with a **structured JSON output** schema:
  - `root_cause` (from a fixed list of ~25 labels + `other`)
  - `severity` (1–5, with a rubric in the prompt)
  - `fee_mentioned` (bool) and `fee_amount_usd` (nullable)
  - `vulnerable_consumer_signal` (e.g. mentions of servicemember, older adult, disability; bool plus evidence span)
  - `one_line_summary`
- **Scope for Free Edition quotas:** enrich only complaints *with narratives* for (a) the last 12–18 months and (b) any series that has an active alert. Process incrementally; never re-enrich an unchanged complaint (keyed on `complaint_id` + prompt version).
- Store `prompt_version` and `model_name` on every row for reproducibility.
- **Label quality check:** hand-label ~200 complaints; report per-label precision/recall for `root_cause` and agreement for `severity`.

### 5.2 Retrieval
- AI Search (vector search) index on `gold.complaint_enriched` (Delta Sync), embedding `narrative`, with metadata filters for company, canonical_product, canonical_issue, state, week and root_cause.
- One endpoint / one search unit (Free Edition limit), so keep the index to the enriched subset.

### 5.3 Agent
- Python agent (code-defined, logged with MLflow; Agent Bricks is not available on Free Edition).
- Tools:
  1. `query_metrics(sql_intent)`: parameterized, read-only SQL over gold tables (allow-listed tables and columns; no free-form DDL/DML).
  2. `search_complaints(query, filters)`: AI Search retrieval, returns complaint IDs, snippets and metadata.
  3. `get_alert(alert_id)`: fetches alert context.
- Answer contract: every factual claim cites either a metric query or complaint IDs; the agent says "insufficient data" when retrieval is empty or volume is below the floor.
- Guardrails: refuse requests to identify individual consumers; never echo `XXXX`-redacted content as if it were data; stay on topic.

### 5.4 Evaluation (MLflow)
- Eval set of ~60 questions across 4 types: metric lookup, spike explanation, peer comparison, out-of-scope/adversarial.
- Metrics: correctness of numbers (vs a SQL ground truth), groundedness / citation validity (every cited ID exists and supports the claim), correct tool routing, refusal correctness, latency p50/p95.
- MLflow tracing on every run; eval results logged per agent version; regressions block promotion.

### 5.5 Databricks App
- Streamlit or Gradio app:
  - **Alerts feed:** sorted by severity, with a sparkline of observed vs expected.
  - **Drill-down:** click an alert → root-cause breakdown, peer comparison, top example narratives.
  - **Chat panel:** the agent, pre-scoped to the selected alert.
- Uses the app's service principal with read-only UC grants on `gold` and `ml`.
- Note: Free Edition apps stop 24 h after start/redeploy, so restart before demos.

### Phase 2 acceptance criteria
- [ ] Enrichment is incremental and versioned; re-running produces no duplicate LLM calls.
- [ ] Root-cause labels hit an agreed quality bar on the hand-labeled set (target macro-F1 ≥ 0.75; report actuals honestly).
- [ ] Agent eval: ≥ 90% numeric correctness, ≥ 95% valid citations, 100% refusal on the adversarial set.
- [ ] App demo: from alert → explanation → cited examples in under 60 seconds.

---

## 6. Repository layout

```
complaint-radar/
├── databricks.yml                 # Asset Bundle: pipeline, jobs, app, dashboard
├── README.md                      # problem, architecture diagram, demo GIF, results
├── spec.md
├── ingest/
│   ├── download_bulk.py           # local: fetch + unzip + upload to Volume
│   └── pull_daily_delta.py        # local / GitHub Action: API delta → Volume
├── pipelines/
│   ├── bronze.sql
│   ├── silver.sql                 # AUTO CDC, expectations
│   ├── gold.sql
│   └── seeds/taxonomy_map.csv
├── jobs/
│   ├── spike_detection.py
│   └── enrich_complaints.sql      # ai_query with structured output
├── agent/
│   ├── agent.py                   # tools + orchestration
│   ├── tools/
│   ├── prompts/
│   └── evals/
│       ├── eval_set.jsonl
│       └── run_evals.py
├── app/
│   ├── app.py
│   └── app.yaml
├── dashboards/complaint_radar.lvdash.json
└── tests/                         # pytest: SQL tool allow-list, spike math, parsers
```

---

## 7. Example questions the finished system must answer

1. Which five companies had the largest week-over-week rise in credit-card complaints last month?
2. How do complaints about checking-account fees at regional banks compare with national banks this quarter?
3. Why did debt-collection complaints spike in Florida in week *N*? Show examples.
4. What share of mortgage-servicing complaints mention a fee, and what's the median fee amount?
5. Which open alerts involve signals of vulnerable consumers?

---

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Free Edition compute/LLM quotas exhausted | Scope enrichment to narratives + recent + alerted series; cache by `complaint_id` + prompt version |
| Reporting lag creates false "drops" and misses | Lag-curve correction; exclude most recent 2 weeks from alerting by default |
| Taxonomy changes break trends | Canonical taxonomy map with tests; trend charts use canonical labels only |
| LLM labels drift or hallucinate | Fixed label set, structured output, hand-labeled benchmark, prompt versioning |
| Agent writes unsafe SQL | Parameterized, allow-listed query tool; read-only grants |
| Model availability differs on Free Edition | Keep model name in config; fall back to any available chat model endpoint |

---

## 9. Milestones

| Week | Deliverable |
|---|---|
| 1 | Bulk load → bronze/silver with expectations; taxonomy map v1 |
| 2 | Gold tables, peer groups, dashboard + Genie space |
| 3 | Spike detection + backtest write-up → **Phase 1 done** (resume-ready) |
| 4 | LLM enrichment + hand-labeled benchmark |
| 5 | AI Search index + agent + eval harness |
| 6 | Databricks App, README with architecture diagram, demo GIF and eval results → **Phase 2 done** |

---

## 10. Resume bullets (fill in real numbers as you go)

- Built a Databricks lakehouse over *N*M CFPB consumer complaints (Auto Loader, Lakeflow declarative pipelines, AUTO CDC, Unity Catalog) with data-quality expectations and incremental daily refresh.
- Designed a lag-corrected spike detector that flags emerging complaint trends by company, product and state; backtested against *K* historical events.
- Shipped a grounded, MLflow-evaluated agent (SQL + AI Search tools) and Databricks App that explains complaint spikes with cited evidence: *X*% numeric accuracy, *Y*% citation validity.