# Company normalization v1

**Status:** approved 2026-09-30 (Milestone 2, step 1), including the N.A. / National Association rule. Code: `ingest/company.py`; tests: `tests/test_company.py`. Numbers below: snapshot 2026-09-29.

## Finding

The CFPB `Company` field already has one entry per company: 8,133 distinct names, and only 13 groups differ by spelling alone. Most of the remaining variation is corporate history (renames, acquisitions), which is an entity-resolution decision, not spelling cleanup.

## Rules

| Key | Rule | Used for |
|---|---|---|
| `company_key` | trailing *National Association* → *NA*; then letters and digits only, upper-cased | **identity**: groups complaints in `silver.company_dim` and gold |
| `match_key` | punctuation to spaces, `&` to AND, trailing legal forms (INC, LLC, CORP, N.A., NATIONAL ASSOCIATION…) stripped | **candidate FDIC matches only** (step 3); never merges CFPB companies |

### Merged by `company_key` (13 groups, 24,999 rows)

| Key | CFPB names (rows; first..last seen) |
|---|---|
| `ONEMAINFINANCECORPORATION` | `OneMain Finance Corporation` (12,514; 2020-05-16..2026-09-25)<br>`ONEMAIN FINANCE CORPORATION` (300; 2019-09-05..2022-07-20) |
| `FLAGSTARBANKNA` | `Flagstar Bank, N.A.` (4,208; 2011-12-01..2022-12-15)<br>`Flagstar Bank, National Association` (499; 2011-12-08..2019-11-27) |
| `SETERUSINC` | `Seterus, Inc.` (4,486; 2012-01-08..2021-03-16)<br>`SETERUS INC` (1; 2017-05-18..2017-05-18) |
| `FIRSTTECHNOLOGYFEDERALCREDITUNION` | `FIRST TECHNOLOGY FEDERAL CREDIT UNION` (992; 2023-12-14..2026-09-13)<br>`First Technology Federal Credit Union` (850; 2018-08-08..2026-01-21) |
| `RIVERWALKFINANCIALCORPORATION` | `Riverwalk Financial Corporation` (617; 2016-02-22..2026-07-28)<br>`Riverwalk Financial CORPORATION` (20; 2016-06-06..2021-07-23) |
| `GLOBALCREDITUNION` | `GLOBAL CREDIT UNION` (251; 2023-03-10..2026-09-14)<br>`Global Credit Union` (41; 2022-04-21..2023-03-13) |
| `RAGANRAGANPC` | `Ragan & Ragan, PC` (70; 2014-03-07..2026-08-25)<br>`Ragan & Ragan, P.C.` (14; 2024-03-20..2026-03-03) |
| `ELITEFINANCIALSERVICESINC` | `Elite Financial Services, Inc.` (46; 2014-10-30..2025-10-03)<br>`ELITE FINANCIAL SERVICES INC` (1; 2023-05-22..2023-05-22) |
| `SYNERGYONELENDINGINC` | `SYNERGY ONE LENDING, INC.` (21; 2017-05-10..2020-12-03)<br>`Synergy One Lending, Inc.` (9; 2021-02-16..2026-07-10) |
| `CASHMAXLLC` | `CashMax LLC` (22; 2020-06-12..2026-06-04)<br>`Cash Max LLC` (2; 2020-01-13..2025-12-12) |
| `VIPMORTGAGEINC` | `V.I.P. MORTGAGE, INC.` (15; 2017-01-04..2026-07-01)<br>`VIP Mortgage Inc.` (7; 2016-04-10..2026-06-26) |
| `SUMMITMORTGAGECORPORATION` | `SUMMIT MORTGAGE CORPORATION` (5; 2013-09-14..2025-07-06)<br>`Summit Mortgage Corporation` (3; 2013-02-21..2024-07-29) |
| `ATMOPSINC` | `ATM OPS INC` (4; 2024-07-01..2026-09-15)<br>`ATM OPS Inc` (1; 2023-03-18..2023-03-18) |

### Would merge if legal forms were stripped, NOT merged (37 groups, 8,368 rows, 0.05% of complaints)

Several are probably different legal entities that CFPB keeps apart deliberately (for example `USCB, Inc.` and `USCB Corporation` both file continuously 2013–2026; `FIRST MORTGAGE CORPORATION` / `FIRST MORTGAGE COMPANY` are generic names). The largest 10:

| match_key | CFPB names (rows; first..last seen) |
|---|---|
| `CREDIT CONTROL` | `Credit Control, LLC` (4,128; 2020-01-07..2026-09-25)<br>`Credit Control Company, Inc.` (19; 2018-02-15..2022-06-29) |
| `USCB` | `USCB, Inc.` (724; 2013-07-19..2026-08-26)<br>`USCB Corporation` (553; 2014-02-24..2026-09-11) |
| `ADP` | `ADP Inc.` (930; 2021-05-02..2026-09-11)<br>`ADP, LLC` (34; 2018-05-02..2021-04-30) |
| `CREDIT SERVICE` | `Credit Service Company, INC` (235; 2013-12-12..2026-08-29)<br>`Credit Service Company` (108; 2013-07-20..2026-08-23)<br>`Credit Service, Inc` (34; 2014-08-15..2025-11-28) |
| `RECEIVABLE SOLUTIONS` | `Receivable Solutions, Inc.` (223; 2015-08-04..2023-02-04)<br>`Receivable Solutions LLC` (59; 2022-12-13..2026-08-28) |
| `MERCHANTS CREDIT` | `Merchants Credit Corporation` (155; 2013-10-01..2026-09-02)<br>`Merchants Credit LLC` (12; 2017-12-21..2026-09-03) |
| `FLAGSHIP FINANCIAL GROUP` | `FLAGSHIP FINANCIAL GROUP LLC` (106; 2026-02-03..2026-09-12)<br>`Flagship Financial Group` (24; 2012-08-07..2021-03-13) |
| `TIME INVESTMENT` | `Time Investment Corporation` (92; 2015-09-21..2026-09-18)<br>`Time Investment Company, Inc.` (30; 2018-03-12..2026-08-18) |
| `PHELAN HALLINAN DIAMOND AND JONES` | `Phelan Hallinan Diamond & Jones, PC` (105; 2013-12-13..2020-04-02)<br>`Phelan Hallinan Diamond & Jones, LLP` (5; 2013-12-26..2018-08-17)<br>`Phelan Hallinan Diamond & Jones, PLLC` (1; 2020-03-05..2020-03-05) |
| `TOWNE MORTGAGE` | `TOWNE MORTGAGE COMPANY` (101; 2013-11-25..2026-09-07)<br>`TOWNE MORTGAGE, LLC` (6; 2013-07-15..2019-05-14) |

### Corporate history: not handled in v1

Some names stop and others start around the same time, for example `NATIONSTAR MORTGAGE LLC` (last seen 2018-10-18) and `Mr. Cooper Group Inc.` (first seen 2017-04-05), or `Santander Consumer USA Holdings Inc.` (2012–2022) and `SANTANDER HOLDINGS USA, INC.` (2018–). Linking them needs outside facts (mergers, renames) and a rule for how history counts in peer benchmarks. v1 keeps them separate, as CFPB does; a curated `company_alias` seed can be added later.

