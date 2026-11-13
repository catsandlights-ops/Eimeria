"""S&P 500 late-day momentum: a published intraday effect, tested with rules fixed IN ADVANCE.

Research: Gao, Han, Li & Zhou, "Market intraday momentum" (Journal of Financial Economics, 2018). The S&P 500
fund's return from yesterday's close to 10:00 tended to predict its return in the last 30 minutes of the day
(1993-2013), probably because big funds rebalance and hedge near the close. Effects often weaken once
published, so this checks whether it still works.

The rules are deliberately NOT settings, so they can't be tuned until the past looks good:
  * Signal: SPY's move from yesterday's 4 PM close to 10:00 AM today.
  * 3:30 PM: buy SPY if that move was up, short it if it was down (no trade if exactly flat).
  * Sell / cover at the 4 PM close. One trade a day, never overnight. Early-close days are skipped.
  * Position: 100% of the account (no leverage). Cost: 0.01% per fill (SPY's spread is ~0.002%; the exit
    is a market-on-close order). The table also shows the result at your own cost setting.
  * Pass/fail, decided before seeing any results: the average trade after costs must be positive BEFORE 2024
    (development period, 2019-2023) AND FROM 2024 ON (data the idea was never fitted to), with a
    t-statistic of at least 2 over the whole test. Anything else is a fail: no adjusting afterwards.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from .backtest import Backtester, BTResult
from .indicators import NY

SYMBOL = "SPY"
COST = 0.0001                      # per fill
HOLDOUT_START = date(2024, 1, 1)
SIGNAL_TIME = time(10, 0)          # the signal uses the close of the 9:55-10:00 bar
ENTRY_TIME = time(15, 30)          # entry at the open of the 15:30 bar
LAST_BAR = time(15, 55)            # exit at the close of the 15:55-16:00 bar
MIN_T = 2.0


def regular_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """5-minute regular-session bars with New York timestamps."""
    if bars is None or bars.empty:
        return pd.DataFrame()
    b = bars.copy()
    b.index = b.index.tz_convert(NY) if b.index.tz is not None else b.index.tz_localize("UTC").tz_convert(NY)
    return b[(b.index.time >= time(9, 30)) & (b.index.time < time(16, 0))].sort_index()


def daily_signals(bars: pd.DataFrame) -> pd.DataFrame:
    """One row per full trading day: signal (prev close -> 10:00), entry (15:30 open), exit (16:00 close)."""
    b = regular_bars(bars)
    if b.empty:
        return pd.DataFrame()
    rows, prev_close = [], None
    for day, g in b.groupby(b.index.date):
        t = g.index.time
        full_day = g.index[-1].time() == LAST_BAR and t[0] == time(9, 30)
        sig_bar, entry_bar = g[t == time(9, 55)], g[t == ENTRY_TIME]
        if full_day and prev_close and len(sig_bar) and len(entry_bar):
            rows.append({"day": day, "signal": float(sig_bar["close"].iloc[0]) / prev_close - 1,
                         "entry": float(entry_bar["open"].iloc[0]), "exit": float(g["close"].iloc[-1]),
                         "open": float(g["open"].iloc[0])})
        # Early-close days still provide the next day's previous close
        prev_close = float(g["close"].iloc[-1])
    return pd.DataFrame(rows)


def _tstat(x: np.ndarray) -> float:
    return float(x.mean() / x.std(ddof=1) * math.sqrt(len(x))) if len(x) > 2 and x.std(ddof=1) > 0 else 0.0


class SpyMomentumBacktester:
    def __init__(self, settings: dict):
        self.s = settings

    def run(self, bars: pd.DataFrame, start: date, end: date) -> BTResult:
        res = BTResult()
        res.allow_short = True
        user_cost = self.s["backtest"]["slippage_pct"]
        equity = start_equity = float(self.s["backtest"]["starting_equity"])
        sig = daily_signals(bars)
        if not sig.empty:
            sig = sig[(sig["day"] >= start) & (sig["day"] <= end)]
        res.equity_curve.append((datetime.combine(start, time(0)), equity))
        if sig.empty:
            res.notes.append("No SPY 5-minute prices for this period.")
            return res
        sig = sig[sig["signal"] != 0].reset_index(drop=True)
        d = np.sign(sig["signal"].to_numpy())
        raw = (sig["exit"] / sig["entry"] - 1).to_numpy() * d
        net = raw - 2 * COST
        for i, r in sig.iterrows():
            side = 1 if d[i] > 0 else -1
            entry = r["entry"] * (1 + COST * side)
            qty = math.floor(equity / entry)
            if qty < 1:
                break
            fill = r["exit"] * (1 - COST * side)
            pnl = (fill - entry) * qty * side
            equity += pnl
            res.trades.append({"symbol": SYMBOL, "side": "long" if side > 0 else "short",
                               "entry_time": datetime.combine(r["day"], ENTRY_TIME, NY),
                               "exit_time": datetime.combine(r["day"], time(16, 0), NY), "qty": qty,
                               "entry": entry, "exit": fill, "pnl": pnl, "pnl_pct": (fill / entry - 1) * side,
                               "exit_reason": "4 PM close", "partial": False, "score": r["signal"],
                               "setup": "spy_momentum"})
            res.equity_curve.append((datetime.combine(r["day"], time(16, 0)), equity))
        res.benchmark_return = float(sig["exit"].iloc[-1] / sig["open"].iloc[0] - 1)
        res.stats = Backtester._stats(res, start_equity)

        dev = (sig["day"] < HOLDOUT_START).to_numpy()
        hold = ~dev
        res.halves = [self._period("before 2024 (development)", res.trades, lambda x: x < HOLDOUT_START, start),
                      self._period("2024 onward (holdout)", res.trades, lambda x: x >= HOLDOUT_START,
                                   max(start, HOLDOUT_START))]
        res.halves = [h for h in res.halves if h["trades"]]

        def row(name, side, mask, values):
            a, b = values[mask & dev], values[mask & hold]
            r = {"band": name, "side": side, "n1": int(len(a)), "bps1": float(a.mean() * 1e4) if len(a) else 0.0,
                 "n2": int(len(b)), "bps2": float(b.mean() * 1e4) if len(b) else 0.0}
            r["consistent"] = r["n1"] >= 100 and r["n2"] >= 100 and r["bps1"] > 0 and r["bps2"] > 0
            return r
        allm = np.ones(len(sig), bool)
        user_net = raw - 2 * user_cost
        res.edge_table = [
            row("Follow the morning move (THE TEST)", "both", allm, net),
            row("  days it bought", "long", d > 0, net),
            row("  days it shorted", "short", d < 0, net),
            row(f"  same, at your cost setting ({user_cost:.3%}/fill)", "both", allm, user_net),
            row("  before any costs", "both", allm, raw),
        ]

        t_all = _tstat(net)
        n_dev, n_hold = int(dev.sum()), int(hold.sum())
        dev_ok = n_dev >= 100 and net[dev].mean() > 0
        hold_ok = n_hold >= 100 and net[hold].mean() > 0
        if n_dev < 100 or n_hold < 100:
            res.headline = ("INCOMPLETE TEST: the fair test needs at least 100 trading days before 2024 AND 100 "
                            "from 2024 on. Set From = 2019-01-01 and To = yesterday, then run again.")
        elif dev_ok and hold_ok and t_all >= MIN_T:
            res.headline = (f"PRE-REGISTERED TEST: PASS. Positive after costs before 2024 ({net[dev].mean()*1e4:+.1f} "
                            f"bps) and from 2024 on ({net[hold].mean()*1e4:+.1f} bps), t = {t_all:.1f}. Worth paper "
                            "trading; real profits per trade are small, so costs matter.")
        else:
            why = []
            if not dev_ok:
                why.append(f"not positive before 2024 ({net[dev].mean()*1e4:+.1f} bps)")
            if not hold_ok:
                why.append(f"not positive from 2024 on ({net[hold].mean()*1e4:+.1f} bps)")
            if 0 <= t_all < MIN_T:
                why.append(f"too weak to tell from luck (t = {t_all:.1f}, needs {MIN_T:.0f})")
            res.headline = ("PRE-REGISTERED TEST: FAIL (" + "; ".join(why) + "). By the rules set in advance this "
                            "idea is dropped: don't tune it until it passes.")
        before = (f"Costs that would wipe it out: {raw.mean()/2:.3%} per fill." if raw.mean() > 0 else
                  f"It lost money even before costs ({raw.mean()*1e4:+.1f} bps a trade).")
        res.notes.append(f"{len(sig)} trading days; average trade after 0.01%/fill costs {net.mean()*1e4:+.1f} bps, "
                         f"t = {t_all:.1f}. {before}")
        return res

    @staticmethod
    def _period(name, trades, sel, start) -> dict:
        t = [x for x in trades if sel(x["entry_time"].date())]
        wins = sum(x["pnl"] for x in t if x["pnl"] > 0)
        losses = -sum(x["pnl"] for x in t if x["pnl"] <= 0)
        return {"name": name, "trades": len(t), "pnl": wins - losses, "from": start,
                "profit_factor": wins / losses if losses > 0 else (float("inf") if wins else 0.0)}


def load_spy_bars(broker, start: date, end: date, feed: str, emit=lambda m: None) -> pd.DataFrame:
    """SPY 5-minute bars, a quarter at a time (plus a few days before the start for the first previous close)."""
    parts, s = [], start - timedelta(days=7)
    while s <= end:
        e = min(s + timedelta(days=90), end)
        emit(f"SPY 5-minute prices: {s:%Y-%m}...")
        b = broker.bars_range([SYMBOL], s, e, minutes=5, feed=feed).get(SYMBOL)
        if b is not None and len(b):
            parts.append(b)
        s = e + timedelta(days=1)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts)
    return out[~out.index.duplicated(keep="last")].sort_index()
