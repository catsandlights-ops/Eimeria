"""Risk manager: the bot's safety rules. The AI and the learner can NOT change these.

Every proposed trade must pass `check_new_trade`. Position size comes from
`size_position`, which limits how much money can be lost on any one trade.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Verdict:
    ok: bool
    reason: str = ""


class RiskManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.halted_reason: str | None = None   # set when the daily loss limit is hit

    def daily_pnl_pct(self, account: dict) -> float:
        last = account["last_equity"] or account["equity"]
        return (account["equity"] - last) / last if last else 0.0

    def check_daily_loss(self, account: dict) -> bool:
        """Returns True if the bot must stop trading for the rest of the day."""
        pnl = self.daily_pnl_pct(account)
        if pnl <= -self.cfg["max_daily_loss_pct"]:
            self.halted_reason = f"Daily loss limit hit ({pnl:.2%}). No more trades today."
            return True
        return False

    def check_new_trade(self, symbol: str, side: str, price: float, account: dict,
                        positions: list[dict], trades_today: int, confidence: float,
                        min_confidence: float) -> Verdict:
        c = self.cfg
        if self.halted_reason:
            return Verdict(False, self.halted_reason)
        if confidence < min_confidence:
            return Verdict(False, f"AI confidence {confidence:.2f} below {min_confidence:.2f}")
        if price < c["min_price"]:
            return Verdict(False, f"Price ${price:.2f} below minimum ${c['min_price']:.2f}")
        if any(p["symbol"] == symbol for p in positions):
            return Verdict(False, "Already holding this stock")
        if len(positions) >= c["max_open_positions"]:
            return Verdict(False, f"Max open positions ({c['max_open_positions']}) reached")
        if trades_today >= c["max_trades_per_day"]:
            return Verdict(False, f"Max trades per day ({c['max_trades_per_day']}) reached")
        if c.get("pdt_protection", True) and account["equity"] < 25_000 and account["daytrade_count"] >= 3:
            return Verdict(False, "Pattern-day-trader protection: 3 day trades used in the last 5 days "
                                  "(accounts under $25k). Check your broker's current rules.")
        if self.check_daily_loss(account):
            return Verdict(False, self.halted_reason or "Daily loss limit")
        return Verdict(True)

    def bracket_prices(self, price: float, side: str, atr: float = 0.0) -> tuple[float, float]:
        """Stop-loss and take-profit prices. Stop is the wider of the fixed % and 1.5x ATR."""
        stop_dist = max(price * self.cfg["stop_loss_pct"], 1.5 * atr)
        stop_dist = min(stop_dist, price * 0.05)  # never risk more than 5% per share
        target_dist = stop_dist * self.cfg["reward_risk_ratio"]
        if side == "long":
            return price - stop_dist, price + target_dist
        return price + stop_dist, price - target_dist

    def size_position(self, price: float, stop_price: float, account: dict) -> int:
        """Number of shares, so that hitting the stop loses at most risk_per_trade_pct of equity."""
        equity = account["equity"]
        per_share_risk = abs(price - stop_price)
        if per_share_risk <= 0 or price <= 0:
            return 0
        by_risk = equity * self.cfg["risk_per_trade_pct"] / per_share_risk
        by_size = equity * self.cfg["max_position_pct"] / price
        by_cash = account["buying_power"] * 0.95 / price
        return max(0, math.floor(min(by_risk, by_size, by_cash)))
