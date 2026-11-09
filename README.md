# Fund Holdings Exceptions: UCITS holdings data pipeline

Takes the public daily holdings files of UCITS ETFs from two fund providers, each in its own
format, turns them into one standard table in a DuckDB database, adds the fields the checks need,
and flags data-quality problems and UCITS concentration-limit cases in a daily exceptions report
with a short escalation list. It runs on Linux (bash, cron-ready) and in Docker.

This is a personal learning project built on public data. The concentration checks are
**screening flags against the public UCITS rules, not compliance findings**: whether a fund is
within its limits depends on its prospectus and the index methodology it follows.

## Why

Fund-risk and fund-services teams receive positions from many clients and administrators,
each in a different layout. Before any risk analysis, the data has to be converted to one
format, enriched, checked, and anything odd escalated. This project does those steps on
real files.

## Data

| Source | Format | What it holds |
|---|---|---|
| iShares Core MSCI World (SWDA), iShares S&P 500 IT Sector (IUIT) | CSV with a date line above the table and numbers written as text (`"8,425,710,293.35"`) | Ticker, name, sector, asset class, value, weight, shares, price. **No ISIN.** |
| SPDR (State Street), all 180 Ireland-domiciled UCITS ETFs | One zip of 180 Excel files, each with a 4-line fund header above the table | Three different table layouts: equity (ISIN, shares, price), bond (par value, coupon, maturity), and commodity/CLO funds (name, identifier, weight, value, no price) |

`config/sources.csv` lists the download URLs. `scripts/fetch.sh` saves each day's files under
`data/raw/<date>/` and refuses an HTML page when a provider returns one instead of the file
(this happened with the old iShares and SPDR links while building it).

## Pipeline

```
fetch.sh ──> data/raw/<date>/            raw provider files, one folder per day
               │
fundx/loaders.py                         one loader per layout -> one standard table (18 columns)
               │
fundx/enrich.py                          issuer key, security vs cash/FX/swap/futures, fund type
               │
DuckDB: all_holdings                     every day kept; re-running a day replaces it
               │
fundx/sql/checks.sql                     data-quality, reconciliation and UCITS limit checks
fundx/sql/day_over_day.sql               today vs the previous stored day
               │
reports/<as_of>/exceptions.csv, escalate.csv, summary.md
```

**Issuer grouping.** UCITS limits apply per issuer, not per line. US and Canadian ISINs contain
a CUSIP whose first 6 characters identify the issuer, so Alphabet class A (`US02079K3059`) and
class C (`US02079K1079`) are grouped as one issuer. Other holdings are grouped by a normalised
name ("Nestle S.A." -> `NESTLE`).

## Checks

| Rule | Severity | What it flags |
|---|---|---|
| DQ1_WEIGHT_TOTAL | MEDIUM | Fund weights add up to more than 0.5 points away from 100% |
| DQ2_MISSING_ID | LOW | A security line with no ISIN and no ticker |
| DQ3_BAD_PRICE | HIGH | A held security with a missing, zero or negative price (only in files that carry prices) |
| DQ4_NEGATIVE_WEIGHT | MEDIUM | Negative weight on a share or bond line |
| DQ5_VALUE_MISMATCH | MEDIUM | Value / (shares x price) more than 5% away from the other lines in the same fund and currency |
| DQ6_DUPLICATE_LINE | LOW | The same ISIN listed twice in one fund |
| REC1_PRICE_DISAGREES | MEDIUM | The same ISIN, day and currency priced more than 1% away from its median across 3+ funds |
| LIM1_ISSUER_OVER_10/20/35 | INFO or HIGH | One issuer above 10% of an equity fund. Standard UCITS: 10% limit. Index-tracking UCITS: 20%, and up to 35% for one issuer only in exceptional market conditions |
| LIM2_5_40_RULE | INFO or HIGH | Issuers above 5% add up to more than 40% (standard UCITS rule; index funds use the 20/35 derogation, so INFO for them) |
| DOD1_WEIGHT_JUMP | MEDIUM | A holding's weight moved by more than 2 points since the previous day |
| DOD2_NEW_OR_GONE | MEDIUM | A holding above 1% appeared or disappeared |
| DOD3_PRICE_JUMP | HIGH | A price moved by more than 25% in one day (corporate action or bad price) |

