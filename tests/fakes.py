"""Fake broker, fake AI and synthetic price data for tests (no keys, no network, no money)."""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from app.indicators import NY

DAY = datetime(2026, 11, 10, tzinfo=NY)


def make_bars(trend: float, n: int = 40, start_price: float = 100.0, day: datetime = DAY, seed: int = 1):
    idx = [day.replace(hour=9, minute=30) + timedelta(minutes=5 * i) for i in range(n)]
    rng = np.random.default_rng(seed)
    close = start_price + np.cumsum(trend + rng.normal(0, 0.05, n))
    return pd.DataFrame({"open": close - trend / 2, "high": close + 0.1, "low": close - 0.1, "close": close,
                         "volume": np.full(n, 20000.0)}, index=pd.DatetimeIndex(idx).tz_convert("UTC"))


def make_daily(price: float = 100.0, end: datetime = DAY, days: int = 20):
    idx = [end - timedelta(days=i) for i in range(days, 0, -1)]
    return pd.DataFrame({"open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 300000.0},
                        index=pd.DatetimeIndex(idx).tz_convert("UTC"))


def make_history(trend_by_day: list[float], start_price=100.0, seed=0):
    """Several full sessions (78 five-minute bars each) plus matching daily bars."""
    frames, daily_rows, price = [], [], start_price
    rng = np.random.default_rng(seed)
    for i, trend in enumerate(trend_by_day):
        day = DAY + timedelta(days=i)
        idx = [day.replace(hour=9, minute=30) + timedelta(minutes=5 * k) for k in range(78)]
        close = price + np.cumsum(trend + rng.normal(0, 0.03, 78))
        frames.append(pd.DataFrame({"open": np.r_[price, close[:-1]], "high": close + 0.08, "low": close - 0.08,
                                    "close": close, "volume": np.full(78, 20000.0)},
                                   index=pd.DatetimeIndex(idx).tz_convert("UTC")))
        daily_rows.append((day, close[-1]))
        price = close[-1]
    intraday = pd.concat(frames)
    hist = make_daily(start_price, DAY)
    extra = pd.DataFrame({"open": [c for _, c in daily_rows], "high": [c for _, c in daily_rows],
                          "low": [c for _, c in daily_rows], "close": [c for _, c in daily_rows],
                          "volume": 300000.0}, index=pd.DatetimeIndex([d for d, _ in daily_rows]).tz_convert("UTC"))
    return intraday, pd.concat([hist, extra])


class FakeBroker:
    def __init__(self):
        self.orders = []
        self.held = []
        self.working = []          # open exit orders
        self.protects = []
        self.exits = []
        self.moved = []
        self.closed = []
        self.closed_all = False
        self.equity = 10000.0
        self.bar_data = {"UP": make_bars(0.08), "FLAT": make_bars(0.0)}
        self.quotes = {}           # symbol -> {"bid", "ask"}; missing = no quote available
        self.cancelled = []
        self.filled = True

    def account(self):
        return {"equity": self.equity, "last_equity": 10000.0, "buying_power": 20000.0, "cash": 10000.0,
                "daytrade_count": 0, "pattern_day_trader": False}

    def clock(self):
        return {"is_open": True, "timestamp": DAY.replace(hour=11), "next_open": DAY + timedelta(days=1),
                "next_close": DAY.replace(hour=16)}

    def positions(self):
        return list(self.held)

    def bars(self, symbols, minutes=5, days_back=1):
        return {s: self.bar_data[s] for s in symbols if s in self.bar_data}

    def daily_bars(self, symbols, days=40):
        return {s: make_daily(float(self.bar_data[s]["open"].iloc[0])) for s in symbols if s in self.bar_data}

    def movers(self, top=10):
        return []

    def news(self, symbols, hours_back=18, limit=50):
        return [{"headline": "UP Corp beats earnings, raises guidance", "summary": "", "symbols": ["UP"],
                 "source": "test", "url": "", "time": datetime.now(NY)},
                {"headline": "UP Corp signs large contract", "summary": "", "symbols": ["UP"],
                 "source": "test", "url": "", "time": datetime.now(NY)}]

    def is_shortable(self, s):
        return True

    paper = True
    order_states: dict = {}

    def account_details(self):
        return {**self.account(), "status": "ACTIVE", "trading_blocked": False, "account_blocked": False,
                "multiplier": 2.0}

    def daily_range(self, symbols, start, end, feed=None):
        return {s: make_daily(400.0, days=60) for s in symbols}

    def news_between(self, symbols, start, end, limit=30):
        return self.news(symbols)

    def order_status(self, oid):
        return self.order_states.get(oid, "new")

    def quote(self, symbol):
        return self.quotes.get(symbol)

    def cancel_order(self, oid):
        self.cancelled.append(oid)
        if getattr(self, "cancel_works", True):
            self.order_states = {**self.order_states, oid: "canceled"}

    def submit_bracket(self, symbol, qty, side, stop, target, limit_price=None):
        self.orders.append((symbol, qty, side, stop, target))
        self.limits = getattr(self, "limits", []) + [limit_price]
        if not self.filled:
            return f"order-{len(self.orders)}"
        self.held.append({"symbol": symbol, "qty": qty, "side": side, "avg_entry": 103, "current_price": 103,
                          "unrealized_pl": 0, "unrealized_plpc": 0})
        self.working.append({"id": f"stop-{len(self.orders)}", "symbol": symbol,
                             "side": "sell" if side == "long" else "buy", "type": "stop", "qty": qty,
                             "stop_price": stop, "limit_price": None})
        return f"order-{len(self.orders)}"

    def protect(self, symbol, qty, side, stop, target):
        self.protects.append((symbol, qty, side, stop, target))
        self.working.append({"id": f"oco-{len(self.protects)}", "symbol": symbol,
                             "side": "sell" if side == "long" else "buy", "type": "limit", "qty": qty,
                             "stop_price": stop, "limit_price": target})
        return "oco"

    def open_orders(self, symbol=None):
        return [o for o in self.working if symbol is None or o["symbol"] == symbol]

    def cancel_symbol_orders(self, symbol, wait_seconds=0):
        self.working = [o for o in self.working if o["symbol"] != symbol]

    def market_exit(self, symbol, qty, side):
        self.exits.append((symbol, qty, side))
        for p in self.held:
            if p["symbol"] == symbol:
                p["qty"] -= qty
        return "exit-1"

    def wait_fill(self, oid, seconds=0):
        return None

    def move_stop(self, oid, price):
        self.moved.append((oid, price))

    def order_fill_price(self, oid):
        return 103.0 if self.filled else None

    def last_exit_price(self, symbol, side, after):
        return 106.0

    def close_position(self, symbol):
        self.closed.append(symbol)
        self.held = [p for p in self.held if p["symbol"] != symbol]

    def close_all(self):
        self.closed_all = True
        self.held = []
        self.working = []


class FakeLLM:
    _schema_ok = True
    model = "fake"

    def __init__(self, confidence=0.8):
        self.confidence = confidence
        self.prompts = []

    def status(self):
        return True, "ok"

    def installed_models(self):
        return [self.model]

    def summary(self):
        return f"{len(self.prompts)} requests"

    def chat_json(self, system, user, schema=None):
        self.prompts.append(user)
        if "Recent news for" in user:
            return {"score": 0.8, "catalyst": True, "summary": "Strong earnings beat."}
        if "durable good news" in user:
            return {"approve": True, "reason": "real contract"}
        if "Today's trades" in user:
            return {"review": "Good day.", "new_lessons": ["Earnings beats with high volume worked well."],
                    "retire_lessons": []}
        return {"action": "buy", "confidence": self.confidence,
                "reasoning": "Uptrend above VWAP with earnings catalyst."}
