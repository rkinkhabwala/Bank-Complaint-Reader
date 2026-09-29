# Taxonomy v1

**Status:** approved 2026-09-29 as proposed. **Source of truth:** `pipelines/seeds/taxonomy_product_map.csv`
and `pipelines/seeds/taxonomy_issue_map.csv`. The CSVs in this folder are the review record: the same
rules plus evidence columns (`rows`, `first_seen`, `last_seen`) from snapshot 2026-09-29. Change the
seeds, not these files. Resolver: `ingest/taxonomy.py`; SQL: `silver.taxonomy_map` in
`pipelines/silver.sql`.

Input: `docs/profile/2026-09-29/taxonomy_combinations.csv` (every observed product / sub-product /
issue / sub-issue combination with counts and first/last `date_received`).

## Design

| Level | Key | Values | Purpose |
|---|---|---|---|
| `canonical_product` (+ `canonical_product_code`) | (source product, source sub-product), with a `*` default per product | the **11 products in use since the 2023-08-24/25 CFPB taxonomy** | consistent product trends across all eras |
| `canonical_issue` | (canonical product, source issue) | current CFPB issue label; legacy labels renamed only when the rename is confident | series key for spike detection and gold |
| `issue_group` | canonical issue | 12 cross-product themes + `other` | long-range trends and cross-product views where issue labels changed too much |

**"Current" label:** one seen on or after 2023-09-01 within that canonical product.
**Sub-issue:** not mapped in v1 (2,645 combinations; most spike analysis runs on recent, current-era data).

### Why the product map needs sub-product

Several legacy products were split between current products. Mapping by product alone would put, for
example, gift cards under Credit card. The 16 sub-product overrides in `product_map.csv`:

| Source (product / sub-product) | Canonical product | Why |
|---|---|---|
| Credit card or prepaid card / {Government benefit, General-purpose prepaid, Gift, Payroll, Student prepaid} card | Prepaid card | 2017–2023 merged product; current taxonomy separates them |
| Consumer Loan / Vehicle loan, Vehicle lease | Vehicle loan or lease | current home |
| Consumer Loan / (other) | Payday loan, title loan, personal loan, or advance loan | installment, personal line of credit, title, pawn |
| Credit reporting, credit repair… / Credit repair services | Debt or credit management | current home of credit repair |
| Other financial service / Debt settlement, Credit repair | Debt or credit management | current home |
| Other financial service / Refund anticipation check; Money transfer… / Refund anticipation check | Payday loan…advance loan | current sub-product "Tax refund anticipation loan or check" |
| Other financial service / (other: check cashing, money order, FX, traveler's checks) | Money transfer, virtual currency, or money service | current home |
| Money transfer… / Debt settlement (2017–2023) | Debt or credit management | current home |
| Prepaid card / Mobile wallet (2014–2017) | Money transfer… | current "Mobile or digital wallet" |
| Vehicle loan or lease / Title loan (46 rows) | Payday loan… | same rule as every other "Title loan" |
| Bank account or service / Cashing a check without an account | Money transfer… | current "Check cashing service" |

## Results on snapshot 2026-09-29

| Issue mapping outcome | Source pairs | Rows | % rows |
|---|---|---|---|
| `current`: already a current label | 140 | 16,737,831 | 92.67% |
| `renamed`: legacy → current label | 118 | 1,282,980 | 7.10% |
| `legacy_kept`: no confident current equivalent; label kept, still grouped | 35 | 41,491 | 0.23% |
| NULL issue (no mapping; `issue_group = 'other'` in SQL) | 2 | 6 | 0.00% |

Total: 18,062,302 mapped + 6 NULL-issue = 18,062,308 = all rows.

The 293 source pairs collapse to 175 (canonical product, canonical issue) labels.

## Judgment calls to review

1. **Lossy credit-card renames (2011–2017, 76,950 rows):** 29 old labels collapse into 10 current ones.
   For example APR / late fee / other fee / balance-transfer fee / over-limit fee / cash-advance fee
   all become *Fees or interest*, and customer service / privacy / arbitration / rewards / sale of
   account become *Other features, terms, or problems*.
2. **Mortgage legacy labels (2011–2017) are broad:** *Loan modification, collection, foreclosure*
   (112k) → *Struggling to pay mortgage*; *Loan servicing, payments, escrow account* (77k) →
   *Trouble during payment process*. Trend lines will still step at 2017-04.
3. **Kept as legacy** (largest): Debt collection *Improper contact or sharing of info* (10,036; could be
   "Communication tactics" or "Threatened to…share information"); Credit card *Identity theft / Fraud /
   Embezzlement* (8,480; no current credit-card fraud issue); Student loan *Repaying your loan*
   (3,820, 2012–2014).
4. **Misfiled low-volume labels** (for example *Getting a credit card* under Credit reporting, ≤12 rows)
   stay under the product the consumer chose; they aren't moved.
5. **Issue groups** are keyword rules applied to the canonical label; full assignment in `issue_map.csv`.

## Deviation from spec §6

The spec lists one seed, `pipelines/seeds/taxonomy_map.csv`. The proposal uses **two seeds**
(`taxonomy_product_map.csv`, `taxonomy_issue_map.csv`) because the two keys differ (the product
depends on sub-product; the issue depends on canonical product). `silver.taxonomy_map` still exists:
one row per observed (product, sub-product, issue), resolved through both seeds, with an `is_mapped`
flag so new CFPB labels show up as unmapped instead of silently disappearing.
