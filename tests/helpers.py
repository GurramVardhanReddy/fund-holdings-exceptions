"""Build small provider files in the real layouts, with known problems planted."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from openpyxl import Workbook

ISHARES_HEADER = (
    "Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,"
    "Shares,Price,Location,Exchange,Market Currency"
)


def ishares_csv(path: Path, as_of: str, rows: list[tuple]) -> Path:
    """rows: (ticker, name, asset_class, market_value, weight, shares, price, ccy)"""
    lines = [f'Fund Holdings as of,"{as_of}"', "", ISHARES_HEADER]
    for t, n, ac, mv, w, sh, px, ccy in rows:
        lines.append(
            f'"{t}","{n}","Information Technology","{ac}","{mv:,.2f}","{w:.2f}","{mv:,.2f}",'
            f'"{sh:,.2f}","{px:,.2f}","United States","NASDAQ","{ccy}"'
        )
    lines += ["", '"The content contained herein is owned or licensed by BlackRock"']
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def spdr_equity_xlsx(fund: str, as_of: str, rows: list[tuple]) -> bytes:
    """rows: (isin, name, ccy, shares, weight, price, market_value)"""
    wb = Workbook()
    ws = wb.active
    ws.append(["Fund Name:", f"Test {fund} UCITS ETF"])
    ws.append(["ISIN:", "IE00TEST0001"])
    ws.append(["Ticker Symbol:", f"{fund} GY"])
    ws.append(["Holdings As Of:", as_of])
    ws.append([])
    ws.append(["ISIN", "SEDOL", "Security Name", "Currency", "Number of Shares", "Percent of Fund",
               "Trade Country Name", "Local Price", "Sector Classification",
               "Industry Classification", "Base Market Value"])
    for isin, name, ccy, sh, w, px, mv in rows:
        ws.append([isin, "X", name, ccy, sh, w, "United States", px, "Tech", "Software", mv])
    ws.append([])
    ws.append(["Holdings are subject to change."])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def spdr_third_layout_xlsx(fund: str, as_of: str, rows: list[tuple]) -> bytes:
    """Commodity-fund layout. rows: (name, identifier, weight, market_value)"""
    wb = Workbook()
    ws = wb.active
    ws.append(["Fund Name:", f"Test {fund} Commodity UCITS ETF"])
    ws.append(["ISIN:", "IE00TEST0002"])
    ws.append(["Ticker Symbol:", f"{fund} IM"])
    ws.append(["Holdings As Of:", as_of])
    ws.append([])
    ws.append(["Name", "Ticker", "Identifier", "SEDOL", "Weight", "MARKET VALUE",
               "Notional Value", "Sector", "Shares Held", "Local Currency"])
    for name, ident, w, mv in rows:
        ws.append([name, "-", ident, "-", w, mv, mv, "-", mv, "USD"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def spdr_zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path