All the ETFs here track an index, so the index limits are the default. `config/fund_limits.csv`
(columns `provider,fund_id,index_tracking`) switches a fund to the standard 10% / 5-40 limits.

## Results on real data (holdings as of 6 Nov 2026)

One run on 9 Nov 2026 (in WSL Ubuntu), from download to report in about 45 seconds:

- **182 funds loaded** (2 iShares, 180 SPDR), **175,737 holding lines**, 0 files that could not be read.
- **887 exceptions**: 15 HIGH, 3 MEDIUM, 799 LOW, 70 INFO. Full table: [`reports/2026-11-06/summary.md`](reports/2026-11-06/summary.md).

What they show:

- **15 equity funds have one issuer above 20%** of the fund, from 20.3% to 34.4%, so they rely on
  the 35% exceptional-conditions provision for index funds. Examples: Shell at 34.4% of SPDR's
  Europe Energy fund, ASML at 33.7% of Europe Technology, Amazon at 33.3% of the S&P US Consumer
  Discretionary Select Sector fund, Apple at 21.6% of iShares' S&P 500 IT fund. All are escalated for a prospectus check.
- **Issuer grouping matters:** in SPDR's MSCI World Communication Services fund, Alphabet's class A
  (17.4%) and class C (13.8%) lines add up to **31.1%**. A line-by-line check would not see this.
- A further **48 issuer positions in 31 funds** sit between 10% and 20%: allowed only because those funds track an index.
- **SPDR's Multi-Asset Global Infrastructure fund (ZPRI): weights add up to 99.2%** instead of 100%.
- **One ISIN (GB00BTQGS779) is priced 45.2 in one fund and 44.5 (median) in six others** on the same day:
  a 1.6% difference, probably a different valuation point, so it is a question for the pricing team rather than an error.
- **797 lines in 69 funds have no identifier**, almost all bonds where the SPDR file says
  `Unassigned` instead of an ISIN. They cannot be matched to reference data until an ISIN is found.
- No value mismatches (DQ5) and no bad prices (DQ3) in this snapshot.

The day-over-day checks are tested with planted changes (see `tests/test_pipeline.py`), but they
have **no real results yet**: they need at least two stored days, and so far one day has been run.

### Calibrating the rules on real data

The first run produced 1,724 exceptions, most of them false alarms. Reading the hits fixed them:

- 372 "bad prices" came from commodity and CLO fund files that have no price column at all.
  DQ3 now only checks files that carry prices for most lines.
- SPDR writes `Unassigned` in the ISIN column, which made unrelated lines look like one security
  held in dozens of funds at very different prices. It is now treated as a missing ISIN.
- Cash (`Cash_USD`, `KUWAITI DINAR`, `KWD999999`), FX forwards (`CNY:HKD 20250930`),
  total-return swaps and index futures were being counted as shares or bonds. They are now
  classified separately, so they no longer trigger price, weight or concentration checks.

## Run it

Linux or WSL, Python 3.11+:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
bash scripts/fetch.sh                      # downloads into data/raw/<today>/
python -m fundx.pipeline data/raw/$(date +%F)
pytest -q
```

Every weekday at 07:30 with cron:

```
30 7 * * 1-5  /path/to/fund-holdings-exceptions/scripts/run_daily.sh >> /path/to/logs/cron.log 2>&1
```

Docker:

```bash
docker build -t fund-holdings-exceptions .
docker run --rm -v "$PWD/data:/app/data" -v "$PWD/reports:/app/reports" fund-holdings-exceptions
```

GitHub Actions runs ruff, a bash syntax check and the tests on Ubuntu, then builds the image.

## Limits

- Concentration checks cover equity funds only. Bond funds need issuer-group and
  government-issuer rules (UCITS allows up to 35%, or 100% with conditions, for government issuers).
- Issuers without a US/Canadian ISIN are grouped by name, which can miss parent/subsidiary links.
- Provider URLs and layouts change; the loaders fail loudly per file instead of guessing.
- Only two providers so far; the Xtrackers download link tried returned a web page instead of a file.
