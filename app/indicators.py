"""Technical indicators turned into signal scores between -1 (bearish) and +1 (bullish).

`compute_series` computes everything for every bar at once. The live bot uses the
last row; the backtester uses every row. Same code, so backtests match live behaviour.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY = ZoneInfo("America/New_York")

# Signals that feed the combined score. The learner adjusts how much each one counts.
# (Insider buying is used to pick which stocks to watch, not as an intraday signal.)
SIGNALS = ["trend", "vwap", "rsi_reversal", "breakout", "gap_momentum", "news"]
DEFAULT_WEIGHTS = {s: 1.0 for s in SIGNALS}
TECH_SIGNALS = [s for s in SIGNALS if s != "news"]


def _to_ny(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return index.tz_convert(NY) if index.tz is not None else index.tz_localize("UTC").tz_convert(NY)


def compute_series(bars: pd.DataFrame, daily: pd.DataFrame | None) -> pd.DataFrame:
    """All indicators + signal scores for every 5-minute bar. Index is New York time."""
    df = bars[["open", "high", "low", "close", "volume"]].astype(float).copy()
    df.index = _to_ny(df.index)
    df = df.sort_index()
    close = df["close"]

    df["ema9"] = close.ewm(span=9, adjust=False).mean()
    df["ema21"] = close.ewm(span=21, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = (100 - 100 / (1 + gain / loss.replace(0, np.nan))).fillna(50)
    prev = close.shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14, min_periods=1).mean()
    df["atr"] = df["atr"].where(df["atr"] > 0, close * 0.005)

    # Regular-session fields (9:30-16:00), reset each day
    t = df.index.hour * 60 + df.index.minute
    df["in_session"] = (t >= 570) & (t < 960)
    df["date"] = df.index.date
    s = df[df["in_session"]]
    grp = s.groupby("date")
    typical = (s["high"] + s["low"] + s["close"]) / 3
    df["vwap"] = ((typical * s["volume"]).groupby(s["date"]).cumsum() /
                  s["volume"].groupby(s["date"]).cumsum().replace(0, np.nan))
    df["bar_no"] = grp.cumcount()
    first3 = s[grp.cumcount() < 3].groupby("date").agg(or_high=("high", "max"), or_low=("low", "min"))
    df = df.join(first3, on="date")
    df["session_volume"] = s["volume"].groupby(s["date"]).cumsum()

    # Yesterday's close and average daily volume
    df["prev_close"] = np.nan
    df["avg_volume"] = np.nan
    if daily is not None and len(daily):
        d = daily[["close", "volume"]].astype(float).copy()
        d.index = _to_ny(d.index)
        d_dates = np.array([ts.date() for ts in d.index])
        for day in pd.unique(df["date"]):
            past = d[d_dates < day]
            if len(past):
                m = df["date"] == day
                df.loc[m, "prev_close"] = past["close"].iloc[-1]
                df.loc[m, "avg_volume"] = past["volume"].tail(20).mean()

    df["change_pct"] = (close / df["prev_close"] - 1).fillna(0.0)
    expected = df["avg_volume"] * np.minimum((df["bar_no"] + 1) * 5 / 390, 1.0)
    df["rel_volume"] = (df["session_volume"] / expected).replace([np.inf, -np.inf], np.nan).fillna(1.0)

    a = df["atr"]
    df["s_trend"] = np.tanh((df["ema9"] - df["ema21"]) / a)
    df["s_vwap"] = np.tanh((close - df["vwap"]) / a).fillna(0.0)
    r = df["rsi"]
    df["s_rsi_reversal"] = np.where(r < 30, (30 - r) / 30, np.where(r > 70, -(r - 70) / 30, 0.0))
    brk = np.where(close > df["or_high"], 1.0, np.where(close < df["or_low"], -1.0, 0.0))
    df["s_breakout"] = np.where(df["bar_no"] >= 3, brk, 0.0)
    df["s_gap_momentum"] = np.tanh(df["change_pct"] / 0.02)
    for sig in TECH_SIGNALS:
        df[f"s_{sig}"] = df[f"s_{sig}"].clip(-1, 1)
    return df


def row_to_analysis(row: pd.Series) -> dict:
    return {
        "price": float(row["close"]), "vwap": float(row["vwap"]), "ema9": float(row["ema9"]),
        "ema21": float(row["ema21"]), "rsi": float(row["rsi"]), "atr": float(row["atr"]),
        "or_high": float(row["or_high"]), "or_low": float(row["or_low"]),
        "prev_close": None if pd.isna(row["prev_close"]) else float(row["prev_close"]),
        "change_pct": float(row["change_pct"]), "rel_volume": float(row["rel_volume"]),
        "scores": {sig: float(row[f"s_{sig}"]) for sig in TECH_SIGNALS},
    }


def analyze(bars: pd.DataFrame | None, daily: pd.DataFrame | None) -> dict | None:
    """Indicators + signal scores for the latest bar. None if not enough data."""
    if bars is None or len(bars) < 6:
        return None
    df = compute_series(bars, daily)
    last = df.iloc[-1]
    if not last["in_session"] or last["bar_no"] < 2 or pd.isna(last["vwap"]):
        return None
    return row_to_analysis(last)


def composite(scores: dict[str, float], weights: dict[str, float], rel_volume: float = 1.0) -> float:
    """Weighted average of signal scores, boosted when volume is unusually high."""
    total_w = sum(weights.get(k, 1.0) for k in scores) or 1.0
    raw = sum(v * weights.get(k, 1.0) for k, v in scores.items()) / total_w
    volume_factor = max(0.6, min(1.5, 0.75 + rel_volume / 4))
    return max(-1.0, min(1.0, raw * volume_factor))


# ------------------------------------------------------------------ market regime
def market_regime(spy: pd.DataFrame | None, spy_daily: pd.DataFrame | None,
                  fear: pd.DataFrame | None = None, fear_daily: pd.DataFrame | None = None) -> dict:
    """How is the whole market doing? Uses SPY (S&P 500 fund) and VIXY (a fund that tracks
    the VIX "fear index"; the VIX itself isn't available on the free data plan).

    Returns {'state': 'risk_on' | 'neutral' | 'risk_off' | 'panic', 'detail': str, ...}.
    """
    a = analyze(spy, spy_daily) if spy is not None else None
    if not a:
        return {"state": "neutral", "detail": "Market data unavailable", "spy_change": 0.0, "fear_change": 0.0}
    f = analyze(fear, fear_daily) if fear is not None else None
    fear_change = f["change_pct"] if f else 0.0
    return classify_regime(a["change_pct"], a["price"] > a["vwap"], a["scores"]["trend"], fear_change)


def classify_regime(spy_change: float, above_vwap: bool, trend: float, fear_change: float) -> dict:
    if spy_change <= -0.02 or fear_change >= 0.12:
        state = "panic"
    elif spy_change <= -0.008 or (not above_vwap and trend < -0.3) or fear_change >= 0.06:
        state = "risk_off"
    elif spy_change >= 0.003 and above_vwap and trend > 0:
        state = "risk_on"
    else:
        state = "neutral"
    detail = (f"S&P 500 {spy_change:+.2%} today, {'above' if above_vwap else 'below'} VWAP; "
              f"fear index fund {fear_change:+.1%}")
    return {"state": state, "detail": detail, "spy_change": spy_change, "fear_change": fear_change}


def regime_adjustment(regime: str, side: str) -> tuple[float, float, bool]:
    """(extra score needed, extra confidence needed, blocked) for a trade direction in a regime."""
    if side == "long":
        return {"risk_on": (0.0, 0.0, False), "neutral": (0.0, 0.0, False),
                "risk_off": (0.15, 0.10, False), "panic": (0.0, 0.0, True)}[regime]
    return {"risk_on": (0.15, 0.10, False), "neutral": (0.05, 0.0, False),
            "risk_off": (0.0, 0.0, False), "panic": (0.0, 0.0, False)}[regime]
