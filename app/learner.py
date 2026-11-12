"""Self-improvement. After each trading day the bot:

1. Re-weights its signals using SHADOW LEARNING: every stock it scanned (traded or not)
   was recorded with its signal scores, and checked 30 and 60 minutes later. Signals
   whose scores line up with what prices actually did get more weight. That gives
   thousands of data points a week instead of a handful of trades, and avoids only
   learning from trades the bot chose to take.
   (Until there's enough shadow data, it falls back to learning from closed trades.)
2. Calibrates confidence: if trades in a confidence range keep losing, it demands
   higher confidence before trading (never lower than your setting).
3. Writes "lessons" with the AI, which are fed back into future trade decisions.

It can never change the risk rules (position size, stop-loss, daily loss limit).
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone

import pandas as pd

from .ai_schemas import LESSONS
from .backtest import information_coefficient, weights_from_ic
from .indicators import DEFAULT_WEIGHTS, SIGNALS

log = logging.getLogger(__name__)

REVIEW_SYSTEM = (
    "You are a strict trading coach reviewing an AI day-trading bot's results. Find concrete, "
    "testable patterns in what worked and what failed. Never suggest increasing risk. Reply ONLY with JSON."
)


class Learner:
    def __init__(self, journal, cfg: dict, base_min_confidence: float):
        self.journal = journal
        self.cfg = cfg
        self.base_min_confidence = base_min_confidence

    def weights(self) -> dict[str, float]:
        return self.journal.get_weights(DEFAULT_WEIGHTS)

    def min_confidence(self) -> float:
        learned = self.journal.get("learned_min_confidence")
        return max(self.base_min_confidence, learned or 0.0)

    def lessons(self) -> list[str]:
        return [l["text"] for l in self.journal.active_lessons(self.cfg["max_lessons_in_prompt"])]

    def _clamp(self, w: dict[str, float]) -> dict[str, float]:
        return {k: round(max(self.cfg["weight_min"], min(self.cfg["weight_max"], v)), 4) for k, v in w.items()}

    # --------------------------------------------------------------- weights
    def update_weights(self) -> str:
        if not self.cfg.get("enabled", True):
            return "Learning disabled."
        msgs = []
        if self.cfg.get("shadow_enabled", True):
            msg, done = self.update_from_observations()
            msgs.append(msg)
            if done:
                self.journal.mark_learned([t["id"] for t in self.journal.unlearned_trades()])
                self._calibrate_confidence()
                return "\n".join(msgs)
        msgs.append(self.update_from_trades())
        self._calibrate_confidence()
        return "\n".join(msgs)

    def signal_quality(self) -> tuple[dict[str, float], int]:
        """Information coefficient per signal over recent shadow observations."""
        since = (datetime.now(timezone.utc) - timedelta(days=self.cfg["shadow_lookback_days"])).isoformat()
        rows = self.journal.learning_observations(since)
        if not rows:
            return {}, 0
        scores = pd.DataFrame([json.loads(r["scores_json"]) for r in rows])
        fwd = pd.Series([r["fwd60"] for r in rows])
        return {s: round(information_coefficient(scores[s], fwd), 4) for s in SIGNALS if s in scores}, len(rows)

    def update_from_observations(self) -> tuple[str, bool]:
        ic, n = self.signal_quality()
        need = self.cfg["shadow_min_observations"]
        if n < need:
            return f"Shadow learning: {n}/{need} observations collected before adjusting weights.", False
        target = weights_from_ic(ic, self.cfg["weight_min"], self.cfg["weight_max"])
        old = self.weights()
        lr = self.cfg["shadow_learning_rate"]
        new = self._clamp({k: old[k] * (1 - lr) + target.get(k, old[k]) * lr for k in old})
        self.journal.set_weights(new, note=f"shadow learning from {n} observations")
        self.journal.set("signal_quality", {"ic": ic, "n": n, "when": datetime.now(timezone.utc).isoformat()})
        return ("Shadow learning from %d observations. Signal quality (IC): %s\nNew weights: %s" % (
            n, ", ".join(f"{k} {v:+.3f}" for k, v in ic.items()),
            ", ".join(f"{k}={v:.2f}" for k, v in new.items()))), True

    def update_from_trades(self) -> str:
        """Fallback: adjust weights from closed trades not yet learned from."""
        total_closed = len(self.journal.closed_trades(limit=100000))
        if total_closed < self.cfg["min_trades_before_adjusting"]:
            return (f"Trade learning: {total_closed}/{self.cfg['min_trades_before_adjusting']} "
                    f"trades before adjusting weights.")
        trades = self.journal.unlearned_trades()
        if not trades:
            return "No new trades to learn from."
        w = self.weights()
        lr = self.cfg["learning_rate"]
        for t in trades:
            scores = json.loads(t["scores_json"] or "{}")
            direction = 1 if t["side"] == "long" else -1
            outcome = math.tanh((t["pnl_pct"] or 0) / 0.01)  # +-1 around a 1% move
            for sig, s in scores.items():
                if sig in w:
                    w[sig] *= 1 + lr * s * direction * outcome
        w = self._clamp(w)
        self.journal.set_weights(w, note=f"learned from {len(trades)} trades")
        self.journal.mark_learned([t["id"] for t in trades])
        return "Updated weights from trades: " + ", ".join(f"{k}={v:.2f}" for k, v in w.items())

    def apply_weights(self, weights: dict[str, float], note: str):
        """Used by 'Apply backtest weights' in the app."""
        w = self.weights()
        w.update({k: v for k, v in weights.items() if k in w})
        self.journal.set_weights(self._clamp(w), note=note)

    def _calibrate_confidence(self):
        """If low-confidence trades lose money overall, raise the bar (up to 0.85)."""
        trades = self.journal.closed_trades(limit=200)
        if len(trades) < 20:
            return
        floor = self.base_min_confidence
        for cutoff in [floor + 0.05 * i for i in range(0, 5)]:
            band = [t for t in trades if cutoff <= (t["confidence"] or 0) < cutoff + 0.05]
            if len(band) >= 5 and sum(t["pnl"] for t in band) < 0:
                continue  # this band loses money: require more than it
            self.journal.set("learned_min_confidence", round(min(0.85, cutoff), 2))
            return
        self.journal.set("learned_min_confidence", 0.85)

    # --------------------------------------------------------------- edge check
    def edge_report(self, gate: dict, min_score: float, allow_short: bool) -> dict:
        """Is there evidence the bot makes money after costs? Two independent checks:

        1. Paper trades: enough closed paper trades with a good profit factor. This measures the
           whole live system, including the AI's news judgement.
        2. Shadow edge: every scan where the signal was strong enough to consider a trade, scored by
           what the price actually did in the next 60 minutes, minus costs. Averaged per DAY (scans
           within a day are not independent), then tested for whether the average is reliably above 0.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=gate["lookback_days"])).isoformat()
        cost = gate["cost_bps"] / 1e4
        reasons = []

        paper = self.journal.closed_trades_since(since, mode="paper")
        wins = sum(t["pnl"] for t in paper if t["pnl"] > 0)
        losses = -sum(t["pnl"] for t in paper if t["pnl"] <= 0)
        pf = wins / losses if losses > 0 else (float("inf") if wins else 0.0)
        paper_ok = len(paper) >= gate["min_paper_trades"] and pf >= gate["min_profit_factor"]
        if len(paper) < gate["min_paper_trades"]:
            reasons.append(f"{len(paper)}/{gate['min_paper_trades']} paper trades in the last "
                           f"{gate['lookback_days']} days")
        elif pf < gate["min_profit_factor"]:
            reasons.append(f"paper profit factor {pf:.2f} is below {gate['min_profit_factor']:.2f}")

        rows = self.journal.learning_observations(since)
        daily: dict[str, list[float]] = {}
        for r in rows:
            sc = r["score"] or 0.0
            if abs(sc) < min_score or (sc < 0 and not allow_short):
                continue
            daily.setdefault(r["ts"][:10], []).append((1 if sc > 0 else -1) * r["fwd60"] - cost)
        day_means = [sum(v) / len(v) for v in daily.values()]
        n_days = len(day_means)
        mean = sum(day_means) / n_days if n_days else 0.0
        if n_days >= 2:
            var = sum((x - mean) ** 2 for x in day_means) / (n_days - 1)
            t = mean / math.sqrt(var / n_days) if var > 0 else 0.0
        else:
            t = 0.0
        shadow_ok = n_days >= gate["min_days"] and mean > 0 and t >= gate["min_t_stat"]
        if n_days < gate["min_days"]:
            reasons.append(f"{n_days}/{gate['min_days']} days of signal measurements")
        elif not shadow_ok:
            reasons.append(f"signals average {mean * 1e4:+.1f} bps after costs (confidence t={t:.1f}, "
                           f"needs {gate['min_t_stat']:.1f})")
        return {"passed": paper_ok and shadow_ok, "reasons": reasons,
                "paper_trades": len(paper), "paper_pf": pf, "paper_ok": paper_ok,
                "shadow_days": n_days, "shadow_bps": mean * 1e4, "shadow_t": t, "shadow_ok": shadow_ok}

    def swing_edge_report(self, gate: dict) -> dict:
        """Swing trades take weeks, so they're judged on closed PAPER swing trades over a longer window."""
        since = (datetime.now(timezone.utc) - timedelta(days=gate["swing_lookback_days"])).isoformat()
        t = self.journal.closed_trades_since(since, mode="paper", style="swing")
        wins = sum(x["pnl"] for x in t if x["pnl"] > 0)
        losses = -sum(x["pnl"] for x in t if x["pnl"] <= 0)
        pf = wins / losses if losses > 0 else (float("inf") if wins else 0.0)
        reasons = []
        if len(t) < gate["swing_min_paper_trades"]:
            reasons.append(f"{len(t)}/{gate['swing_min_paper_trades']} closed paper swing trades")
        elif pf < gate["min_profit_factor"]:
            reasons.append(f"paper swing profit factor {pf:.2f} is below {gate['min_profit_factor']:.2f}")
        return {"passed": not reasons, "reasons": reasons, "trades": len(t), "profit_factor": pf}

    # --------------------------------------------------------------- lessons
    def write_lessons(self, llm, todays_trades: list[dict]) -> str:
        """Ask the AI to review today's trades and produce up to 3 lessons."""
        if not todays_trades:
            return "No trades today, no new lessons."
        lines = []
        for t in todays_trades:
            lines.append(
                f"{t['symbol']} {t['side']} entry {t['entry_price']:.2f} exit {t['exit_price'] or 0:.2f} "
                f"P&L {(t['pnl_pct'] or 0):+.2%} | exit: {t.get('exit_reason') or '?'} | AI confidence "
                f"{t['confidence']:.2f} | signals {t['scores_json']} | reason: {(t['reasoning'] or '')[:200]}")
        existing = "\n".join(f"- {l}" for l in self.lessons()) or "(none)"
        ic, n = self.signal_quality()
        quality = (f"\nHow well each signal predicted 60-minute moves recently (rank correlation, {n} samples): "
                   + ", ".join(f"{k} {v:+.3f}" for k, v in ic.items())) if ic else ""
        prompt = (
            "Today's trades:\n" + "\n".join(lines) + quality +
            f"\n\nExisting lessons:\n{existing}\n\n"
            'Return JSON: {"review": "short paragraph", "new_lessons": ["max 3 short, specific rules '
            'that would have improved results, not repeating existing ones"], '
            '"retire_lessons": ["exact text of existing lessons that today\'s evidence contradicts"]}'
        )
        try:
            res = llm.chat_json(REVIEW_SYSTEM, prompt, schema=LESSONS)
        except Exception as e:
            return f"AI review failed: {e}"
        for text in (res.get("new_lessons") or [])[:3]:
            if isinstance(text, str) and 10 < len(text) < 300:
                self.journal.add_lesson(text.strip())
        retire = {r.strip() for r in (res.get("retire_lessons") or []) if isinstance(r, str)}
        for l in self.journal.active_lessons(1000):
            if l["text"] in retire:
                self.journal.deactivate_lesson(l["id"])
        return str(res.get("review", ""))
