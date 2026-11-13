"""Stress test: run the real Engine through weeks of simulated trading against a simulated broker
and market, with random failures injected, and check safety rules after every simulated minute.

    python -m tests.stress_sim --days 30 --faults 0.05 --seed 1

Exit code 0 = all invariants held.
"""
from __future__ import annotations

import argparse
import copy
import math
import random
import sys
import tempfile
import time as real_time
import tracemalloc
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app.config import DEFAULTS
from app.indicators import NY

# ----------------------------------------------------------------------------- simulated clock
SIM = {"now": datetime(2026, 3, 2, 8, 0, tzinfo=NY)}


class SimDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        n = SIM["now"]
        return n.astimezone(tz) if tz else n.astimezone(timezone.utc).replace(tzinfo=None)


class SimTime:
    @staticmethod
    def time():
        return SIM["now"].timestamp()

    @staticmethod
    def sleep(_s):
        pass


EARLY_CLOSE = set()          # dates closing at 13:00


def market_hours(d: date):
    if d.weekday() >= 5:
        return None
    close = 13 * 60 if d in EARLY_CLOSE else 16 * 60
    return 9 * 60 + 30, close


class Fault(Exception):
    pass


# ----------------------------------------------------------------------------- simulated broker/market
class SimBroker:
    def __init__(self, symbols, rng: random.Random, fault_rate: float, start: date):
        self.rng, self.fault = rng, fault_rate
        self.paper = True
        self.cash = 100_000.0
        self.last_equity = self.cash
        self.pos: dict[str, dict] = {}            # symbol -> {qty, avg}
        self.orders: dict[str, dict] = {}         # id -> order
        self.fills: list[dict] = []
        self.n = 0
        self.price = {s: rng.uniform(20, 300) for s in symbols}
        self.price["SPY"], self.price["SH"], self.price["VIXY"] = 500.0, 40.0, 20.0
        self.symbols = list(self.price)
        self.minute_hist = {s: [] for s in self.symbols}          # (timestamp, o, h, l, c, v)
        self.daily_hist = {s: [] for s in self.symbols}
        self.day_open = dict(self.price)
        # 300 days of daily history before the start, with an uptrend for most stocks
        d = start - timedelta(days=430)
        while d < start:
            if d.weekday() < 5:
                for s in self.symbols:
                    drift, vol = (0.0010, 0.004) if s == "SPY" else (0.0006, 0.015)   # the index trends up steadily
                    p = self.price[s] * (1 + rng.gauss(drift, vol))
                    self.price[s] = max(p, 1.0)
                    self.daily_hist[s].append((pd.Timestamp(d), p * 0.99, p * 1.01, p * 0.98, p, 2e6))
            d += timedelta(days=1)
        self.calls = 0
        self.faults = 0

    # ---- helpers
    def _maybe_fail(self, name):
        self.calls += 1
        if self.rng.random() < self.fault:
            self.faults += 1
            raise Fault(f"simulated broker failure in {name}")

    def _id(self):
        self.n += 1
        return f"o{self.n}"

    def is_open(self):
        now = SIM["now"]
        h = market_hours(now.date())
        m = now.hour * 60 + now.minute
        return bool(h and h[0] <= m < h[1])

    # ---- market moves (called by the driver every simulated minute)
    def new_day(self, crash=False):
        self.last_equity = self.equity()
        for s in self.symbols:
            gap = self.rng.gauss(0, 0.01) - (0.06 if crash and s != "SH" else 0) + (0.06 if crash and s == "SH" else 0)
            self.price[s] *= 1 + gap
            self.day_open[s] = self.price[s]

    def tick_minute(self):
        ts = pd.Timestamp(SIM["now"])
        for s in self.symbols:
            o = self.price[s]
            c = max(o * (1 + self.rng.gauss(0.00001, 0.0006)), 0.5)
            self.price[s] = c
            self.minute_hist[s].append((ts, o, max(o, c) * 1.0005, min(o, c) * 0.9995, c, 20000.0))
        self._match()

    def end_day(self):
        d = pd.Timestamp(SIM["now"].date())
        for s in self.symbols:
            day = [m for m in self.minute_hist[s][-400:] if m[0].date() == d.date()]
            if day:
                self.daily_hist[s].append((d, day[0][1], max(m[2] for m in day), min(m[3] for m in day), day[-1][4],
                                           sum(m[5] for m in day) * 50))
            self.minute_hist[s] = self.minute_hist[s][-2000:]

    def _fill(self, sym, qty_signed, price, order):
        p = self.pos.get(sym, {"qty": 0.0, "avg": price})
        new_qty = p["qty"] + qty_signed
        if p["qty"] == 0 or (p["qty"] > 0) == (qty_signed > 0):
            p["avg"] = (p["avg"] * abs(p["qty"]) + price * abs(qty_signed)) / max(abs(new_qty), 1e-9)
        p["qty"] = new_qty
        self.cash -= qty_signed * price
        if abs(new_qty) < 1e-9:
            self.pos.pop(sym, None)
        else:
            self.pos[sym] = p
        order.update(status="filled", filled_avg_price=price, filled_qty=abs(qty_signed),
                     filled_at=SIM["now"])
        self.fills.append({"symbol": sym, "qty": qty_signed, "price": price, "time": SIM["now"], "id": order["id"]})

    def _match(self):
        if not self.is_open():
            return
        for o in list(self.orders.values()):
            if o["status"] not in ("new", "held"):
                continue
            px = self.price[o["symbol"]]
            if o["kind"] == "entry":
                ok = px <= o["limit"] if o["side"] == "buy" else px >= o["limit"]
                if ok:
                    self._fill(o["symbol"], o["qty"] if o["side"] == "buy" else -o["qty"], px, o)
                    for leg in o["legs"]:
                        self.orders[leg]["status"] = "new"
            elif o["kind"] in ("stop", "target") and o["status"] == "new":
                held = self.pos.get(o["symbol"], {}).get("qty", 0)
                if held == 0:
                    o["status"] = "canceled"
                    continue
                hit = (px <= o["price"]) if (o["kind"] == "stop") == (o["side"] == "sell") else (px >= o["price"])
                if hit:
                    q = min(o["qty"], abs(held))
                    self._fill(o["symbol"], -q if o["side"] == "sell" else q, px, o)
                    for sib in o.get("oco", []):
                        if self.orders[sib]["status"] in ("new", "held"):
                            self.orders[sib]["status"] = "canceled"

    def equity(self):
        return self.cash + sum(p["qty"] * self.price[s] for s, p in self.pos.items())

    # ---- broker API used by the app
    def account(self):
        self._maybe_fail("account")
        e = self.equity()
        return {"equity": e, "last_equity": self.last_equity, "buying_power": max(0.0, e * 2 - sum(
            abs(p["qty"]) * self.price[s] for s, p in self.pos.items())), "cash": self.cash, "daytrade_count": 0,
                "pattern_day_trader": False}

    def account_details(self):
        return {**self.account(), "status": "ACTIVE", "trading_blocked": False, "account_blocked": False,
                "multiplier": 2.0}

    def clock(self):
        self._maybe_fail("clock")
        now = SIM["now"]
        d = now.date()
        h = market_hours(d)
        close = datetime.combine(d, datetime.min.time(), NY) + timedelta(minutes=h[1] if h else 16 * 60)
        nd = d + timedelta(days=1)
        while not market_hours(nd):
            nd += timedelta(days=1)
        return {"is_open": self.is_open(), "timestamp": now, "next_open":
                datetime.combine(nd, datetime.min.time(), NY) + timedelta(minutes=570),
                "next_close": close if self.is_open() else
                datetime.combine(nd, datetime.min.time(), NY) + timedelta(minutes=market_hours(nd)[1])}

    def positions(self):
        self._maybe_fail("positions")
        return [{"symbol": s, "qty": abs(p["qty"]), "side": "long" if p["qty"] > 0 else "short",
                 "avg_entry": p["avg"], "current_price": self.price[s],
                 "unrealized_pl": (self.price[s] - p["avg"]) * p["qty"],
                 "unrealized_plpc": (self.price[s] / p["avg"] - 1) * (1 if p["qty"] > 0 else -1)}
                for s, p in self.pos.items()]

    def bars(self, symbols, minutes=5, days_back=1):
        self._maybe_fail("bars")
        out = {}
        cache = self.__dict__.setdefault("_bar_cache", {})
        for s in symbols:
            rows = self.minute_hist.get(s, [])[-2000:]
            if not rows or self.rng.random() < self.fault:
                continue
            key = (s, minutes, len(self.minute_hist[s]), rows[-1][0])
            if key not in cache:
                if len(cache) > 500:
                    cache.clear()
                df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")
                df.index = pd.DatetimeIndex(df.index).tz_convert("UTC")
                cache[key] = df.resample(f"{minutes}min").agg({"open": "first", "high": "max", "low": "min",
                                                               "close": "last", "volume": "sum"}).dropna()
            out[s] = cache[key]
        return out

    def daily_bars(self, symbols, days=40):
        self._maybe_fail("daily_bars")
        out = {}
        for s in symbols:
            rows = self.daily_hist.get(s, [])[-days:]
            if rows:
                df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")
                df.index = pd.DatetimeIndex(df.index).tz_localize(NY)
                out[s] = df
        return out

    def movers(self, top=10):
        return self.rng.sample(self.symbols[:-3], 3)

    def news(self, symbols, hours_back=18, limit=50):
        if self.rng.random() < self.fault:
            return []
        s = (symbols or ["SPY"])[0]
        return [{"headline": f"{s} wins large contract", "summary": "", "symbols": [s], "source": "wire",
                 "url": "", "time": SIM["now"]}]

    def news_between(self, symbols, start, end, limit=30):
        return self.news(symbols)

    def is_shortable(self, s):
        return True

    def quote(self, symbol):
        if self.rng.random() < self.fault:
            return None
        p = self.price[symbol]
        return {"bid": p * 0.9998, "ask": p * 1.0002}

    def submit_bracket(self, symbol, qty, side, stop, target, limit_price=None):
        self._maybe_fail("submit_bracket")
        assert qty >= 1 and int(qty) == qty, f"bad qty {qty}"
        assert (stop < (limit_price or self.price[symbol]) < target) if side == "long" else True, "bad bracket"
        oid, sid, tid = self._id(), self._id(), self._id()
        exit_side = "sell" if side == "long" else "buy"
        lim = limit_price or self.price[symbol] * (1.01 if side == "long" else 0.99)
        self.orders[oid] = {"id": oid, "symbol": symbol, "kind": "entry", "side": "buy" if side == "long" else "sell",
                            "qty": qty, "limit": lim, "status": "new", "legs": [sid, tid], "submitted": SIM["now"]}
        self.orders[sid] = {"id": sid, "symbol": symbol, "kind": "stop", "side": exit_side, "qty": qty, "price": stop,
                            "status": "held", "oco": [tid]}
        self.orders[tid] = {"id": tid, "symbol": symbol, "kind": "target", "side": exit_side, "qty": qty,
                            "price": target, "status": "held", "oco": [sid]}
        self._match()
        return oid

    def protect(self, symbol, qty, side, stop, target):
        self._maybe_fail("protect")
        sid, tid = self._id(), self._id()
        exit_side = "sell" if side == "long" else "buy"
        self.orders[sid] = {"id": sid, "symbol": symbol, "kind": "stop", "side": exit_side, "qty": qty, "price": stop,
                            "status": "new", "oco": [tid]}
        self.orders[tid] = {"id": tid, "symbol": symbol, "kind": "target", "side": exit_side, "qty": qty,
                            "price": target, "status": "new", "oco": [sid]}
        return sid

    def open_orders(self, symbol=None):
        self._maybe_fail("open_orders")
        return [{"id": o["id"], "symbol": o["symbol"], "side": o["side"],
                 "type": "stop" if o["kind"] == "stop" else "limit", "qty": o["qty"],
                 "stop_price": o.get("price") if o["kind"] == "stop" else None,
                 "limit_price": o.get("price") if o["kind"] == "target" else o.get("limit")}
                for o in self.orders.values() if o["status"] in ("new", "held")
                and (symbol is None or o["symbol"] == symbol)]

    def move_stop(self, oid, price):
        self._maybe_fail("move_stop")
        self.orders[oid]["price"] = price

    def cancel_order(self, oid):
        o = self.orders.get(oid)
        if o and o["status"] in ("new", "held"):
            o["status"] = "canceled"
            for leg in o.get("legs", []):
                self.orders[leg]["status"] = "canceled"

    def cancel_symbol_orders(self, symbol, wait_seconds=0):
        for o in self.orders.values():
            if o["symbol"] == symbol and o["status"] in ("new", "held"):
                o["status"] = "canceled"

    def market_exit(self, symbol, qty, side):
        self._maybe_fail("market_exit")
        oid = self._id()
        o = {"id": oid, "symbol": symbol, "kind": "market", "side": "sell" if side == "long" else "buy",
             "qty": qty, "status": "new"}
        self.orders[oid] = o
        self._fill(symbol, -qty if side == "long" else qty, self.price[symbol], o)
        return oid

    def trade_notional(self, symbol, side, dollars):
        self._maybe_fail("trade_notional")
        oid = self._id()
        q = dollars / self.price[symbol]
        if side == "sell":
            q = min(q, self.pos.get(symbol, {}).get("qty", 0))
        o = {"id": oid, "symbol": symbol, "kind": "notional", "side": side, "qty": q, "status": "new"}
        self.orders[oid] = o
        if q > 0:
            self._fill(symbol, q if side == "buy" else -q, self.price[symbol], o)
        return oid

    def order_fill_price(self, oid):
        self._maybe_fail("order_fill_price")
        o = self.orders.get(oid)
        return o.get("filled_avg_price") if o else None

    def wait_fill(self, oid, seconds=10):
        return self.order_fill_price(oid)

    def order_status(self, oid):
        return self.orders[oid]["status"]

    def last_exit_price(self, symbol, side, after):
        self._maybe_fail("last_exit_price")
        sign = -1 if side == "long" else 1
        f = [x for x in self.fills if x["symbol"] == symbol and x["time"] >= after and x["qty"] * sign > 0]
        q = sum(abs(x["qty"]) for x in f)
        return sum(abs(x["qty"]) * x["price"] for x in f) / q if q else None

    def close_position(self, symbol):
        self._maybe_fail("close_position")
        self.cancel_symbol_orders(symbol)
        p = self.pos.get(symbol)
        if p:
            oid = self._id()
            o = {"id": oid, "symbol": symbol, "kind": "market", "side": "sell" if p["qty"] > 0 else "buy",
                 "qty": abs(p["qty"]), "status": "new"}
            self.orders[oid] = o
            self._fill(symbol, -p["qty"], self.price[symbol], o)

    def close_all(self):
        self._maybe_fail("close_all")
        for o in self.orders.values():
            if o["status"] in ("new", "held"):
                o["status"] = "canceled"
        for s in list(self.pos):
            self.close_position(s)


