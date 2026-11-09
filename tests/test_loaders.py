from datetime import date

import pandas as pd

from fundx.enrich import issuer_from_name, issuer_key
from fundx.loaders import COLUMNS, load_ishares_csv, load_spdr_xlsx

from .helpers import ishares_csv, spdr_equity_xlsx, spdr_third_layout_xlsx


def test_ishares_csv_parses_date_numbers_and_drops_footer(tmp_path):
    f = ishares_csv(
        tmp_path / "ishares_TEST.csv",
        "06/Nov/2026",
        [
            ("AAPL", "APPLE", "Equity", 8_425_710_293.35, 60.0, 24_609_955, 342.37, "USD"),
            ("USD", "USD CASH", "Cash", 1_000.0, 40.0, 1_000, 1.0, "USD"),
        ],
    )
    df = load_ishares_csv(f)
    assert list(df.columns) == COLUMNS
    assert len(df) == 2  # disclaimer line under the table is not a holding
    assert df["as_of"].iloc[0] == date(2026, 11, 6)
    assert df["fund_id"].iloc[0] == "TEST"
    assert df["market_value"].iloc[0] == 8_425_710_293.35  # "8,425,710,293.35" parsed
    assert df["weight_pct"].sum() == 100.0


def test_spdr_equity_layout_and_unassigned_isin():
    data = spdr_equity_xlsx(
        "ABC", "06-Nov-2026",
        [
            ("US0378331005", "Apple Inc.", "USD", 100, 50.0, 342.37, 34_237.0),
            ("Unassigned", "New Listing Corp", "USD", 10, 49.9, 10.0, 100.0),
            (None, "CNY:HKD 20250930", "HKD", 0, -0.0, 0.0, 0.0),
        ],
    )
    df = load_spdr_xlsx(data, "holdings-daily-emea-en-abc-gy.xlsx")
    assert len(df) == 3  # footnote under the table dropped
    assert df["fund_id"].iloc[0] == "ABC"
    assert df["fund_isin"].iloc[0] == "IE00TEST0001"
    assert pd.isna(df.loc[1, "isin"])  # 'Unassigned' is not an ISIN
    assert df.loc[0, "asset_class"] == "Equity"
    assert df.loc[2, "asset_class"] == "FX"


def test_spdr_third_layout_is_not_equity_and_swaps_are_marked():
    data = spdr_third_layout_xlsx(
        "COM", "06-Nov-2026",
        [
            ("TREASURY BILL 01/27 0.00000", "912797VU8", 60.0, 600.0),
            ("TRS USD DJCI3MQT", "-", -5.0, -50.0),
            ("US DOLLAR", "-", 45.0, 450.0),
        ],
    )
    df = load_spdr_xlsx(data, "holdings-daily-emea-en-com-im.xlsx")
    assert df.loc[0, "asset_class"] == "Fixed Income"
    assert pd.isna(df.loc[0, "isin"])  # a CUSIP is not an ISIN
    assert df.loc[1, "asset_class"] == "Swap"
    assert df.loc[2, "asset_class"] == "Cash"


def test_issuer_key_groups_share_classes():
    # Alphabet class A and class C share the CUSIP issuer code 02079K
    assert issuer_key("US02079K3059", "Alphabet Inc. Class A") == issuer_key("US02079K1079", "Alphabet Inc. Class C")
    assert issuer_key("US0378331005", "Apple Inc.") == "CUSIP:037833"
    # No ISIN (iShares files): fall back to the normalised name
    assert issuer_key(None, "ALPHABET CLASS A") == issuer_key(None, "ALPHABET CLASS C") == "NAME:ALPHABET"
    assert issuer_from_name("Nestle S.A.") == "NESTLE"
    assert issuer_from_name(None) is None
