"""Strategy health: are real (paper or live) swing results in line with what the backtest predicted?

Edges fade: more traders discover them, markets change. This compares your closed swing trades with
the per-trade results of your latest swing backtest. It only flags a problem when the shortfall is
bigger than normal luck would explain.
"""
from __future__ import annotations

import math


def expectation_from_backtest(res, start, end) -> dict | None:
    """Per-trade statistics from a swing backtest result (needs 30+ trades)."""
    t = [x["pnl_pct"] for x in res.trades]
    if len(t) < 30:
        return None
    avg = sum(t) / len(t)
    std = math.sqrt(sum((x - avg) ** 2 for x in t) / (len(t) - 1))
    return {"avg": avg, "std": std, "n": len(t), "win_rate": sum(1 for x in t if x > 0) / len(t),
            "from": str(start), "to": str(end)}


def health_report(journal, min_trades: int = 10) -> dict:
    exp = journal.get("swing_expectation")
    trades = [t for t in journal.closed_trades(100000) if t["style"] == "swing"]
    n = len(trades)
    if not exp:
        return {"status": "no_baseline", "text": "Run a swing backtest (Backtest tab) so there's something to "
                                                  "compare your real results with.", "n": n}
    if n < min_trades:
        return {"status": "collecting", "n": n,
                "text": f"Collecting results: {n}/{min_trades} closed swing trades before checking. Backtest "
                        f"average per trade: {exp['avg']:+.2%} (win rate {exp['win_rate']:.0%})."}
    avg = sum(t["pnl_pct"] for t in trades) / n
    win = sum(1 for t in trades if t["pnl_pct"] > 0) / n
    z = (avg - exp["avg"]) / (exp["std"] / math.sqrt(n)) if exp["std"] > 0 else 0.0
    base = (f"{n} closed swing trades: average {avg:+.2%} per trade, win rate {win:.0%}. "
            f"Backtest ({exp['from']} to {exp['to']}): {exp['avg']:+.2%} per trade, win rate {exp['win_rate']:.0%}.")
    if z <= -2.5:
        status, verdict = "warning", ("WARNING: results are far below the backtest, more than bad luck usually "
                                      "explains. The edge may have faded. Consider pausing swing trading and "
                                      "re-running the backtest on recent data.")
    elif z <= -1.5:
        status, verdict = "watch", "WATCH: results are below the backtest. Could still be bad luck; keep an eye on it."
    else:
        status, verdict = "ok", "ON TRACK: results are within the normal range of the backtest."
    return {"status": status, "n": n, "z": z, "avg": avg, "text": f"{verdict}\n{base}"}
