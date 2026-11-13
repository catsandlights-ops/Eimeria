"""Swing trading: hold for days to weeks instead of minutes.

Why: trading costs matter far less over weeks than over an hour, and the bot's best data
(insider buying, big news moves) is documented to play out over weeks.

Setups (detected the same way in live trading and in backtests):
  insider_buy  Company officers/directors bought >= $X of their own stock on the open market.
  news_gap     The stock gapped up >= 4% on >= 3x normal volume (usually earnings/news) and
               HELD the gain into the close.
Both can require the stock to be in an uptrend (above its 50-day average), because the research
found that buying falling stocks after these events did badly.

Trades: enter at the next day's open, stop-loss at 4 x the daily ATR below entry, exit after
N trading days (or at the stop). Not closed at the end of each day.

Research note (2019-2026, SEC insider data + daily prices; see README): insider buys of $1M+ in
uptrending stocks beat small caps over 20-60 days in both halves of the period; most profit came
from a few big winners, so many positions and a wide stop work better. News gaps did not work.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .backtest import BTResult, Backtester
from .indicators import NY
from .market_timing import state_series


@dataclass
class Event:
    symbol: str
    signal_date: date        # information fully public by the END of this day
    setup: str
    strength: float          # for ranking when there are more events than free slots
    detail: str = ""
    extra: dict = field(default_factory=dict)


def _daily(df: pd.DataFrame) -> pd.DataFrame:
    d = df[["open", "high", "low", "close", "volume"]].astype(float).copy()
    idx = d.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(NY)
    d.index = pd.DatetimeIndex([pd.Timestamp(x.date()) for x in idx])
    d = d[~d.index.duplicated(keep="last")].sort_index()
    prev = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - prev).abs(), (d["low"] - prev).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14, min_periods=10).mean()
    d["ma50"] = d["close"].rolling(50, min_periods=40).mean()
    d["ma200"] = d["close"].rolling(200, min_periods=150).mean()
    d["avg_vol"] = d["volume"].shift(1).rolling(20, min_periods=15).mean()
    d["dollar_vol"] = (d["close"] * d["volume"]).shift(1).rolling(20, min_periods=15).mean()
    return d


def prepare(daily: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {s: _daily(df) for s, df in daily.items() if df is not None and len(df) >= 60}


def trend_ok(d: pd.DataFrame, i: int, cfg: dict) -> bool:
    if not cfg.get("require_uptrend", True):
        return True
    ma = d["ma50"].iloc[i]
    return bool(not pd.isna(ma) and d["close"].iloc[i] > ma)


def liquid_ok(d: pd.DataFrame, i: int, cfg: dict) -> bool:
    return d["close"].iloc[i] >= cfg["min_price"] and (d["dollar_vol"].iloc[i] or 0) >= cfg["min_dollar_volume"]


# Common stock-split ratios. Swing backtest prices are split-adjusted, but the SEC filing shows the price paid
# on the day, so after e.g. a later 4-for-1 split the two differ by a factor of 4.
SPLIT_FACTORS = (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 100)


def price_matches(filed_price: float, market_price: float) -> bool:
    """True if a filing's price matches the market (allowing for later splits); False for typos or a ticker
    that now belongs to a different company."""
    if not (filed_price > 0 and market_price > 0):
        return False
    r = filed_price / market_price
    if abs(r - 1) <= 0.5:
        return True
    return any(abs(r / f - 1) <= 0.10 or abs(r * f - 1) <= 0.10 for f in SPLIT_FACTORS[1:])


# ------------------------------------------------------------------ setup detection
def news_gap_events(px: dict[str, pd.DataFrame], cfg: dict, start: date, end: date) -> list[Event]:
    g = cfg["news_gap"]
    out = []
    for sym, d in px.items():
        prev = d["close"].shift(1)
        gap = d["open"] / prev - 1
        held = d["close"] >= d["open"]
        rng = (d["high"] - d["low"]).replace(0, np.nan)
        close_pos = (d["close"] - d["low"]) / rng
        hit = (gap >= g["min_gap"]) & (d["volume"] >= g["min_volume_ratio"] * d["avg_vol"]) & held & \
              (close_pos >= g["min_close_position"])
        for i in np.flatnonzero(hit.values):
            day = d.index[i].date()
            if not (start <= day <= end) or not liquid_ok(d, i, cfg) or not trend_ok(d, i, cfg):
                continue
            out.append(Event(sym, day, "news_gap", float(gap.iloc[i]),
                             f"gapped {gap.iloc[i]:+.1%} on {d['volume'].iloc[i] / d['avg_vol'].iloc[i]:.1f}x volume "
                             f"and held into the close"))
    return out


def insider_events(purchases: pd.DataFrame, px: dict[str, pd.DataFrame], cfg: dict,
                   start: date, end: date) -> list[Event]:
    """purchases: rows with symbol, filed (date), owner_id, title, value, price (see sec_history)."""
    c = cfg["insider"]
    if purchases is None or purchases.empty:
        return []
    p = purchases[purchases["symbol"].isin(px.keys())].copy()
    if p.empty:
        return []
    # Drop individual filings whose price doesn't match the real market price
    # (typos, or a ticker that now belongs to a different company)
    def market_close(row):
        d = px[row["symbol"]]
        i = d.index.searchsorted(pd.Timestamp(row["filed"]), side="right") - 1
        return d["close"].iloc[i] if i >= 0 else np.nan
    mkt = p.apply(market_close, axis=1)
    p = p[[price_matches(a, b) for a, b in zip(p["price"], mkt)]]
    p["ceo_cfo"] = p["title"].fillna("").str.upper().str.contains("CEO|CHIEF EXECUTIVE|CFO|CHIEF FINANCIAL|PRESIDENT")
    by_symbol = {sym: g for sym, g in p.groupby("symbol")}
    out = []
    for (sym, filed), grp in p.groupby(["symbol", "filed"]):
        if not (start <= filed <= end):
            continue
        sp = by_symbol[sym]
        window = sp[(sp["filed"] <= filed) & (sp["filed"] > filed - timedelta(days=14))]
        total, insiders = float(window["value"].sum()), int(window["owner_id"].nunique())
        if float(grp["value"].sum()) <= 0 or total < c["min_value"] or insiders < c["min_insiders"]:
            continue
        if c.get("require_ceo_cfo") and not window["ceo_cfo"].any():
            continue
        d = px[sym]
        i = d.index.searchsorted(pd.Timestamp(filed), side="right") - 1   # last trading day on/before filing
        if i < 50:
            continue
        if not liquid_ok(d, i, cfg) or not trend_ok(d, i, cfg):
            continue
        who = ", ".join(sorted(set(window["title"].fillna("insider").str.slice(0, 25))))[:120]
        out.append(Event(sym, filed, "insider_buy", math.log10(max(total, 1)) + 0.5 * insiders,
                         f"{insiders} insider(s) bought ${total:,.0f} in 14 days ({who})"))
    return out


# ------------------------------------------------------------------ backtest
class SwingBacktester:
    """Daily portfolio simulation of the swing strategy.

    Options (settings["swing"]):
      trail_atr_mult      0 = fixed exit after hold_days. >0 = the stop trails the highest close by this
                          many ATRs, and hold_days becomes the MAXIMUM hold ("let winners run").
      market_filter       no new buys while the S&P 500 (SPY) is below its 200-day average.
      idle_cash_in_index  money not in swing trades sits in an S&P 500 fund (SPY); it's sold as needed
                          to pay for new swing trades.
      crash_protection    "off" | "step_aside" | "short": 10-month trend rule on the S&P 500 (see
                          market_timing.py). Defensive = no new buys, index fund sold; "short" holds
                          an inverse S&P 500 fund (simulated: -1x daily S&P return, 0.89%/yr fee).
    """

    def __init__(self, settings: dict):
        self.s = settings
        self.cfg = settings["swing"]

    def run(self, events: list[Event], px: dict[str, pd.DataFrame], spy: pd.DataFrame | None,
            start: date, end: date) -> BTResult:
        cfg, res = self.cfg, BTResult()
        trail = float(cfg.get("trail_atr_mult") or 0)
        use_index = bool(cfg.get("idle_cash_in_index")) and spy is not None and len(spy) > 0
        use_filter = bool(cfg.get("market_filter")) and spy is not None and len(spy) > 0
        start_equity = cash = float(self.s["backtest"]["starting_equity"])
        sp = _daily(spy) if spy is not None and len(spy) else None
        crash = cfg.get("crash_protection", "off") if sp is not None else "off"
        regime = state_series(sp) if crash != "off" else None
        inverse_value = 0.0
        by_day: dict[pd.Timestamp, list[Event]] = {}
        for e in events:
            d = px.get(e.symbol)
            if d is None:
                continue
            j = d.index.searchsorted(pd.Timestamp(e.signal_date), side="right")   # next trading day
            if j < len(d):
                by_day.setdefault(d.index[j], []).append(e)
        days = sorted({t for d in px.values() for t in d.index if start <= t.date() <= end})
        if not days:
            res.notes.append("No price data in this range.")
            return res
        open_pos: dict[str, dict] = {}
        index_shares = 0.0
        res.equity_curve.append((datetime.combine(start, datetime.min.time()), cash))

        def cost(d, i):
            return 0.0005 if (d["dollar_vol"].iloc[i] or 0) >= 20e6 else 0.002   # per side

        def index_px(day, col):
            return float(sp[col].asof(day)) if sp is not None else 0.0

        def close(sym, price, day, reason):
            nonlocal cash
            p = open_pos.pop(sym)
            fill = price * (1 - p["cost"])
            cash += fill * p["qty"]
            res.trades.append({"symbol": sym, "side": "long", "entry_time": p["time"], "exit_time": day,
                               "qty": p["qty"], "entry": p["entry"], "exit": fill, "pnl": (fill - p["entry"]) * p["qty"],
                               "pnl_pct": fill / p["entry"] - 1, "exit_reason": reason, "partial": False,
                               "score": p["strength"], "setup": p["setup"]})

        last_equity = cash
        prev_day = None
        for day in days:
            # 0. Crash protection: switch between the index fund and cash/inverse fund at the open
            defensive = regime is not None and regime.asof(day) == "defensive"
            if regime is not None:
                o, c = index_px(day, "open"), index_px(day, "close")
                if inverse_value > 0 and prev_day is not None:              # inverse fund's overnight move
                    inverse_value *= 1 - (o / index_px(prev_day, "close") - 1)
                if defensive:
                    if index_shares > 0:
                        cash += index_shares * o * 0.9999
                        index_shares = 0.0
                    if crash == "short" and cash > 0.01 * last_equity:
                        amt = cash - 0.01 * last_equity
                        inverse_value += amt * 0.9999
                        cash -= amt
                elif inverse_value > 0:
                    cash += inverse_value * 0.9999
                    inverse_value = 0.0
                if inverse_value > 0:                                       # its move during today
                    inverse_value *= 1 - (c / o - 1) - 0.0089 / 252
            prev_day = day
            # 1. Entries at today's open (signals from earlier days)
            blocked = defensive
            if use_filter:
                k = sp.index.searchsorted(day) - 1          # yesterday's close: known before the open
                blocked = blocked or (k >= 0 and not pd.isna(sp["ma200"].iloc[k])
                                      and sp["close"].iloc[k] < sp["ma200"].iloc[k])
            cands = [] if blocked else sorted(by_day.get(day, []), key=lambda e: -e.strength)
            for e in cands:
                if len(open_pos) >= cfg["max_positions"] or e.symbol in open_pos:
                    continue
                d = px[e.symbol]
                i = d.index.get_loc(day)
                o, atr = d["open"].iloc[i], d["atr"].iloc[i - 1]
                if not (o > 0) or pd.isna(atr):
                    continue
                stop = o - cfg["stop_atr_mult"] * atr
                if stop <= 0:
                    continue
                c = cost(d, i - 1)
                entry = o * (1 + c)
                qty = math.floor(min(last_equity * cfg["risk_per_trade_pct"] / (entry - stop),
                                     last_equity * cfg["max_position_pct"] / entry))
                available = cash + (index_shares * index_px(day, "open") * 0.9999 if use_index else 0.0)
                qty = min(qty, math.floor(max(0.0, available) / entry))       # no margin for swing
                if qty < 1:
                    continue
                need = qty * entry - cash
                if need > 0 and use_index:                                       # sell index fund to pay
                    ipx = index_px(day, "open") * 0.9999
                    sell = min(index_shares, need / ipx)
                    index_shares -= sell
                    cash += sell * ipx
                cash -= qty * entry
                open_pos[e.symbol] = {"entry": entry, "stop": stop, "qty": qty, "time": day, "held": 0,
                                      "cost": c, "strength": e.strength, "setup": e.setup, "atr": atr,
                                      "best": o}
            # 2. Exits during/at the end of today
            for sym in list(open_pos):
                d = px[sym]
                if day not in d.index:
                    continue
                i = d.index.get_loc(day)
                p = open_pos[sym]
                p["held"] += 1
                if d["low"].iloc[i] <= p["stop"]:
                    reason = "trailing stop" if p["stop"] > p["entry"] else "stop"
                    close(sym, min(p["stop"], d["open"].iloc[i]) if p["held"] > 1 else p["stop"], day, reason)
                elif p["held"] >= cfg["hold_days"]:
                    close(sym, d["close"].iloc[i], day, "time exit")
                elif trail > 0:
                    p["best"] = max(p["best"], d["close"].iloc[i])
                    p["stop"] = max(p["stop"], p["best"] - trail * p["atr"])
            # 3. Idle cash into the index fund at the close (keep 1% as cash)
            if crash == "short" and defensive and cash > 0.02 * last_equity:   # freed cash -> inverse
                amt = cash - 0.01 * last_equity
                inverse_value += amt * 0.9999
                cash -= amt
            if use_index and not defensive:
                ipx = index_px(day, "close")
                spare = cash - 0.01 * last_equity
                if spare > 0 and ipx > 0:
                    index_shares += spare / (ipx * 1.0001)
                    cash -= spare
            # 4. Mark to market
            last_equity = cash + inverse_value + index_shares * index_px(day, "close") + \
                sum(px[s]["close"].asof(day) * p["qty"] for s, p in open_pos.items())
            res.equity_curve.append((day, float(last_equity)))
        for sym in list(open_pos):
            close(sym, float(px[sym]["close"].asof(days[-1])), days[-1], "end of test")
        if crash != "off":
            res.notes.append(f"Crash protection: {crash.replace('_', ' ')} (10-month rule).")
        if sp is not None:
            s = sp[(sp.index.date >= start) & (sp.index.date <= end)]
            if len(s):
                res.benchmark_return = float(s["close"].iloc[-1] / s["open"].iloc[0] - 1)
        res.stats = Backtester._stats(res, start_equity)
        res.halves = Backtester._halves(res, sorted({d.date() for d in days}))
        res.allow_short = False
        if use_index:
            res.notes.append("Idle cash was held in an S&P 500 fund (SPY); total return includes it.")
        return res


def setup_breakdown(res: BTResult) -> list[dict]:
    """Results per setup (insider_buy / news_gap)."""
    out = []
    for setup in sorted({t.get("setup") for t in res.trades}):
        t = [x for x in res.trades if x.get("setup") == setup]
        wins = sum(x["pnl"] for x in t if x["pnl"] > 0)
        losses = -sum(x["pnl"] for x in t if x["pnl"] <= 0)
        out.append({"setup": setup, "trades": len(t), "pnl": wins - losses,
                    "win_rate": sum(1 for x in t if x["pnl"] > 0) / len(t),
                    "avg_pct": sum(x["pnl_pct"] for x in t) / len(t),
                    "profit_factor": wins / losses if losses > 0 else float("inf")})
    return out
