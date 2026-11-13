"""Backtester: replays past market data through the bot's signals, risk rules and exits.

What it tests: the chart-signal strategy, the market filter, position sizing, stops,
partial profits, trailing stops, time stops and end-of-day closing, with trading costs.
What it can't test: the AI's news reading and final approval (you can't replay the AI
fairly on old news without it "knowing the future"). In live trading the AI acts as an
extra filter on top of what's tested here.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .exits import TradeState, apply, bar_hit, manage
from .indicators import NY, TECH_SIGNALS, classify_regime, compute_series, regime_adjustment
from .risk import RiskManager

log = logging.getLogger(__name__)
FWD_BARS = 12  # 60 minutes of 5-minute bars, for measuring signal quality


@dataclass
class BTResult:
    trades: list[dict] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    signal_quality: dict = field(default_factory=dict)   # signal -> information coefficient
    suggested_weights: dict = field(default_factory=dict)
    benchmark_return: float = 0.0
    notes: list[str] = field(default_factory=list)
    halves: list[dict] = field(default_factory=list)       # results for the 1st and 2nd half separately
    edge_table: list[dict] = field(default_factory=list)   # avg result after costs by signal strength
    allow_short: bool = False
    headline: str = ""                                     # verdict that overrides the generic one (pre-registered tests)


def information_coefficient(scores: pd.Series, fwd: pd.Series) -> float:
    """Rank correlation between a signal and what the price did next. >0.03 is useful for intraday."""
    m = scores.notna() & fwd.notna() & (scores != 0)
    if m.sum() < 50:
        return 0.0
    v = scores[m].rank().corr(fwd[m].rank())
    return 0.0 if pd.isna(v) else float(v)


IC_NOISE = 0.015  # signal-quality values this close to zero are treated as noise


def weights_from_ic(ic: dict[str, float], wmin: float, wmax: float) -> dict[str, float]:
    """Turn signal quality into weights: IC 0 -> 1.0, +0.05 -> ~1.5, negative -> lower.
    Values within +-IC_NOISE of zero are ignored, so random wobbles don't move the weights."""
    out = {}
    for k, v in ic.items():
        v = 0.0 if abs(v) < IC_NOISE else v - IC_NOISE * (1 if v > 0 else -1)
        out[k] = round(max(wmin, min(wmax, 1.0 + 15 * v)), 3)
    return out


