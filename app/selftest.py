"""Self-test: checks every outside connection Eimeria depends on and reports pass / warn / fail.

The optional order test only ever runs on a PAPER account. It places the exact kind of order the bot
uses (a limit entry with stop-loss and take-profit attached), priced far below the market so it can't
fill, checks the broker accepted it, then cancels it.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import requests

from . import config

PASS, WARN, FAIL, SKIP = "pass", "warning", "FAIL", "skipped"


@dataclass
class Check:
    name: str
    status: str
    detail: str


def run_self_test(engine, settings: dict, order_test: bool, progress=lambda m: None) -> list[Check]:
    out: list[Check] = []

    def check(name, fn):
        progress(f"Checking: {name}...")
        try:
            status, detail = fn()
        except Exception as e:
            status, detail = FAIL, f"{type(e).__name__}: {e}"
            config_log(traceback.format_exc())
        out.append(Check(name, status, detail))
        return status

    b = engine.broker if engine else None

    # ---------------------------------------------------------------- setup
    def files():
        test = config.BASE_DIR / ".selftest_write"
        test.write_text("ok")
        test.unlink()
        return PASS, f"App folder is writable: {config.BASE_DIR}"
    check("App folder", files)

    def keys():
        k, sec = config.load_keys()
        if not k or not sec:
            return FAIL, "Alpaca keys missing. Enter them in Settings."
        kind = "paper" if k.startswith("PK") else "live" if k.startswith("AK") else "unknown"
        if kind != "unknown" and kind != settings["mode"]:
            return FAIL, (f"Mode is '{settings['mode']}' but the API key looks like a {kind} key "
                          f"(paper keys start with PK, live keys with AK).")
        return PASS, f"Keys present ({kind} key, mode {settings['mode']})."
    check("Alpaca keys", keys)

    if not b:
        out.append(Check("Alpaca connection", FAIL, "Not connected. Fix the keys, then Save settings."))
        return out

    # ---------------------------------------------------------------- broker
    def account():
        a = b.account_details()
        if a.get("trading_blocked") or a.get("account_blocked"):
            return FAIL, "Alpaca says this account is blocked from trading. Check the Alpaca dashboard."
        msg = (f"Connected ({'paper' if b.paper else 'LIVE'}). Status {a.get('status')}, equity "
               f"${a['equity']:,.2f}, buying power ${a['buying_power']:,.2f}.")
        if a.get("multiplier", 1) <= 1:
            if settings["swing"].get("idle_cash_in_index"):
                return WARN, msg + (" This is a CASH account: with 'idle cash in an S&P 500 fund' on, buying with "
                                    "unsettled sale proceeds can cause trading restrictions. A margin account avoids it.")
            return PASS, msg + " Cash account."
        return PASS, msg + f" Margin account ({a.get('multiplier')}x)."
    check("Alpaca account", account)

    def clock():
        c = b.clock()
        return PASS, (f"Market is {'OPEN' if c['is_open'] else 'closed'}. Next open {c['next_open']:%Y-%m-%d %H:%M}, "
                      f"next close {c['next_close']:%Y-%m-%d %H:%M} (New York).")
    check("Market clock", clock)

    def daily():
        d = b.daily_bars(["SPY"], days=400).get("SPY")
        if d is None or len(d) < 200:
            return FAIL, "Couldn't get a year of daily S&P 500 prices (needed for swing trading and crash protection)."
        h = b.daily_range(["SPY"], date(2019, 1, 1), date(2019, 3, 1), feed=settings["backtest"].get("data_feed", "sip"))
        hs = h.get("SPY")
        if hs is None or len(hs) < 30:
            return WARN, "Recent daily prices OK, but historical (2019) prices failed: backtests may not work."
        return PASS, f"Daily prices OK ({len(d)} recent days; historical data back to 2019 available)."
    check("Daily prices (swing + backtests)", daily)

    def intraday():
        if not settings["day_trading"]["enabled"]:
            bars = b.bars(["SPY"], minutes=5, days_back=5).get("SPY")
            return (PASS if bars is not None and len(bars) else WARN,
                    "5-minute prices " + ("OK" if bars is not None and len(bars) else "unavailable") +
                    " (only needed for day trading, which is off).")
        bars = b.bars(["SPY"], minutes=5, days_back=5).get("SPY")
        if bars is None or not len(bars):
            return FAIL, "No 5-minute prices: day trading can't work. Check the live data feed setting (iex is free)."
        return PASS, f"5-minute prices OK ({len(bars)} bars, feed {settings['data']['live_feed']})."
    check("Intraday prices (day trading)", intraday)

    def quote():
        q = b.quote("SPY")
        if not q:
            try:
                market_open = b.clock()["is_open"]
            except Exception:
                market_open = True
            if not market_open:
                return SKIP, ("No live bid/ask quote right now, which is normal while the market is closed. "
                              "Run the self-test again during market hours (9:30-16:00 New York) to check quotes.")
            return WARN, ("No live bid/ask quote although the market is open. Orders still work, but the spread "
                          "check is skipped and entries use the last price.")
        spread = (q["ask"] - q["bid"]) / ((q["ask"] + q["bid"]) / 2)
        return PASS, f"Quote OK: bid {q['bid']:.2f} / ask {q['ask']:.2f} (spread {spread:.3%})."
    check("Live quotes", quote)

    def news():
        n = b.news(["AAPL", "MSFT", "NVDA"], hours_back=96, limit=10)
        end = datetime.now(timezone.utc) - timedelta(days=200)
        hist = b.news_between(["AAPL"], end - timedelta(days=7), end)
        if not n:
            return FAIL, "No recent news from Alpaca: the AI has nothing to read."
        if not hist:
            return WARN, f"Recent news OK ({len(n)} headlines), but the news archive returned nothing (AI test needs it)."
        return PASS, f"News OK: {len(n)} recent headlines; archive available ({len(hist)} headlines from 200 days ago)."
    check("Alpaca news (AI + AI test)", news)

    def movers():
        m = b.movers(top=10)
        return (PASS, f"Top movers OK ({len(m)} stocks).") if m else \
            (WARN, "Top movers list unavailable (only used to find extra stocks to scan).")
    check("Top movers", movers)

    # ---------------------------------------------------------------- SEC
    def sec():
        ua = settings["smart_money"].get("sec_user_agent", "")
        if "@" not in ua:
            return FAIL, "SEC contact missing: insider buying (the main swing strategy) can't work. Add it in Settings."
        r = requests.get("https://efts.sec.gov/LATEST/search-index", headers={"User-Agent": ua}, timeout=30,
                         params={"forms": "4", "dateRange": "custom",
                                 "startdt": (date.today() - timedelta(days=5)).isoformat(),
                                 "enddt": date.today().isoformat()})
        if r.status_code == 403:
            return FAIL, "The SEC refused the request (403). Check the SEC contact is a real name and email."
        r.raise_for_status()
        n = r.json()["hits"]["total"]["value"]
        h = requests.head("https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/"
                          "2024q1_form345.zip", headers={"User-Agent": ua}, timeout=30, allow_redirects=True)
        hist = "historical files OK" if h.status_code == 200 else f"historical files returned {h.status_code}"
        return PASS, f"SEC filings reachable: {n} insider filings in the last 5 days; {hist}."
    check("SEC insider data", sec)

    # ---------------------------------------------------------------- AI
    def ai():
        ok, msg = engine.llm.status()
        if not ok:
            return FAIL, msg
        from .ai_schemas import SWING_REVIEW
        t0 = time.time()
        res = engine.llm.chat_json("Reply ONLY with JSON.",
                                   'Is "Company wins a $2B multi-year government contract" durable good news for '
                                   'a 2-month hold? Return JSON: {"approve": true/false, "reason": "..."}',
                                   schema=SWING_REVIEW)
        secs = time.time() - t0
        if "approve" not in res:
            return FAIL, f"{msg}, but it didn't give a valid answer ({secs:.0f}s). Try another model (AI tab)."
        exact = "exact answer format supported" if engine.llm._schema_ok else \
            "Ollama is older than 0.5 (exact answer format unsupported; update Ollama for more reliable answers)"
        status = PASS if secs < 60 and engine.llm._schema_ok else WARN
        slow = " Slow: consider a smaller model." if secs >= 60 else ""
        return status, f"{msg}. Answered correctly in {secs:.1f}s; {exact}.{slow}"
    check("Local AI (Ollama)", ai)

    # ---------------------------------------------------------------- orders (paper only)
    def orders():
        if not order_test:
            return SKIP, "Not run (you can tick 'Include a test order' to check order placement)."
        if not b.paper or settings["mode"] != "paper":
            return SKIP, "Only runs on a PAPER account, never with real money."
        q = b.quote("SPY")
        d = b.daily_bars(["SPY"], days=10).get("SPY")
        ref = q["bid"] if q else float(d["close"].iloc[-1])
        limit = round(ref * 0.7, 2)                       # far below the market: can't fill
        oid = b.submit_bracket("SPY", 1, "long", round(limit * 0.95, 2), round(limit * 1.2, 2), limit_price=limit)
        time.sleep(2)
        status = b.order_status(oid)
        b.cancel_order(oid)
        for _ in range(10):
            time.sleep(1)
            after = b.order_status(oid)
            if after in ("canceled", "cancelled", "pending_cancel"):
                break
        if after not in ("canceled", "cancelled", "pending_cancel"):
            return FAIL, (f"Test order {oid} was accepted ({status}) but NOT cancelled ({after}). "
                          "Cancel it in the Alpaca dashboard.")
        return PASS, (f"Placed the bot's order type (1 SPY share, limit ${limit} with stop + target attached), "
                      f"broker accepted it ({status}), then cancelled it.")
    check("Order placement (paper)", orders)

    # ---------------------------------------------------------------- updates
    def updates():
        from .updater import Updater
        from .version import VERSION
        up = Updater(settings)
        info = up.fetch_info()
        if not info:
            return WARN, f"Nothing published at {up.repo} ({up.cfg.get('branch')}). Check Settings > Updates."
        up.check()
        return PASS, f"GitHub reachable; {up.last_status} (source: {up.repo}, {up.cfg.get('branch')}; you have {VERSION})."
    check("Updates (GitHub)", updates)

    # ---------------------------------------------------------------- PC
    def awake():
        if sys.platform != "win32":
            return SKIP, "Not Windows: keep-awake not available."
        from .system import keep_awake
        ok = keep_awake(True) and keep_awake(False)
        return (PASS, "Windows sleep can be blocked while the bot runs.") if ok else \
            (WARN, "Couldn't block sleep: set Windows to never sleep during market hours.")
    check("Keep PC awake", awake)
    return out


def config_log(text: str):
    try:
        config.LOG_DIR.mkdir(exist_ok=True)
        with open(config.LOG_DIR / "selftest_errors.log", "a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now()}\n{text}\n")
    except Exception:  # nosec B110 - best-effort, must never raise
        pass


def report_text(checks: list[Check]) -> str:
    counts = {s: sum(1 for c in checks if c.status == s) for s in (PASS, WARN, FAIL, SKIP)}
    lines = [f"Eimeria self-test {datetime.now():%Y-%m-%d %H:%M} ({os.name}, Python {sys.version.split()[0]})",
             f"{counts[PASS]} passed, {counts[WARN]} warnings, {counts[FAIL]} failed, {counts[SKIP]} skipped", ""]
    lines += [f"[{c.status.upper():7s}] {c.name}: {c.detail}" for c in checks]
    return "\n".join(lines)
