"""Downloads what a confirmed-gap backtest needs: daily bars to find gap days, then 5-minute bars only for
the stocks and days that gapped (keeps a multi-year test down to minutes)."""
from __future__ import annotations

from datetime import date, timedelta

from .gap import daily_gaps, session_bars

MAX_GAPS_PER_DAY = 25            # biggest gaps first; far more than the 5 positions a day can trade


def load_gap_backtest(broker, settings: dict, syms: list[str], start: date, end: date,
                      all_stocks: bool, emit=lambda m: None):
    cfg, feed = settings["gap_trading"], settings["backtest"].get("data_feed", "sip")
    notes = []
    universe = set(syms)
    if all_stocks:
        emit("Getting the list of tradable US stocks...")
        universe |= set(broker.tradable_symbols())
        notes.append("Universe: stocks listed TODAY (delisted companies aren't included, which can flatter results).")
    universe = sorted(universe | {"SPY"})
    daily = {}
    for i in range(0, len(universe), 200):
        emit(f"Daily prices: {i}/{len(universe)} stocks...")
        daily.update(broker.daily_range(universe[i:i + 200], start - timedelta(days=45), end, feed=feed))
    spy = daily.get("SPY")
    events = daily_gaps(daily, cfg, start, end)
    by_day: dict[date, list] = {}
    for e in events:
        by_day.setdefault(e.day, []).append(e)
    kept = []
    for day in sorted(by_day):
        kept += sorted(by_day[day], key=lambda e: -abs(e.gap))[:MAX_GAPS_PER_DAY]
    intraday = {}
    days = sorted({e.day for e in kept})
    for n, day in enumerate(days):
        if n % 10 == 0:
            emit(f"5-minute prices for gap days: {n}/{len(days)} days...")
        todays = sorted({e.symbol for e in kept if e.day == day})
        bars = broker.bars_range(todays, day, day, minutes=5, feed=feed)
        for sym in todays:
            b = session_bars(bars.get(sym), day)
            if len(b):
                intraday[(sym, day)] = b
    notes.append(f"{len(daily)} stocks with daily prices; {len(kept)} gaps checked on 5-minute prices.")
    return kept, intraday, spy, notes
