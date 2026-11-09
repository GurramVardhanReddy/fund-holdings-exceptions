import duckdb
import pandas as pd

from fundx.pipeline import run

from .helpers import ishares_csv, spdr_equity_xlsx, spdr_third_layout_xlsx, spdr_zip

GOOD = [
    # isin, name, ccy, shares, weight, price, market_value (value = shares x price)
    ("US02079K3059", "Alphabet Inc. Class A", "USD", 100, 15.0, 100.0, 10_000.0),
    ("US02079K1079", "Alphabet Inc. Class C", "USD", 100, 15.0, 100.0, 10_000.0),
    ("US5949181045", "Microsoft Corporation", "USD", 100, 9.0, 100.0, 10_000.0),
    ("US0378331005", "Apple Inc.", "USD", 100, 9.0, 100.0, 10_000.0),
    ("US67066G1040", "NVIDIA Corporation", "USD", 100, 8.0, 100.0, 10_000.0),
    ("US11135F1012", "Broadcom Inc.", "USD", 100, 8.0, 100.0, 10_000.0),
    ("US5951121038", "Micron Technology Inc.", "USD", 100, 8.0, 100.0, 10_000.0),
    ("US0079031078", "Advanced Micro Devices Inc.", "USD", 100, 8.0, 100.0, 10_000.0),
    ("US4581401001", "Intel Corporation", "USD", 100, 10.0, 100.0, 10_000.0),
    ("US68389X1054", "Oracle Corporation", "USD", 100, 10.0, 100.0, 10_000.0),
]


def make_day(root, folder, as_of_spdr, as_of_ishares, spdr_rows, extra_funds=True):
    d = root / folder
    d.mkdir(parents=True)
    files = {"holdings-daily-emea-en-tst-gy.xlsx": spdr_equity_xlsx("TST", as_of_spdr, spdr_rows)}
    if extra_funds:
        # Two more funds holding Apple at 100.0, so a wrong Apple price in TST stands out.
        for f in ("AAA", "BBB"):
            files[f"holdings-daily-emea-en-{f.lower()}-gy.xlsx"] = spdr_equity_xlsx(
                f, as_of_spdr, [("US0378331005", "Apple Inc.", "USD", 100, 100.0, 100.0, 10_000.0)]
            )
        files["holdings-daily-emea-en-com-im.xlsx"] = spdr_third_layout_xlsx(
            "COM", as_of_spdr, [("TREASURY BILL 01/27 0.00000", "912797VU8", 100.0, 1000.0)]
        )
    spdr_zip(d / "spdr_ie_all.zip", files)
    ishares_csv(
        d / "ishares_ITT.csv", as_of_ishares,
        [("AAPL", "APPLE", "Equity", 2_500.0, 25.0, 10, 250.0, "USD"),
         ("NVDA", "NVIDIA", "Equity", 7_400.0, 74.0, 74, 100.0, "USD")],  # adds up to 99%
    )
    return d


def rules(exc, fund=None):
    e = exc if fund is None else exc[exc["fund_id"] == fund]
    return set(e["rule_id"])


def test_clean_fund_only_raises_index_info_flags(tmp_path):
    raw = make_day(tmp_path, "d1", "06-Nov-2026", "06/Nov/2026", GOOD)
    exc = run(raw, tmp_path / "db.duckdb", tmp_path / "reports")
    tst = exc[exc["fund_id"] == "TST"]
    # Alphabet A + C = 30% -> above 20% for an index fund, found only by grouping the issuer
    lim = tst[tst["rule_id"] == "LIM1_ISSUER_OVER_20"]
    assert len(lim) == 1 and lim["value"].iloc[0] == 30.0
    assert "Class A + Alphabet Inc. Class C" in lim["security"].iloc[0]
    assert set(tst["severity"]) <= {"HIGH", "INFO"}
    assert not rules(exc, "COM") & {"LIM1_ISSUER_OVER_20", "LIM1_ISSUER_OVER_35", "DQ3_BAD_PRICE"}


