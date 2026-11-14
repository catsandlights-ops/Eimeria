"""Four strategy tests with rules fixed in advance (see research.py for the protocol).

  insider_refinements  Do "cluster" buys (3+ insiders) or CEO/CFO buys beat the market? (swing, 40 days)
  earnings_drift       After a big earnings beat, does the stock keep outperforming for 60 trading days?
  momentum             Each month, hold the 50 biggest winners of the past year (skipping the last month).
  trend_following      Hold US stocks, foreign stocks, bonds, gold and real estate funds only while each is
                       above its 10-month average; otherwise that slice sits in a cash-like fund.

Only backtests: live trading would be added only for a test that PASSES.
"""
from __future__ import annotations

import copy
import math
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from . import config
from .backtest import Backtester, BTResult
from .research import (HOLDOUT_START, _norm_daily, dev_holdout_periods, event_returns, headline, history_universe,
                       liquid_ok_at, load_daily, split_row, universe_note, verdict_line)
from .swing import Event, SwingBacktester, insider_events, prepare


def _sec_contact(settings: dict) -> str:
    ua = settings["smart_money"].get("sec_user_agent", "")
    if "@" not in ua:
        raise ValueError("This test uses SEC data: put your name and email in Settings > 'SEC contact' first.")
    return ua


def _portfolio(settings: dict, events: list[Event], px: dict, spy, start: date, end: date, hold_days: int) -> BTResult:
    """Realistic portfolio simulation (8 positions, 0.5% risk each, 4x ATR stop), the pure strategy only."""
    s = copy.deepcopy(settings)
    s["swing"].update(hold_days=hold_days, idle_cash_in_index=False, market_filter=False, crash_protection="off",
                      trail_atr_mult=0)
    res = SwingBacktester(s).run(events, px, spy, start, end)
    res.halves = dev_holdout_periods(res.trades, start)
    return res


# ------------------------------------------------------------------ 1. insider refinements
INSIDER_HOLD = 40
INSIDER_VARIANTS = {
    # name: (setup id, minimum $ in 14 days, minimum different insiders, CEO/CFO required)
    "Cluster buy: 3+ insiders, $100k+ in 14 days": ("insider_cluster", 100_000, 3, False),
    "CEO or CFO bought $100k+": ("insider_ceo_cfo", 100_000, 1, True),
}


def run_insider_refinements(broker, settings: dict, start: date, end: date, emit=lambda m: None) -> BTResult:
    from .sec_history import insider_history
    ua = _sec_contact(settings)
    purchases = insider_history(start - timedelta(days=20), end, config.BASE_DIR / "cache", ua, emit)
    if purchases is None or purchases.empty:
        raise ValueError("No SEC insider records could be downloaded.")
    big = purchases.groupby("symbol")["value"].sum()
    universe = sorted(set(big[big >= 100_000].index) | {"SPY"})
    daily = load_daily(broker, settings, universe, start - timedelta(days=320), end, emit,
                       adjustment="split")                 # comparable with the prices in SEC filings
    spy = daily.pop("SPY", None)
    emit("Finding insider buys...")
    px = prepare(daily)
    base_cfg = settings["swing"]
    variants = {"Current default: $1M+ by insiders": ("insider_buy", base_cfg["insider"]["min_value"], 1, False)}
    variants.update(INSIDER_VARIANTS)
    found, study = {}, {}
    for name, (setup, min_value, min_insiders, ceo) in variants.items():
        cfg = copy.deepcopy(base_cfg)
        cfg["require_uptrend"] = True
        cfg["insider"] = {"min_value": min_value, "min_insiders": min_insiders, "require_ceo_cfo": ceo}
        ev = insider_events(purchases, px, cfg, start, end)
        for e in ev:
            e.setup = setup
        found[name] = ev
        study[name] = event_returns(ev, px, spy, INSIDER_HOLD)
    tested = list(INSIDER_VARIANTS)
    traded = {(e.symbol, e.signal_date): e for n in tested for e in found[n]}
    res = _portfolio(settings, list(traded.values()), px, spy, start, end, INSIDER_HOLD)
    res.edge_table = []
    for name in variants:
        df = study[name]
        label = name + (" (THE TEST)" if name in tested else " (for comparison)")
        res.edge_table.append(split_row(label, "long", df["net"], df["day"], 30))
    res.headline = headline([verdict_line(n, study[n]["net"], study[n]["day"], 30) for n in tested])
    res.notes.append(f"Each row: average result of buying at the next open and selling {INSIDER_HOLD} trading days "
                     "later, minus the S&P 500 over the same days, after costs (in bps; 100 bps = 1%). Stocks must "
                     "be above their 50-day average, $5+, with $5M+ traded a day. The portfolio (cards and chart) "
                     "trades both new rules together with stops and at most 8 positions.")
    res.notes.append("Caution: these rules come from published research, but the same SEC data (2019-2026) was "
                     "used to choose the current default insider rule, so treat a PASS from 2024 on as the "
                     "stronger evidence.")
    return res


