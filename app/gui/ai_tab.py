"""The AI tab: local model status, speed test, and the test of the AI's swing veto."""
from __future__ import annotations

import threading
import time
import traceback
from datetime import date, timedelta

from PySide6.QtCore import QDate, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDateEdit, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QSplitter, QVBoxLayout, QWidget,
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
        lay.addWidget(box, 1)
        self.refresh()

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
