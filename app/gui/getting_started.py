"""'Getting started' checklist on the Dashboard: the recommended first steps, one click each."""
from __future__ import annotations

import re
from datetime import date, timedelta

from PySide6.QtCore import QDate, QTimer
from PySide6.QtWidgets import QGridLayout, QGroupBox, QInputDialog, QLabel, QMessageBox, QPushButton

from .. import config

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class GettingStarted(QGroupBox):
    STEPS = [
        ("keys", "Connect your Alpaca paper-trading account", "Open Settings"),
        ("selftest", "Run the self-test: checks Alpaca, prices, news, SEC, your local AI and order placement", "Run"),
        ("sec", "Add your SEC contact (required to see insider buying)", "Add"),
        ("backtest", "Run the recommended swing backtest: 2019 to now, every stock insiders bought", "Run it"),
        ("paper", "Start paper trading (fake money) and let it build a track record", "Start"),
        ("aitest", "Optional: test whether the AI's veto helps (AI tab). The backtests above ran without it", "Open"),
    ]

    def __init__(self, window):
        super().__init__("Getting started")
        self.win = window
        grid = QGridLayout(self)
        self.marks, self.buttons = {}, {}
        for row, (key, text, btn) in enumerate(self.STEPS):
            mark = QLabel()
            label = QLabel(f"{row + 1}. {text}")
            label.setWordWrap(True)
            b = QPushButton(btn)
            b.clicked.connect(lambda _=False, k=key: self.act(k))
            grid.addWidget(mark, row, 0)
            grid.addWidget(label, row, 1)
            grid.addWidget(b, row, 2)
            self.marks[key], self.buttons[key] = mark, b
        self.footer = QLabel()
        self.footer.setWordWrap(True)
        grid.addWidget(self.footer, len(self.STEPS), 0, 1, 3)
        grid.setColumnStretch(1, 1)
        hide = QPushButton("Hide this checklist")
        hide.clicked.connect(self.hide_forever)
        grid.addWidget(hide, len(self.STEPS) + 1, 2)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(3000)
        self.refresh()

    # ------------------------------------------------------------------ state
    def status(self) -> dict[str, bool]:
        w, j = self.win, self.win.ctx.journal
        return {
            "keys": w.engine is not None,
            "selftest": (j.get("selftest_last") or {}).get("fails", 1) == 0,
            "sec": "@" in (w.ctx.settings["smart_money"].get("sec_user_agent") or ""),
            "backtest": bool(j.get("recommended_backtest_done")),
            "paper": bool(j.get("paper_started")),
            "aitest": bool(j.get("ai_test_last")),
        }

    def refresh(self):
        from .main_window import GREEN, MUTED
        if self.win.ctx.journal.get("hide_getting_started"):
            self.hide()
            return
        st = self.status()
        for key, done in st.items():
            self.marks[key].setText("✔" if done else "○")
            self.marks[key].setStyleSheet(f"color: {GREEN if done else MUTED}; font-size: 14pt;")
            self.buttons[key].setEnabled(not done or key == "backtest")
        bt = self.win.ctx.journal.get("recommended_backtest_done")
        if bt:
            self.buttons["backtest"].setText("Run again")
        lines = []
        if bt:
            lines.append(f"Your backtest: {bt['trades']} trades, total {bt['ret']:+.1%} "
                         f"(S&P 500 {bt['spy']:+.1%}), profit factor {bt['pf']:.2f}; halves: {bt['halves']}.")
        ai = self.win.ctx.journal.get("ai_test_last")
        if ai:
            from ..ai_eval import verdict
            lines.append("AI test: " + "; ".join(f"{r['model']}: {verdict(r).split(':')[0]}" for r in ai) +
                         ". If it isn't HELPS, consider turning the AI veto off (Settings > Strategies).")
        stl = self.win.ctx.journal.get("selftest_last")
        if stl and stl.get("fails"):
            lines.append(f"Self-test: {stl['fails']} check(s) failed. Run it again after fixing them "
                         "(SEC contact and AI are checked too, so steps below may fix some).")
        if all(v for k, v in st.items() if k != "aitest"):
            lines.append("All set. Real money stays locked until 20 paper swing trades have closed with a profit "
                         "factor of 1.2+ (progress on the Swing tab and Learning tab). With 40-day holds that "
                         "takes a few months: that's expected.")
        self.footer.setText("\n".join(lines))
        self.footer.setObjectName("help")

    def hide_forever(self):
        self.win.ctx.journal.set("hide_getting_started", True)
        self.hide()

    # ------------------------------------------------------------------ actions
    def act(self, key: str):
        w = self.win
        if key == "keys":
            w.show_settings("Broker account")
        elif key == "selftest":
            w.open_self_test()
        elif key == "sec":
            self.ask_sec_contact()
        elif key == "backtest":
            self.run_backtest()
        elif key == "aitest":
            w.tabs.setCurrentWidget(w.ai_tab)
        elif key == "paper":
            if not w.engine:
                QMessageBox.information(self, "Connect first", "Do step 1 first: enter your Alpaca paper keys.")
                return
            if w.ctx.settings["mode"] != "paper":
                QMessageBox.information(self, "Paper mode", "Switch Mode to 'paper' in Settings first.")
                return
            if not w.engine.running:
                w.toggle_bot()
        self.refresh()

    def ask_sec_contact(self):
        name, ok = QInputDialog.getText(
            self, "SEC contact (1/2)",
            "The SEC asks every program that downloads its filings to identify itself.\n"
            "This is sent only to sec.gov, in each request for insider filings.\n\nYour name:")
        if not ok or not name.strip():
            return
        email, ok = QInputDialog.getText(self, "SEC contact (2/2)", "Your email address:")
        if not ok:
            return
        email = email.strip()
        if not EMAIL.match(email):
            QMessageBox.warning(self, "Check the email", "That doesn't look like an email address.")
            return
        s = self.win.ctx.settings
        s["smart_money"]["sec_user_agent"] = f"{name.strip()} {email}"
        config.save_settings(s)
        self.win.f_sec.setText(s["smart_money"]["sec_user_agent"])
        self.win.log("SEC contact saved. Insider buying will be checked hourly while the bot runs.")
        if self.win.engine:
            self.win.engine.insiders.cfg = s["smart_money"]

    def run_backtest(self):
        w = self.win
        if not w.engine:
            QMessageBox.information(self, "Connect first", "Do step 1 first: the backtest downloads prices "
                                                           "through your Alpaca account.")
            return
        if not self.status()["sec"]:
            QMessageBox.information(self, "SEC contact needed", "Do step 2 first: insider records come from the SEC.")
            return
        if QMessageBox.question(
                self, "Run the recommended backtest?",
                "This tests the default swing strategy from 2019 to yesterday on every stock insiders bought.\n\n"
                "The first run downloads about 30 quarters of SEC records and daily prices for a few thousand "
                "stocks, so it can take 10-30 minutes. Later runs reuse the SEC files.\n\nStart now?") \
                != QMessageBox.Yes:
            return
        t = w.backtest_tab
        t.strategy.setCurrentIndex(0)
        t.universe.setCurrentIndex(1)
        t.start.setDate(QDate(2019, 1, 1))
        y = date.today() - timedelta(days=1)
        t.end.setDate(QDate(y.year, y.month, y.day))
        t.recommended = True
        w.tabs.setCurrentWidget(t)
        t.run()