# ----------------------------------------------------------------------------- flaky AI
class SimLLM:
    model = "sim"
    _schema_ok = True

    def __init__(self, rng, fault):
        self.rng, self.fault = rng, fault
        self.stats = {"calls": 0, "failures": 0, "retries": 0, "seconds": 0.0}

    def status(self):
        return (self.rng.random() > self.fault), "sim"

    def installed_models(self):
        return ["sim"]

    def summary(self):
        return "sim"

    def chat_json(self, system, user, schema=None):
        r = self.rng.random()
        if r < self.fault:
            raise TimeoutError("simulated AI timeout")
        if r < 2 * self.fault:
            return {}                                         # garbage answer
        if r < 3 * self.fault:
            return {"action": "BUY!!", "confidence": "very", "approve": "maybe"}   # wrong types
        if "Recent news for" in user:
            return {"score": self.rng.uniform(-1, 1), "catalyst": self.rng.random() < 0.5, "summary": "sim"}
        if "Candidate:" in user:
            return {"approve": self.rng.random() < 0.8, "reason": "sim"}
        if "Return JSON: {\"action\"" in user or '"action": "buy" | "short" | "skip"' in user:
            return {"action": self.rng.choice(["buy", "buy", "short", "skip"]),
                    "confidence": self.rng.uniform(0.5, 1.0), "reasoning": "sim"}
        if "Today's trades" in user:
            return {"review": "sim", "new_lessons": ["Sim lesson " + str(self.rng.randint(0, 9))],
                    "retire_lessons": []}
        if "outlook" in user:
            return {"outlook": "neutral", "summary": "sim", "focus": [{"symbol": "BAL", "bias": "long", "reason": "x"}],
                    "risks": []}
        return {"summary": "sim", "watch_tomorrow": []}


