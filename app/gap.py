"""Confirmed-gap day trading: trade overnight news only once the market has confirmed it.

The rule (shared by live trading and backtests):
  1. A stock opens >= 2% above yesterday's close (overnight news), is liquid and costs >= $5.
  2. Wait 30 minutes. If, at 10:05 New York time, it's still above where it stood after the first
     5 minutes ("confirmed"), buy. If it fell back, skip it: reversals did badly in testing.
  3. Stop-loss at the lowest price of those first 30 minutes (the pattern has failed if it goes there),
     but never more than 4% below the entry.
  4. Sell 60 minutes after buying (or earlier at the stop). Never held overnight.
Shorting confirmed gap-DOWNS is optional and off by default: in testing it worked in one period and
lost in the other.

Bars are 5 minutes: bar 0 = 9:30-9:35, bar 6 = 10:00-10:05. Decision uses bar 0's and bar 6's closes;
entry is the open of bar 7 (10:05).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time

import numpy as np
import pandas as pd

from .backtest import Backtester, BTResult
from .indicators import NY

CONFIRM_BARS = 6          # bar index whose close completes the 30-minute confirmation window


@dataclass
class GapEvent:
    symbol: str
    day: date
    prev_close: float
    gap: float                     # open / previous close - 1
    dollar_volume: float           # average daily $ volume before the gap
    extra: dict = field(default_factory=dict)


def daily_gaps(daily: dict[str, pd.DataFrame], cfg: dict, start: date, end: date) -> list[GapEvent]:
    """Gap days from daily bars (open vs previous close), with price/liquidity filters."""
    out = []
    for sym, df in daily.items():
        if df is None or len(df) < 25:
            continue
        d = df[["open", "close", "volume"]].astype(float).copy()
        idx = d.index.tz_convert(NY) if getattr(d.index, "tz", None) is not None else d.index
        d.index = pd.DatetimeIndex([pd.Timestamp(x.date()) for x in idx])
        d = d[~d.index.duplicated(keep="last")].sort_index()
        prev = d["close"].shift(1)
        dv = (d["close"] * d["volume"]).shift(1).rolling(20, min_periods=15).mean()
        gap = d["open"] / prev - 1
        for i in np.flatnonzero((gap.abs() >= cfg["min_gap"]).values):
            day = d.index[i].date()
            if not (start <= day <= end) or pd.isna(prev.iloc[i]) or prev.iloc[i] < cfg["min_price"]:
                continue
            if pd.isna(dv.iloc[i]) or dv.iloc[i] < cfg["min_dollar_volume"]:
                continue
            if gap.iloc[i] < 0 and not cfg.get("allow_short"):
                continue
            out.append(GapEvent(sym, day, float(prev.iloc[i]), float(gap.iloc[i]), float(dv.iloc[i])))
    return out


def session_bars(bars: pd.DataFrame, day: date) -> pd.DataFrame:
    """5-minute regular-session bars of one day, index reset so bar 0 = 9:30."""
    if bars is None or bars.empty:
        return pd.DataFrame()
    b = bars.copy()
    b.index = b.index.tz_convert(NY) if getattr(b.index, "tz", None) is not None else b.index.tz_localize("UTC").tz_convert(NY)
    b = b[(b.index.date == day)]
    b = b[(b.index.time >= time(9, 30)) & (b.index.time < time(16, 0))]
    return b.reset_index(names="ts")


def decide(ev: GapEvent, day_bars: pd.DataFrame, cfg: dict) -> dict | None:
    """The 10:05 decision. Returns {"side", "confirmed", "entry_ref", "stop", "move30"} or None if data is missing."""
    if len(day_bars) <= CONFIRM_BARS or day_bars.loc[0, "ts"].time() != time(9, 30):
        return None
    d0 = 1 if ev.gap > 0 else -1
    first, at_10 = float(day_bars.loc[0, "close"]), float(day_bars.loc[CONFIRM_BARS, "close"])
    move30 = at_10 / first - 1
    confirmed = (at_10 - first) * d0 > 0
    window = day_bars.loc[:CONFIRM_BARS]
    ref = at_10
    if d0 > 0:
        stop = max(float(window["low"].min()), ref * (1 - cfg["max_stop_pct"]))
    else:
        stop = min(float(window["high"].max()), ref * (1 + cfg["max_stop_pct"]))
    return {"side": "long" if d0 > 0 else "short", "confirmed": bool(confirmed), "entry_ref": ref,
            "stop": stop, "move30": move30}


# ------------------------------------------------------------------ backtest
class GapBacktester:
    def __init__(self, settings: dict):
        self.s = settings
        self.cfg = settings["gap_trading"]

    def run(self, events: list[GapEvent], intraday: dict[tuple[str, date], pd.DataFrame],
            spy: pd.DataFrame | None, start: date, end: date) -> BTResult:
        cfg, res = self.cfg, BTResult()
        slip = self.s["backtest"]["slippage_pct"]
        equity = start_equity = float(self.s["backtest"]["starting_equity"])
        res.equity_curve.append((datetime.combine(start, datetime.min.time()), equity))
        by_day: dict[date, list[GapEvent]] = {}
        for e in events:
            by_day.setdefault(e.day, []).append(e)
        stats_rows = []                       # every gap, traded or not, for the event table
        for day in sorted(by_day):
            decisions = []
            for e in by_day[day]:
                bars = intraday.get((e.symbol, day))
                if bars is None or len(bars) <= CONFIRM_BARS + cfg["hold_minutes"] // 5:
                    continue
                dec = decide(e, bars, cfg)
                if dec is None:
                    continue
                # outcome of simply following the 30-minute direction for the holding period (for the table)
                exit_i = min(CONFIRM_BARS + cfg["hold_minutes"] // 5, len(bars) - 1)
                entry_px = float(bars.loc[CONFIRM_BARS + 1, "open"])
                raw = float(bars.loc[exit_i, "close"]) / entry_px - 1
                d0 = 1 if e.gap > 0 else -1
                stats_rows.append({"day": day, "gap_dir": d0, "confirmed": dec["confirmed"],
                                   "net": d0 * raw - 2 * slip if dec["confirmed"] else -d0 * raw - 2 * slip})
                if dec["confirmed"]:
                    decisions.append((abs(e.gap), e, dec, bars))
            # Take the biggest confirmed gaps first, up to max_positions
            day_pnl = 0.0
            for _, e, dec, bars in sorted(decisions, key=lambda x: -x[0])[: cfg["max_positions"]]:
                d = 1 if dec["side"] == "long" else -1
                entry = float(bars.loc[CONFIRM_BARS + 1, "open"]) * (1 + slip * d)
                stop = dec["stop"]
                risk = abs(entry - stop)
                if risk <= 0 or (stop - entry) * d >= 0:
                    continue
                qty = math.floor(min(equity * cfg["risk_per_trade_pct"] / risk,
                                     equity * cfg["max_position_pct"] / entry))
                if qty < 1:
                    continue
                last = min(CONFIRM_BARS + cfg["hold_minutes"] // 5, len(bars) - 1)
                exit_px, reason, exit_i = float(bars.loc[last, "close"]), "time exit", last
                for i in range(CONFIRM_BARS + 1, last + 1):
                    hi, lo, op = float(bars.loc[i, "high"]), float(bars.loc[i, "low"]), float(bars.loc[i, "open"])
                    if (d > 0 and lo <= stop) or (d < 0 and hi >= stop):
                        exit_px = min(stop, op) if d > 0 else max(stop, op)
                        reason, exit_i = "stop", i
                        break
                fill = exit_px * (1 - slip * d)
                pnl = (fill - entry) * qty * d
                day_pnl += pnl
                # bar timestamps are bar starts: a time exit happens at the bar's close (5 minutes later)
                exit_ts = bars.loc[exit_i, "ts"] + (pd.Timedelta(minutes=5) if reason == "time exit" else pd.Timedelta(0))
                res.trades.append({"symbol": e.symbol, "side": dec["side"], "entry_time": bars.loc[CONFIRM_BARS + 1, "ts"],
                                   "exit_time": exit_ts, "qty": qty, "entry": entry, "exit": fill,
                                   "pnl": pnl, "pnl_pct": (fill / entry - 1) * d, "exit_reason": reason,
                                   "partial": False, "score": e.gap, "setup": "gap_confirmed"})
            equity += day_pnl
            res.equity_curve.append((datetime.combine(day, time(16, 0)), equity))
        if spy is not None and len(spy):
            sp = spy.copy()
            idx = sp.index.tz_convert(NY) if getattr(sp.index, "tz", None) is not None else sp.index
            sp.index = pd.DatetimeIndex([pd.Timestamp(x.date()) for x in idx])
            sp = sp[(sp.index.date >= start) & (sp.index.date <= end)]
            if len(sp):
                res.benchmark_return = float(sp["close"].iloc[-1] / sp["open"].iloc[0] - 1)
        res.stats = Backtester._stats(res, start_equity)
        res.halves = Backtester._halves(res, sorted(by_day))
        res.edge_table = event_table(stats_rows, sorted(by_day))
        res.allow_short = bool(cfg.get("allow_short"))
        res.notes.append(f"{len(events)} gaps found, {sum(1 for r in stats_rows if r['confirmed'])} confirmed "
                         f"after 30 minutes, {len(res.trades)} traded.")
        return res


def event_table(rows: list[dict], days: list[date]) -> list[dict]:
    """Every gap, grouped by what happened in the first 30 minutes: average 60-minute result after costs
    of trading in the direction of the 30-minute move, in each half of the period."""
    if not rows or not days:
        return []
    mid = days[len(days) // 2]
    df = pd.DataFrame(rows)
    df["half"] = np.where(df["day"] < mid, 1, 2)
    out = []
    for gap_dir, gname in ((1, "gap up"), (-1, "gap down")):
        for conf, cname in ((True, "kept going (confirmed)"), (False, "reversed")):
            g = df[(df["gap_dir"] == gap_dir) & (df["confirmed"] == conf)]
            if g.empty:
                continue
            side = ("long" if gap_dir > 0 else "short") if conf else ("short" if gap_dir > 0 else "long")
            row = {"band": f"{gname}, {cname}", "side": side}
            for h in (1, 2):
                x = g[g["half"] == h]["net"]
                row[f"n{h}"], row[f"bps{h}"] = int(len(x)), float(x.mean() * 1e4) if len(x) else 0.0
            row["consistent"] = row["n1"] >= 30 and row["n2"] >= 30 and row["bps1"] > 0 and row["bps2"] > 0
            out.append(row)
    return out
