"""Self-test window: run every connection check and show pass / warning / FAIL."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

from .. import config
from ..selftest import FAIL, PASS, WARN, report_text, run_self_test


class _Bridge(QObject):
    progress = Signal(str)
    done = Signal(object)


class SelfTestDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        from .main_window import MUTED, STYLE, make_table
        self.win = window
        self.setWindowTitle("Eimeria self-test")
        self.resize(1100, 620)
        self.setStyleSheet(STYLE)
        self.checks = []
        self.bridge = _Bridge()
        self.bridge.progress.connect(lambda m: self.status.setText(m))
        self.bridge.done.connect(self._done)
        lay = QVBoxLayout(self)
        info = QLabel("Checks every connection Eimeria needs: your Alpaca account, prices, news, the SEC, your local AI, "
                      "and (optionally) placing an order. Takes about a minute. Nothing here trades for real.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED};")
        lay.addWidget(info)
        self.order = QCheckBox("Include a test order: places 1 share of SPY far below the market price on your PAPER "
                               "account (can't fill), then cancels it. Never runs on a live account.")
        self.order.setChecked(window.ctx.settings["mode"] == "paper")
        self.order.setEnabled(window.ctx.settings["mode"] == "paper")
        lay.addWidget(self.order)
        row = QHBoxLayout()
        self.run_btn = QPushButton("Run self-test")
        self.run_btn.setObjectName("start")
        self.run_btn.clicked.connect(self.run)
        self.copy_btn = QPushButton("Copy report")
        self.copy_btn.setToolTip("Copies the results as text, e.g. to send to someone helping you")
        self.copy_btn.clicked.connect(self.copy)
        self.copy_btn.setEnabled(False)
        self.status = QLabel("")
        row.addWidget(self.run_btn)
        row.addWidget(self.copy_btn)
        row.addWidget(self.status, 1)
        lay.addLayout(row)
        self.table = make_table(["Check", "Result", "Details"])
        lay.addWidget(self.table, 1)

    def run(self):
        if not self.win.engine:
            QMessageBox.warning(self, "Not connected", "Enter your Alpaca keys in Settings and Save first; "
                                                       "the self-test will then check everything else.")
        self.run_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        threading.Thread(target=self._work, args=(self.order.isChecked() and self.order.isEnabled(),),
                         daemon=True).start()

    def _work(self, order_test: bool):
        res = run_self_test(self.win.engine, self.win.ctx.settings, order_test, self.bridge.progress.emit)
        self.bridge.done.emit(res)

    def _done(self, checks):
        from .main_window import GREEN, MUTED, RED, fill_table
        self.checks = checks
        self.run_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)
        color = {PASS: GREEN, WARN: "#d29922", FAIL: RED}
        fill_table(self.table, [[c.name, c.status.upper(), c.detail] for c in checks],
                   [[None, color.get(c.status, MUTED), None] for c in checks])
        self.table.resizeRowsToContents()
        fails = sum(1 for c in checks if c.status == FAIL)
        warns = sum(1 for c in checks if c.status == WARN)
        self.status.setText("All good!" if not fails and not warns else
                            f"{fails} failed, {warns} warnings: see the details for what to do.")
        text = report_text(checks)
        try:
            config.LOG_DIR.mkdir(exist_ok=True)
            (config.LOG_DIR / "selftest_report.txt").write_text(text, encoding="utf-8")
        except Exception:
            pass
        self.win.ctx.journal.set("selftest_last", {"fails": fails, "warns": warns})
        if hasattr(self.win, "getting_started"):
            self.win.getting_started.refresh()

    def copy(self):
        QApplication.clipboard().setText(report_text(self.checks))
        self.status.setText("Report copied. It's also saved in logs\\selftest_report.txt")
