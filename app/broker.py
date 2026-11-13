"""Thin wrapper around Alpaca's API (orders, account, prices, news, movers).

Everything the rest of the app needs from the broker goes through this class,
so the bot can be tested with a fake broker and swapped to another broker later.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger(__name__)


def is_auth_error(e: Exception) -> bool:
    t = str(e).lower()
    return any(w in t for w in ("unauthorized", "forbidden", "401", "403"))


def diagnose_keys(api_key: str, secret_key: str, paper: bool, client_factory=None) -> str:
    """Plain-English explanation of why Alpaca rejected these keys. Only does read-only account checks."""
    if client_factory is None:
        from alpaca.trading.client import TradingClient
        client_factory = TradingClient
    mode, other = ("paper", "live") if paper else ("live", "paper")
    tips = []
    if len(api_key) > len(secret_key):
        tips.append("The Key ID is longer than the Secret Key: they're probably swapped. The Key ID is the "
                    "shorter one (about 20 characters).")
    if api_key[:2] == "AK" and paper:
        tips.append("The Key ID starts with 'AK', which is how LIVE keys usually start; paper keys usually start "
                    "with 'PK'.")
    if api_key[:2] == "PK" and not paper:
        tips.append("The Key ID starts with 'PK', which is how PAPER keys usually start, but Mode is live.")
    try:                                   # do these keys work for the OTHER kind of account? (read-only)
        client_factory(api_key, secret_key, paper=not paper).get_account()
        return (f"These are {other.upper()}-account keys, but Mode is set to '{mode}'. Either generate keys on "
                f"your Alpaca {mode.upper()} dashboard (account switcher, top left), or change Mode in Settings.")
    except Exception:
        pass
    tips.append(f"Alpaca rejected these keys for the {mode} account. Most likely: the Secret Key is from an older "
                f"generation (each 'Generate/Regenerate' makes the previous secret stop working), or part of a key "
                f"wasn't copied. On alpaca.markets, check the switcher says {mode.capitalize()}, click Regenerate, "
                f"and copy BOTH new values.")
    tips.append(f"Key ID entered: {api_key[:4]}... ({len(api_key)} characters); Secret: {len(secret_key)} characters.")
    return "\n\n".join(tips)


class AlpacaBroker:
    def __init__(self, api_key: str, secret_key: str, paper: bool = True, feed: str = "iex"):
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.historical.news import NewsClient
        from alpaca.trading.client import TradingClient

        if not api_key or not secret_key:
            raise ValueError("Alpaca API keys are missing. Add them on the Settings tab.")
        self.paper = paper
        self.feed = feed
        self.trading = TradingClient(api_key, secret_key, paper=paper)
        self.data = StockHistoricalDataClient(api_key, secret_key)
        self.news_client = NewsClient(api_key, secret_key)
        try:
            from alpaca.data.historical.screener import ScreenerClient
            self.screener = ScreenerClient(api_key, secret_key)
        except Exception:  # older library versions
            self.screener = None

    # ---------- account ----------
    def account(self) -> dict:
        a = self.trading.get_account()
        return {
            "equity": float(a.equity),
            "last_equity": float(a.last_equity),
            "buying_power": float(a.buying_power),
            "cash": float(a.cash),
            "daytrade_count": int(a.daytrade_count or 0),
            "pattern_day_trader": bool(a.pattern_day_trader),
        }

    def account_details(self) -> dict:
        """Account plus status flags (used by the self-test)."""
        a = self.trading.get_account()
        status = getattr(a.status, "value", a.status)
        return {**self.account(), "status": str(status), "trading_blocked": bool(a.trading_blocked),
                "account_blocked": bool(a.account_blocked), "multiplier": float(a.multiplier or 1)}

    def clock(self) -> dict:
        c = self.trading.get_clock()
        return {"is_open": c.is_open, "timestamp": c.timestamp,
                "next_open": c.next_open, "next_close": c.next_close}

    def positions(self) -> list[dict]:
        out = []
        for p in self.trading.get_all_positions():
            out.append({
                "symbol": p.symbol,
                "qty": abs(float(p.qty)),
                "side": "long" if float(p.qty) > 0 else "short",
                "avg_entry": float(p.avg_entry_price),
                "current_price": float(p.current_price or 0),
                "unrealized_pl": float(p.unrealized_pl or 0),
                "unrealized_plpc": float(p.unrealized_plpc or 0),
            })
        return out

    def is_shortable(self, symbol: str) -> bool:
        try:
            a = self.trading.get_asset(symbol)
            return bool(a.shortable and a.easy_to_borrow)
        except Exception:
            return False

    def tradable_symbols(self) -> list[str]:
        """Every active, tradable US stock/ETF on the main exchanges (plain tickers only)."""
        import re
        from alpaca.trading.enums import AssetClass, AssetStatus
        from alpaca.trading.requests import GetAssetsRequest
        assets = self.trading.get_all_assets(GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY))
        ok = {"NYSE", "NASDAQ", "ARCA", "NYSEARCA", "AMEX", "BATS"}
        return sorted(a.symbol for a in assets if a.tradable and str(getattr(a.exchange, "value", a.exchange)) in ok
                      and re.fullmatch(r"[A-Z]{1,5}", a.symbol or ""))

    # ---------- market data ----------
    def _feed(self, feed: str | None):
        from alpaca.data.enums import DataFeed
        return DataFeed(feed or self.feed)

    def bars(self, symbols: list[str], minutes: int = 5, days_back: int = 1) -> dict[str, pd.DataFrame]:
        """Recent intraday bars per symbol (live feed)."""
        start = datetime.now(timezone.utc) - timedelta(days=days_back)
        return self._get_bars(symbols, start, None, minutes, None, "split")

    def daily_bars(self, symbols: list[str], days: int = 40) -> dict[str, pd.DataFrame]:
        start = datetime.now(timezone.utc) - timedelta(days=days)
        return self._get_bars(symbols, start, None, 0, None, "split")

    def bars_range(self, symbols, start: date, end: date, minutes: int = 5, feed: str | None = None):
        """Historical intraday bars between two dates (for backtesting)."""
        return self._get_bars(symbols, *self._range(start, end), minutes, feed, "split")

    def daily_range(self, symbols, start: date, end: date, feed: str | None = None, adjustment: str = "all"):
        """Daily history for backtests. "all" adjusts for splits AND dividends (otherwise a 2-for-1 split looks
        like a 50% crash, a reverse split like a huge gain, and dividends are missing). "split" adjusts for
        splits only, so prices can be compared with the prices in SEC filings."""
        return self._get_bars(symbols, *self._range(start, end), 0, feed, adjustment)

    @staticmethod
    def _range(start: date, end: date):
        s = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
        e = datetime(end.year, end.month, end.day, 23, 59, tzinfo=timezone.utc)
        # The free plan allows full-market (SIP) history only if it ends 15+ minutes ago
        e = min(e, datetime.now(timezone.utc) - timedelta(minutes=16))
        return s, e

    def _get_bars(self, symbols, start, end, minutes, feed, adjustment: str = "split") -> dict[str, pd.DataFrame]:
        """adjustment: "split" (prices comparable across splits) or "all" (also dividends, for backtests)."""
        from alpaca.data.enums import Adjustment
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        if not symbols:
            return {}
        tf = TimeFrame(minutes, TimeFrameUnit.Minute) if minutes else TimeFrame.Day
        symbols = list(symbols)
        try:
            req = StockBarsRequest(symbol_or_symbols=symbols, start=start, end=end, timeframe=tf,
                                   feed=self._feed(feed), adjustment=Adjustment(adjustment))
            return self._split(self.data.get_stock_bars(req).df)
        except Exception as e:
            # One unknown/delisted ticker can make the whole request fail. Split the list in half
            # and retry, so only the bad ticker is skipped.
            if len(symbols) == 1:
                log.info("No price data for %s: %s", symbols[0], e)
                return {}
            if any(w in str(e).lower() for w in ("forbidden", "unauthorized", "subscription", "rate limit", "429")):
                raise
            mid = len(symbols) // 2
            return {**self._get_bars(symbols[:mid], start, end, minutes, feed, adjustment),
                    **self._get_bars(symbols[mid:], start, end, minutes, feed, adjustment)}

    @staticmethod
    def _split(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
        if df is None or df.empty:
            return {}
        out = {}
        for sym in df.index.get_level_values(0).unique():
            out[sym] = df.xs(sym, level=0).sort_index()
        return out

    def movers(self, top: int = 10) -> list[str]:
        """Today's biggest gainers/losers and most active stocks."""
        if self.screener is None:
            return []
        syms: list[str] = []
        try:
            from alpaca.data.requests import MarketMoversRequest, MostActivesRequest
            m = self.screener.get_market_movers(MarketMoversRequest(top=top))
            syms += [x.symbol for x in (m.gainers or [])] + [x.symbol for x in (m.losers or [])]
            a = self.screener.get_most_actives(MostActivesRequest(top=top))
            syms += [x.symbol for x in (a.most_actives or [])]
        except Exception as e:
            log.warning("Could not load market movers: %s", e)
        return list(dict.fromkeys(syms))

    def news(self, symbols: list[str] | None, hours_back: int = 18, limit: int = 50) -> list[dict]:
        from alpaca.data.requests import NewsRequest

        start = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        req = NewsRequest(symbols=",".join(symbols) if symbols else None, start=start,
                          limit=min(limit, 50), sort="desc", exclude_contentless=False)
        try:
            res = self.news_client.get_news(req)
        except Exception as e:
            log.warning("Alpaca news failed: %s", e)
            return []
        items = res.data.get("news", []) if hasattr(res, "data") else []
        return [{
            "headline": n.headline, "summary": n.summary or "", "symbols": list(n.symbols or []),
            "source": n.source, "url": n.url, "time": n.created_at,
        } for n in items]

    def news_between(self, symbols: list[str], start: datetime, end: datetime, limit: int = 30) -> list[dict]:
        """Historical news for a time window (Alpaca's archive goes back to 2015)."""
        from alpaca.data.requests import NewsRequest
        try:
            res = self.news_client.get_news(NewsRequest(symbols=",".join(symbols), start=start, end=end,
                                                        limit=min(limit, 50), sort="desc"))
        except Exception as e:
            log.warning("Alpaca news (history) failed: %s", e)
            return []
        items = res.data.get("news", []) if hasattr(res, "data") else []
        return [{"headline": n.headline, "summary": n.summary or "", "symbols": list(n.symbols or []),
                 "source": n.source, "url": n.url, "time": n.created_at} for n in items]

    def news_window(self, start: datetime, end: datetime, max_items: int = 200) -> list[dict]:
        """All news (any company) published between start and end, newest first, up to max_items.
        Pages backwards in time 50 articles at a time."""
        from alpaca.data.requests import NewsRequest
        out, until = [], end
        while len(out) < max_items:
            try:
                res = self.news_client.get_news(NewsRequest(start=start, end=until, limit=50, sort="desc"))
            except Exception as e:
                log.warning("Alpaca news (window) failed: %s", e)
                break
            items = res.data.get("news", []) if hasattr(res, "data") else []
            batch = [{"headline": n.headline, "summary": n.summary or "", "symbols": list(n.symbols or []),
                      "source": n.source, "url": n.url, "time": n.created_at} for n in items]
            out += batch
            times = [n["time"] for n in batch if n["time"] is not None]
            if len(batch) < 50 or not times:
                break
            until = min(times) - timedelta(seconds=1)
        return out[:max_items]

    # ---------- orders ----------
    def quote(self, symbol: str) -> dict | None:
        """Latest bid/ask, or None if unavailable."""
        from alpaca.data.requests import StockLatestQuoteRequest
        try:
            q = self.data.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=symbol,
                                                                        feed=self._feed(None)))[symbol]
            bid, ask = float(q.bid_price or 0), float(q.ask_price or 0)
            return {"bid": bid, "ask": ask} if bid > 0 and ask >= bid else None
        except Exception as e:
            log.debug("quote %s failed: %s", symbol, e)
            return None

    def submit_bracket(self, symbol: str, qty: int, side: str, stop_price: float, target_price: float,
                       limit_price: float | None = None) -> str:
        """Entry with an automatic stop-loss and take-profit attached. With `limit_price`, the entry
        won't pay more than that (protects against bad fills). Good-till-cancelled, so the
        protection survives even if the app is closed."""
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import (LimitOrderRequest, MarketOrderRequest, StopLossRequest,
                                             TakeProfitRequest)

        common = dict(
            symbol=symbol, qty=qty,
            side=OrderSide.BUY if side == "long" else OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(target_price, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
        )
        req = LimitOrderRequest(limit_price=round(limit_price, 2), **common) if limit_price \
            else MarketOrderRequest(**common)
        return str(self.trading.submit_order(req).id)

    def order_status(self, order_id: str) -> str:
        o = self.trading.get_order_by_id(order_id)
        return str(getattr(o.status, "value", o.status)).lower()

    def cancel_order(self, order_id: str) -> None:
        try:
            self.trading.cancel_order_by_id(order_id)
        except Exception as e:
            log.debug("cancel %s: %s", order_id, e)

    def protect(self, symbol: str, qty: int, side: str, stop_price: float, target_price: float) -> str:
        """Attach a stop-loss + take-profit pair (one-cancels-other) to an existing position."""
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, StopLossRequest, TakeProfitRequest

        req = LimitOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.SELL if side == "long" else OrderSide.BUY,
            time_in_force=TimeInForce.GTC, order_class=OrderClass.OCO,
            take_profit=TakeProfitRequest(limit_price=round(target_price, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
        )
        return str(self.trading.submit_order(req).id)

    def open_orders(self, symbol: str | None = None) -> list[dict]:
        """All working orders (including stop-loss legs waiting to trigger)."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=False, limit=500,
                               symbols=[symbol] if symbol else None)
        out = []
        for o in self.trading.get_orders(req):
            for leg in [o] + list(o.legs or []):
                if str(leg.status.value if hasattr(leg.status, "value") else leg.status) in (
                        "filled", "canceled", "expired", "rejected", "replaced"):
                    continue
                out.append({"id": str(leg.id), "symbol": leg.symbol,
                            "side": "buy" if "buy" in str(leg.side).lower() else "sell",
                            "type": str(leg.order_type.value if hasattr(leg.order_type, "value") else leg.order_type),
                            "qty": float(leg.qty or 0),
                            "stop_price": float(leg.stop_price) if leg.stop_price else None,
                            "limit_price": float(leg.limit_price) if leg.limit_price else None})
        return out

    def move_stop(self, order_id: str, stop_price: float):
        from alpaca.trading.requests import ReplaceOrderRequest
        self.trading.replace_order_by_id(order_id, ReplaceOrderRequest(stop_price=round(stop_price, 2)))

    def cancel_symbol_orders(self, symbol: str, wait_seconds: float = 5.0):
        """Cancel every working order for a stock and wait until the shares are released."""
        for o in self.open_orders(symbol):
            try:
                self.trading.cancel_order_by_id(o["id"])
            except Exception as e:
                log.debug("cancel %s: %s", o["id"], e)
        deadline = time.time() + wait_seconds
        while time.time() < deadline and self.open_orders(symbol):
            time.sleep(0.5)

    def trade_notional(self, symbol: str, side: str, dollars: float) -> str:
        """Buy or sell a dollar amount (fractional shares) at market, e.g. for the index fund."""
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        req = MarketOrderRequest(symbol=symbol, notional=round(dollars, 2), time_in_force=TimeInForce.DAY,
                                 side=OrderSide.BUY if side == "buy" else OrderSide.SELL)
        return str(self.trading.submit_order(req).id)

    def market_exit(self, symbol: str, qty: int, side: str) -> str:
        """Sell (or buy back, for shorts) part of a position at market."""
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        req = MarketOrderRequest(symbol=symbol, qty=qty, time_in_force=TimeInForce.DAY,
                                 side=OrderSide.SELL if side == "long" else OrderSide.BUY)
        return str(self.trading.submit_order(req).id)

    def order_fill_price(self, order_id: str) -> float | None:
        try:
            o = self.trading.get_order_by_id(order_id)
            return float(o.filled_avg_price) if o.filled_avg_price else None
        except Exception:
            return None

    def wait_fill(self, order_id: str, seconds: float = 10.0) -> float | None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            p = self.order_fill_price(order_id)
            if p:
                return p
            time.sleep(0.5)
        return None

    def last_exit_price(self, symbol: str, side: str, after: datetime) -> float | None:
        """Average price of the orders that closed a position in `symbol` (handles partial exits)."""
        from alpaca.trading.enums import OrderSide, QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        exit_side = OrderSide.SELL if side == "long" else OrderSide.BUY
        orders = self.trading.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.CLOSED, symbols=[symbol], after=after, nested=True, limit=100))
        qty = value = 0.0
        for o in orders:
            for leg in [o] + list(o.legs or []):
                if leg.side == exit_side and leg.filled_avg_price and leg.filled_qty:
                    q = float(leg.filled_qty)
                    qty += q
                    value += q * float(leg.filled_avg_price)
        return value / qty if qty else None

    def close_position(self, symbol: str):
        self.cancel_symbol_orders(symbol)
        self.trading.close_position(symbol)

    def close_all(self):
        """Emergency / end-of-day: cancel every order and close every position."""
        self.trading.close_all_positions(cancel_orders=True)
