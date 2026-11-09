"""Load one raw snapshot, store it in DuckDB, run the checks and write the reports."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

from .enrich import enrich
from .loaders import load_snapshot

ROOT = Path(__file__).resolve().parents[1]
SQL = Path(__file__).resolve().parent / "sql"
SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}


def fund_limits(holdings: pd.DataFrame, overrides: Path | None) -> pd.DataFrame:
    """Every fund here is an ETF, and these ETFs track an index, so the index
    derogation is the default. config/fund_limits.csv can override per fund."""
    funds = holdings[["provider", "fund_id"]].drop_duplicates().assign(index_tracking=True)
    if overrides and overrides.exists():
        o = pd.read_csv(overrides)
        funds = funds.merge(o, on=["provider", "fund_id"], how="left", suffixes=("", "_o"))
        funds["index_tracking"] = funds["index_tracking_o"].fillna(funds["index_tracking"]).astype(bool)
        funds = funds.drop(columns=["index_tracking_o"])
    return funds


def run_checks(con: duckdb.DuckDBPyConnection, with_prev: bool) -> pd.DataFrame:
    parts = [con.execute((SQL / "checks.sql").read_text()).df()]
    if with_prev:
        parts.append(con.execute((SQL / "day_over_day.sql").read_text()).df())
    exc = pd.concat(parts, ignore_index=True)
    if exc.empty:
        return exc
    exc["sev_rank"] = exc["severity"].map(SEVERITY_ORDER)
    exc = exc.sort_values(["sev_rank", "rule_id", "provider", "fund_id", "security"]).drop(columns="sev_rank")
    return exc.reset_index(drop=True)


def summary_md(exc: pd.DataFrame, holdings: pd.DataFrame, load_errors: list[str], prev_as_of) -> str:
    as_of = holdings["as_of"].max()
    lines = [
        f"# Exceptions report, holdings as of {as_of}",
        "",
        (
            f"- Funds loaded: {holdings[['provider', 'fund_id']].drop_duplicates().shape[0]}"
            f" ({', '.join(f'{p}: {n}' for p, n in holdings.groupby('provider')['fund_id'].nunique().items())})"
        ),
        f"- Holding lines loaded: {len(holdings):,}",
        f"- Files that could not be read: {len(load_errors)}",
        f"- Compared with previous snapshot: {prev_as_of if prev_as_of is not None else 'none stored yet'}",
        f"- Exceptions: {len(exc):,}",
        "",
        "## By rule and severity",
        "",
        "| Rule | Severity | Count | Funds |",
        "|---|---|---|---|",
    ]
    if not exc.empty:
        g = exc.groupby(["rule_id", "severity"]).agg(n=("fund_id", "size"), funds=("fund_id", "nunique")).reset_index()
        g["r"] = g["severity"].map(SEVERITY_ORDER)
        for _, r in g.sort_values(["r", "rule_id"]).iterrows():
            lines.append(f"| {r.rule_id} | {r.severity} | {r.n} | {r.funds} |")
    high = exc[exc["severity"] == "HIGH"] if not exc.empty else exc
    lines += ["", "## Escalate (HIGH)", ""]
    if high.empty:
        lines.append("None.")
    else:
        lines += ["| Fund | Rule | Security / issuer | Value | Detail |", "|---|---|---|---|---|"]
        for _, r in high.iterrows():
            lines.append(f"| {r.provider}:{r.fund_id} | {r.rule_id} | {r.security} | {r.value} | {r.detail} |")
    if load_errors:
        lines += ["", "## Files not read", ""] + [f"- {e}" for e in load_errors]
    return "\n".join(lines) + "\n"


def run(raw_dir: Path, db_path: Path, out_dir: Path, overrides: Path | None = None) -> pd.DataFrame:
    holdings, load_errors = load_snapshot(raw_dir)
    if holdings.empty:
        raise SystemExit(f"No holdings loaded from {raw_dir}: {load_errors}")
    holdings = enrich(holdings)
    as_of = holdings["as_of"].max()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.register("snap", holdings)
    con.execute("CREATE TABLE IF NOT EXISTS all_holdings AS SELECT * FROM snap WHERE false")
    # Re-running the same day replaces that day's rows instead of duplicating them.
    con.execute("DELETE FROM all_holdings WHERE as_of = ?", [as_of])
    con.execute("INSERT INTO all_holdings SELECT * FROM snap")
    con.execute(f"CREATE OR REPLACE TEMP VIEW holdings AS SELECT * FROM all_holdings WHERE as_of = DATE '{as_of}'")
    prev_as_of = con.execute("SELECT max(as_of) FROM all_holdings WHERE as_of < ?", [as_of]).fetchall()[0][0]
    if prev_as_of is not None:
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW prev_holdings AS SELECT * FROM all_holdings WHERE as_of = DATE '{prev_as_of}'"
        )
    con.register("fund_limits", fund_limits(holdings, overrides))

    exc = run_checks(con, with_prev=prev_as_of is not None)
    con.execute("CREATE TABLE IF NOT EXISTS exceptions AS SELECT * FROM exc WHERE false")
    con.execute("DELETE FROM exceptions WHERE as_of = ?", [as_of])
    if not exc.empty:
        con.execute("INSERT INTO exceptions SELECT * FROM exc")
    con.close()

    day_dir = out_dir / str(as_of)
    day_dir.mkdir(parents=True, exist_ok=True)
    exc.to_csv(day_dir / "exceptions.csv", index=False)
    exc[exc["severity"] == "HIGH"].to_csv(day_dir / "escalate.csv", index=False) if not exc.empty else None
    (day_dir / "summary.md").write_text(summary_md(exc, holdings, load_errors, prev_as_of))
    return exc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Standardise UCITS fund holdings and flag exceptions.")
    p.add_argument("raw_dir", type=Path, help="folder with one day's raw files, e.g. data/raw/2026-11-09")
    p.add_argument("--db", type=Path, default=ROOT / "data" / "fundx.duckdb")
    p.add_argument("--out", type=Path, default=ROOT / "reports")
    p.add_argument("--limits", type=Path, default=ROOT / "config" / "fund_limits.csv")
    a = p.parse_args(argv)
    exc = run(a.raw_dir, a.db, a.out, a.limits)
    counts = exc["severity"].value_counts().to_dict() if not exc.empty else {}
    print(f"exceptions: {len(exc)} {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
