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


def main():
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .gui.main_window import MainWindow

    copied = config.migrate_from_old_folder()
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Eimeria")
    if copied:
        QMessageBox.information(None, "Welcome to Eimeria",
                                "Your settings, keys and trade history were copied from your old app folder:\n\n"
                                + "\n".join(copied) + "\n\nThe old folder wasn't changed; you can delete it later.")
    ctx = AppContext()
    if not ctx.journal.get("accepted_disclaimer"):
        if QMessageBox.question(None, "Eimeria: risk notice", DISCLAIMER) != QMessageBox.Yes:
            return 0
        ctx.journal.set("accepted_disclaimer", True)
    win = MainWindow(ctx)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
