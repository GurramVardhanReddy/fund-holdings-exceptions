"""Turn each provider's holdings file into one standard table.

Every loader returns a DataFrame with exactly the columns in ``COLUMNS``.
Provider-specific quirks (header blocks above the table, thousands separators,
different column names, bond vs equity layouts) are handled here, so the checks
downstream only ever see one format.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd

COLUMNS = [
    "as_of",          # date the provider says the holdings are valid for
    "provider",       # ishares | spdr
    "fund_id",        # provider ticker of the fund, e.g. SWDA, WTCH
    "fund_isin",      # ISIN of the fund itself, when the file gives it
    "fund_name",
    "isin",           # ISIN of the holding (None when the file has no ISIN)
    "ticker",         # ticker of the holding (None when the file has no ticker)
    "security_name",
    "asset_class",    # Equity | Fixed Income | Cash | Futures | Money Market | ...
    "sector",
    "country",
    "currency",       # currency the price is quoted in
    "shares",         # number of shares, or par value for bonds
    "price",          # local price
    "market_value",   # market value in the fund's base currency
    "weight_pct",     # % of fund
    "source_file",
    "source_row",     # row number in the source table (1 = first data row)
]

_NUM_RE = re.compile(r"[,\s]")


def _num(s: pd.Series) -> pd.Series:
    """Parse numbers written as text with thousands separators; '-' or '' -> NaN."""
    if s.dtype.kind in "fi":
        return s.astype(float)
    cleaned = s.astype(str).str.replace(_NUM_RE, "", regex=True)
    cleaned = cleaned.replace({"-": None, "": None, "nan": None, "None": None})
    return pd.to_numeric(cleaned, errors="coerce")


def _clean_str(s: pd.Series) -> pd.Series:
    out = s.astype("string").str.strip()
    return out.mask(out.isin(["", "-", "nan", "<NA>"]))


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[COLUMNS].reset_index(drop=True)


# --------------------------------------------------------------------------- iShares


def load_ishares_csv(path: str | Path, fund_id: str | None = None) -> pd.DataFrame:
    """iShares 'latest-holdings.csv': a 'Fund Holdings as of' line, a blank line,
    then a normal CSV with numbers written as text ("8,425,710,293.35")."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    m = re.search(r'"(\d{2}/\w{3}/\d{4})"', lines[0])
    if not m:
        raise ValueError(f"{path.name}: no 'Fund Holdings as of' date on line 1")
    as_of = datetime.strptime(m.group(1), "%d/%b/%Y").date()
    header_idx = next(i for i, ln in enumerate(lines) if ln.startswith("Ticker,"))
    raw = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])), dtype=str)
    raw = raw.dropna(how="all")
    # iShares appends disclaimer text below the table; real rows have a Weight.
    raw = raw[raw["Weight (%)"].notna()]

    fund_id = fund_id or path.stem.split("_", 1)[-1]
    df = pd.DataFrame(
        {
            "as_of": as_of,
            "provider": "ishares",
            "fund_id": fund_id,
            "fund_isin": None,
            "fund_name": None,
            "isin": None,  # this file has no ISIN column
            "ticker": _clean_str(raw["Ticker"]),
            "security_name": _clean_str(raw["Name"]),
            "asset_class": _clean_str(raw["Asset Class"]),
            "sector": _clean_str(raw["Sector"]),
            "country": _clean_str(raw["Location"]),
            "currency": _clean_str(raw["Market Currency"]),
            "shares": _num(raw["Shares"]),
            "price": _num(raw["Price"]),
            "market_value": _num(raw["Market Value"]),
            "weight_pct": _num(raw["Weight (%)"]),
            "source_file": path.name,
            "source_row": range(1, len(raw) + 1),
        }
    )
    return _finish(df)


# --------------------------------------------------------------------------- SPDR

# SPDR uses different column names for equity and bond funds.
_SPDR_MAP = {
    "ISIN": "isin",
    "Security Name": "security_name",
    "Currency": "currency",
    "Currency Local": "currency",
    "Number of Shares": "shares",
    "PAR Value Local": "shares",
    "Percent of Fund": "weight_pct",
    "Local Price": "price",
    "Base Market Value": "market_value",
    "Sector Classification": "sector",
    "Trade Country Name": "country",
    "Country of Issue": "country",
    # third layout (CLO / commodity funds): Name, Identifier, ..., Weight, Market Value
    "Name": "security_name",
    "Identifier": "identifier",
    "Weight": "weight_pct",
    "Market Value": "market_value",
    "MARKET VALUE": "market_value",
    "Par Value": "shares",
    "Shares Held": "shares",
    "Local Currency": "currency",
    "Sector": "sector",
}

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")


def _spdr_header(block: pd.DataFrame) -> dict[str, str]:
    info = {}
    for _, row in block.iterrows():
        key = str(row.iloc[0]).strip().rstrip(":")
        if key and key != "nan":
            info[key] = str(row.iloc[1]).strip()
    return info


