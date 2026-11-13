"""Shared tools for the fixed ("pre-registered") strategy tests.

Every fixed test follows the same protocol, decided before any results were seen:
  * Rules are constants in code, not settings, so they can't be tuned until the past looks good.
  * Development period: before 2024. Holdout: 2024 onward (data the ideas were never fitted to).
  * PASS needs a positive average after costs in BOTH periods, enough samples in each, and a t-statistic of
    at least 2 over the whole test. Samples that overlap in time aren't independent, so the t-statistic is
    computed on calendar-month averages, not on single trades.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

HOLDOUT_START = date(2024, 1, 1)
MIN_T = 2.0


# ------------------------------------------------------------------ statistics
def monthly_t(values, days) -> float:
    """t-statistic of the mean, using one average per calendar month (overlapping trades aren't independent)."""
    s = pd.Series(np.asarray(values, float), index=pd.DatetimeIndex(pd.to_datetime(list(days))))
    if len(s) < 3:
        return 0.0
    m = s.groupby(s.index.to_period("M")).mean()
    if len(m) < 3 or m.std(ddof=1) == 0:
        return 0.0
    return float(m.mean() / m.std(ddof=1) * math.sqrt(len(m)))


def split_row(name: str, side: str, values, days, min_n: int, scale: float = 1e4) -> dict:
    """An edge-table row: average (in bps) and count before 2024 and from 2024 on."""
    v = np.asarray(values, float)
    d = np.array([x < HOLDOUT_START for x in days], bool) if len(v) else np.zeros(0, bool)
    a, b = v[d], v[~d]
    r = {"band": name, "side": side, "n1": int(len(a)), "bps1": float(a.mean() * scale) if len(a) else 0.0,
         "n2": int(len(b)), "bps2": float(b.mean() * scale) if len(b) else 0.0}
    r["consistent"] = r["n1"] >= min_n and r["n2"] >= min_n and r["bps1"] > 0 and r["bps2"] > 0
    return r


def verdict_line(name: str, values, days, min_n: int, unit: str = "bps", scale: float = 1e4) -> tuple[str, bool]:
    """The pre-registered PASS/FAIL sentence for one rule."""
    v = np.asarray(values, float)
    days = list(days)
    dev = np.array([x < HOLDOUT_START for x in days], bool) if len(v) else np.zeros(0, bool)
    n1, n2 = int(dev.sum()), int((~dev).sum())
    if n1 < min_n or n2 < min_n:
        return (f"{name}: INCOMPLETE ({n1} samples before 2024, {n2} from 2024 on; needs {min_n} in each). "
                "Test 2019-01-01 to yesterday."), False
    m1, m2, t = v[dev].mean() * scale, v[~dev].mean() * scale, monthly_t(v, days)
    if m1 > 0 and m2 > 0 and t >= MIN_T:
        return f"{name}: PASS ({m1:+.1f} {unit} before 2024, {m2:+.1f} {unit} from 2024 on, t = {t:.1f}).", True
    why = []
    if m1 <= 0:
        why.append(f"not positive before 2024 ({m1:+.1f} {unit})")
    if m2 <= 0:
        why.append(f"not positive from 2024 on ({m2:+.1f} {unit})")
    if 0 <= t < MIN_T and m1 > 0 and m2 > 0:
        why.append(f"too weak to tell from luck (t = {t:.1f}, needs {MIN_T:.0f})")
    elif not why:
        why.append(f"t = {t:.1f}")
    return f"{name}: FAIL ({'; '.join(why)}).", False


def headline(lines: list[tuple[str, bool]]) -> str:
    verdicts = " ".join(text for text, _ in lines)
    if any(ok for _, ok in lines):
        return ("PRE-REGISTERED TEST: " + verdicts + " A PASS is worth paper trading; a FAIL is dropped, not tuned.")
    return "PRE-REGISTERED TEST: " + verdicts + " By the rules set in advance, a FAIL is dropped, not tuned."


def dev_holdout_periods(trades: list[dict], start: date) -> list[dict]:
    """Portfolio results before 2024 and from 2024 on (replaces the usual first/second half)."""
    out = []
    for name, sel, frm in (("before 2024 (development)", lambda x: x < HOLDOUT_START, start),
                           ("2024 onward (holdout)", lambda x: x >= HOLDOUT_START, max(start, HOLDOUT_START))):
        t = [x for x in trades if sel(_as_date(x["entry_time"]))]
        if not t:
            continue
        wins = sum(x["pnl"] for x in t if x["pnl"] > 0)
        losses = -sum(x["pnl"] for x in t if x["pnl"] <= 0)
        out.append({"name": name, "trades": len(t), "pnl": wins - losses, "from": frm,
                    "profit_factor": wins / losses if losses > 0 else (float("inf") if wins else 0.0)})
    return out


def _as_date(x) -> date:
    return x.date() if isinstance(x, (datetime, pd.Timestamp)) else x


# ------------------------------------------------------------------ event study
def round_trip_cost(dollar_volume: float) -> float:
    """Both sides together, the same estimate the swing backtest uses per side (0.05% liquid, 0.2% otherwise)."""
    return 2 * (0.0005 if (dollar_volume or 0) >= 20e6 else 0.002)


def event_returns(events, px: dict, spy: pd.DataFrame, hold_days: int) -> pd.DataFrame:
    """For each event: buy at the next day's open, sell at the close `hold_days` trading days later.
    Returns symbol, day (signal date), ret, spy_ret, excess, net (excess after costs).
    No stop-loss: this measures the signal itself; the portfolio simulation adds stops and position limits."""
    rows = []
    sp = _norm_daily(spy) if spy is not None else None
    for e in events:
        d = px.get(e.symbol)
        if d is None:
            continue
        j = d.index.searchsorted(pd.Timestamp(e.signal_date), side="right")
        if j >= len(d):
            continue
        k = min(j + hold_days - 1, len(d) - 1)
        if k - j < hold_days // 2:                     # not enough data after the signal (end of the test)
            continue
        entry, exit_ = float(d["open"].iloc[j]), float(d["close"].iloc[k])
        if not (entry > 0 and exit_ > 0):
            continue
        ret = exit_ / entry - 1
        spy_ret = 0.0
        if sp is not None and len(sp):
            a, b = sp["open"].asof(d.index[j]), sp["close"].asof(d.index[k])
            if a > 0 and b > 0:
                spy_ret = float(b / a - 1)
        dv = float(d["dollar_vol"].iloc[j - 1]) if "dollar_vol" in d and j > 0 else 0.0
        rows.append({"symbol": e.symbol, "day": e.signal_date, "setup": e.setup, "ret": ret, "spy_ret": spy_ret,
                     "excess": ret - spy_ret, "net": ret - spy_ret - round_trip_cost(dv)})
    return pd.DataFrame(rows, columns=["symbol", "day", "setup", "ret", "spy_ret", "excess", "net"])


def _norm_daily(df: pd.DataFrame) -> pd.DataFrame:
    from .indicators import NY
    d = df[["open", "high", "low", "close", "volume"]].astype(float).copy()
    idx = d.index.tz_convert(NY) if getattr(d.index, "tz", None) is not None else d.index
    d.index = pd.DatetimeIndex([pd.Timestamp(x.date()) for x in idx])
    return d[~d.index.duplicated(keep="last")].sort_index()


# ------------------------------------------------------------------ data
def liquid_universe(broker, settings: dict, min_dollar_volume: float, emit=lambda m: None) -> list[str]:
    """Tradable US stocks/ETFs whose average daily $ volume over the last ~3 months is at least the minimum.
    Note: this is TODAY's list, so companies that were delisted are missing (survivorship bias)."""
    feed = settings["backtest"].get("data_feed", "sip")
    emit("Getting the list of tradable US stocks...")
    syms = sorted(broker.tradable_symbols())
    end = date.today() - timedelta(days=1)
    keep = []
    for i in range(0, len(syms), 200):
        emit(f"Checking which stocks trade enough: {i}/{len(syms)}...")
        for s, df in broker.daily_range(syms[i:i + 200], end - timedelta(days=95), end, feed=feed).items():
            if df is not None and len(df) >= 20 and float((df["close"] * df["volume"]).mean()) >= min_dollar_volume:
                keep.append(s)
    return keep


def load_daily(broker, settings: dict, syms: list[str], start: date, end: date, emit=lambda m: None,
               label: str = "Daily prices", adjustment: str = "all") -> dict:
    feed = settings["backtest"].get("data_feed", "sip")
    out = {}
    syms = sorted(set(syms))
    for i in range(0, len(syms), 100):
        emit(f"{label}: {i}/{len(syms)} stocks...")
        out.update(broker.daily_range(syms[i:i + 100], start, end, feed=feed, adjustment=adjustment))
    return out


def liquid_ok_at(d: pd.DataFrame, day: date, cfg: dict) -> bool:
    """Price and liquidity filter (swing settings) on the last trading day on/before `day`, with enough history."""
    from .swing import liquid_ok
    i = d.index.searchsorted(pd.Timestamp(day), side="right") - 1
    return i >= 50 and bool(liquid_ok(d, i, cfg))