class Backtester:
    def __init__(self, settings: dict, weights: dict[str, float]):
        self.s = settings
        self.weights = {k: v for k, v in weights.items() if k in TECH_SIGNALS}
        self.bt = settings["backtest"]

    # -------------------------------------------------------------- data
    def load(self, broker, symbols: list[str], start: date, end: date, progress=None) -> dict:
        feed = self.bt.get("data_feed", "sip")
        warm = start - timedelta(days=7)
        data = {}
        syms = list(dict.fromkeys(symbols + ["SPY", "VIXY"]))
        for i in range(0, len(syms), 5):
            chunk = syms[i:i + 5]
            if progress:
                progress(f"Downloading {', '.join(chunk)}...")
            intraday = broker.bars_range(chunk, warm, end, minutes=5, feed=feed)
            daily = broker.daily_range(chunk, warm - timedelta(days=40), end, feed=feed)
            for s in chunk:
                if s in intraday:
                    data[s] = (intraday[s], daily.get(s))
        return data

    # -------------------------------------------------------------- simulation
    def run(self, data: dict, start: date, end: date, symbols: list[str], progress=None) -> BTResult:
        res = BTResult()
        rcfg, xcfg, sched = self.s["risk"], self.s["exits"], self.s["schedule"]
        risk = RiskManager(rcfg)
        slip = self.bt["slippage_pct"]
        threshold = self.bt["entry_score"]
        use_regime = self.s["market_filter"]["enabled"]

        series = {}
        for sym in symbols:
            if sym not in data:
                res.notes.append(f"No data for {sym}")
                continue
            df = compute_series(*data[sym])
            df = df[(df["date"] >= start) & (df["date"] <= end) & df["in_session"]]
            if len(df):
                series[sym] = df
        if not series:
            res.notes.append("No usable data in this date range.")
            return res
        spy = compute_series(*data["SPY"]) if "SPY" in data else None
        fear = compute_series(*data["VIXY"]) if "VIXY" in data else None

        # Signal quality (information coefficient) on every bar, independent of trades
        res.signal_quality = self._signal_quality(series)
        res.edge_table = self._edge_table(series)
        res.allow_short = bool(self.s["allow_short"])
        lr = self.s["learning"]
        res.suggested_weights = weights_from_ic(res.signal_quality, lr["weight_min"], lr["weight_max"])

        # Fast lookups: timestamp -> row for each symbol
        rows = {sym: {ts: r for ts, r in zip(df.index, df.to_dict("records"))} for sym, df in series.items()}
        timeline = sorted(set().union(*[set(df.index) for df in series.values()]))

        equity = self.bt["starting_equity"]
        res.equity_curve.append((datetime.combine(start, datetime.min.time(), NY), equity))
        open_pos: dict[str, dict] = {}
        day, day_start_equity, trades_today, halted = None, equity, 0, False

        def close(sym: str, price: float, ts, reason: str, qty: int | None = None):
            nonlocal equity
            p = open_pos[sym]
            st: TradeState = p["state"]
            q = qty or st.qty
            fill = price * (1 - slip * st.direction)
            pnl = (fill - p["fill_entry"]) * q * st.direction
            equity += pnl
            p["realized"] += pnl
            if qty and qty < st.qty:
                return
            total_qty = p["qty0"]
            res.trades.append({
                "symbol": sym, "side": st.side, "entry_time": p["time"], "exit_time": ts, "qty": total_qty,
                "entry": p["fill_entry"], "exit": fill, "pnl": p["realized"],
                "pnl_pct": p["realized"] / (p["fill_entry"] * total_qty), "exit_reason": reason,
                "partial": st.partial_taken, "score": p["score"]})
            del open_pos[sym]

        for n, ts in enumerate(timeline):
            if progress and n % 500 == 0:
                progress(f"Simulating {ts:%Y-%m-%d}... ({n}/{len(timeline)} bars)")
            if ts.date() != day:
                day, day_start_equity, trades_today, halted = ts.date(), equity, 0, False
            minute = ts.hour * 60 + ts.minute
            close_minute = 16 * 60  # (half-days are rare; treated as full days)
            bar_end = minute + 5

            # 1. Manage open positions with this bar
            for sym in list(open_pos):
                r = rows[sym].get(ts)
                if r is None:
                    continue
                p = open_pos[sym]
                st = p["state"]
                hit = bar_hit(st, r["open"], r["high"], r["low"])
                if hit:
                    close(sym, hit[1], ts, hit[0])
                    continue
                if close_minute - bar_end <= sched["close_all_minutes_before_close"]:
                    close(sym, r["close"], ts, "end of day")
                    continue
                mins_open = (ts - p["time"]).total_seconds() / 60 + 5
                act = manage(st, r["close"], r["atr"], mins_open, xcfg)
                if act:
                    if act["type"] == "partial":
                        close(sym, r["close"], ts, "partial", qty=act["qty"])
                    elif act["type"] == "time_exit":
                        close(sym, r["close"], ts, "time stop")
                        continue
                    apply(st, act)

            # 2. Daily loss limit
            if not halted and (equity - day_start_equity) / day_start_equity <= -rcfg["max_daily_loss_pct"]:
                halted = True
                for sym in list(open_pos):
                    r = rows[sym].get(ts)
                    if r:
                        close(sym, r["close"], ts, "daily loss limit")

            if bar_end >= close_minute:
                res.equity_curve.append((ts, equity))

            # 3. New entries
            if halted or minute - 570 < sched["no_new_trades_first_minutes"] \
                    or close_minute - bar_end < sched["no_new_trades_last_minutes"]:
                continue
            regime = "neutral"
            if use_regime and spy is not None and ts in spy.index:
                sr = spy.loc[ts]
                fc = float(fear.loc[ts]["change_pct"]) if fear is not None and ts in fear.index else 0.0
                regime = classify_regime(float(sr["change_pct"]), bool(sr["close"] > sr["vwap"]),
                                         float(sr["s_trend"]), fc)["state"]
            cands = []
            for sym, rr in rows.items():
                r = rr.get(ts)
                if r is None or sym in open_pos or r["close"] < rcfg["min_price"] or pd.isna(r["vwap"]):
                    continue
                sc = {k: r[f"s_{k}"] for k in TECH_SIGNALS}
                total_w = sum(self.weights.get(k, 1.0) for k in sc) or 1
                raw = sum(v * self.weights.get(k, 1.0) for k, v in sc.items()) / total_w
                score = max(-1, min(1, raw * max(0.6, min(1.5, 0.75 + r["rel_volume"] / 4))))
                side = "long" if score > 0 else "short"
                if side == "short" and not self.s["allow_short"]:
                    continue
                extra, _, blocked = regime_adjustment(regime, side) if use_regime else (0, 0, False)
                if blocked or abs(score) < threshold + extra:
                    continue
                cands.append((abs(score), sym, side, r, score))
            for _, sym, side, r, score in sorted(cands, reverse=True):
                if len(open_pos) >= rcfg["max_open_positions"] or trades_today >= rcfg["max_trades_per_day"]:
                    break
                price = r["close"]
                stop, target = risk.bracket_prices(price, side, r["atr"])
                acct = {"equity": equity, "buying_power": equity * 2}
                exposure = sum(p["state"].qty * p["fill_entry"] for p in open_pos.values())
                acct["buying_power"] = max(0.0, equity * 2 - exposure)
                qty = risk.size_position(price, stop, acct)
                if qty < 1:
                    continue
                d = 1 if side == "long" else -1
                open_pos[sym] = {
                    "state": TradeState(side, price, qty, stop, stop, target, best_price=price),
                    "fill_entry": price * (1 + slip * d), "time": ts, "qty0": qty, "realized": 0.0,
                    "score": score}
                trades_today += 1

        # Anything still open at the very end
        for sym in list(open_pos):
            last = series[sym].iloc[-1]
            close(sym, float(last["close"]), series[sym].index[-1], "end of test")
        res.equity_curve.append((timeline[-1], equity))

        if "SPY" in data:
            s = compute_series(*data["SPY"])
            s = s[(s["date"] >= start) & (s["date"] <= end) & s["in_session"]]
            if len(s):
                res.benchmark_return = float(s["close"].iloc[-1] / s["open"].iloc[0] - 1)
        res.stats = self._stats(res, self.bt["starting_equity"])
        res.halves = self._halves(res, sorted({d for df in series.values() for d in df["date"].unique()}))
        return res

    # -------------------------------------------------------------- analysis
    def _signal_quality(self, series: dict[str, pd.DataFrame]) -> dict[str, float]:
        frames = []
        for df in series.values():
            fwd = df.groupby("date")["close"].shift(-FWD_BARS) / df["close"] - 1
            f = df[[f"s_{k}" for k in TECH_SIGNALS]].copy()
            f["fwd"] = fwd
            frames.append(f)
        allf = pd.concat(frames)
        return {k: round(information_coefficient(allf[f"s_{k}"], allf["fwd"]), 4) for k in TECH_SIGNALS}

    def _edge_table(self, series: dict[str, pd.DataFrame]) -> list[dict]:
        """For each band of signal strength: the average 60-minute result of trading in the signal's
        direction, AFTER costs, in the first and second half of the period separately.
        An edge you can trust is positive in BOTH halves, with plenty of samples."""
        sched = self.s["schedule"]
        cost = 2 * self.bt["slippage_pct"]
        frames = []
        for df in series.values():
            fwd = df.groupby("date")["close"].shift(-FWD_BARS) / df["close"] - 1
            minute = df.index.hour * 60 + df.index.minute
            ok = (minute - 570 >= sched["no_new_trades_first_minutes"]) & \
                 (960 - (minute + 5) >= sched["no_new_trades_last_minutes"]) & fwd.notna()
            sc = df[[f"s_{k}" for k in TECH_SIGNALS]]
            w = np.array([self.weights.get(k, 1.0) for k in TECH_SIGNALS])
            raw = (sc.values * w).sum(axis=1) / w.sum()
            vol = np.clip(0.75 + df["rel_volume"].values / 4, 0.6, 1.5)
            frames.append(pd.DataFrame({"score": np.clip(raw * vol, -1, 1), "fwd": fwd.values,
                                        "date": df["date"].values})[ok.values])
        if not frames:
            return []
        a = pd.concat(frames)
        dates = sorted(a["date"].unique())
        mid = dates[len(dates) // 2] if dates else None
        a["half"] = np.where(a["date"] < mid, 1, 2)
        bands = [(-1.0, -0.6), (-0.6, -0.4), (-0.4, -0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 1.0)]
        out = []
        for lo, hi in bands:
            m = (a["score"] > lo) & (a["score"] <= hi) if lo >= 0 else (a["score"] >= lo) & (a["score"] < hi)
            side = 1 if lo >= 0 else -1
            row = {"band": f"{lo:+.1f} to {hi:+.1f}", "side": "long" if side > 0 else "short"}
            for h in (1, 2):
                net = side * a.loc[m & (a["half"] == h), "fwd"] - cost
                row[f"n{h}"] = int(len(net))
                row[f"bps{h}"] = float(net.mean() * 1e4) if len(net) else 0.0
            row["consistent"] = row["n1"] >= 100 and row["n2"] >= 100 and row["bps1"] > 0 and row["bps2"] > 0
            out.append(row)
        return out

    @staticmethod
    def _halves(res: BTResult, dates: list) -> list[dict]:
        if len(dates) < 2:
            return []
        mid = dates[len(dates) // 2]
        out = []
        for name, sel in (("first half", lambda d: d < mid), ("second half", lambda d: d >= mid)):
            t = [x for x in res.trades if sel(x["entry_time"].date())]
            wins = sum(x["pnl"] for x in t if x["pnl"] > 0)
            losses = -sum(x["pnl"] for x in t if x["pnl"] <= 0)
            out.append({"name": name, "trades": len(t), "pnl": wins - losses,
                        "profit_factor": wins / losses if losses > 0 else (float("inf") if wins else 0.0),
                        "from": (dates[0] if name == "first half" else mid)})
        return out

    @staticmethod
    def _stats(res: BTResult, start_equity: float) -> dict:
        t = res.trades
        eq = pd.Series([e for _, e in res.equity_curve])
        peak = eq.cummax()
        max_dd = float(((eq - peak) / peak).min()) if len(eq) else 0.0
        wins = [x["pnl"] for x in t if x["pnl"] > 0]
        losses = [-x["pnl"] for x in t if x["pnl"] <= 0]
        daily = pd.Series([e for _, e in res.equity_curve]).pct_change().dropna()
        sharpe = float(daily.mean() / daily.std() * math.sqrt(252)) if len(daily) > 2 and daily.std() > 0 else 0.0
        final = eq.iloc[-1] if len(eq) else start_equity
        days = len({x["entry_time"].date() for x in t}) if t else 0
        return {
            "trades": len(t), "trading_days_with_trades": days,
            "total_return": final / start_equity - 1, "final_equity": float(final),
            "win_rate": len(wins) / len(t) if t else 0.0,
            "avg_win": float(np.mean(wins)) if wins else 0.0, "avg_loss": float(np.mean(losses)) if losses else 0.0,
            "profit_factor": (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (float("inf") if wins else 0.0),
            "max_drawdown": max_dd, "sharpe": sharpe,
            "exit_reasons": pd.Series([x["exit_reason"] for x in t]).value_counts().to_dict() if t else {},
        }


def verdict(res: BTResult) -> str:
    """Plain-English summary of whether the backtest looks promising."""
    s = res.stats
    if not s or s["trades"] == 0:
        return "No trades were taken. Try a longer period, more stocks, or a lower entry score."
    if res.headline:                      # a pre-registered test: its own pass/fail decides, nothing else
        msgs = []
        if s["max_drawdown"] < -0.10:
            msgs.append(f"Worst drop from a peak was {s['max_drawdown']:.1%}.")
        if res.benchmark_return:
            msgs.append(f"Simply holding the S&P 500 returned {res.benchmark_return:+.1%} vs {s['total_return']:+.1%}.")
        return "\n".join(msgs)
    msgs = []
    if s["trades"] < 50:
        msgs.append(f"Only {s['trades']} trades: too few to trust. Test a longer period or more stocks.")
    pf = s["profit_factor"]
    h = res.halves
    if len(h) == 2 and min(x["profit_factor"] for x in h) < 1.0 <= max(x["profit_factor"] for x in h):
        msgs.append(f"INCONSISTENT: profit factor {h[0]['profit_factor']:.2f} in the first half but "
                    f"{h[1]['profit_factor']:.2f} in the second. Results that flip like this are usually luck.")
    usable = [r for r in res.edge_table if r["side"] == "long" or res.allow_short]
    if usable and not any(r["consistent"] for r in usable):
        msgs.append("EDGE TABLE: no signal strength made money after costs in BOTH halves of the period.")
    if pf < 1.0:
        msgs.append("LOSING strategy over this period (profit factor below 1). Don't trade this live.")
    elif pf < 1.3:
        msgs.append("Roughly break-even. After real-world costs this probably wouldn't make money.")
    else:
        msgs.append("Promising on this period. Check it holds on OTHER periods before trusting it "
                    "(a strategy that only works on one period is usually luck).")
    if s["max_drawdown"] < -0.10:
        msgs.append(f"Worst drop from a peak was {s['max_drawdown']:.1%}: could you live with that?")
    if res.benchmark_return and s["total_return"] < res.benchmark_return:
        msgs.append(f"Simply holding the S&P 500 returned {res.benchmark_return:+.1%} vs the bot's "
                    f"{s['total_return']:+.1%}.")
    return "\n".join(msgs)
