"""AI test: does the local AI's swing veto actually help?

For past swing signals, replay exactly what the live bot does at 4:20 PM: give the AI the headlines
that existed at that moment (Alpaca's news archive) and ask it to approve or veto. Then check what the
stock actually did over the next N trading days (vs the S&P 500). If the AI is useful, the stocks it
approved should do better than the ones it vetoed.

Important: AI models learn from text up to a "training cutoff" date. For events before that date the
model may simply remember what happened to the stock, which would make it look smarter than it is.
So only signals AFTER the cutoff you enter are used.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from .indicators import NY
from .swing import _daily
from .swing_live import ai_review


@dataclass
class ModelResult:
    model: str
    approved: list[float] = field(default_factory=list)     # excess returns vs S&P 500
    vetoed: list[float] = field(default_factory=list)
    no_answer: int = 0
    seconds: float = 0.0
    examples: list[dict] = field(default_factory=list)

    @staticmethod
    def _mean(x):
        return sum(x) / len(x) if x else float("nan")

    def summary(self) -> dict:
        a, v = self.approved, self.vetoed
        all_ = a + v
        diff = self._mean(a) - self._mean(v) if a and v else float("nan")
        t = float("nan")
        if len(a) >= 3 and len(v) >= 3:
            va = sum((x - self._mean(a)) ** 2 for x in a) / (len(a) - 1)
            vv = sum((x - self._mean(v)) ** 2 for x in v) / (len(v) - 1)
            se = math.sqrt(va / len(a) + vv / len(v))
            t = diff / se if se > 0 else float("nan")
        n = len(all_) + self.no_answer
        return {"model": self.model, "answered": len(all_), "no_answer": self.no_answer,
                "approved": len(a), "vetoed": len(v),
                "avg_all": self._mean(all_), "avg_approved": self._mean(a), "avg_vetoed": self._mean(v),
                "diff": diff, "t": t, "seconds_each": self.seconds / n if n else float("nan")}


def verdict(s: dict) -> str:
    if s["answered"] < 20:
        return "Too few answers to judge. Test more signals."
    if not s["vetoed"]:
        return "The AI approved everything, so it made no difference."
    if not s["approved"]:
        return "The AI vetoed everything: it would block the whole strategy."
    t = s["t"]
    if math.isnan(t):
        return "Not enough vetoes to compare yet."
    if t >= 2:
        return (f"HELPS: approved stocks beat vetoed ones by {s['diff']:+.1%} on average (t={t:.1f}). "
                "Keep the AI veto on.")
    if t <= -2:
        return (f"HURTS: the stocks it vetoed did BETTER by {-s['diff']:.1%} (t={t:.1f}). "
                "Turn the AI veto off (Settings > Strategies).")
    return (f"NO CLEAR EFFECT: difference {s['diff']:+.1%} (t={t:.1f}) could easily be chance. "
            f"The veto removed {s['vetoed']} of {s['answered']} trades without proven benefit.")


def build_cases(events, px: dict, spy: pd.DataFrame, hold_days: int) -> list[dict]:
    """Outcome for each event: entry at the next open, exit at the close `hold_days` later, vs S&P 500."""
    sp = _daily(spy)
    cases = []
    for e in events:
        d = px.get(e.symbol)
        if d is None:
            continue
        j = d.index.searchsorted(pd.Timestamp(e.signal_date), side="right")
        if j + hold_days - 1 >= len(d):
            continue                                             # outcome not known yet
        entry, exit_ = float(d["open"].iloc[j]), float(d["close"].iloc[j + hold_days - 1])
        sj = sp.index.searchsorted(d.index[j])
        if sj + hold_days - 1 >= len(sp) or entry <= 0:
            continue
        mkt = float(sp["close"].iloc[sj + hold_days - 1]) / float(sp["open"].iloc[sj]) - 1
        cases.append({"symbol": e.symbol, "setup": e.setup, "detail": e.detail, "signal_date": e.signal_date,
                      "excess": exit_ / entry - 1 - mkt})
    return cases


def run_ai_test(broker, llms: list, cases: list[dict], sample: int, seed: int = 7, progress=None) -> list[ModelResult]:
    rng = random.Random(seed)  # nosec B311 - sampling for a statistics test, not security
    picked = cases if len(cases) <= sample else rng.sample(cases, sample)
    picked.sort(key=lambda c: c["signal_date"])
    results = [ModelResult(llm.model) for llm in llms]
    for i, c in enumerate(picked):
        if progress:
            progress(f"AI test: {i + 1}/{len(picked)} ({c['symbol']} {c['signal_date']})")
        close_time = datetime.combine(c["signal_date"], datetime.min.time(), NY).replace(hour=16, minute=20)
        news = broker.news_between([c["symbol"]], close_time - timedelta(days=3), close_time)
        news = [n for n in news if n["time"] is None or n["time"] <= close_time]    # nothing from the future
        for llm, r in zip(llms, results):
            t0 = time.time()
            ok, reason, answered = ai_review(llm, c, news)
            r.seconds += time.time() - t0
            if not answered:
                r.no_answer += 1
                continue
            (r.approved if ok else r.vetoed).append(c["excess"])
            if len(r.examples) < 200:
                r.examples.append({**c, "approve": ok, "reason": reason, "headlines": len(news)})
    return results


def default_cutoff() -> date:
    return date(2025, 7, 1)