def load_spdr_xlsx(data: bytes, name: str) -> pd.DataFrame:
    """One SPDR 'holdings-daily-emea-en-<ticker>-<exch>.xlsx' file: a 4-line
    fund header (name, ISIN, ticker, as-of date), a blank line, then the table."""
    raw = pd.read_excel(io.BytesIO(data), header=None, dtype=object)
    first = raw.iloc[:, 0].astype(str).str.strip()
    hdr_row = raw.index[first.isin(["ISIN", "Name"])]
    if len(hdr_row) == 0:
        raise ValueError(f"{name}: no table header row starting with 'ISIN' or 'Name'")
    h = int(hdr_row[0])
    info = _spdr_header(raw.iloc[:h])
    table = raw.iloc[h + 1 :].copy()
    table.columns = [str(c).strip() for c in raw.iloc[h]]
    table = table.dropna(how="all")
    weight_col = "Percent of Fund" if "Percent of Fund" in table.columns else "Weight"
    # Footnotes sit under the table; real rows have a numeric weight.
    table = table[pd.to_numeric(table[weight_col], errors="coerce").notna()]

    is_bond = "PAR Value Local" in table.columns or "Coupon" in table.columns
    # The 'Name' layout is used by commodity-swap and CLO funds; without a coupon
    # column it is a commodity fund holding bills, deposits and swaps, not equity.
    third_layout = "Name" in table.columns and "ISIN" not in table.columns
    out = pd.DataFrame(index=table.index)
    for src, dst in _SPDR_MAP.items():
        if src in table.columns and dst not in out:
            out[dst] = table[src]
    if "identifier" in out:
        ident = _clean_str(out.pop("identifier"))
        out["isin"] = ident.where(ident.fillna("").str.match(_ISIN_RE))
    for col in ("shares", "price", "market_value", "weight_pct"):
        out[col] = _num(out[col]) if col in out else None
    for col in ("isin", "security_name", "currency", "sector", "country"):
        out[col] = _clean_str(out[col]) if col in out else None
    # SPDR writes the placeholder 'Unassigned' where a line has no ISIN.
    out["isin"] = out["isin"].mask(out["isin"].str.upper() == "UNASSIGNED")

    as_of = datetime.strptime(info["Holdings As Of"], "%d-%b-%Y").date()
    out["as_of"] = as_of
    out["provider"] = "spdr"
    out["fund_id"] = info.get("Ticker Symbol", name).split()[0]
    out["fund_isin"] = info.get("ISIN")
    out["fund_name"] = info.get("Fund Name")
    out["ticker"] = None
    out["asset_class"] = "Fixed Income" if (is_bond or third_layout) else "Equity"
    names = out["security_name"].fillna("")
    # SPDR lists cash, currency and swap lines inside the table; mark them by name.
    cash = names.str.contains(
        r"^cash_|\bcash\b|currency contract|^(?:usd|eur|gbp|us dollar|euro currency|pound sterling)$",
        case=False, regex=True,
    ) & out["isin"].isna()
    out.loc[cash, "asset_class"] = "Cash"
    # FX forwards are written like 'CNY:HKD 20250930'.
    fx = names.str.match(r"^[A-Z]{3}:[A-Z]{3}\b") & out["isin"].isna()
    out.loc[fx, "asset_class"] = "FX"
    # Index futures sit in the equity table ('SP500 MIC EMIN FUTDEC26 ...').
    fut = names.str.contains(r"\bFUT", case=True, regex=True)
    out.loc[fut, "asset_class"] = "Futures"
    # Currency balances: placeholder codes such as 'KWD999999', or currency names
    # ('KUWAITI DINAR', 'HONG KONG DOLLAR') on lines with no ISIN.
    ccy = (
        names.str.match(r"^[A-Z]{3}9{6}$")
        | (
            names.str.contains(
                r"\b(?:DINAR|DOLLAR|EURO|POUND|YEN|FRANC|KRONA|KRONE|RIYAL|DIRHAM|RAND|PESO|RUPEE|"
                r"WON|REAL|RINGGIT|BAHT|ZLOTY|FORINT|SHEKEL|LIRA|YUAN|RENMINBI|KORUNA|RUPIAH)S?$",
                case=False, regex=True,
            )
            & out["isin"].isna()
        )
    )
    out.loc[ccy, "asset_class"] = "Cash"
    swap = names.str.contains(r"\bTRS\b|total return swap", case=False, regex=True)
    out.loc[swap, "asset_class"] = "Swap"
    out["source_file"] = name
    out["source_row"] = range(1, len(out) + 1)
    return _finish(out)


def load_spdr_zip(path: str | Path) -> tuple[pd.DataFrame, list[str]]:
    """All funds in SPDR's daily 'allholdings' zip. Returns the holdings and a
    list of member files that could not be read (reported, never silently dropped)."""
    frames, errors = [], []
    with zipfile.ZipFile(path) as z:
        for name in sorted(z.namelist()):
            if not name.endswith(".xlsx"):
                continue
            try:
                frames.append(load_spdr_xlsx(z.read(name), name))
            except Exception as exc:  # noqa: BLE001  one broken file must not stop the run
                errors.append(f"{name}: {exc}")
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    return df, errors


# --------------------------------------------------------------------------- folder


def load_snapshot(folder: str | Path) -> tuple[pd.DataFrame, list[str]]:
    """Load every supported file in one dated raw folder."""
    import warnings

    folder = Path(folder)
    frames, errors = [], []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # openpyxl style warnings on SPDR files
        for f in sorted(folder.iterdir()):
            try:
                if f.suffix == ".csv" and f.name.startswith("ishares_"):
                    frames.append(load_ishares_csv(f))
                elif f.suffix == ".zip" and f.name.startswith("spdr_"):
                    df, errs = load_spdr_zip(f)
                    frames.append(df)
                    errors.extend(errs)
            except Exception as exc:  # noqa: BLE001  reported in the summary, run continues
                errors.append(f"{f.name}: {exc}")
    if not frames:
        return pd.DataFrame(columns=COLUMNS), errors
    return pd.concat(frames, ignore_index=True), errors
