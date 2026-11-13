"""The AI tab: local model status, speed test, and the test of the AI's swing veto."""
from __future__ import annotations

import threading
import time
import traceback
from datetime import date, timedelta

from PySide6.QtCore import QDate, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDateEdit, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .. import config
from ..ai_eval import build_cases, default_cutoff, run_ai_test, verdict


class _Bridge(QObject):
    progress = Signal(str)
    done = Signal(object, object)


class AITab(QWidget):
    def __init__(self, window):
        super().__init__()
        from .main_window import MUTED, make_table
        self.win = window
        self.bridge = _Bridge()
        self.bridge.progress.connect(lambda m: self.test_status.setText(m))
        self.bridge.done.connect(self._on_done)
        lay = QVBoxLayout(self)

        box = QGroupBox("Your local AI")
        bl = QVBoxLayout(box)
        self.model_lbl = QLabel()
        self.model_lbl.setWordWrap(True)
        bl.addWidget(self.model_lbl)
        row = QHBoxLayout()
        b = QPushButton("Refresh")
        b.clicked.connect(self.refresh)
        sp = QPushButton("Speed test")
        sp.setToolTip("Times one typical request with the current model")
        sp.clicked.connect(self.speed_test)
        row.addWidget(b)
        row.addWidget(sp)
        row.addStretch()
        bl.addLayout(row)
        lay.addWidget(box)

        box = QGroupBox("Test the AI: does its swing veto actually help?")
        tl = QVBoxLayout(box)
        info = QLabel(
            "Replays past swing signals through the AI exactly as the bot would have seen them (only the headlines "
            "published before 4:20 PM that day, from Alpaca's news archive), then checks how each stock did over "
            "the holding period vs the S&P 500. If the AI is useful, the stocks it approved should beat the ones "
            "it vetoed. AI models may 'remember' what happened before their training cutoff, so only signals AFTER "
            "the cutoff date below are used: check each model's release date on ollama.com and set it later than "
            "that. Several models can be compared on the same signals (comma-separated).")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED};")
        tl.addWidget(info)
        f = QFormLayout()
        self.models = QLineEdit(window.ctx.settings["ai"]["model"])
        c = default_cutoff()
        self.cutoff = QDateEdit(QDate(c.year, c.month, c.day), calendarPopup=True)
        y = date.today() - timedelta(days=1)
        self.end = QDateEdit(QDate(y.year, y.month, y.day), calendarPopup=True)
        for d in (self.cutoff, self.end):
            d.setDisplayFormat("yyyy-MM-dd")
        self.sample = QSpinBox(minimum=10, maximum=500, value=60)
        self.sample.setToolTip("More signals = more reliable verdict, but slower (each needs one AI answer per model)")
        f.addRow("Models to test", self.models)
        f.addRow("AI knowledge cutoff (use signals after)", self.cutoff)
        f.addRow("Up to", self.end)
        f.addRow("Signals to test", self.sample)
        tl.addLayout(f)
        row = QHBoxLayout()
        self.run_btn = QPushButton("Run AI test")
        self.run_btn.setObjectName("start")
        self.run_btn.clicked.connect(self.run)
        self.test_status = QLabel("")
        row.addWidget(self.run_btn)
        row.addWidget(self.test_status, 1)
        tl.addLayout(row)
        split = QSplitter(Qt.Vertical)
        self.result_table = make_table(["Model", "Answered", "Approved", "Vetoed", "Approved: avg vs S&P",
                                        "Vetoed: avg vs S&P", "Difference", "Confidence (t)", "Sec/answer", "Verdict"])
        self.result_table.setToolTip("Double-click a model to make it Eimeria's AI model")
        self.result_table.cellDoubleClicked.connect(self.use_model)
        self.examples = make_table(["Date", "Symbol", "AI", "Reason", "Headlines", "Result vs S&P (holding period)"])
        split.addWidget(self.result_table)
        split.addWidget(self.examples)
        tl.addWidget(split, 1)
        tests = QTabWidget()
        tests.addTab(box, "Swing veto test")
        tests.addTab(self._trader_box(MUTED, make_table), "AI trader test (long or short on news)")
        lay.addWidget(tests, 1)
        self.refresh()

    # ------------------------------------------------------------------ AI trader test (long/short on news)
    def _trader_box(self, muted, make_table) -> QWidget:
        self.tbridge = _Bridge()
        self.tbridge.progress.connect(lambda m: self.t_status.setText(m))
        self.tbridge.done.connect(self._trader_done)
        w = QWidget()
        tl = QVBoxLayout(w)
        info = QLabel(
            "Can your AI make money by reading the news and choosing to BUY or SHORT? For random past days after "
            "the AI's training cutoff, it reads each company's headlines from yesterday's close to 9:20 AM (only "
            "news that existed then) and answers buy, short or skip. Buys are bought at the open and sold at the "
            "close; shorts the opposite. Results are vs the S&P 500 over the same hours, after costs. Rules are "
            "fixed in advance: it PASSES only if its trades make money in both halves of the period. Expect about "
            "5-15 seconds per answer on a typical PC (300 answers: roughly 30-60 minutes).")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {muted};")
        tl.addWidget(info)
        f = QFormLayout()
        c = default_cutoff()
        self.t_cutoff = QDateEdit(QDate(c.year, c.month, c.day), calendarPopup=True)
        y = date.today() - timedelta(days=7)
        self.t_end = QDateEdit(QDate(y.year, y.month, y.day), calendarPopup=True)
        for d in (self.t_cutoff, self.t_end):
            d.setDisplayFormat("yyyy-MM-dd")
        self.t_sample = QSpinBox(minimum=40, maximum=3000, value=300, singleStep=50)
        self.t_sample.setToolTip("Company-days to ask about. More = more reliable, slower. 300+ recommended.")
        f.addRow("AI knowledge cutoff (use news after)", self.t_cutoff)
        f.addRow("Up to", self.t_end)
        f.addRow("Company-days to ask the AI about", self.t_sample)
        tl.addLayout(f)
        row = QHBoxLayout()
        self.t_run = QPushButton("Run AI trader test")
        self.t_run.setObjectName("start")
        self.t_run.clicked.connect(self.run_trader)
        self.t_status = QLabel("")
        self.t_status.setWordWrap(True)
        row.addWidget(self.t_run)
        row.addWidget(self.t_status, 1)
        tl.addLayout(row)
        self.t_verdict = QPlainTextEdit()
        self.t_verdict.setReadOnly(True)
        self.t_verdict.setMaximumHeight(110)
        tl.addWidget(self.t_verdict)
        split = QSplitter(Qt.Vertical)
        self.t_table = make_table(["Rule", "Trade", "1st half: avg vs S&P after costs", "count",
                                   "2nd half: avg vs S&P after costs", "count", "Edge?"])
        self.t_examples = make_table(["Date", "Symbol", "AI", "Confidence", "Reason", "Headlines",
                                      "Stock vs S&P that day"])
        split.addWidget(self.t_table)
        split.addWidget(self.t_examples)
        tl.addWidget(split, 1)
        return w

    def run_trader(self):
        e = self.win.engine
        if not e:
            QMessageBox.warning(self, "Not connected", "Connect Alpaca first: the test downloads prices and news.")
            return
        ok, msg = e.llm.status()
        if not ok:
            QMessageBox.warning(self, "AI not ready", msg)
            return
        start, end = self.t_cutoff.date().toPython(), self.t_end.date().toPython()
        if end <= start + timedelta(days=60):
            QMessageBox.warning(self, "Dates", "Leave at least a few months between the cutoff and the end date.")
            return
        self.t_run.setEnabled(False)
        self.t_verdict.setPlainText("")
        threading.Thread(target=self._trader_work, args=(e.broker, dict(self.win.ctx.settings), e.llm, start, end,
                                                         self.t_sample.value()), daemon=True).start()

    def _trader_work(self, broker, settings, llm, start, end, sample):
        from ..ai_news_test import run_ai_trader_test
        try:
            self.tbridge.done.emit(run_ai_trader_test(broker, settings, llm, start, end, sample,
                                                      emit=self.tbridge.progress.emit), None)
        except Exception as ex:
            self.tbridge.done.emit(None, f"{ex}\n{traceback.format_exc(limit=2)}")

    def _trader_done(self, res, err):
        from .main_window import GREEN, MUTED, RED, fill_table, pnl_color
        self.t_run.setEnabled(True)
        if err:
            self.t_status.setText("AI trader test failed.")
            QMessageBox.warning(self, "AI trader test", err)
            return
        self.t_status.setText("Done.")
        self.t_verdict.setPlainText(res.headline + "\n" + " ".join(res.notes))
        rows, colors = [], []
        for r in res.edge_table:
            rows.append([r["band"], r["side"], f"{r['bps1'] / 100:+.2f}%", r["n1"], f"{r['bps2'] / 100:+.2f}%",
                         r["n2"], "YES" if r["consistent"] else "no"])
            colors.append([None, None, pnl_color(r["bps1"]), None, pnl_color(r["bps2"]), None,
                           GREEN if r["consistent"] else MUTED])
        fill_table(self.t_table, rows, colors)
        ex_rows, ex_colors = [], []
        for c in getattr(res, "ai_cases", [])[:500]:
            ex_rows.append([str(c.day), c.symbol, c.action.upper(), f"{c.confidence:.2f}", c.reason,
                            len(c.headlines), f"{c.excess:+.2%}"])
            col = GREEN if c.action == "buy" else RED if c.action == "short" else None
            ex_colors.append([None, None, col, None, None, None, pnl_color(c.excess)])
        fill_table(self.t_examples, ex_rows, ex_colors)
        self.win.ctx.journal.set("ai_trader_test_last", {"headline": res.headline, "rows": res.edge_table})

    # ------------------------------------------------------------------ status
    def refresh(self):
        e = self.win.engine
        ai = self.win.ctx.settings["ai"]
        if not e:
            self.model_lbl.setText(f"Model: {ai['model']}. Connect Alpaca first (Settings) to start the bot's AI.")
            return
        ok, msg = e.llm.status()
        try:
            installed = ", ".join(e.llm.installed_models()) or "none"
        except Exception:
            installed = "(Ollama not reachable)"
        from html import escape
        summary = e.llm.summary() if hasattr(e.llm, "summary") else ""
        self.model_lbl.setText(f"<b>{'Ready' if ok else 'Not ready'}</b>: {escape(msg)}<br>Installed models: "
                               f"{escape(installed)}<br>This session: {escape(summary)}")

    def speed_test(self):
        e = self.win.engine
        if not e:
            return
        from ..ai_schemas import SWING_REVIEW
        t0 = time.time()
        try:
            res = e.llm.chat_json("Reply ONLY with JSON.", 'Is "Company wins $2B government contract" durable good '
                                  'news for a 2-month hold? Return JSON: {"approve": true/false, "reason": "..."}',
                                  schema=SWING_REVIEW)
            QMessageBox.information(self, "Speed test", f"{time.time() - t0:.1f} seconds.\nAnswer: {res}")
        except Exception as ex:
            QMessageBox.warning(self, "Speed test", f"Failed: {ex}")
        self.refresh()

    def use_model(self, row, _col):
        item = self.result_table.item(row, 0)
        if not item:
            return
        model = item.text()
        if QMessageBox.question(self, "Use this model?", f"Make '{model}' Eimeria's AI model?") != QMessageBox.Yes:
            return
        self.win.ctx.settings["ai"]["model"] = model
        config.save_settings(self.win.ctx.settings)
        self.win.f_model.setText(model)
        if self.win.engine:
            self.win.engine.llm.model = model
        self.refresh()

    # ------------------------------------------------------------------ the test
    def run(self):
        e = self.win.engine
        if not e:
            QMessageBox.warning(self, "Not connected", "Connect Alpaca first: the test downloads prices and news.")
            return
        if "@" not in self.win.ctx.settings["smart_money"].get("sec_user_agent", ""):
            QMessageBox.warning(self, "SEC contact needed", "Add your SEC contact in Settings first.")
            return
        models = [m.strip() for m in self.models.text().split(",") if m.strip()]
        missing = []
        try:
            installed = e.llm.installed_models()
            missing = [m for m in models if not any(i == m or i.split(":")[0] == m for i in installed)]
        except Exception:
            QMessageBox.warning(self, "Ollama", "Ollama isn't running.")
            return
        if missing:
            QMessageBox.warning(self, "Model not installed", "Download first, in Command Prompt:\n\n" +
                                "\n".join(f"ollama pull {m}" for m in missing))
            return
        start, end = self.cutoff.date().toPython(), self.end.date().toPython()
        if end <= start + timedelta(days=30):
            QMessageBox.warning(self, "Dates", "Choose a cutoff at least a few months before the end date.")
            return
        self.run_btn.setEnabled(False)
        llms = [e.llm.with_model(m) for m in models]
        threading.Thread(target=self._work, args=(e.broker, dict(self.win.ctx.settings), llms, start, end,
                                                  self.sample.value()), daemon=True).start()

    def _work(self, broker, settings, llms, start, end, sample):
        from ..swing_data import load_swing_signals
        try:
            emit = self.bridge.progress.emit
            hold = settings["swing"]["hold_days"]
            events, px, spy, notes = load_swing_signals(broker, settings, settings["watchlist"], start, end,
                                                        True, emit, extra_days_after=0)
            cases = build_cases(events, px, spy, hold)
            if not cases:
                self.bridge.done.emit(None, "No signals with a known outcome in this period. Signals need "
                                            f"{hold} trading days after them, and the SEC's insider files lag a "
                                            "few months; try an earlier cutoff." + " " + " ".join(notes))
                return
            emit(f"{len(cases)} signals with known outcomes; asking the AI about up to {sample}...")
            results = run_ai_test(broker, llms, cases, sample, progress=emit)
            self.bridge.done.emit((results, len(cases), notes), None)
        except Exception as ex:
            self.bridge.done.emit(None, f"{ex}\n{traceback.format_exc(limit=2)}")

    def _on_done(self, payload, err):
        from .main_window import GREEN, RED, fill_table, pnl_color
        self.run_btn.setEnabled(True)
        if err:
            self.test_status.setText("AI test failed.")
            QMessageBox.warning(self, "AI test", err)
            return
        results, n_cases, notes = payload
        self.test_status.setText(f"Done: {n_cases} signals available. " + " ".join(notes))
        rows, colors = [], []
        pct = lambda x: "-" if x != x else f"{x:+.2%}"   # noqa: E731  (x != x is NaN)
        for r in results:
            s = r.summary()
            v = verdict(s)
            rows.append([s["model"], s["answered"], s["approved"], s["vetoed"], pct(s["avg_approved"]),
                         pct(s["avg_vetoed"]), pct(s["diff"]), "-" if s["t"] != s["t"] else f"{s['t']:+.1f}",
                         "-" if s["seconds_each"] != s["seconds_each"] else f"{s['seconds_each']:.1f}", v])
            colors.append([None] * 9 + [GREEN if v.startswith("HELPS") else RED if v.startswith("HURTS") else None])
        fill_table(self.result_table, rows, colors)
        ex_rows, ex_colors = [], []
        for x in results[0].examples:
            ex_rows.append([str(x["signal_date"]), x["symbol"], "approve" if x["approve"] else "VETO", x["reason"],
                            x["headlines"], f"{x['excess']:+.1%}"])
            ex_colors.append([None, None, GREEN if x["approve"] else RED, None, None, pnl_color(x["excess"])])
        fill_table(self.examples, ex_rows, ex_colors)
        self.win.ctx.journal.set("ai_test_last", [r.summary() for r in results])