# ------------------------------------------------------------------ 2. earnings drift
EARNINGS_HOLD = 60
SUE_MIN = 2.0


def run_earnings_drift(broker, settings: dict, start: date, end: date, emit=lambda m: None) -> BTResult:
    from .sec_earnings import eps_history, surprises, ticker_map
    ua = _sec_contact(settings)
    cache = config.BASE_DIR / "cache"
    _, info = history_universe(broker, settings, 5e6, start - timedelta(days=120), end, emit, keep_data=False)
    universe = info["symbols"]
    tmap = ticker_map(cache, ua)
    ciks = {t: tmap[t] for t in universe if t in tmap}
    eps = eps_history(ciks, cache / "eps", ua, emit)
    cands = []
    for sym, q in eps.items():
        su = surprises(q)
        for _, r in su.iterrows():
            if start <= r["filed"] <= end and abs(r["sue"]) >= SUE_MIN:
                cands.append((sym, r["filed"], float(r["sue"])))
    if not cands:
        raise ValueError("No earnings surprises found in this period.")
    daily = load_daily(broker, settings, sorted({c[0] for c in cands} | {"SPY"}), start - timedelta(days=120),
                       end, emit)
    spy = daily.pop("SPY", None)
    px = prepare(daily)
    cfg = settings["swing"]
    beats, misses = [], []
    for sym, filed, sue in cands:
        d = px.get(sym)
        if d is None or not liquid_ok_at(d, filed, cfg):
            continue
        e = Event(sym, filed, "earnings_beat" if sue > 0 else "earnings_miss", sue, f"earnings surprise {sue:+.1f}")
        (beats if sue > 0 else misses).append(e)
    study, miss = event_returns(beats, px, spy, EARNINGS_HOLD), event_returns(misses, px, spy, EARNINGS_HOLD)
    res = _portfolio(settings, beats, px, spy, start, end, EARNINGS_HOLD)
    res.edge_table = [
        split_row(f"Big earnings beat (surprise {SUE_MIN:.0f}+): buy, hold {EARNINGS_HOLD} days (THE TEST)", "long",
                  study["net"], study["day"], 50),
        split_row("  same, before costs", "long", study["excess"], study["day"], 50),
        split_row(f"  big miss (surprise -{SUE_MIN:.0f} or worse): shorting it (for comparison)", "short",
                  -miss["excess"] + (miss["net"] - miss["excess"]), miss["day"], 50),     # -excess - costs
    ]
    res.headline = headline([verdict_line("Earnings drift", study["net"], study["day"], 50)])
    res.notes.append(f"{len(eps)} companies with SEC earnings data; {len(beats)} big beats and {len(misses)} big "
                     f"misses. Results are vs the S&P 500 over the same days, after costs (bps; 100 bps = 1%). The "
                     "signal date is the day the 10-Q report was filed, which can be after the earnings "
                     "announcement. Stocks that traded $5M+ a day in any year of the test; delisted companies are "
                     "only included if the SEC's current ticker list still has them, so most are missing.")
    return res


# ------------------------------------------------------------------ 3. momentum
MOM_UNIVERSE = 500          # most-traded stocks each month
MOM_HOLD = 50               # winners held
MOM_LOOKBACK, MOM_SKIP = 252, 21
MOM_COST = 0.0005           # per side, liquid large stocks


