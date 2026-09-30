# Peer groups v1

**Status:** proposal, awaiting review (Milestone 2, step 3). Snapshot: CFPB 2026-09-29 complaints, FDIC 2026-06-30 institutions.
Code: `ingest/company_matching.py`; seeds: `pipelines/seeds/company_overrides.csv` (hand-curated), `pipelines/seeds/company_fdic_match.csv` (generated); tests: `tests/test_company_matching.py`.

## Rules (in order)

1. **Curated override** (13 rows, each with evidence in `company_overrides.csv`): the 3 credit bureaus; 7 banks whose FDIC link is clear from FDIC's own records but not from the name (e.g. `U.S. BANCORP` ↔ FDIC `U S BCORP`; `CITIZENS FINANCIAL GROUP`, where 3 FDIC holding companies share the name); 3 banks with no active FDIC record and no bank word in the name (Comerica, BB&T, BBVA: general knowledge, flagged in the notes).
2. **Automatic FDIC match** (106 companies): the name without legal form and articles (branch names: the part before ` - `) equals exactly one FDIC holding company, else exactly one bank. Several candidates → no match. An LLC/partnership-type name never matches a corporation (e.g. `Dakota Financial, LLC` ≠ FDIC `DAKOTA FINANCIAL INC`; `MORGAN STANLEY & CO. LLC`, the broker-dealer, ≠ holding company `MORGAN STANLEY`).
3. **Default by name** (no seed row; evaluated in SQL so new companies are classified too): `CREDIT UNION` → credit union; the word BANK/BANKS/BANCORP/BANCSHARES/BANKSHARES (not an LLC) → bank without FDIC assets; else non-bank.
4. **Peer group**: banks by holding-company assets (sum of member banks' FDIC `ASSET`, thousands of USD): >$250B, $100–250B, $10–100B, <$10B; non-banks by **main canonical product** (the product with most complaints).
5. **Not linked** (would need outside corporate knowledge): companies that own a small FDIC bank without an FDIC holding-company link, e.g. Block (Square Financial Services), Bread Financial (Comenity), SLM (Sallie Mae Bank), Toyota Motor Credit. Grouped as non-banks by main product.

## Distribution

| Peer group | Companies | Complaints | % | Largest (complaints) |
|---|---|---|---|---|
| Credit bureau | 3 | 14,153,361 | 78.4% | TRANSUNION INTERMEDIATE HOLDINGS, INC. (4,986,912); EQUIFAX, INC. (4,796,009); Experian Information Solutions Inc. (4,370,440) |
| Bank >$250B | 17 | 1,056,544 | 5.8% | BANK OF AMERICA, NATIONAL ASSOCIATION (187,449); JPMORGAN CHASE & CO. (176,500); WELLS FARGO & COMPANY (175,447); CAPITAL ONE FINANCIAL CORPORATION (172,911) |
| Bank $100-250B | 14 | 276,713 | 1.5% | SYNCHRONY FINANCIAL (84,179); AMERICAN EXPRESS COMPANY (60,396); ALLY FINANCIAL INC. (30,482); UNITED SERVICES AUTOMOBILE ASSOCIATION (19,805) |
| Bank $10-100B | 68 | 72,227 | 0.4% | BARCLAYS BANK DELAWARE (28,877); SOFI TECHNOLOGIES, INC. (7,233); Flagstar Bank, N.A. (4,707); FIRST NATIONAL BANK OF OMAHA (4,251) |
| Bank <$10B | 14 | 1,052 | 0.0% | GATEWAY FIRST BANK (300); FIRST AMERICAN FINANCIAL CORPORATION (257); Eagle Financial Services, Inc. (212); ALCAR INC. (91) |
| Bank (no active FDIC record) | 70 | 101,082 | 0.6% | DISCOVER BANK (44,503); SUNTRUST BANKS, INC. (9,634); Comerica (8,603); BB&T CORPORATION (5,522) |
| Credit union | 25 | 71,132 | 0.4% | NAVY FEDERAL CREDIT UNION (49,593); PENTAGON FEDERAL CREDIT UNION (6,480); STATE EMPLOYEES’ CREDIT UNION (2,101); FIRST TECHNOLOGY FEDERAL CREDIT UNION (1,842) |
| Non-bank: Debt collection | 3,607 | 1,016,694 | 5.6% | Resurgent Capital Services L.P. (72,462); ENCORE CAPITAL GROUP INC. (67,901); Portfolio Recovery Associates, LLC (66,502); CL Holdings LLC (54,524) |
| Non-bank: Credit reporting or other personal consumer reports | 662 | 405,693 | 2.2% | LEXISNEXIS (68,172); CBC Companies, Inc. (58,810); Fidelity National Information Services, Inc. (FNIS) (20,645); Affirm Holdings, Inc (17,497) |
| Non-bank: Mortgage | 1,837 | 276,503 | 1.5% | Ocwen Financial Corporation (38,028); Shellpoint Partners, LLC (19,717); NATIONSTAR MORTGAGE LLC (19,296); SELECT PORTFOLIO SERVICING, INC. (16,509) |
| Non-bank: Money transfer, virtual currency, or money service | 197 | 189,615 | 1.0% | Block, Inc. (71,330); Paypal Holdings, Inc (42,506); Early Warning Services, LLC (36,973); Coinbase, Inc. (9,706) |
| Non-bank: Student loan | 187 | 169,099 | 0.9% | Navient Solutions, LLC. (43,751); MOHELA (29,643); Nelnet, Inc. (27,927); AES/PHEAA (16,555) |
| Non-bank: Vehicle loan or lease | 416 | 86,127 | 0.5% | Westlake Services, LLC (14,151); Santander Consumer USA Holdings Inc. (12,455); TOYOTA MOTOR CREDIT CORPORATION (9,378); HYUNDAI CAPITAL AMERICA (8,260) |
| Non-bank: Credit card | 41 | 63,545 | 0.4% | Bread Financial Holdings, Inc. (49,208); Atlanticus Services Corporation (5,571); Continental Finance Company, LLC (3,580); Avant Holding Company, Inc. (3,112) |
| Non-bank: Payday loan, title loan, personal loan, or advance loan | 566 | 55,399 | 0.3% | ENOVA INTERNATIONAL, INC. (5,964); Klarna AB (2,921); CCF Intermediate Holdings LLC (2,707); ONEMAIN FINANCIAL HOLDINGS, LLC. (2,646) |
| Non-bank: Checking or savings account | 84 | 39,716 | 0.2% | Chime Financial Inc (28,559); FinCo Services Inc DBA Current (2,343); MORGAN STANLEY & CO. LLC (1,247); Social Finance, Inc. (992) |
| Non-bank: Prepaid card | 31 | 20,113 | 0.1% | Netspend Corporation (5,830); FISERV FINXACT CORE (3,536); Incomm Holdings Inc. (3,497); Conduent Incorporated (2,367) |
| Non-bank: Debt or credit management | 281 | 7,693 | 0.0% | John C. Heath, Attorney at Law, PLLC (2,088); FREEDOM FINANCIAL NETWORK (1,156); NATIONAL DEBT RELIEF LLC (400); Consumer Financial Services Solutions, Inc. (397) |

## Banks with ≥ 1,000 complaints

| Company | Complaints | Peer group | Method | FDIC organization | Assets |
|---|---|---|---|---|---|
| BANK OF AMERICA, NATIONAL ASSOCIATION | 187,449 | Bank >$250B | auto_holding_company | BANK OF AMERICA CORP | $2,669.5B |
| JPMORGAN CHASE & CO. | 176,500 | Bank >$250B | auto_holding_company | JPMORGAN CHASE&CO | $4,091.4B |
| WELLS FARGO & COMPANY | 175,447 | Bank >$250B | auto_holding_company | WELLS FARGO&COMPANY | $1,919.3B |
| CAPITAL ONE FINANCIAL CORPORATION | 172,911 | Bank >$250B | auto_holding_company | CAPITAL ONE FINANCIAL CORP | $662.2B |
| CITIBANK, N.A. | 142,299 | Bank >$250B | auto_bank_name | CITIGROUP INC | $1,976.2B |
| SYNCHRONY FINANCIAL | 84,179 | Bank $100-250B | auto_holding_company | SYNCHRONY FINANCIAL | $115.2B |
| AMERICAN EXPRESS COMPANY | 60,396 | Bank $100-250B | auto_holding_company | AMERICAN EXPRESS CO | $213.9B |
| U.S. BANCORP | 49,993 | Bank >$250B | curated | U S BCORP | $705.6B |
| DISCOVER BANK | 44,503 | Bank (no active FDIC record) | default_rule |  |  |
| TD BANK US HOLDING COMPANY | 37,684 | Bank >$250B | curated | TORONTO-DOMINION BANK THE | $374.9B |
| PNC Bank N.A. | 32,607 | Bank >$250B | auto_bank_name | PNC FINL SERVICES GROUP INC | $609.8B |
| ALLY FINANCIAL INC. | 30,482 | Bank $100-250B | auto_holding_company | ALLY FINANCIAL INC | $188.2B |
| BARCLAYS BANK DELAWARE | 28,877 | Bank $10-100B | auto_bank_name | BARCLAYS PLC | $43.5B |
| TRUIST FINANCIAL CORPORATION | 24,901 | Bank >$250B | auto_holding_company | TRUIST FINANCIAL CORP | $548.3B |
| GOLDMAN SACHS BANK USA | 21,293 | Bank >$250B | auto_bank_name | GOLDMAN SACHS GROUP INC THE | $758.8B |
| UNITED SERVICES AUTOMOBILE ASSOCIATION | 19,805 | Bank $100-250B | curated | UNITED SERVICES AUTOMOBILE ASSN | $106.2B |
| CITIZENS FINANCIAL GROUP, INC. | 18,239 | Bank $100-250B | curated | CITIZENS FINANCIAL GROUP INC | $232.5B |
| SANTANDER HOLDINGS USA, INC. | 17,768 | Bank $100-250B | curated | BANCO SANTANDER SA | $104.0B |
| FIFTH THIRD FINANCIAL CORPORATION | 15,546 | Bank >$250B | curated | FIFTH THIRD BCORP | $299.2B |
| HSBC NORTH AMERICA HOLDINGS INC. | 12,471 | Bank $100-250B | curated | HSBC HOLDINGS PLC | $173.2B |
| M&T BANK CORPORATION | 10,297 | Bank $100-250B | auto_holding_company | M&T BANK CORP | $219.6B |
| REGIONS FINANCIAL CORPORATION | 10,209 | Bank $100-250B | auto_holding_company | REGIONS FINANCIAL CORP | $159.8B |
| SUNTRUST BANKS, INC. | 9,634 | Bank (no active FDIC record) | default_rule |  |  |
| HUNTINGTON NATIONAL BANK, THE | 8,994 | Bank >$250B | auto_bank_name | HUNTINGTON BANCSHARES INC | $283.1B |
| Comerica | 8,603 | Bank (no active FDIC record) | curated |  |  |
| KEYCORP | 8,287 | Bank $100-250B | auto_holding_company | KEYCORP | $188.6B |
| SOFI TECHNOLOGIES, INC. | 7,233 | Bank $10-100B | auto_holding_company | SOFI TECHNOLOGIES INC | $56.8B |
| BMO BANK NATIONAL ASSOCIATION | 7,165 | Bank >$250B | auto_bank_name | BANK OF MONTREAL | $255.0B |
| BB&T CORPORATION | 5,522 | Bank (no active FDIC record) | curated |  |  |
| BBVA FINANCIAL CORPORATION | 5,180 | Bank (no active FDIC record) | curated |  |  |
| Flagstar Bank, N.A. | 4,707 | Bank $10-100B | auto_bank_name | Flagstar Bank, National Association | $87.7B |
| Synovus Bank | 4,632 | Bank (no active FDIC record) | default_rule |  |  |
| SANTANDER BANK, NATIONAL ASSOCIATION | 4,274 | Bank $100-250B | auto_bank_name | BANCO SANTANDER SA | $104.0B |
| FIRST NATIONAL BANK OF OMAHA | 4,251 | Bank $10-100B | auto_bank_name | LAURITZEN CORP | $34.2B |
| TCF NATIONAL BANK | 2,494 | Bank (no active FDIC record) | default_rule |  |  |
| BANK OF THE WEST | 2,443 | Bank (no active FDIC record) | default_rule |  |  |
| CIT BANK, NATIONAL ASSOCIATION | 2,185 | Bank (no active FDIC record) | default_rule |  |  |
| MIDFIRST BANK | 2,121 | Bank $10-100B | auto_bank_name | G JEFFREY RECORDS JR 2020 FAMILY TR | $42.8B |
| NEW YORK COMMUNITY BANCORP INC | 2,019 | Bank (no active FDIC record) | default_rule |  |  |
| BANCO POPULAR DE PUERTO RICO | 1,890 | Bank $10-100B | auto_bank_name | POPULAR INC | $78.4B |
| EVERBANK, NATIONAL ASSOCIATION | 1,758 | Bank $10-100B | auto_bank_name | EVERBANK FINANCIAL CORP | $46.7B |
| ARVEST BANK GROUP, INC. | 1,693 | Bank $10-100B | auto_holding_company | ARVEST BANK GROUP INC | $28.1B |
| WEBSTER BANK, NATIONAL ASSOCIATION | 1,632 | Bank (no active FDIC record) | default_rule |  |  |
| FIRST HORIZON BANK | 1,607 | Bank $10-100B | auto_bank_name | FIRST HORIZON CORP | $84.1B |
| FIRST CITIZENS BANCSHARES, INC. | 1,599 | Bank (no active FDIC record) | default_rule |  |  |
| CHARLES SCHWAB CORPORATION, THE | 1,438 | Bank >$250B | auto_holding_company | CHARLES SCHWAB CORP THE | $287.9B |
| AXOS FINANCIAL, INC. | 1,241 | Bank $10-100B | auto_holding_company | AXOS FINANCIAL INC | $28.9B |
| ZIONS BANCORPORATION | 1,119 | Bank $10-100B | auto_bank_name | Zions Bancorporation, N.A. | $89.0B |
| COMMERCE BANK | 1,064 | Bank (no active FDIC record) | default_rule |  |  |
| BANK OF NEW YORK MELLON CORPORATION, THE | 1,045 | Bank >$250B | auto_bank_name | BANK OF NY MELLON CORP THE | $457.4B |

## Low-confidence automatic matches (17)

Holding-company name match, < 200 complaints, no bank word in the CFPB name. Plausible but unverified: 1,033 complaints (0.006%).

| Company | Complaints | FDIC holding company | Assets |
|---|---|---|---|
| BANNER CORPORATION | 169 | BANNER CORP | $16.6B |
| BEACON FINANCIAL CORPORATION | 166 | BEACON FINANCIAL CORP | $22.2B |
| FIRST MERCHANTS CORPORATION | 153 | FIRST MERCHANTS CORP | $21.3B |
| Northern Trust Company, The | 135 | NORTHERN TRUST CORP | $178.6B |
| ALCAR INC. | 91 | ALCAR INC | $0.9B |
| FB FINANCIAL CORPORATION | 79 | FB FINANCIAL CORP | $16.8B |
| OCEANFIRST FINANCIAL CORP. | 54 | OCEANFIRST FINANCIAL CORP | $23.2B |
| Premier Holdings | 38 | PREMIER HOLDINGS LTD | $0.8B |
| CVB FINANCIAL CORP. | 33 | CVB FINANCIAL CORP | $21.2B |
| Flagship Financial Group | 24 | FLAGSHIP FINANCIAL GROUP INC | $1.3B |
| PARK NATIONAL CORPORATION | 20 | PARK NATIONAL CORP | $12.6B |
| STIFEL FINANCIAL CORP. | 18 | STIFEL FINANCIAL CORP | $35.5B |
| River Financial Inc. | 17 | RIVER FINANCIAL CORP | $4.0B |
| Town Financial Corporation | 14 | TOWN FINANCIAL CORP | $0.9B |
| Oak Tree Financial Inc. | 9 | OAK TREE FINANCIAL CORP INC | $0.1B |
| PALOMAR ENTERPRISES, LLC | 8 | PALOMAR ENTERPRISES LLC | $11.8B |
| Covington Capital Corporation | 5 | COVINGTON CAPITAL CORP | $0.1B |

## Known limitations

- Peer group is **as of today** (current FDIC assets and names); history isn't re-tiered (e.g. Discover is *Bank (no active FDIC record)* for all years).
- *Main product* follows the data, not the business model: e.g. Affirm Holdings lands in *Non-bank: Credit reporting…* because most complaints about it concern how its loans are reported.
- Corporate history is not linked (see `README.md` in this folder). Credit unions have no asset data (NCUA not loaded), so they form one group.

