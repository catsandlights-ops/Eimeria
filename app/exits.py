"""Exit rules, shared by the live bot and the backtester.

  1. Partial profit: when a trade is up 1R (R = the stop-loss distance), sell half
     and move the stop to the entry price, so the rest of the trade can't lose money.
  2. Trailing stop: after that, the stop follows the price up (for shorts, down),
     staying 1.5 x ATR behind the best price reached.
  3. Time stop: if a trade hasn't gone anywhere after 90 minutes, close it.
The stop-loss/take-profit orders at the broker remain the hard safety net.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class TradeState:
    side: str            # 'long' or 'short'
    entry: float
    qty: int
    initial_stop: float
    stop: float
    target: float
    partial_taken: bool = False
    best_price: float = 0.0

    @property
    def direction(self) -> int:
        return 1 if self.side == "long" else -1

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry - self.initial_stop) or self.entry * 0.01

    def gain_r(self, price: float) -> float:
        return (price - self.entry) * self.direction / self.risk_per_share


def manage(state: TradeState, price: float, atr: float, minutes_open: float, cfg: dict) -> dict | None:
    """Decide the next exit action for an open trade. Updates state.best_price.

    Returns None, or one of:
      {"type": "partial", "qty": n, "new_stop": price}
      {"type": "trail", "new_stop": price}
      {"type": "time_exit"}
    """
    d = state.direction
    if not state.best_price or (price - state.best_price) * d > 0:
        state.best_price = price
    r = state.gain_r(price)

    if cfg.get("partial_enabled", True) and not state.partial_taken and r >= cfg["partial_at_r"] and state.qty >= 2:
        n = max(1, math.floor(state.qty * cfg["partial_fraction"]))
        return {"type": "partial", "qty": n, "new_stop": state.entry}

    if cfg.get("trailing_enabled", True) and state.partial_taken and atr > 0:
        candidate = state.best_price - d * cfg["trail_atr_mult"] * atr
        min_move = 0.1 * state.risk_per_share
        if (candidate - state.stop) * d > min_move and (price - candidate) * d > 0:
            return {"type": "trail", "new_stop": candidate}

    if cfg.get("time_stop_minutes") and minutes_open >= cfg["time_stop_minutes"] \
            and not state.partial_taken and r < cfg["time_stop_min_r"]:
        return {"type": "time_exit"}
    return None


def apply(state: TradeState, action: dict) -> None:
    """Update the state after an action was carried out."""
    if action["type"] == "partial":
        state.qty -= action["qty"]
        state.partial_taken = True
        state.stop = action["new_stop"]
    elif action["type"] == "trail":
        state.stop = action["new_stop"]


def bar_hit(state: TradeState, bar_open: float, high: float, low: float) -> tuple[str, float] | None:
    """Backtest helper: did this bar hit the stop or the target? Assumes the stop is hit first
    if both are inside one bar (the cautious assumption). Gaps fill at the bar's open."""
    if state.side == "long":
        if low <= state.stop:
            return "stop", min(state.stop, bar_open)
        if high >= state.target:
            return "target", max(state.target, bar_open)
    else:
        if high >= state.stop:
            return "stop", max(state.stop, bar_open)
        if low <= state.target:
            return "target", min(state.target, bar_open)
    return None
