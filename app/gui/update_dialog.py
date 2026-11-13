"""Update UI: background check, 'Update available' dialog, install and roll back."""
from __future__ import annotations

import json
import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from .. import config
from ..updater import Updater, can_self_update
from ..version import VERSION


class UpdateBridge(QObject):
    found = Signal(object)          # update info dict, or None
    failed = Signal(str)
    downloaded = Signal(object)     # staging path
    progress = Signal(str)


class UpdateManager:
    """Owns update checks for the main window."""

    def __init__(self, window):
        self.win = window
        self.info = None
        self.bridge = UpdateBridge()
        self.bridge.found.connect(self._found)
        self.bridge.failed.connect(self._failed)
        self.timer = QTimer(window)
        self.timer.timeout.connect(lambda: self.check(silent=True))
        self.timer.start(6 * 3600 * 1000)            # check every 6 hours while the app is open
        if window.ctx.settings.get("updates", {}).get("check_on_start", True):
            QTimer.singleShot(4000, lambda: self.check(silent=True))

    def check(self, silent: bool = False):
        self._silent = silent
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            up = Updater(self.win.ctx.settings)
            info = up.check()
            self.status_text = up.last_status
            self.bridge.found.emit(info)
        except Exception as e:
            self.bridge.failed.emit(str(e))

    def _failed(self, msg):
        self.win.log(f"Update check failed: {msg}", 30)
        if not getattr(self, "_silent", True):          # the user clicked "Check now": always answer
            QMessageBox.warning(self.win, "Updates", f"The update check failed:\n\n{msg}")

    def _found(self, info):
        self.info = info
        btn = self.win.update_btn
        if info:
            btn.setText(f"Update available: v{info['version']}")
            btn.show()
            self.win.log(f"Update available: Eimeria {info['version']} (you have {VERSION}).")
        else:
            btn.hide()
            text = getattr(self, "status_text", "") or f"You have the latest version (Eimeria {VERSION})."
            self.win.log(f"Update check: {text}")
            if not getattr(self, "_silent", True):
                QMessageBox.information(self.win, "Updates", text)

    def open_dialog(self):
        if self.info:
            UpdateDialog(self.win, self.info).exec()


class UpdateDialog(QDialog):
    def __init__(self, window, info: dict):
        super().__init__(window)
        from .main_window import STYLE
        self.win, self.info = window, info
        self.setWindowTitle("Eimeria update")
        self.setStyleSheet(STYLE)
        self.resize(640, 420)
        lay = QVBoxLayout(self)
        from html import escape
        notes = "".join(f"<li>{escape(str(n))}</li>" for n in info.get("notes", []))
        text = QLabel(f"<h3>Eimeria {escape(str(info['version']))} is available</h3>You have {VERSION}. Released "
                      f"{escape(str(info.get('date', '')))}.<br><br><b>What's new:</b><ul>{notes}</ul>"
                      "Your settings, keys, trade history and caches are kept. The current version is backed up "
                      "first and restored automatically if anything goes wrong.")
        text.setWordWrap(True)
        lay.addWidget(text, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.install_btn = QPushButton("Install and restart")
        self.install_btn.setObjectName("start")
        self.install_btn.clicked.connect(self.install)
        later = QPushButton("Later")
        later.clicked.connect(self.reject)
        row.addStretch()
        row.addWidget(later)
        row.addWidget(self.install_btn)
        lay.addLayout(row)
        self.bridge = UpdateBridge()
        self.bridge.progress.connect(self.status.setText)
        self.bridge.downloaded.connect(self._ready)
        self.bridge.failed.connect(self._failed)
        if not can_self_update():
            self.install_btn.setEnabled(False)
            self.status.setText("This copy runs as a standalone .exe, which can't update itself. Download the new "
                                "version and rebuild it with build_exe.bat.")
        elif window.engine and window.engine.running:
            self.install_btn.setEnabled(False)
            self.status.setText("Stop the bot first (top right), so an update never interrupts trading. "
                                "Best time: after the market closes.")

    def install(self):
        self.install_btn.setEnabled(False)
        threading.Thread(target=self._download, daemon=True).start()

    def _download(self):
        try:
            self.bridge.downloaded.emit(Updater(self.win.ctx.settings).download(self.info, self.bridge.progress.emit))
        except Exception as e:
            self.bridge.failed.emit(str(e))

    def _failed(self, msg):
        self.status.setText(f"Update failed, nothing was changed:\n{msg}")
        self.install_btn.setEnabled(True)

    def _ready(self, staging):
        if self.win.engine and self.win.engine.running:
            self._failed("The bot was started meanwhile. Stop it and try again.")
            return
        self.status.setText("Verified. Eimeria will now close, update and restart (about a minute).")
        QApplication.processEvents()
        Updater(self.win.ctx.settings).launch_installer(staging)
        self.win.quitting_for_update = True
        QTimer.singleShot(800, QApplication.instance().quit)


def rollback(window):
    up = Updater(window.ctx.settings)
    backups = up.backups()
    if not backups:
        QMessageBox.information(window, "Roll back", "There's no earlier version backed up yet "
                                                     "(a backup is made every time an update is installed).")
        return
    if window.engine and window.engine.running:
        QMessageBox.information(window, "Roll back", "Stop the bot first.")
        return
    b = backups[0]
    if QMessageBox.question(window, "Roll back?", f"Go back to the version backed up in:\n{b.name}\n\n"
                                                  "Eimeria will close, restore it and restart. Your settings, keys and "
                                                  "trade history are kept.") != QMessageBox.Yes:
        return
    up.launch_rollback(b)
    window.quitting_for_update = True
    QTimer.singleShot(500, QApplication.instance().quit)


def show_last_update_result(parent):
    """After a restart: tell the user whether the update worked (once)."""
    f = config.LOG_DIR / "last_update.json"
    if not f.exists():
        return
    try:
        r = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        r = {"ok": False, "error": "unreadable result"}
    f.rename(f.with_name("last_update_shown.json"))
    if r.get("ok"):
        QMessageBox.information(parent, "Eimeria updated", f"Updated from {r.get('from')} to {r.get('to')}.")
    else:
        QMessageBox.warning(parent, "Update didn't install",
                            f"The update failed and the previous version was restored.\n\n{r.get('error', '')}\n\n"
                            "Details: logs\\update.log")