def _panels(daily: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    d = {s: _norm_daily(df) for s, df in daily.items() if df is not None and len(df)}
    close = pd.DataFrame({s: x["close"] for s, x in d.items()}).sort_index()
    open_ = pd.DataFrame({s: x["open"] for s, x in d.items()}).reindex(close.index)
    dv = pd.DataFrame({s: x["close"] * x["volume"] for s, x in d.items()}).reindex(close.index)
    return close, open_, dv


def _rebalance_days(index: pd.DatetimeIndex, start: date, end: date) -> list[int]:
    days = [i for i, t in enumerate(index) if start <= t.date() <= end]
    out, last_month = [], None
    for i in days:
        m = (index[i].year, index[i].month)
        if m != last_month:
            out.append(i)
            last_month = m
    return out


def _period_return(open_: pd.DataFrame, close: pd.DataFrame, a: int, b: int | None, cols) -> pd.Series:
    """Open of day a to open of day b (or the last close if b is None); a stock that stops trading keeps its
    last price."""
    start_px = open_.iloc[a][cols]
    if b is None:
        end_px = close.iloc[:].ffill().iloc[-1][cols]
    else:
        end_px = open_.iloc[b][cols].fillna(close.iloc[:b].ffill().iloc[-1][cols])
    return (end_px / start_px - 1).replace([np.inf, -np.inf], np.nan)


def _monthly_equity(res: BTResult, start: date, months: list[tuple[date, float]], equity: float):
    res.equity_curve = [(datetime.combine(start, time(0)), equity)]
    for d, r in months:
        equity *= 1 + r
        res.equity_curve.append((datetime.combine(d, time(16)), equity))
    return equity


def run_momentum(broker, settings: dict, start: date, end: date, emit=lambda m: None) -> BTResult:
    daily, info = history_universe(broker, settings, 20e6, start - timedelta(days=400), end, emit)
    if "SPY" not in daily:
        daily.update(load_daily(broker, settings, ["SPY"], start - timedelta(days=400), end, emit))
    emit("Simulating month by month...")
    close, open_, dv = _panels(daily)
    spy_close, spy_open = close.pop("SPY") if "SPY" in close else None, open_.pop("SPY") if "SPY" in open_ else None
    dv = dv.drop(columns=["SPY"], errors="ignore")
    dv60 = dv.rolling(60, min_periods=40).mean()
    rebal = _rebalance_days(close.index, start, end)
    rows, trades, held = [], [], set()
    equity = start_equity = float(settings["backtest"]["starting_equity"])
    for n, i in enumerate(rebal):
        p = i - 1                                                    # yesterday's close: known at today's open
        if p - MOM_LOOKBACK < 0:
            continue
        past, recent = close.iloc[p - MOM_LOOKBACK], close.iloc[p - MOM_SKIP]
        ok = (close.iloc[p] >= 5) & past.notna() & recent.notna() & dv60.iloc[p].notna() & open_.iloc[i].notna()
        elig = dv60.iloc[p][ok].nlargest(MOM_UNIVERSE).index
        if len(elig) < MOM_HOLD * 2:
            continue
        mom = (recent[elig] / past[elig] - 1).dropna()
        top = list(mom.nlargest(MOM_HOLD).index)
        nxt = rebal[n + 1] if n + 1 < len(rebal) else None
        r_all = _period_return(open_, close, i, nxt, list(elig))
        r_top = r_all[top]
        changed = len(set(top) - held)
        cost = (changed / MOM_HOLD) * 2 * MOM_COST if held else MOM_COST
        held = set(top)
        strat = float(r_top.fillna(0).mean()) - cost
        univ = float(r_all.fillna(0).mean())
        spy_r = 0.0
        if spy_open is not None:
            a = spy_open.iloc[i]
            b = spy_open.iloc[nxt] if nxt is not None else spy_close.ffill().iloc[-1]
            spy_r = float(b / a - 1) if a > 0 else 0.0
        day = close.index[i].date()
        rows.append({"day": day, "strat": strat, "univ": univ, "spy": spy_r, "excess": strat - univ,
                     "vs_spy": strat - spy_r})
        exit_day = close.index[nxt] if nxt is not None else close.index[-1]
        for s in top:
            o = float(open_.iloc[i][s])
            r = float(r_top.get(s, 0.0) or 0.0)
            qty = math.floor(equity / MOM_HOLD / o) if o > 0 else 0
            if qty >= 1:
                trades.append({"symbol": s, "side": "long", "entry_time": close.index[i].to_pydatetime(),
                               "exit_time": exit_day.to_pydatetime(), "qty": qty, "entry": o, "exit": o * (1 + r),
                               "pnl": o * r * qty, "pnl_pct": r, "exit_reason": "monthly rebalance",
                               "partial": False, "score": float(mom[s]), "setup": "momentum"})
        equity *= 1 + strat
    res = BTResult()
    if not rows:
        res.notes.append("Not enough price history: momentum needs a year of prices before the start date.")
        return res
    df = pd.DataFrame(rows)
    _monthly_equity(res, start, list(zip(df["day"], df["strat"])), start_equity)
    res.trades = trades
    if spy_open is not None and len(rebal):
        res.benchmark_return = float(np.prod(1 + df["spy"].values) - 1)
    res.stats = Backtester._stats(res, start_equity)
    res.halves = dev_holdout_periods(trades, start)
    res.edge_table = [
        split_row(f"Top {MOM_HOLD} winners minus all {MOM_UNIVERSE}, per month (THE TEST)", "long", df["excess"],
                  df["day"], 12),
        split_row("  top winners minus the S&P 500 (SPY), per month", "long", df["vs_spy"], df["day"], 12),
        split_row("  top winners, per month (after costs)", "long", df["strat"], df["day"], 12),
        split_row(f"  all {MOM_UNIVERSE} stocks, per month", "long", df["univ"], df["day"], 12),
    ]
    res.headline = headline([verdict_line("Momentum", df["excess"], df["day"], 12, unit="bps a month")])
    res.notes.append(f"Each month: the {MOM_UNIVERSE} most-traded stocks ($5+), ranked by their return from 12 "
                     f"months to 1 month ago; the top {MOM_HOLD} are held in equal amounts until the next month "
                     f"(costs {MOM_COST:.2%} per trade). The test compares them with ALL {MOM_UNIVERSE}. "
                     + universe_note(info))
    return res


# ------------------------------------------------------------------ 4. trend following
TREND_ASSETS = {"SPY": "US stocks", "EFA": "foreign stocks", "IEF": "US Treasury bonds", "GLD": "gold",
                "VNQ": "real estate"}
TREND_CASH = "BIL"                  # 1-3 month Treasury bills: cash that earns interest
TREND_MONTHS = 10
TREND_COST = 0.0005


def _sharpe(r: np.ndarray) -> float:
    return float(r.mean() / r.std(ddof=1) * math.sqrt(12)) if len(r) > 2 and r.std(ddof=1) > 0 else 0.0


def _max_dd(r: np.ndarray) -> float:
    eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(np.r_[1.0, eq])[1:]
    return float((eq / peak - 1).min()) if len(eq) else 0.0


def run_trend_following(broker, settings: dict, start: date, end: date, emit=lambda m: None) -> BTResult:
    syms = list(TREND_ASSETS) + [TREND_CASH]
    daily = load_daily(broker, settings, syms, start - timedelta(days=400), end, emit, label="Fund prices")
    missing = [s for s in syms if s not in daily]
    if missing:
        raise ValueError(f"No price data for {', '.join(missing)}.")
    close, open_, _ = _panels(daily)
    close, open_ = close.ffill(), open_.fillna(close)
    month_end = close.groupby(close.index.to_period("M")).tail(1)
    rebal = _rebalance_days(close.index, start, end)
    rows, trades, on_prev = [], [], {}
    equity = start_equity = float(settings["backtest"]["starting_equity"])
    w = 1 / len(TREND_ASSETS)
    for n, i in enumerate(rebal):
        known = month_end[month_end.index < close.index[i]]             # month-end closes before today
        if len(known) < TREND_MONTHS:
            continue
        sma = known.tail(TREND_MONTHS).mean()
        last = known.iloc[-1]
        nxt = rebal[n + 1] if n + 1 < len(rebal) else None
        r = _period_return(open_, close, i, nxt, syms).fillna(0.0)
        on = {a: bool(last[a] > sma[a]) for a in TREND_ASSETS}
        switches = sum(1 for a in TREND_ASSETS if on_prev and on[a] != on_prev.get(a))
        cost = switches * w * 2 * TREND_COST if on_prev else TREND_COST
        on_prev = on
        strat = sum(w * (r[a] if on[a] else r[TREND_CASH]) for a in TREND_ASSETS) - cost
        hold = sum(w * r[a] for a in TREND_ASSETS)
        day = close.index[i].date()
        rows.append({"day": day, "strat": strat, "hold": hold, "spy": float(r["SPY"]), "on": sum(on.values())})
        exit_day = close.index[nxt] if nxt is not None else close.index[-1]
        for a in TREND_ASSETS:
            s = a if on[a] else TREND_CASH
            o = float(open_.iloc[i][s])
            qty = math.floor(equity * w / o) if o > 0 else 0
            if qty >= 1:
                trades.append({"symbol": s, "side": "long", "entry_time": close.index[i].to_pydatetime(),
                               "exit_time": exit_day.to_pydatetime(), "qty": qty, "entry": o,
                               "exit": o * (1 + r[s]), "pnl": o * r[s] * qty, "pnl_pct": float(r[s]),
                               "exit_reason": "monthly check" if on[a] else f"{a} below its 10-month average",
                               "partial": False, "score": 0.0, "setup": "trend"})
        equity *= 1 + strat
    res = BTResult()
    if not rows:
        res.notes.append("Not enough price history: the trend rule needs 10 months of prices before the start date.")
        return res
    df = pd.DataFrame(rows)
    _monthly_equity(res, start, list(zip(df["day"], df["strat"])), start_equity)
    res.trades = trades
    res.benchmark_return = float(np.prod(1 + df["spy"].values) - 1)
    res.stats = Backtester._stats(res, start_equity)
    res.halves = dev_holdout_periods(trades, start)
    dev = np.array([d < HOLDOUT_START for d in df["day"]])
    res.edge_table = [
        split_row("Trend rule, per month (after costs)", "long", df["strat"], df["day"], 12),
        split_row("  buy and hold the same 5 funds, per month", "long", df["hold"], df["day"], 12),
        split_row("  trend rule minus buy and hold, per month", "long", df["strat"] - df["hold"], df["day"], 12),
    ]
    # Pre-registered: the point of trend following is smaller crashes for similar returns, so it PASSES if
    # its return per unit of risk (Sharpe ratio) beats buy-and-hold of the same funds in BOTH periods and its
    # worst drop over the whole test is smaller.
    lines, ok_all = [], True
    for name, m in (("before 2024", dev), ("from 2024 on", ~dev)):
        if m.sum() < 12:
            res.headline = ("PRE-REGISTERED TEST: Trend following: INCOMPLETE (needs 12+ months before 2024 and "
                            "from 2024 on). Test 2019-01-01 to yesterday.")
            break
        a, b = _sharpe(df["strat"].values[m]), _sharpe(df["hold"].values[m])
        lines.append(f"Sharpe {name}: {a:.2f} vs {b:.2f} for buy and hold")
        ok_all &= a > b
    else:
        dd_a, dd_b = _max_dd(df["strat"].values), _max_dd(df["hold"].values)
        lines.append(f"worst drop {dd_a:.1%} vs {dd_b:.1%}")
        ok_all &= dd_a > dd_b
        res.headline = ("PRE-REGISTERED TEST: Trend following: " + ("PASS" if ok_all else "FAIL") + " ("
                        + "; ".join(lines) + "). " +
                        ("Less risk for the return: worth considering for long-term money."
                         if ok_all else "It didn't improve return per unit of risk: dropped, not tuned."))
    res.notes.append("Each month, each of 5 funds (" + ", ".join(f"{k} = {v}" for k, v in TREND_ASSETS.items()) +
                     f") gets 1/5 of the money if it closed last month above its {TREND_MONTHS}-month average; "
                     f"otherwise that 1/5 goes to {TREND_CASH} (Treasury bills). Prices include dividends.")
    return res
