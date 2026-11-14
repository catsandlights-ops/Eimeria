"""The Swing tab: tomorrow's plan, open swing positions and results per setup."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..indicators import NY
from ..swing_live import trading_days_between


class SwingTab(QWidget):
    def __init__(self, window):
        super().__init__()
        from .main_window import make_table
        from .ui import section_title
        self.win = window
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        info = QLabel(
            "Swing trades are held for weeks (default 40 trading days) instead of minutes. After the close the bot "
            "looks for fresh insider purchases of $1M+ in stocks that are in an uptrend (plus big news jumps, if you "
            "turn that setup on); the AI reads each one's news and can veto it. Planned stocks are bought the next "
            "morning with a stop-loss, and sold after the holding period. Swing trades are NOT closed at the end of "
            "the day. Crash protection (Settings) can pause new buys when the S&P 500 is below its 10-month average.")
        info.setWordWrap(True)
        info.setObjectName("help")
        lay.addWidget(info)
        row = QHBoxLayout()
        b = QPushButton("Make tomorrow's plan now")
        b.setToolTip("Normally runs automatically at 4:20 PM New York time")
        b.clicked.connect(lambda: self.win.run_task("swing_plan"))
        row.addWidget(b)
        self.status = QLabel()
        row.addWidget(self.status, 1)
        lay.addLayout(row)
        self.health = QLabel()
        self.health.setWordWrap(True)
        lay.addWidget(self.health)
        lay.addWidget(section_title("Plan for the next session"))
        self.plan_table = make_table(["Symbol", "Setup", "Why", "AI review"])
        lay.addWidget(self.plan_table, 1)
        lay.addWidget(section_title("Open swing positions"))
        self.open_table = make_table(["Symbol", "Setup", "Shares", "Entry", "Stop", "Days held", "Days left", "Why"])
        lay.addWidget(self.open_table, 1)
        lay.addWidget(section_title("Closed swing trades by setup"))
        self.setup_table = make_table(["Setup", "Trades", "Win rate", "Avg per trade", "P&L $", "Profit factor"])
        lay.addWidget(self.setup_table)
        self.refresh()

    def refresh(self, plan: list[dict] | None = None):
        from .main_window import GREEN, RED, fill_table, money, pnl_color
        j = self.win.ctx.journal
        stored = j.get("swing_plan") or {}
        items = plan if plan is not None else stored.get("items", [])
        made = stored.get("made")
        sw = self.win.ctx.settings["swing"]
        crash = sw.get("crash_protection", "off")
        cs = j.get("crash_state") or {}
        if crash == "off":
            crash_txt = "crash protection off"
        else:
            state = cs.get("state", "not checked yet")
            crash_txt = (f"crash protection ({crash.replace('_', ' ')}): "
                         f"{'DEFENSIVE (no new buys)' if state == 'defensive' else state}")
        self.status.setText(("Swing trading is ON" if sw["enabled"] else "Swing trading is OFF (Settings)") +
                            (f"   |   plan made {made}" if made else "") + f"   |   {crash_txt}")
        self.status.setToolTip(cs.get("detail", ""))
        from ..health import health_report
        hr = health_report(j)
        color = {"warning": RED, "watch": "#d29922", "ok": GREEN}.get(hr["status"], "#8b949e")
        self.health.setText("Strategy health: " + hr["text"])
        self.health.setStyleSheet(f"color: {color};")
        self.status.setStyleSheet(f"color: {GREEN if sw['enabled'] else RED};")
        fill_table(self.plan_table, [[p["symbol"], p["setup"], p["detail"], p.get("ai", "")] for p in items])
        today = datetime.now(NY).date()
        rows = []
        for t in j.open_trades():
            if t["style"] != "swing":
                continue
            held = trading_days_between(datetime.fromisoformat(t["entry_time"]).astimezone(NY).date(), today)
            hold = t["hold_days"] or sw["hold_days"]
            rows.append([t["symbol"], t["setup"] or "", f"{t['qty']:g}", f"{t['entry_price']:.2f}",
                         f"{(t['current_stop'] or t['stop_price']):.2f}", held, max(0, hold - held), t["reasoning"]])
        fill_table(self.open_table, rows)
        closed = [t for t in j.closed_trades(5000) if t["style"] == "swing"]
        rows, colors = [], []
        for setup in sorted({t["setup"] or "?" for t in closed}):
            ts = [t for t in closed if (t["setup"] or "?") == setup]
            wins = sum(t["pnl"] for t in ts if t["pnl"] > 0)
            losses = -sum(t["pnl"] for t in ts if t["pnl"] <= 0)
            pf = wins / losses if losses > 0 else float("inf")
            pnl = wins - losses
            rows.append([setup, len(ts), f"{sum(1 for t in ts if t['pnl'] > 0) / len(ts):.0%}",
                         f"{sum(t['pnl_pct'] for t in ts) / len(ts):+.2%}", money(pnl),
                         "∞" if pf == float("inf") else f"{pf:.2f}"])
            colors.append([None, None, None, None, pnl_color(pnl), GREEN if pf >= 1.2 else RED if pf < 1 else None])
        fill_table(self.setup_table, rows, colors)