def test_planted_errors_are_caught(tmp_path):
    bad = list(GOOD)
    bad[2] = ("US5949181045", "Microsoft Corporation", "USD", 100, 9.0, 100.0, 50_000.0)  # value 5x too big
    bad[3] = ("US0378331005", "Apple Inc.", "USD", 100, 9.0, 130.0, 13_000.0)  # price disagrees with AAA/BBB
    bad[4] = ("US67066G1040", "NVIDIA Corporation", "USD", 100, 8.0, None, 10_000.0)  # no price
    bad.append(("US4581401001", "Intel Corporation", "USD", 100, -1.0, 100.0, 10_000.0))  # duplicate, negative
    raw = make_day(tmp_path, "d1", "06-Nov-2026", "06/Nov/2026", bad)
    exc = run(raw, tmp_path / "db.duckdb", tmp_path / "reports")
    got = rules(exc, "TST")
    for r in ("DQ1_WEIGHT_TOTAL", "DQ3_BAD_PRICE", "DQ4_NEGATIVE_WEIGHT", "DQ5_VALUE_MISMATCH",
              "DQ6_DUPLICATE_LINE", "REC1_PRICE_DISAGREES"):
        assert r in got, r
    assert "DQ1_WEIGHT_TOTAL" in rules(exc, "ITT")  # iShares file adds up to 99%
    assert "LIM1_ISSUER_OVER_20" in rules(exc, "ITT")  # Apple 25%


def test_non_index_fund_uses_10_percent_limit(tmp_path):
    raw = make_day(tmp_path, "d1", "06-Nov-2026", "06/Nov/2026", GOOD)
    limits = tmp_path / "limits.csv"
    pd.DataFrame({"provider": ["spdr"], "fund_id": ["TST"], "index_tracking": [False]}).to_csv(limits, index=False)
    exc = run(raw, tmp_path / "db.duckdb", tmp_path / "reports", limits)
    tst = exc[exc["fund_id"] == "TST"]
    assert (tst[tst["rule_id"].str.startswith("LIM")]["severity"] == "HIGH").all()
    assert "LIM2_5_40_RULE" in set(tst["rule_id"])  # 30+9+9+8*4+10+10 > 40 from issuers above 5%


def test_rerun_same_day_does_not_duplicate(tmp_path):
    raw = make_day(tmp_path, "d1", "06-Nov-2026", "06/Nov/2026", GOOD)
    db = tmp_path / "db.duckdb"
    run(raw, db, tmp_path / "reports")
    run(raw, db, tmp_path / "reports")
    con = duckdb.connect(str(db))
    n_rows = con.execute("SELECT count(*) FROM all_holdings").fetchall()[0][0]
    n_days = con.execute("SELECT count(DISTINCT as_of) FROM all_holdings").fetchall()[0][0]
    assert n_days == 1 and n_rows == 10 + 2 + 1 + 2


def test_day_over_day_checks(tmp_path):
    db = tmp_path / "db.duckdb"
    run(make_day(tmp_path, "d1", "06-Nov-2026", "06/Nov/2026", GOOD), db, tmp_path / "reports")
    day2 = list(GOOD)
    day2[2] = ("US5949181045", "Microsoft Corporation", "USD", 100, 12.0, 100.0, 10_000.0)  # 9% -> 12%
    day2[3] = ("US0378331005", "Apple Inc.", "USD", 100, 9.0, 100.0, 10_000.0)
    day2[8] = ("US4581401001", "Intel Corporation", "USD", 100, 7.0, 40.0, 4_000.0)  # price -60%
    day2[9] = ("US30303M1027", "Meta Platforms Inc.", "USD", 100, 10.0, 100.0, 10_000.0)  # Oracle out, Meta in
    exc = run(make_day(tmp_path, "d2", "07-Nov-2026", "07/Nov/2026", day2), db, tmp_path / "reports")
    got = rules(exc, "TST")
    assert {"DOD1_WEIGHT_JUMP", "DOD2_NEW_OR_GONE", "DOD3_PRICE_JUMP"} <= got
    gone = exc[(exc["rule_id"] == "DOD2_NEW_OR_GONE") & (exc["fund_id"] == "TST")]
    assert set(gone["security"]) == {"US68389X1054", "US30303M1027"}  # Oracle gone, Meta new


def test_reports_are_written(tmp_path):
    raw = make_day(tmp_path, "d1", "06-Nov-2026", "06/Nov/2026", GOOD)
    run(raw, tmp_path / "db.duckdb", tmp_path / "reports")
    day = tmp_path / "reports" / "2026-11-06"
    assert (day / "exceptions.csv").exists() and (day / "escalate.csv").exists()
    text = (day / "summary.md").read_text()
    assert "Funds loaded: 5" in text and "LIM1_ISSUER_OVER_20" in text
