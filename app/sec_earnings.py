"""Quarterly earnings per share from the SEC's XBRL data (free, every US-listed company since ~2009).

Used for the earnings-drift test. One small request per company
(data.sec.gov/api/xbrl/companyconcept/CIK.../us-gaap/EarningsPerShareDiluted.json), cached on disk and
refreshed after 30 days. The SEC asks for at most 10 requests per second and a contact in the User-Agent.

Earnings surprise ("SUE", standardized unexpected earnings; Bernard & Thomas 1989): this quarter's EPS minus
the same quarter a year earlier, divided by how much that year-over-year change has varied over the previous
4-8 quarters. Only 10-Q quarters (Q1-Q3) are used: annual reports don't state the 4th quarter separately.
The signal date is the day the 10-Q was FILED. Companies often announce earnings a few days to weeks before
filing, so this is conservative (it may miss the first part of the move).
"""
from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{concept}.json"
CONCEPTS = ("EarningsPerShareDiluted", "EarningsPerShareBasic")
REFRESH_DAYS = 30


def ticker_map(cache_dir: Path, user_agent: str) -> dict[str, int]:
    """Ticker -> SEC company number (CIK). Today's tickers only."""
    f = cache_dir / "sec_tickers.csv"
    if f.exists() and (time.time() - f.stat().st_mtime) < 7 * 86400:
        df = pd.read_csv(f, dtype={"ticker": str})
    else:
        r = requests.get(TICKERS_URL, headers={"User-Agent": user_agent}, timeout=60)
        r.raise_for_status()
        df = pd.DataFrame([{"ticker": str(v["ticker"]).upper(), "cik": int(v["cik_str"])} for v in r.json().values()])
        cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(f, index=False)
    return dict(zip(df["ticker"], df["cik"].astype(int)))


def _quarterly(facts: list[dict]) -> pd.DataFrame:
    """Three-month EPS values from original 10-Q filings: end, filed, val (earliest filing per quarter)."""
    rows = []
    for x in facts:
        if x.get("form") != "10-Q" or not x.get("start") or not x.get("end") or x.get("val") is None:
            continue
        try:
            s, e, filed = date.fromisoformat(x["start"]), date.fromisoformat(x["end"]), date.fromisoformat(x["filed"])
        except (TypeError, ValueError):
            continue
        if 80 <= (e - s).days <= 100 and filed >= e:
            rows.append({"end": e, "filed": filed, "val": float(x["val"])})
    if not rows:
        return pd.DataFrame(columns=["end", "filed", "val"])
    df = pd.DataFrame(rows).sort_values(["end", "filed"])
    return df.drop_duplicates("end", keep="first").reset_index(drop=True)


def fetch_company(cik: int, user_agent: str) -> pd.DataFrame | None:
    """Quarterly EPS for one company; empty frame if it has none (e.g. a fund); None on a network error."""
    for concept in CONCEPTS:
        try:
            r = requests.get(CONCEPT_URL.format(cik=cik, concept=concept), headers={"User-Agent": user_agent},
                             timeout=30)
        except requests.RequestException as e:
            log.info("EPS for CIK %s failed: %s", cik, e)
            return None
        time.sleep(0.12)                                  # stay under the SEC's 10 requests per second
        if r.status_code == 404:
            continue
        if r.status_code != 200:
            return None
        units = r.json().get("units", {})
        facts = units.get("USD/shares") or next(iter(units.values()), [])
        q = _quarterly(facts)
        if len(q):
            return q
    return pd.DataFrame(columns=["end", "filed", "val"])


def eps_history(ciks: dict[str, int], cache_dir: Path, user_agent: str, emit=lambda m: None) -> dict[str, pd.DataFrame]:
    """Quarterly EPS per ticker, from the disk cache where it's fresh, otherwise from the SEC."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    data_f, seen_f = cache_dir / "eps_quarterly.csv.gz", cache_dir / "eps_fetched.csv"
    data = pd.read_csv(data_f) if data_f.exists() else pd.DataFrame(columns=["cik", "end", "filed", "val"])
    seen = pd.read_csv(seen_f) if seen_f.exists() else pd.DataFrame(columns=["cik", "fetched"])
    fresh = {int(c) for c, f in zip(seen["cik"], seen["fetched"])
             if (date.today() - date.fromisoformat(str(f))).days < REFRESH_DAYS}
    todo = sorted({c for c in ciks.values() if c not in fresh})
    new_rows, fetched = [], []
    for n, cik in enumerate(todo):
        if n % 50 == 0:
            emit(f"SEC earnings data: {n}/{len(todo)} companies (first run only; cached afterwards)...")
        q = fetch_company(cik, user_agent)
        if q is None:
            continue
        fetched.append(cik)
        if len(q):
            q = q.copy()
            q["cik"] = cik
            new_rows.append(q)
        if len(fetched) % 500 == 0:                       # save progress on long first runs
            data, seen = _save(data, seen, new_rows, fetched, data_f, seen_f)
            new_rows, fetched = [], []
    data, seen = _save(data, seen, new_rows, fetched, data_f, seen_f)
    data["end"] = pd.to_datetime(data["end"]).dt.date
    data["filed"] = pd.to_datetime(data["filed"]).dt.date
    by_cik = {int(c): g[["end", "filed", "val"]].sort_values("end").reset_index(drop=True)
              for c, g in data.groupby("cik")}
    return {t: by_cik[c] for t, c in ciks.items() if c in by_cik}


def _save(data, seen, new_rows, fetched, data_f, seen_f):
    if fetched:
        done = set(fetched)
        data = data[~data["cik"].astype(int).isin(done)]
        if new_rows:
            data = pd.concat([data] + new_rows, ignore_index=True)
        seen = pd.concat([seen[~seen["cik"].astype(int).isin(done)],
                          pd.DataFrame({"cik": fetched, "fetched": date.today().isoformat()})], ignore_index=True)
        data.to_csv(data_f, index=False)
        seen.to_csv(seen_f, index=False)
    return data, seen


def surprises(q: pd.DataFrame) -> pd.DataFrame:
    """SUE for each quarter that has a year-earlier quarter and at least 4 earlier year-over-year changes."""
    if q is None or len(q) < 6:
        return pd.DataFrame(columns=["end", "filed", "sue"])
    ends = pd.to_datetime(q["end"]).values
    diffs = []                                            # (end, change vs the same quarter a year earlier)
    for i in range(len(q)):
        gap = (ends[i] - ends[:i]).astype("timedelta64[D]").astype(int)
        j = np.flatnonzero((gap >= 345) & (gap <= 385))
        if len(j):
            diffs.append((i, float(q["val"].iloc[i] - q["val"].iloc[j[-1]])))
    rows = []
    for k, (i, change) in enumerate(diffs):
        prior = [c for _, c in diffs[max(0, k - 8):k]]
        if len(prior) < 4:
            continue
        sd = float(np.std(prior, ddof=1))
        if sd <= 1e-9:
            continue
        rows.append({"end": q["end"].iloc[i], "filed": q["filed"].iloc[i], "sue": change / sd})
    return pd.DataFrame(rows, columns=["end", "filed", "sue"])
