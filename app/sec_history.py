"""Historical insider purchases from the SEC's free quarterly bulk data sets (2006 onward).

Each quarter is downloaded once (about 15 MB), reduced to open-market purchases by real
insiders (officers and directors; funds that merely own 10%+ are left out) and cached.
Used for backtesting the insider-buying swing strategy.
"""
from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)
URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{y}q{q}_form345.zip"


def quarters(start: date, end: date) -> list[tuple[int, int]]:
    out, y, q = [], start.year, (start.month - 1) // 3 + 1
    while (y, q) <= (end.year, (end.month - 1) // 3 + 1):
        out.append((y, q))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def load_quarter(y: int, q: int, cache_dir: Path, user_agent: str) -> pd.DataFrame | None:
    """Purchases filed in one quarter, or None if the SEC hasn't published it yet."""
    f = cache_dir / f"insider_{y}q{q}.csv.gz"     # plain data (not pickle, which can run code when loaded)
    if f.exists():
        df = pd.read_csv(f, dtype={"owner_id": str, "symbol": str, "owner": str, "title": str})
        df["filed"] = pd.to_datetime(df["filed"]).dt.date
        df["title"] = df["title"].fillna("")
        return df
    r = requests.get(URL.format(y=y, q=q), headers={"User-Agent": user_agent}, timeout=180)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))

    def rd(name, cols):
        return pd.read_csv(z.open(name), sep="\t", usecols=cols, dtype=str, on_bad_lines="skip")
    sub = rd("SUBMISSION.tsv", ["ACCESSION_NUMBER", "FILING_DATE", "ISSUERTRADINGSYMBOL", "DOCUMENT_TYPE"])
    tr = rd("NONDERIV_TRANS.tsv", ["ACCESSION_NUMBER", "TRANS_CODE", "TRANS_SHARES", "TRANS_PRICEPERSHARE"])
    own = rd("REPORTINGOWNER.tsv", ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP",
                                    "RPTOWNER_TITLE"])
    tr = tr[tr["TRANS_CODE"] == "P"].copy()
    shares = pd.to_numeric(tr["TRANS_SHARES"], errors="coerce")
    tr["price"] = pd.to_numeric(tr["TRANS_PRICEPERSHARE"], errors="coerce")
    tr["value"] = shares * tr["price"]
    g = tr.groupby("ACCESSION_NUMBER").agg(value=("value", "sum"), price=("price", "median")).reset_index()
    m = (g.merge(sub[sub["DOCUMENT_TYPE"].isin(["4", "4/A"])], on="ACCESSION_NUMBER")
          .merge(own.drop_duplicates("ACCESSION_NUMBER"), on="ACCESSION_NUMBER", how="left"))
    rel = m["RPTOWNER_RELATIONSHIP"].fillna("").str.lower()
    m = m[rel.str.contains("director|officer")]             # real insiders only
    out = pd.DataFrame({
        "symbol": m["ISSUERTRADINGSYMBOL"].str.upper().str.strip(),
        "filed": pd.to_datetime(m["FILING_DATE"], format="%d-%b-%Y", errors="coerce").dt.date,
        "owner": m["RPTOWNERNAME"], "owner_id": m["RPTOWNERCIK"], "title": m["RPTOWNER_TITLE"].fillna(""),
        "value": m["value"], "price": m["price"]})
    out = out.dropna(subset=["filed", "value", "price"])
    out = out[out["symbol"].str.fullmatch(r"[A-Z]{1,5}", na=False) & (out["value"] > 0)
              & (out["price"] < 100_000) & (out["value"] < 1e9)]
    cache_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(f, index=False)
    time.sleep(0.2)
    return out


def insider_history(start: date, end: date, cache_dir: Path, user_agent: str, progress=None) -> pd.DataFrame:
    frames = []
    for y, q in quarters(start, end):
        if progress:
            progress(f"Insider filings {y} Q{q}...")
        try:
            df = load_quarter(y, q, cache_dir, user_agent)
        except Exception as e:
            log.warning("SEC data %sQ%s failed: %s", y, q, e)
            df = None
        if df is not None:
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["symbol", "filed", "owner", "owner_id", "title", "value", "price"])
    all_ = pd.concat(frames)
    return all_[(all_["filed"] >= start) & (all_["filed"] <= end)]
