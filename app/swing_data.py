"""Download what a swing test needs: SEC insider records + daily prices, then find the signals.
Shared by the Backtest tab and the AI test."""
from __future__ import annotations

from datetime import date, timedelta

from . import config
from .sec_history import insider_history
from .swing import insider_events, news_gap_events, prepare


def load_swing_signals(broker, settings: dict, syms: list[str], start: date, end: date,
                       all_insiders: bool = True, emit=lambda m: None, extra_days_after: int = 0):
    """Returns (events, px, spy, notes). `extra_days_after` downloads prices past `end`
    (the AI test needs to see what happened after each signal)."""
    cfg = settings["swing"]
    ua = settings["smart_money"].get("sec_user_agent", "")
    notes, purchases = [], None
    if cfg["setups"]["insider_buy"]:
        if "@" in ua:
            purchases = insider_history(start - timedelta(days=20), end, config.BASE_DIR / "cache", ua, emit)
        else:
            notes.append("Insider setup skipped: add your name + email as 'SEC contact' in Settings.")
    if purchases is not None and len(purchases):
        last = max(purchases["filed"])
        if (end - last).days > 45:
            notes.append(f"The SEC's published insider records currently end on {last}, so there are no "
                         f"insider trades after that (the SEC publishes each quarter's file a few weeks to "
                         f"months later).")
    universe = set(syms)
    if all_insiders and purchases is not None and len(purchases):
        big = purchases.groupby("symbol")["value"].sum()
        universe |= set(big[big >= cfg["insider"]["min_value"]].index)
    universe = sorted(universe | {"SPY"})
    daily = {}
    for i in range(0, len(universe), 100):
        emit(f"Downloading daily prices {i}/{len(universe)} stocks...")
        daily.update(broker.daily_range(universe[i:i + 100], start - timedelta(days=320),
                                        end + timedelta(days=extra_days_after),
                                        feed=settings["backtest"].get("data_feed", "sip")))
    spy = daily.pop("SPY", None)
    emit("Finding setups...")
    px = prepare(daily)
    events = []
    if purchases is not None:
        events += insider_events(purchases, px, cfg, start, end)
    if cfg["setups"]["news_gap"]:
        events += news_gap_events(px, cfg, start, end)
    return events, px, spy, notes
