"""Starts the Eimeria desktop app."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import config
from .broker import AlpacaBroker
from .engine import Engine
from .journal import Journal
from .llm import LocalLLM

DISCLAIMER = (
    "Before you start, please read:\n\n"
    "- Day trading is risky. Most day traders, human or automated, lose money.\n"
    "- This bot can and will make losing trades. Past results don't predict future results.\n"
    "- It starts in PAPER mode (fake money). Keep it there until it has been profitable for weeks.\n"
    "- Never trade money you can't afford to lose.\n"
    "- This software is not financial advice.\n\n"
    "Do you understand and want to continue?"
)


class AppContext:
    def __init__(self):
        self.settings = config.load_settings()
        self.journal = Journal(config.DB_PATH)

    def build_engine(self, emit=None) -> Engine:
        key, secret = config.load_keys()
        broker = AlpacaBroker(key, secret, paper=self.settings["mode"] != "live",
                              feed=self.settings["data"]["live_feed"])
        broker.account()  # fails fast if the keys are wrong
        ai = self.settings["ai"]
        llm = LocalLLM(ai["ollama_url"], ai["model"], ai["temperature"], ai["timeout_seconds"])
        engine = Engine(self.settings, broker, llm, self.journal, emit)
        if emit:
            emit("insiders", engine.insiders.scores())
        return engine


def setup_logging():
    config.LOG_DIR.mkdir(exist_ok=True)
    handler = RotatingFileHandler(config.LOG_DIR / "trader.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    for noisy in ("urllib3", "alpaca"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def install_crash_handlers():
    """The app runs without a console window, so unexpected errors must be written to a file
    (logs/crash.log) and shown, instead of the app silently disappearing."""
    import threading
    import traceback
    from datetime import datetime

    def write(kind, exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            config.LOG_DIR.mkdir(exist_ok=True)
            with open(config.LOG_DIR / "crash.log", "a", encoding="utf-8") as f:
                f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S} {kind}\n{text}\n")
        except Exception:  # nosec B110 - best-effort, must never raise
            pass
        logging.getLogger("crash").error("%s: %s", kind, text)
        return text

    def on_error(exc_type, exc, tb):
        write("unexpected error", exc_type, exc, tb)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance():
                QMessageBox.critical(None, "Eimeria: unexpected error",
                                     f"{exc_type.__name__}: {exc}\n\nDetails were saved to logs\\crash.log. "
                                     "The bot keeps running if it can; restart Eimeria if something looks wrong.")
        except Exception:  # nosec B110 - best-effort, must never raise
            pass

    sys.excepthook = on_error
    threading.excepthook = lambda a: write(f"error in background thread {a.thread.name if a.thread else ''}",
                                           a.exc_type, a.exc_value, a.exc_traceback)


def main():
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .gui.main_window import MainWindow

    install_crash_handlers()
    copied = config.migrate_from_old_folder()
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Eimeria")
    if copied:
        QMessageBox.information(None, "Welcome to Eimeria",
                                "Your settings, keys and trade history were copied from your old app folder:\n\n"
                                + "\n".join(copied) + "\n\nThe old folder wasn't changed; you can delete it later.")
    ctx = AppContext()
    for problem in config.SETTINGS_PROBLEM:
        QMessageBox.warning(None, "Eimeria: settings problem", problem)
    if not ctx.journal.get("accepted_disclaimer"):
        if QMessageBox.question(None, "Eimeria: risk notice", DISCLAIMER) != QMessageBox.Yes:
            return 0
        ctx.journal.set("accepted_disclaimer", True)
    win = MainWindow(ctx)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