# ----------------------------------------------------------------------------- driver + invariants
def run(days=20, faults=0.05, seed=1, verbose=False, kill_on_day=None, gap=True, crash="step_aside") -> dict:
    import app.engine as engine_mod
    import app.journal as journal_mod
    import app.learner as learner_mod
    import app.swing_live as swing_mod
    import app.gap_live as gap_mod
    rng = random.Random(seed)
    np.random.seed(seed)
    for mod in (engine_mod, journal_mod, learner_mod, swing_mod, gap_mod):
        mod.datetime = SimDatetime
    engine_mod.time = SimTime
    import app.news as news_mod

    class _NoNet:                                     # the simulation never touches the internet
        @staticmethod
        def get(*a, **k):
            raise ConnectionError("network disabled in simulation")
    news_mod.requests = _NoNet
    real_sleep = real_time.sleep
    real_time.sleep = lambda s: None
    try:
        return _run(days, faults, rng, verbose, kill_on_day, engine_mod, gap, crash)
    finally:
        real_time.sleep = real_sleep


def _run(days, faults, rng, verbose, kill_on_day, engine_mod, gap=True, crash_mode="step_aside"):
    from app.journal import Journal
    start = date(2026, 3, 2)
    SIM["now"] = datetime(2026, 3, 2, 7, 0, tzinfo=NY)
    EARLY_CLOSE.clear()
    EARLY_CLOSE.add(start + timedelta(days=10))
    import itertools
    symbols = ["".join(t) for t in itertools.islice(itertools.product("BCDFGHJK", "AEIOU", "LMNR"), 40)]
    broker = SimBroker(symbols, rng, faults, start)
    s = copy.deepcopy(DEFAULTS)
    s["watchlist"] = symbols[:15]
    s["day_trading"]["enabled"] = True
    s["allow_short"] = True
    s["swing"].update(enabled=True, use_ai_review=True, idle_cash_in_index=True, crash_protection=crash_mode,
                      require_uptrend=False, hold_days=5)
    s["swing"]["insider"]["min_value"] = 1_000_000
    # confirmed-gap day trading, with loose filters so simulated (small) gaps qualify
    s["gap_trading"].update(enabled=gap, min_gap=0.003, min_dollar_volume=0, min_price=1, allow_short=True)
    s["alerts"]["desktop"] = False
    s["smart_money"]["enabled"] = False
    db = Path(tempfile.mkdtemp()) / "stress.db"
    journal = Journal(db)
    events = []
    eng = engine_mod.Engine(s, broker, SimLLM(rng, faults), journal, emit=lambda k, d: events.append(k))
    violations, errors = [], []
    orig_log = eng._log

    swing_log = []

    def capture(msg, level=20):
        if level >= 40:
            errors.append(msg)
        if "wing" in msg or "rash" in msg:
            swing_log.append(f"{SIM['now']:%m-%d %H:%M} {msg[:140]}")
        orig_log(msg, level)
    eng._log = capture

    def violate(msg):
        violations.append(f"{SIM['now']:%Y-%m-%d %H:%M} {msg}")
        if verbose:
            print("VIOLATION:", violations[-1], flush=True)

    tracemalloc.start()
    mem0 = None
    t0 = real_time.time()
    unprotected_since: dict[str, datetime] = {}
    killed_at = None
    orders_at_kill = None
    day = start
    trading_days = 0
    max_day_positions = max_swing_positions = 0
    while trading_days < days:
        h = market_hours(day)
        crash = trading_days == days // 2
        SIM["now"] = datetime.combine(day, datetime.min.time(), NY) + timedelta(hours=7)
        if h:
            broker.new_day(crash=crash)
            # a few fresh $1M+ insider purchases each day, priced at the market
            for k in range(2):
                sym = rng.choice(symbols)
                journal.add_insider_buy({"accession": f"{day}-{k}-{sym}", "symbol": sym, "owner": f"CEO {sym}",
                                         "title": "CEO", "trade_date": day.isoformat(), "shares": 1e4,
                                         "price": broker.price[sym], "value": 2e6, "filed": day.isoformat()})
        trades_today_start = len(journal.closed_trades(10 ** 6)) + len(journal.open_trades())
        # one tick per simulated minute while open; every 10 minutes otherwise
        end = datetime.combine(day, datetime.min.time(), NY) + timedelta(hours=18)
        halted_seen = False
        while SIM["now"] < end:
            open_ = broker.is_open()
            if open_:
                broker.tick_minute()
            try:
                eng._tick()
            except Exception as e:                       # _loop() catches these; count them
                errors.append(f"tick raised {type(e).__name__}: {e}")
            if kill_on_day is not None and trading_days == kill_on_day and open_ and killed_at is None \
                    and SIM["now"].hour == 11:
                eng.kill_switch()
                killed_at = SIM["now"]
                orders_at_kill = len([o for o in broker.orders.values() if o["kind"] == "entry" or (o["kind"] == "notional" and o["side"] == "buy")])
            # ---------------- invariants
            opens = journal.open_trades()
            day_syms = {t["symbol"] for t in opens if t["style"] == "day" and t["setup"] != "gap_confirmed"}
            gap_syms = {t["symbol"] for t in opens if t["setup"] == "gap_confirmed"}
            swing_syms = {t["symbol"] for t in opens if t["style"] == "swing"}
            held = {p: v for p, v in broker.pos.items() if p not in ("SPY", "SH")}
            max_day_positions = max(max_day_positions, len([x for x in held if x in day_syms]))
            max_swing_positions = max(max_swing_positions, len([x for x in held if x in swing_syms]))
            if len([x for x in held if x in day_syms]) > s["risk"]["max_open_positions"]:
                violate(f"too many day positions: {len(day_syms)}")
            if len([x for x in held if x in gap_syms]) > s["gap_trading"]["max_positions"]:
                violate(f"too many gap positions: {len(gap_syms)}")
            if len([x for x in held if x in swing_syms]) > s["swing"]["max_positions"]:
                violate(f"too many swing positions: {len(swing_syms)}")
            eq = broker.equity()
            for sym, p in held.items():
                val = abs(p["qty"]) * p["avg"]
                limit = max(s["risk"]["max_position_pct"], s["swing"]["max_position_pct"], s["gap_trading"]["max_position_pct"]) * max(eq, 1)
                if val > limit * 1.25:
                    violate(f"{sym} position ${val:,.0f} exceeds size limit ${limit:,.0f}")
                has_stop = any(o["symbol"] == sym and o["kind"] == "stop" and o["status"] in ("new", "held")
                               for o in broker.orders.values())
                if not has_stop and open_:
                    unprotected_since.setdefault(sym, SIM["now"])
                    if SIM["now"] - unprotected_since[sym] > timedelta(minutes=10) and killed_at is None:
                        violate(f"{sym} has had NO stop-loss for over 10 minutes")
                        unprotected_since[sym] = SIM["now"]
                else:
                    unprotected_since.pop(sym, None)
            if killed_at is not None:
                n_entries = len([o for o in broker.orders.values() if o["kind"] == "entry" or (o["kind"] == "notional" and o["side"] == "buy")])
                if n_entries > orders_at_kill:
                    violate("a buy/entry order was placed AFTER the kill switch")
                    orders_at_kill = n_entries
            if eng.risk.halted_reason and "Daily loss" in eng.risk.halted_reason:
                halted_seen = True
            step = 1 if open_ or (SIM["now"].hour in (9, 16) and SIM["now"].minute < 40) else 10
            SIM["now"] += timedelta(minutes=step)
        if h:
            broker.end_day()
            trading_days += 1
            # no day trade may survive the night
            overnight = [t["symbol"] for t in journal.open_trades() if t["style"] == "day"
                         and t["symbol"] in broker.pos]
            if overnight and killed_at is None:
                violate(f"day trades held overnight: {overnight}")
            entries_today = len(journal.closed_trades(10 ** 6)) + len(journal.open_trades()) - trades_today_start
            if entries_today > s["risk"]["max_trades_per_day"] + s["swing"]["max_positions"] + s["gap_trading"]["max_positions"]:
                violate(f"too many new trades in one day: {entries_today}")
            if mem0 is None:
                mem0 = tracemalloc.get_traced_memory()[0]
            if verbose:
                print(f"{day} equity ${broker.equity():,.0f} positions {len(broker.pos)} errors {len(errors)} "
                      f"violations {len(violations)}{' HALTED' if halted_seen else ''}", flush=True)
        day += timedelta(days=1)
    mem1 = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    closed = journal.closed_trades(10 ** 6)
    bad_closed = [t for t in closed if t["exit_price"] is None or t["pnl"] is None or math.isnan(t["pnl"])]
    if bad_closed:
        violate(f"{len(bad_closed)} closed trades without a valid exit/P&L")
    return {"violations": violations, "errors": errors, "swing_log": swing_log, "trades": len(closed),
            "swing_trades": len([t for t in closed if t["style"] == "swing"]),
            "gap_trades": len([t for t in closed if t["setup"] == "gap_confirmed"]),
            "open": len(journal.open_trades()), "equity": broker.equity(), "broker_calls": broker.calls,
            "faults": broker.faults, "seconds": real_time.time() - t0, "max_day_positions": max_day_positions,
            "max_swing_positions": max_swing_positions, "memory_growth_mb": (mem1 - (mem0 or mem1)) / 1e6,
            "db_mb": Path(journal.conn.execute("PRAGMA database_list").fetchone()[2]).stat().st_size / 1e6}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--faults", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--kill-on-day", type=int, default=None)
    ap.add_argument("--no-gap", action="store_true", help="leave confirmed-gap day trading off")
    ap.add_argument("--crash", default="step_aside", choices=["off", "step_aside", "short"])
    a = ap.parse_args()
    r = run(a.days, a.faults, a.seed, verbose=True, kill_on_day=a.kill_on_day, gap=not a.no_gap, crash=a.crash)
    print({k: v for k, v in r.items() if k not in ("violations", "errors", "swing_log")})
    if a.days <= 5:
        print("swing log:", *r["swing_log"][:25], sep="\n  ")
    print(f"{len(r['errors'])} errors logged (expected with faults on). First few:", r["errors"][:5])
    print(f"{len(r['violations'])} SAFETY VIOLATIONS:", *r["violations"][:30], sep="\n  ")
    sys.exit(1 if r["violations"] else 0)


if __name__ == "__main__":
    main()
