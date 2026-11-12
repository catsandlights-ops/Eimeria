"""'Copy smart money': tracks company insiders (CEOs, CFOs, directors) buying their own stock.

Why insiders? You can't see what other traders are holding in real time. Insider
open-market PURCHASES are public (SEC Form 4, filed within 2 business days) and have
historically been one of the more informative public signals, especially when
several insiders buy at once ("cluster buys").
"""
from __future__ import annotations

import logging
import math
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import requests

log = logging.getLogger(__name__)

SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{file}"


class InsiderTracker:
    def __init__(self, journal, cfg: dict):
        self.journal = journal
        self.cfg = cfg

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled")) and "@" in self.cfg.get("sec_user_agent", "")

    def refresh(self, max_filings: int = 1500, progress=None) -> int:
        """Download new Form 4 filings and store any open-market purchases. Returns # of new buys."""
        if not self.enabled:
            log.info("Insider tracking off: add your name + email as 'sec_user_agent' in Settings.")
            return 0
        headers = {"User-Agent": self.cfg["sec_user_agent"]}
        # Only recent filings need fetching; older ones are already stored from earlier refreshes
        start = (date.today() - timedelta(days=min(5, self.cfg["lookback_days"]))).isoformat()
        end = date.today().isoformat()
        new_buys, checked, offset = 0, 0, 0
        while checked < max_filings:
            try:
                r = requests.get(SEARCH_URL, headers=headers, timeout=30, params={
                    "forms": "4", "dateRange": "custom", "startdt": start, "enddt": end, "from": offset})
                r.raise_for_status()
                hits = r.json()["hits"]["hits"]
            except Exception as e:
                log.warning("SEC search failed: %s", e)
                break
            if not hits:
                break
            offset += len(hits)
            for h in hits:
                acc, _, filename = h["_id"].partition(":")
                if not filename.endswith(".xml") or self.journal.has_filing(acc):
                    continue
                checked += 1
                ciks = h["_source"].get("ciks") or []
                filed = h["_source"].get("file_date", "")
                row = self._parse_filing(headers, ciks[-1] if ciks else "", acc, filename, filed)
                self.journal.add_insider_buy(row)  # stored even if not a buy, so it isn't fetched again
                if row["value"] > 0:
                    new_buys += 1
                if progress:
                    progress(checked)
                time.sleep(0.15)  # SEC fair-use limit is 10 requests/second
                if checked >= max_filings:
                    break
        log.info("Insider scan: checked %d filings, %d new purchases.", checked, new_buys)
        return new_buys

    def _parse_filing(self, headers, cik, acc, filename, filed) -> dict:
        row = {"accession": acc, "symbol": None, "owner": None, "title": None, "trade_date": filed,
               "shares": 0.0, "price": 0.0, "value": 0.0, "filed": filed}
        try:
            url = ARCHIVE_URL.format(cik=int(cik), acc=acc.replace("-", ""), file=filename)
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            row.update(parse_form4(r.content))
        except Exception as e:
            log.debug("Could not parse filing %s: %s", acc, e)
        return row

    def scores(self) -> dict[str, dict]:
        """Score per symbol 0..1 from recent insider purchases."""
        since = (date.today() - timedelta(days=self.cfg["lookback_days"])).isoformat()
        by_sym: dict[str, list[dict]] = {}
        for b in self.journal.recent_insider_buys(since):
            if b["symbol"] and b["value"] >= self.cfg["min_purchase_usd"] / 4:
                by_sym.setdefault(b["symbol"], []).append(b)
        out = {}
        for sym, buys in by_sym.items():
            total = sum(b["value"] for b in buys)
            if total < self.cfg["min_purchase_usd"]:
                continue
            insiders = {b["owner"] for b in buys}
            top_exec = any(t in (b["title"] or "").upper() for b in buys for t in ("CEO", "CHIEF EXECUTIVE", "CFO", "CHIEF FINANCIAL"))
            score = math.tanh(total / 1_000_000) * (1.3 if top_exec else 1.0) + 0.15 * (len(insiders) - 1)
            out[sym] = {"score": min(1.0, score), "total": total, "insiders": len(insiders),
                        "detail": ", ".join(sorted(f"{b['owner']} ({b['title'] or 'insider'})" for b in buys))[:200]}
        return out


def parse_form4(xml_bytes: bytes) -> dict:
    """Pull open-market purchases (transaction code 'P') out of a Form 4 XML document."""
    root = ET.fromstring(xml_bytes)
    t = lambda path: (root.findtext(path) or "").strip()  # noqa: E731
    title_parts = []
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    if rel is not None:
        if (rel.findtext("officerTitle") or "").strip():
            title_parts.append(rel.findtext("officerTitle").strip())
        if (rel.findtext("isDirector") or "").strip().lower() in ("1", "true"):
            title_parts.append("Director")
        if (rel.findtext("isTenPercentOwner") or "").strip().lower() in ("1", "true"):
            title_parts.append("10% Owner")
    shares = value = 0.0
    trade_date = None
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if (tx.findtext("transactionCoding/transactionCode") or "").strip() != "P":
            continue
        try:
            s = float(tx.findtext("transactionAmounts/transactionShares/value") or 0)
            p = float(tx.findtext("transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        shares += s
        value += s * p
        trade_date = (tx.findtext("transactionDate/value") or "").strip() or trade_date
    out = {"symbol": t("issuer/issuerTradingSymbol").upper() or None,
           "owner": t("reportingOwner/reportingOwnerId/rptOwnerName"),
           "title": ", ".join(title_parts), "shares": shares, "value": value,
           "price": value / shares if shares else 0.0}
    if trade_date:
        out["trade_date"] = trade_date
    return out
