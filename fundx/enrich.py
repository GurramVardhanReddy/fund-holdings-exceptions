"""Add the fields the checks need but the provider files do not give directly."""
from __future__ import annotations

import re

import pandas as pd

# Words that differ between share lines of the same company ("Alphabet Inc. Class A"
# vs "Class C") or between providers ("APPLE" vs "Apple Inc.").
_DROP = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "PLC", "AG", "SA",
    "NV", "SE", "SPA", "AB", "ASA", "AS", "OYJ", "LTD", "LIMITED", "LLC", "HOLDINGS",
    "HOLDING", "GROUP", "THE", "CLASS", "CL", "ORD", "ORDINARY", "REG", "REGISTERED",
    "SHS", "SHARES", "PREF", "PREFERENCE", "NPV", "ADR", "A", "B", "C", "H",
}
_NON_SECURITY = {"Cash", "FX", "Futures", "Swap", "Money Market", "Cash Collateral and Margins"}


def issuer_from_name(name: str | None) -> str | None:
    if not name:
        return None
    words = re.sub(r"[^A-Z0-9 ]", " ", name.upper()).split()
    # Drop legal-form words and stray single letters ("NESTLE S.A." -> "NESTLE").
    kept = [w for w in words if w not in _DROP and len(w) > 1]
    return " ".join(kept) or None


def issuer_key(isin: str | None, name: str | None) -> str | None:
    """Group share lines of the same company.

    US and Canadian ISINs embed a CUSIP whose first 6 characters identify the
    issuer, so Alphabet class A (US02079K3059) and class C (US02079K1079) both
    map to CUSIP:02079K. Other ISINs carry no issuer code, so the normalised
    security name is used instead.
    """
    if isinstance(isin, str) and len(isin) == 12 and isin[:2] in ("US", "CA"):
        return "CUSIP:" + isin[2:8]
    n = issuer_from_name(name)
    return "NAME:" + n if n else None


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["is_security"] = ~out["asset_class"].isin(_NON_SECURITY)
    out["issuer_key"] = [
        issuer_key(i, n) if s else None
        for i, n, s in zip(out["isin"], out["security_name"], out["is_security"], strict=True)
    ]
    # Fund type from what it holds: an equity fund is more than half equity by weight.
    eq = (
        out.assign(w=out["weight_pct"].where(out["asset_class"] == "Equity", 0))
        .groupby(["provider", "fund_id", "as_of"])["w"]
        .sum()
        .rename("equity_weight_pct")
        .reset_index()
    )
    out = out.merge(eq, on=["provider", "fund_id", "as_of"], how="left")
    out["fund_type"] = out["equity_weight_pct"].map(lambda w: "equity" if w > 50 else "other")
    return out
