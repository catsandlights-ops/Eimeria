"""Crash protection: a slow trend rule on the S&P 500.

Rule (checked once a month, at the last close of each month):
  S&P 500 above its 10-month average  -> "invested"  (normal)
  S&P 500 below its 10-month average  -> "defensive"
In defensive mode, "step aside" sells the S&P 500 fund into cash and pauses new swing buys;
"short" also moves that money into an inverse S&P 500 fund (rises when the market falls).

Tested on 1994-2026 (see README): stepping aside cut the worst drop from about -50% to about
-20% and lost only 4-8% in the dot-com, 2008 and COVID crashes, but lagged a strong bull market
(2017-2026). Shorting gained in crashes but lost more in the rebounds: worse overall than holding
the index in every period tested. Nothing here predicts a crash; the rule reacts after prices
have already started falling.
"""
from __future__ import annotations

import pandas as pd

MODES = ("off", "step_aside", "short")


def month_end_closes(daily: pd.DataFrame) -> pd.Series:
    close = daily["close"].astype(float)
    idx = close.index.tz_convert(None) if getattr(close.index, "tz", None) is not None else close.index
    close = pd.Series(close.values, index=idx)
    return close.groupby(close.index.to_period("M")).tail(1)


def state_series(daily: pd.DataFrame, months: int = 10) -> pd.Series:
    """'invested' / 'defensive' for every trading day, using only month-ends that are complete.
    A month-end signal applies from the NEXT trading day."""
    close = daily["close"].astype(float)
    idx = close.index.tz_convert(None) if getattr(close.index, "tz", None) is not None else close.index
    close = pd.Series(close.values, index=idx)
    me = month_end_closes(daily)
    avg = me.rolling(months).mean()
    sig = pd.Series(pd.NA, index=close.index, dtype="object")
    for day, c, a in zip(me.index, me.values, avg.values):
        if pd.isna(a):
            continue
        sig[day] = "invested" if c > a else "defensive"
    # The signal set at a month-end close takes effect the next trading day
    return sig.ffill().shift(1).fillna("invested")


def current_state(daily: pd.DataFrame, months: int = 10, today=None) -> dict:
    """State for trading today. Only uses months that have finished."""
    close = daily["close"].astype(float)
    me = month_end_closes(daily)
    if today is not None:
        cur = pd.Timestamp(today).to_period("M")
        me = me[me.index.to_period("M") < cur]       # ignore the month still in progress
    if len(me) < months:
        return {"state": "invested", "detail": "Not enough history for the 10-month rule."}
    avg = float(me.tail(months).mean())
    last = float(me.iloc[-1])
    state = "invested" if last > avg else "defensive"
    return {"state": state, "detail": f"S&P 500 month-end close {last:.2f} vs 10-month average {avg:.2f} "
                                      f"({last / avg - 1:+.1%}); latest price {float(close.iloc[-1]):.2f}"}
