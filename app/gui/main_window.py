"""The desktop window (PySide6 / Qt)."""
from __future__ import annotations

import logging
import webbrowser

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QStyle, QSystemTrayIcon, QDoubleSpinBox, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .. import config, system
from ..indicators import SIGNALS

GREEN, RED, MUTED = "#3fb950", "#f85149", "#8b949e"

STYLE = """
QWidget { background: #0d1117; color: #e6edf3; font-size: 10pt; }
QTabWidget::pane { border: 1px solid #30363d; border-radius: 6px; }
QTabBar::tab { background: #161b22; padding: 8px 16px; border: 1px solid #30363d; border-bottom: none;
               border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; color: #8b949e; }
QTabBar::tab:selected { background: #0d1117; color: #e6edf3; }
QPushButton { background: #21262d; border: 1px solid #30363d; border-radius: 6px; padding: 7px 14px; }
QPushButton:hover { background: #30363d; }
QPushButton#start { background: #238636; border-color: #2ea043; font-weight: bold; }
QPushButton#stop { background: #9e6a03; border-color: #bb8009; font-weight: bold; }
QPushButton#kill { background: #b62324; border-color: #da3633; font-weight: bold; }
QTableWidget { gridline-color: #21262d; background: #0d1117; alternate-background-color: #111821;
               border: 1px solid #30363d; border-radius: 6px; }
QTableWidget::item:selected { background: #1f3a5f; color: #e6edf3; }
QHeaderView::section { background: #161b22; padding: 6px; border: none; border-bottom: 1px solid #30363d;
                       color: #8b949e; font-weight: bold; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QListWidget {
    background: #010409; border: 1px solid #30363d; border-radius: 6px; padding: 4px; }
QGroupBox { border: 1px solid #30363d; border-radius: 6px; margin-top: 14px; padding-top: 10px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #8b949e; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #6e7681; border-radius: 3px; background: #010409; }
QCheckBox::indicator:checked { background: #238636; border-color: #2ea043; }
QFrame#card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; }
"""


class Bridge(QObject):
    """Carries events from the engine's background thread to the window safely."""
    event = Signal(str, object)


class QtLogHandler(logging.Handler):
    def __init__(self, bridge: Bridge):
        super().__init__(level=logging.WARNING)
        self.bridge = bridge

    def emit(self, record):
        if record.name.startswith("app.engine"):
            return  # engine already reports its own messages
        self.bridge.event.emit("log", {"time": "", "msg": record.getMessage(), "level": record.levelno})


def make_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def fill_table(t: QTableWidget, rows: list[list], colors: list[list[str | None]] | None = None):
    t.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            item = QTableWidgetItem("" if val is None else str(val))
            if colors and colors[r][c]:
                item.setForeground(QColor(colors[r][c]))
            t.setItem(r, c, item)


def money(v: float) -> str:
    sign = "+" if v > 0 else "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def pnl_color(v: float) -> str:
    return GREEN if v > 0 else RED if v < 0 else MUTED


class StatCard(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        t = QLabel(title)
        t.setStyleSheet(f"color: {MUTED}; background: transparent;")
        self.value = QLabel("-")
        self.value.setStyleSheet("font-size: 18pt; font-weight: bold; background: transparent;")
        lay.addWidget(t)
        lay.addWidget(self.value)

    def set(self, text: str, color: str | None = None):
        self.value.setText(text)
        self.value.setStyleSheet(f"font-size: 18pt; font-weight: bold; background: transparent; color: {color or '#e6edf3'};")


class MainWindow(QMainWindow):
    def __init__(self, app_ctx):
        super().__init__()
        self.ctx = app_ctx            # holds settings, journal and builds the engine
        self.bridge = Bridge()
        self.bridge.event.connect(self.on_event)
        logging.getLogger().addHandler(QtLogHandler(self.bridge))
        self.engine = None
        from ..version import VERSION
        self.setWindowTitle(f"Eimeria {VERSION}")
        self.quitting_for_update = False
        self.resize(1280, 840)
        self.setStyleSheet(STYLE)

        central = QWidget()
        root = QVBoxLayout(central)
        root.addLayout(self._build_top_bar())
        splitter = QSplitter(Qt.Vertical)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_dashboard(), "Dashboard")
        self.tabs.addTab(self._build_signals(), "Signals")
        from .swing_tab import SwingTab
        self.swing_tab = SwingTab(self)
        self.tabs.addTab(self.swing_tab, "Swing")
        self.tabs.addTab(self._build_news(), "News && Reports")
        self.tabs.addTab(self._build_smart_money(), "Smart Money")
        self.tabs.addTab(self._build_journal(), "Trade Journal")
        self.tabs.addTab(self._build_learning(), "Learning")
        from .ai_tab import AITab
        self.ai_tab = AITab(self)
        self.tabs.addTab(self.ai_tab, "AI")
        from .backtest_tab import BacktestTab
        self.backtest_tab = BacktestTab(self)
        self.tabs.addTab(self.backtest_tab, "Backtest")
        self.tabs.addTab(self._build_settings(), "Settings")
        splitter.addWidget(self.tabs)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(2000)
        self.log_box.setFont(QFont("Consolas", 9))
        splitter.addWidget(self.log_box)
        splitter.setSizes([760, 90])               # the log starts small; drag the divider to enlarge it
        splitter.setStretchFactor(0, 1)            # extra window height goes to the tabs, not the log
        splitter.setStretchFactor(1, 0)
        root.addWidget(splitter)
        self.setCentralWidget(central)

        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.SP_ComputerIcon), self)
            self.tray.setToolTip("Eimeria")
            self.tray.show()

        from .update_dialog import UpdateManager, show_last_update_result
        self.updates = UpdateManager(self)
        QTimer.singleShot(300, lambda: show_last_update_result(self))

        self.refresh_journal()
        self.refresh_learning()
        self._load_saved_reports()
        QTimer.singleShot(200, self.connect_broker)

    # ================================================================= layout
    def _build_top_bar(self):
        bar = QHBoxLayout()
        title = QLabel("Eimeria")
        title.setStyleSheet("font-size: 16pt; font-weight: bold;")
        self.mode_badge = QLabel()
        self.market_lbl = QLabel("Market: ?")
        self.ai_lbl = QLabel("AI: ?")
        self.regime_lbl = QLabel("Market mood: ?")
        self.regime_lbl.setToolTip("How the whole market is doing (S&P 500 and the fear index).\n"
                                   "In a falling market the bot is pickier, and stops buying in a selloff.")
        self.start_btn = QPushButton("Start Bot")
        self.start_btn.setObjectName("start")
        self.start_btn.clicked.connect(self.toggle_bot)
        kill = QPushButton("STOP && CLOSE ALL")
        kill.setObjectName("kill")
        kill.setToolTip("Emergency: stop the bot, cancel all orders and sell/close every position now.")
        kill.clicked.connect(self.kill_switch)
        bar.addWidget(title)
        bar.addSpacing(10)
        bar.addWidget(self.mode_badge)
        self.update_btn = QPushButton("Update available")
        self.update_btn.setStyleSheet("background: #9e6a03; border-color: #bb8009; font-weight: bold;")
        self.update_btn.clicked.connect(lambda: self.updates.open_dialog())
        self.update_btn.hide()
        bar.addSpacing(10)
        bar.addWidget(self.update_btn)
        bar.addStretch()
        bar.addWidget(self.market_lbl)
        bar.addSpacing(14)
        bar.addWidget(self.regime_lbl)
        bar.addSpacing(14)
        bar.addWidget(self.ai_lbl)
        bar.addSpacing(14)
        bar.addWidget(self.start_btn)
        bar.addWidget(kill)
        self._update_mode_badge()
        return bar

    def _build_dashboard(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        from .getting_started import GettingStarted
        self.getting_started = GettingStarted(self)
        lay.addWidget(self.getting_started)
        cards = QHBoxLayout()
        self.c_equity, self.c_today = StatCard("Account value"), StatCard("Today's P&L")
        self.c_bp, self.c_win, self.c_total = StatCard("Buying power"), StatCard("Win rate"), StatCard("Bot P&L (all time)")
        for c in (self.c_equity, self.c_today, self.c_bp, self.c_win, self.c_total):
            cards.addWidget(c)
        lay.addLayout(cards)
        lay.addWidget(QLabel("Open positions"))
        self.pos_table = make_table(["Symbol", "Side", "Shares", "Entry", "Price", "P&L $", "P&L %"])
        lay.addWidget(self.pos_table)
        actions = QHBoxLayout()
        for label, task, tip in [
            ("Refresh", "refresh", "Reload account and positions"),
            ("Run pre-market briefing now", "briefing", "Read the news and write today's briefing"),
            ("Scan stocks now", "scan", "Analyze stocks now (orders only sent while the market is open)"),
            ("Run after-close review now", "review", "Review today's trades and learn from them"),
            ("Check insider buying", "insiders", "Download recent SEC insider purchase filings"),
            ("Check stop-losses", "protect", "Make sure every open position has a stop-loss at the broker"),
            ("Self-test", "selftest", "Check every connection: Alpaca, prices, news, SEC, local AI, orders"),
        ]:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, t=task: self.run_task(t))
            actions.addWidget(b)
        actions.addStretch()
        lay.addLayout(actions)
        return w

    def _build_signals(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("Latest scan. Score: -1 strongly bearish ... +1 strongly bullish. The strongest few "
                      "are checked against the news and sent to the AI for a decision.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED};")
        lay.addWidget(info)
        self.sig_table = make_table(["Symbol", "Price", "Change", "RSI", "Rel. volume", "Score", "AI decision",
                                     "Confidence", "AI reasoning"])
        lay.addWidget(self.sig_table)
        return w

    def _build_news(self):
        w = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("Pre-market briefing"))
        self.briefing_box = QTextEdit(readOnly=True)
        ll.addWidget(self.briefing_box)
        ll.addWidget(QLabel("After-close review"))
        self.review_box = QTextEdit(readOnly=True)
        ll.addWidget(self.review_box)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("Headlines (double-click to open)"))
        self.headlines = QListWidget()
        self.headlines.setWordWrap(True)
        self.headlines.itemDoubleClicked.connect(lambda it: it.data(Qt.UserRole) and webbrowser.open(it.data(Qt.UserRole)))
        rl.addWidget(self.headlines)
        w.addWidget(left)
        w.addWidget(right)
        w.setSizes([700, 500])
        return w

    def _build_smart_money(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("Company insiders (executives, directors) buying their own company's stock on the open "
                      "market, from SEC Form 4 filings. Multiple insiders buying together is a stronger signal. "
                      "Insider buying predicts moves over weeks to months, not minutes, so the bot uses it to "
                      "choose which stocks to WATCH (the top 5 join each scan) and tells the AI about it. "
                      "Trades still need good intraday signals.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED};")
        lay.addWidget(info)
        self.ins_table = make_table(["Symbol", "Score", "Total bought", "# Insiders", "Who"])
        lay.addWidget(self.ins_table)
        return w

    def _build_journal(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.stats_lbl = QLabel()
        self.stats_lbl.setStyleSheet("font-size: 11pt;")
        lay.addWidget(self.stats_lbl)
        self.journal_table = make_table(["Date", "Symbol", "Side", "Shares", "Entry", "Exit", "P&L $", "P&L %",
                                         "Exit reason", "Confidence", "Why it traded"])
        lay.addWidget(self.journal_table)
        return w

    def _build_learning(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        left = QVBoxLayout()
        left.addWidget(QLabel("Signal weights (learned): higher = trusted more"))
        self.weights_table = make_table(["Signal", "Weight"])
        left.addWidget(self.weights_table)
        self.conf_lbl = QLabel()
        left.addWidget(self.conf_lbl)
        self.shadow_lbl = QLabel()
        self.shadow_lbl.setWordWrap(True)
        self.shadow_lbl.setStyleSheet(f"color: {MUTED};")
        left.addWidget(self.shadow_lbl)
        edge_box = QGroupBox("Edge check: does the bot actually make money after costs?")
        el = QVBoxLayout(edge_box)
        self.edge_lbl = QLabel()
        self.edge_lbl.setWordWrap(True)
        self.edge_lbl.setTextFormat(Qt.RichText)
        el.addWidget(self.edge_lbl)
        left.addWidget(edge_box)
        right = QVBoxLayout()
        right.addWidget(QLabel("Lessons the AI has learned (used in every trade decision)"))
        self.lessons_list = QListWidget()
        self.lessons_list.setWordWrap(True)
        right.addWidget(self.lessons_list)
        forget = QPushButton("Forget selected lesson")
        forget.clicked.connect(self.forget_lesson)
        right.addWidget(forget)
        lay.addLayout(left, 1)
        lay.addLayout(right, 2)
        return w

    def _build_settings(self):
        s = self.ctx.settings
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        w = QWidget()
        lay = QVBoxLayout(w)

        g = QGroupBox("1. Alpaca broker keys (from alpaca.markets > Paper Trading > API Keys)")
        f = QFormLayout(g)
        key, secret = config.load_keys()
        self.f_key = QLineEdit(key)
        self.f_secret = QLineEdit(secret)
        self.f_secret.setEchoMode(QLineEdit.Password)
        self.f_mode = QComboBox()
        self.f_mode.addItems(["paper", "live"])
        self.f_mode.setCurrentText(s["mode"])
        f.addRow("API Key ID", self.f_key)
        f.addRow("Secret Key", self.f_secret)
        f.addRow("Mode (paper = fake money)", self.f_mode)
        lay.addWidget(g)

        g = QGroupBox("2. Local AI (Ollama)")
        f = QFormLayout(g)
        self.f_ollama = QLineEdit(s["ai"]["ollama_url"])
        self.f_model = QLineEdit(s["ai"]["model"])
        self.f_require_ai = QCheckBox("Only trade when the AI approves (recommended)")
        self.f_require_ai.setChecked(s["ai"]["require_ai_approval"])
        f.addRow("Ollama address", self.f_ollama)
        f.addRow("Model name", self.f_model)
        f.addRow("", self.f_require_ai)
        lay.addWidget(g)

        g = QGroupBox("3. What to trade")
        f = QFormLayout(g)
        self.f_watch = QLineEdit(", ".join(s["watchlist"]))
        self.f_movers = QCheckBox("Also scan today's top movers / most active stocks")
        self.f_movers.setChecked(s["add_top_movers"])
        self.f_short = QCheckBox("Allow short selling (betting on drops; riskier)")
        self.f_short.setChecked(s["allow_short"])
        self.f_interval = QSpinBox(minimum=1, maximum=60, value=s["scan_interval_minutes"], suffix=" min")
        self.f_sec = QLineEdit(s["smart_money"]["sec_user_agent"])
        self.f_sec.setPlaceholderText("Your Name your@email.com  (required by the SEC for insider data)")
        f.addRow("Watchlist (comma separated)", self.f_watch)
        f.addRow("", self.f_movers)
        f.addRow("", self.f_short)
        f.addRow("Scan every", self.f_interval)
        f.addRow("SEC contact (insider data)", self.f_sec)
        self.f_feed = QComboBox()
        self.f_feed.addItems(["iex", "sip"])
        self.f_feed.setCurrentText(s["data"]["live_feed"])
        self.f_feed.setToolTip("iex = free, one exchange only. sip = every exchange; needs Alpaca's paid "
                               "market data plan (otherwise scans will fail).")
        f.addRow("Live price data (iex = free, sip = paid)", self.f_feed)
        lay.addWidget(g)

        g = QGroupBox("4. Risk limits (the bot can never change these)")
        f = QFormLayout(g)
        r = s["risk"]

        def pct(v, mx=100.0):
            sb = QDoubleSpinBox(minimum=0.1, maximum=mx, decimals=2, suffix=" %", singleStep=0.1)
            sb.setValue(v * 100)
            return sb
        self.f_maxpos = pct(r["max_position_pct"])
        self.f_risk = pct(r["risk_per_trade_pct"], 5)
        self.f_stop = pct(r["stop_loss_pct"], 10)
        self.f_rr = QDoubleSpinBox(minimum=0.5, maximum=10, decimals=1, value=r["reward_risk_ratio"], singleStep=0.5)
        self.f_maxdl = pct(r["max_daily_loss_pct"], 20)
        self.f_maxopen = QSpinBox(minimum=1, maximum=50, value=r["max_open_positions"])
        self.f_maxtrades = QSpinBox(minimum=1, maximum=200, value=r["max_trades_per_day"])
        self.f_conf = QDoubleSpinBox(minimum=0.3, maximum=0.99, decimals=2, value=r["min_confidence"], singleStep=0.05)
        self.f_pdt = QCheckBox("Pattern-day-trader protection for accounts under $25,000")
        self.f_pdt.setChecked(r["pdt_protection"])
        f.addRow("Max size of one position (% of account)", self.f_maxpos)
        f.addRow("Max loss per trade (% of account)", self.f_risk)
        f.addRow("Stop-loss distance", self.f_stop)
        f.addRow("Take-profit = stop distance x", self.f_rr)
        f.addRow("Stop trading for the day after losing", self.f_maxdl)
        f.addRow("Max open positions", self.f_maxopen)
        f.addRow("Max trades per day", self.f_maxtrades)
        f.addRow("Min AI confidence to trade (0-1)", self.f_conf)
        f.addRow("", self.f_pdt)
        self.f_gate = QCheckBox("Lock real-money trading until the bot has proven an edge (Learning tab)")
        self.f_gate.setChecked(s["edge_gate"]["enabled"])
        f.addRow("", self.f_gate)
        lay.addWidget(g)

        g = QGroupBox("Strategies")
        f = QFormLayout(g)
        sw = s["swing"]
        self.f_day_on = QCheckBox("Day trading: 5-minute scans, every trade closed before 4 PM")
        self.f_day_on.setChecked(s["day_trading"]["enabled"])
        self.f_day_on.setToolTip("Backtests on real data found the day-trading signals did NOT beat costs.")
        self.f_swing_on = QCheckBox("Swing trading: hold for days to weeks (see the Swing tab)")
        self.f_swing_on.setChecked(sw["enabled"])
        self.f_sw_insider = QCheckBox("Setup: insider buying")
        self.f_sw_insider.setChecked(sw["setups"]["insider_buy"])
        self.f_sw_gap = QCheckBox("Setup: big news jump that held (gap up on heavy volume)")
        self.f_sw_gap.setChecked(sw["setups"]["news_gap"])
        self.f_sw_trend = QCheckBox("Only buy stocks above their 50-day average (uptrend)")
        self.f_sw_trend.setChecked(sw["require_uptrend"])
        self.f_sw_ai = QCheckBox("Let the AI read the news and veto swing candidates")
        self.f_sw_ai.setChecked(sw["use_ai_review"])
        self.f_sw_index = QCheckBox("Keep idle cash in an S&&P 500 fund (SPY) instead of cash")
        self.f_sw_index.setChecked(sw["idle_cash_in_index"])
        self.f_sw_index.setToolTip(
            "Money not in swing trades is held in SPY and sold when a swing trade needs it.\n"
            "Tested 2019-2026: beat holding the S&P 500 alone in both 2019-22 and 2023-26, BUT the account\n"
            "then rises and falls with the market (worst drop -35% in 2020 vs -6% without it).")
        self.f_sw_filter = QCheckBox("Market filter: no new swing buys while the S&&P 500 is below its 200-day average")
        self.f_sw_filter.setChecked(sw["market_filter"])
        self.f_sw_filter.setToolTip("Tested: helped in 2019-22 (smaller drops), slightly hurt in 2023-26.")
        self.f_crash = QComboBox()
        self.f_crash.addItems(["Off", "Step aside: move to cash in a falling market (tested best)",
                               "Short: switch to an inverse S&P 500 fund (tested WORSE)"])
        self.f_crash.setCurrentIndex(["off", "step_aside", "short"].index(sw.get("crash_protection", "off")))
        self.f_crash.setToolTip(
            "Checked at each month's end: if the S&P 500 closed below its 10-month average, stop buying, sell the\n"
            "S&P 500 fund (if held), and either hold cash or an inverse fund (SH) until it closes a month back above.\n"
            "1994-2026 tests: stepping aside cut the worst drop from ~50% to ~20%, but lagged strong bull markets.\n"
            "Shorting made money in crashes but lost more in the rebounds: worse than holding the index overall.\n"
            "It reacts AFTER prices start falling; it can't see a crash coming.")
        self.f_sw_hold = QSpinBox(minimum=2, maximum=120, value=sw["hold_days"], suffix=" trading days")
        self.f_sw_max = QSpinBox(minimum=1, maximum=30, value=sw["max_positions"])
        f.addRow("", self.f_day_on)
        f.addRow("", self.f_swing_on)
        f.addRow("", self.f_sw_insider)
        f.addRow("", self.f_sw_gap)
        f.addRow("", self.f_sw_trend)
        f.addRow("", self.f_sw_ai)
        f.addRow("", self.f_sw_index)
        f.addRow("", self.f_sw_filter)
        f.addRow("Crash protection", self.f_crash)
        f.addRow("Swing: hold for", self.f_sw_hold)
        f.addRow("Swing: max positions", self.f_sw_max)
        lay.insertWidget(1, g)

        g = QGroupBox("5. Smarter exits and market filter")
        f = QFormLayout(g)
        x = s["exits"]
        self.f_partial = QCheckBox("Take half profit at 1x the stop distance, then move the stop to break-even")
        self.f_partial.setChecked(x["partial_enabled"])
        self.f_trail = QCheckBox("Then trail the stop behind the price")
        self.f_trail.setChecked(x["trailing_enabled"])
        self.f_timestop = QSpinBox(minimum=0, maximum=390, value=x["time_stop_minutes"], suffix=" min")
        self.f_timestop.setToolTip("Close a trade that hasn't moved after this long (0 = off)")
        self.f_regime = QCheckBox("Be pickier when the whole market is falling; no new buys in a selloff")
        self.f_regime.setChecked(s["market_filter"]["enabled"])
        f.addRow("", self.f_partial)
        f.addRow("", self.f_trail)
        f.addRow("Time stop (close trades going nowhere after)", self.f_timestop)
        f.addRow("", self.f_regime)
        lay.addWidget(g)

        g = QGroupBox("6. Alerts")
        f = QFormLayout(g)
        al = s["alerts"]
        self.f_desktop = QCheckBox("Windows pop-up notifications")
        self.f_desktop.setChecked(al["desktop"])
        self.f_ntfy = QLineEdit(al["ntfy_topic"])
        self.f_ntfy.setPlaceholderText("Optional: a long random name, e.g. eimeria-7f3k9q2x (see README)")
        self.f_on_trade = QCheckBox("Trades")
        self.f_on_trade.setChecked(al["on_trade"])
        self.f_on_problem = QCheckBox("Problems (loss limit, errors, missing stop-loss)")
        self.f_on_problem.setChecked(al["on_problem"])
        self.f_on_report = QCheckBox("Daily briefing and review")
        self.f_on_report.setChecked(al["on_report"])
        test = QPushButton("Send a test alert")
        test.clicked.connect(self.test_alert)
        f.addRow("", self.f_desktop)
        f.addRow("Phone alerts (ntfy topic)", self.f_ntfy)
        f.addRow("Alert me about", self.f_on_trade)
        f.addRow("", self.f_on_problem)
        f.addRow("", self.f_on_report)
        f.addRow("", test)
        lay.addWidget(g)

        g = QGroupBox("Updates")
        f = QFormLayout(g)
        from ..version import VERSION
        f.addRow("Installed version", QLabel(VERSION))
        self.f_upd_repo = QLineEdit(s.get("updates", {}).get("repo", ""))
        self.f_upd_repo.setToolTip("GitHub repository that publishes Eimeria updates (owner/name)")
        self.f_upd_auto = QCheckBox("Check for updates when Eimeria starts (and every 6 hours)")
        self.f_upd_auto.setChecked(s.get("updates", {}).get("check_on_start", True))
        row = QHBoxLayout()
        chk = QPushButton("Check for updates now")
        chk.clicked.connect(lambda: self.updates.check(silent=False))
        rb = QPushButton("Roll back to the previous version")
        rb.clicked.connect(self.roll_back)
        row.addWidget(chk)
        row.addWidget(rb)
        row.addStretch()
        f.addRow("Update source", self.f_upd_repo)
        f.addRow("", self.f_upd_auto)
        f.addRow("", row)
        lay.addWidget(g)

        save = QPushButton("Save settings")
        save.setObjectName("start")
        save.clicked.connect(self.save_settings)
        lay.addWidget(save)
        lay.addStretch()
        scroll.setWidget(w)
        return scroll

    # ================================================================= actions
    def connect_broker(self):
        try:
            self.engine = self.ctx.build_engine(emit=self.bridge.event.emit)
        except Exception as e:
            self.engine = None
            self.log(f"Not connected: {e}", logging.WARNING)
            self.tabs.setCurrentIndex(self.tabs.count() - 1)
            from ..broker import diagnose_keys, is_auth_error
            if is_auth_error(e):
                key, secret = config.load_keys()
                try:
                    why = diagnose_keys(key, secret, paper=self.ctx.settings["mode"] != "live")
                except Exception as ex:                       # never let the diagnosis itself break anything
                    why = f"(Couldn't check further: {ex})"
                self.log("Why: " + why.replace("\n\n", " "), logging.WARNING)
                QMessageBox.warning(self, "Alpaca didn't accept the keys", why)
            return
        self.log(f"Connected to Alpaca ({self.ctx.settings['mode'].upper()} account).")
        self.getting_started.refresh()
        if hasattr(self, "ai_tab"):
            self.ai_tab.refresh()
        ok, msg = self.engine.llm.status()
        self.set_ai_label(ok, msg)
        self.run_task("refresh")

    def toggle_bot(self):
        if not self.engine:
            QMessageBox.warning(self, "Not connected", "Enter your Alpaca keys on the Settings tab and save first.")
            return
        if self.engine.running:
            self.engine.stop()
            system.keep_awake(False)
            self.start_btn.setText("Start Bot")
            self.start_btn.setObjectName("start")
        else:
            if self.ctx.settings["mode"] == "live":
                ok = QMessageBox.question(self, "LIVE TRADING", "The bot will trade with REAL MONEY. Continue?")
                if ok != QMessageBox.Yes:
                    return
            self.engine.start()
            if self.ctx.settings["mode"] == "paper":
                self.ctx.journal.set("paper_started", True)
                self.getting_started.refresh()
            if system.keep_awake(True):
                self.log("Sleep mode blocked while the bot runs (the screen can still turn off).")
            self.start_btn.setText("Stop Bot")
            self.start_btn.setObjectName("stop")
        self.start_btn.setStyle(self.start_btn.style())  # re-apply stylesheet

    def kill_switch(self):
        if not self.engine:
            return
        if QMessageBox.question(self, "Close everything?",
                                "Stop the bot, cancel all orders and close ALL positions right now?") == QMessageBox.Yes:
            self.engine.kill_switch()
            system.keep_awake(False)
            self.start_btn.setText("Start Bot")
            self.start_btn.setObjectName("start")
            self.start_btn.setStyle(self.start_btn.style())
            self.run_task("refresh")

    def open_self_test(self):
        from .selftest_dialog import SelfTestDialog
        SelfTestDialog(self).exec()

    def run_task(self, name: str):
        if name == "selftest":
            self.open_self_test()
            return
        if not self.engine:
            QMessageBox.warning(self, "Not connected", "Enter your Alpaca keys on the Settings tab and save first.")
            return
        self.engine.run_task(name)

    def save_settings(self):
        s = self.ctx.settings
        new_mode = self.f_mode.currentText()
        if new_mode == "live" and s["mode"] != "live":
            text, ok = QInputDialog.getText(
                self, "Switch to LIVE trading?",
                "Live mode trades REAL MONEY and you can lose it.\nOnly do this after weeks of good paper results.\n"
                "Live mode also needs your LIVE account keys (different from paper keys).\n\nType LIVE to confirm:")
            if not ok or text.strip() != "LIVE":
                self.f_mode.setCurrentText("paper")
                new_mode = "paper"
        if self.engine and self.engine.running:
            QMessageBox.information(self, "Stop the bot first", "Stop the bot before changing settings.")
            return
        if not self.f_gate.isChecked() and s["edge_gate"]["enabled"]:
            text, ok = QInputDialog.getText(
                self, "Remove the safety lock?",
                "Without the lock, the bot can trade real money before there is any evidence it makes money.\n"
                "Backtests on real data found the chart signals alone do NOT beat trading costs.\n\n"
                "Type I ACCEPT THE RISK to remove the lock:")
            if not ok or text.strip().upper() != "I ACCEPT THE RISK":
                self.f_gate.setChecked(True)
        s["mode"] = new_mode
        s["ai"]["ollama_url"] = self.f_ollama.text().strip()
        s["ai"]["model"] = self.f_model.text().strip()
        s["ai"]["require_ai_approval"] = self.f_require_ai.isChecked()
        s["watchlist"] = [x.strip().upper() for x in self.f_watch.text().replace(";", ",").split(",") if x.strip()]
        s["add_top_movers"] = self.f_movers.isChecked()
        s["allow_short"] = self.f_short.isChecked()
        s["scan_interval_minutes"] = self.f_interval.value()
        s["smart_money"]["sec_user_agent"] = self.f_sec.text().strip()
        r = s["risk"]
        r["max_position_pct"] = self.f_maxpos.value() / 100
        r["risk_per_trade_pct"] = self.f_risk.value() / 100
        r["stop_loss_pct"] = self.f_stop.value() / 100
        r["reward_risk_ratio"] = self.f_rr.value()
        r["max_daily_loss_pct"] = self.f_maxdl.value() / 100
        r["max_open_positions"] = self.f_maxopen.value()
        r["max_trades_per_day"] = self.f_maxtrades.value()
        r["min_confidence"] = self.f_conf.value()
        r["pdt_protection"] = self.f_pdt.isChecked()
        s.setdefault("updates", {})["repo"] = self.f_upd_repo.text().strip()
        s["updates"]["check_on_start"] = self.f_upd_auto.isChecked()
        s["edge_gate"]["enabled"] = self.f_gate.isChecked()
        s["data"]["live_feed"] = self.f_feed.currentText()
        s["day_trading"]["enabled"] = self.f_day_on.isChecked()
        sw = s["swing"]
        sw["enabled"] = self.f_swing_on.isChecked()
        sw["setups"]["insider_buy"] = self.f_sw_insider.isChecked()
        sw["setups"]["news_gap"] = self.f_sw_gap.isChecked()
        sw["require_uptrend"] = self.f_sw_trend.isChecked()
        sw["use_ai_review"] = self.f_sw_ai.isChecked()
        sw["hold_days"] = self.f_sw_hold.value()
        sw["max_positions"] = self.f_sw_max.value()
        if self.f_sw_index.isChecked() and not sw["idle_cash_in_index"]:
            if QMessageBox.question(
                    self, "Keep idle cash in the S&P 500?",
                    "Money that isn't in swing trades will be invested in an S&P 500 fund (SPY).\n\n"
                    "In 2019-2026 tests this beat simply holding the S&P 500, but your account will then rise "
                    "AND FALL with the whole market: in the 2020 crash it would have dropped about 35%, versus "
                    "about 6% without this option.\n\nSwitch it on?") != QMessageBox.Yes:
                self.f_sw_index.setChecked(False)
        sw["idle_cash_in_index"] = self.f_sw_index.isChecked()
        new_crash = ["off", "step_aside", "short"][self.f_crash.currentIndex()]
        if new_crash == "short" and sw.get("crash_protection") != "short":
            if QMessageBox.question(
                    self, "Short the market in downturns?",
                    "In tests from 1994 to 2026, shorting (holding an inverse S&P 500 fund) when the market fell below "
                    "its 10-month average made money DURING crashes, but lost more in the rebounds that followed "
                    "(e.g. -14% after 2009, -19% after 2020). Overall it did worse than simply holding the index in "
                    "every period, and worse than 'Step aside'.\n\nUse 'Short' anyway?") != QMessageBox.Yes:
                new_crash = sw.get("crash_protection", "off")
                self.f_crash.setCurrentIndex(["off", "step_aside", "short"].index(new_crash))
        sw["crash_protection"] = new_crash
        sw["market_filter"] = self.f_sw_filter.isChecked()
        x = s["exits"]
        x["partial_enabled"] = self.f_partial.isChecked()
        x["trailing_enabled"] = self.f_trail.isChecked()
        x["time_stop_minutes"] = self.f_timestop.value()
        s["market_filter"]["enabled"] = self.f_regime.isChecked()
        al = s["alerts"]
        al["desktop"] = self.f_desktop.isChecked()
        al["ntfy_topic"] = self.f_ntfy.text().strip()
        al["on_trade"] = self.f_on_trade.isChecked()
        al["on_problem"] = self.f_on_problem.isChecked()
        al["on_report"] = self.f_on_report.isChecked()
        config.save_settings(s)
        config.save_keys(self.f_key.text(), self.f_secret.text())
        self._update_mode_badge()
        self.log("Settings saved.")
        self.connect_broker()

    def roll_back(self):
        from .update_dialog import rollback
        rollback(self)

    def forget_lesson(self):
        item = self.lessons_list.currentItem()
        if item:
            self.ctx.journal.deactivate_lesson(item.data(Qt.UserRole))
            self.refresh_learning()

    def closeEvent(self, ev):
        if self.quitting_for_update:
            if self.engine:
                self.engine.stop()
            system.keep_awake(False)
            ev.accept()
            return
        if self.engine and self.engine.running:
            if QMessageBox.question(
                    self, "Quit?", "The bot is running. If you quit, it will NOT close your positions at the end "
                    "of the day (their stop-loss/take-profit orders stay active).\n\nQuit anyway?") != QMessageBox.Yes:
                ev.ignore()
                return
            self.engine.stop()
        system.keep_awake(False)
        ev.accept()

    # ================================================================= events from engine
    def on_event(self, kind: str, data):
        if kind == "log":
            self.log(data["msg"], data.get("level", logging.INFO), data.get("time"))
        elif kind == "status":
            self.market_lbl.setText("Market: OPEN" if data["market_open"] else "Market: closed")
            self.market_lbl.setStyleSheet(f"color: {GREEN if data['market_open'] else MUTED};")
            self.set_ai_label(data["ai_ok"], data["ai_msg"])
            if data.get("regime"):
                self.show_regime(data["regime"])
        elif kind == "regime":
            self.show_regime(data)
        elif kind == "alert":
            self.show_alert(data)
        elif kind == "account":
            self.show_account(data)
        elif kind == "signals":
            self.show_signals(data)
        elif kind == "headlines":
            self.show_headlines(data)
        elif kind == "report":
            (self.briefing_box if data["kind"] == "briefing" else self.review_box).setPlainText(data["text"])
        elif kind == "insiders":
            self.show_insiders(data)
        elif kind == "journal":
            self.refresh_journal()
            self.swing_tab.refresh()
        elif kind == "swing_plan":
            self.swing_tab.refresh(data)
        elif kind == "crash_state":
            self.swing_tab.refresh()
        elif kind == "learning":
            self.refresh_learning()

    def log(self, msg: str, level=logging.INFO, t: str | None = None):
        color = RED if level >= logging.ERROR else "#d29922" if level >= logging.WARNING else "#e6edf3"
        prefix = f"{t}  " if t else ""
        self.log_box.appendHtml(f'<span style="color:{color}">{prefix}{_esc(msg)}</span>')

    def show_regime(self, r: dict):
        names = {"risk_on": ("rising", GREEN), "neutral": ("normal", MUTED),
                 "risk_off": ("falling", "#d29922"), "panic": ("SELLOFF", RED)}
        name, color = names.get(r.get("state"), ("?", MUTED))
        self.regime_lbl.setText(f"Market mood: {name}")
        self.regime_lbl.setStyleSheet(f"color: {color};")
        self.regime_lbl.setToolTip(r.get("detail", ""))

    def show_alert(self, a: dict):
        if self.tray:
            icon = QSystemTrayIcon.Warning if a.get("urgent") else QSystemTrayIcon.Information
            self.tray.showMessage(a["title"], a["message"][:250], icon, 8000)

    def test_alert(self):
        from ..alerts import Alerter
        cfg = {**self.ctx.settings["alerts"], "desktop": self.f_desktop.isChecked(),
               "ntfy_topic": self.f_ntfy.text().strip(), "on_problem": True}
        Alerter(cfg, self.bridge.event.emit).send("Test alert", "Eimeria alerts are working.", "problem")
        self.log("Test alert sent." + ("" if cfg["ntfy_topic"] else " (No phone topic set: desktop only.)"))

    def set_ai_label(self, ok: bool, msg: str):
        self.ai_lbl.setText("AI: ready" if ok else "AI: offline")
        self.ai_lbl.setToolTip(msg)
        self.ai_lbl.setStyleSheet(f"color: {GREEN if ok else RED};")

    def _update_mode_badge(self):
        live = self.ctx.settings["mode"] == "live"
        self.mode_badge.setText(" LIVE: REAL MONEY " if live else " PAPER TRADING ")
        self.mode_badge.setStyleSheet(
            f"background: {'#b62324' if live else '#1f6feb'}; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 3px 8px;")

    def show_account(self, data):
        a, st = data["account"], data["stats"]
        today = a["equity"] - a["last_equity"]
        self.c_equity.set(f"${a['equity']:,.2f}")
        self.c_today.set(f"{money(today)} ({data['daily_pnl_pct']:+.2%})", pnl_color(today))
        self.c_bp.set(f"${a['buying_power']:,.0f}")
        self.c_win.set(f"{st['win_rate']:.0%} of {st['trades']}" if st["trades"] else "no trades yet")
        self.c_total.set(money(st["total_pnl"]), pnl_color(st["total_pnl"]))
        rows, colors = [], []
        for p in data["positions"]:
            rows.append([p["symbol"], p["side"], f"{p['qty']:g}", f"{p['avg_entry']:.2f}", f"{p['current_price']:.2f}",
                         f"{p['unrealized_pl']:+.2f}", f"{p['unrealized_plpc']:+.2%}"])
            c = pnl_color(p["unrealized_pl"])
            colors.append([None] * 5 + [c, c])
        fill_table(self.pos_table, rows, colors)

    def show_signals(self, table):
        rows, colors = [], []
        for r in table:
            rows.append([r["symbol"], f"{r['price']:.2f}", f"{r['change']:+.2%}", f"{r['rsi']:.0f}",
                         f"{r['rel_volume']:.1f}x", f"{r['score']:+.2f}", r["decision"],
                         f"{r['confidence']:.2f}" if r["confidence"] is not None else "", r["reason"]])
            dc = GREEN if r["decision"] == "buy" else RED if r["decision"] == "short" else None
            colors.append([None, None, pnl_color(r["change"]), None, None, pnl_color(r["score"]), dc, None, None])
        fill_table(self.sig_table, rows, colors)

    def show_headlines(self, items):
        self.headlines.clear()
        for h in items:
            when = h["time"].strftime("%m/%d %H:%M") if hasattr(h["time"], "strftime") else ""
            syms = f"  [{', '.join(h['symbols'][:4])}]" if h.get("symbols") else ""
            it = QListWidgetItem(f"{when}  {h['headline']}{syms}\n    {h['source']}")
            it.setData(Qt.UserRole, h.get("url"))
            self.headlines.addItem(it)

    def show_insiders(self, scores: dict):
        rows = [[sym, f"{v['score']:.2f}", f"${v['total']:,.0f}", v["insiders"], v["detail"]]
                for sym, v in sorted(scores.items(), key=lambda kv: -kv[1]["score"])]
        fill_table(self.ins_table, rows)

    def refresh_journal(self):
        j = self.ctx.journal
        st = j.stats()
        pf = "∞" if st["profit_factor"] == float("inf") else f"{st['profit_factor']:.2f}"
        self.stats_lbl.setText(
            f"Closed trades: {st['trades']}   |   Win rate: {st['win_rate']:.0%}   |   Total P&L: "
            f"{money(st['total_pnl'])}   |   Avg per trade: {st['avg_pnl_pct']:+.2%}   |   Profit factor: {pf}")
        trades = j.open_trades()[::-1] + j.closed_trades(300)
        rows, colors = [], []
        for t in trades:
            pnl = t["pnl"]
            rows.append([(t["entry_time"] or "")[:16].replace("T", " "), t["symbol"], t["side"], f"{t['qty']:g}",
                         f"{t['entry_price']:.2f}" if t["entry_price"] else "",
                         f"{t['exit_price']:.2f}" if t["exit_price"] else "(open)",
                         f"{pnl:+.2f}" if pnl is not None else "", f"{t['pnl_pct']:+.2%}" if t["pnl_pct"] is not None else "",
                         (t["exit_reason"] or "") + (" (after partial)" if t["partial_taken"] and t["status"] == "closed"
                                                    else " (partial taken)" if t["partial_taken"] else ""),
                         f"{t['confidence']:.2f}" if t["confidence"] is not None else "", t["reasoning"]])
            c = pnl_color(pnl) if pnl is not None else None
            colors.append([None] * 6 + [c, c, None, None, None])
        fill_table(self.journal_table, rows, colors)

    def refresh_learning(self):
        from ..indicators import DEFAULT_WEIGHTS
        j = self.ctx.journal
        w = j.get_weights(DEFAULT_WEIGHTS)
        fill_table(self.weights_table, [[k, f"{w[k]:.2f}"] for k in SIGNALS],
                   [[None, GREEN if w[k] > 1.05 else RED if w[k] < 0.95 else None] for k in SIGNALS])
        base = self.ctx.settings["risk"]["min_confidence"]
        learned = j.get("learned_min_confidence") or 0
        self.conf_lbl.setText(f"Confidence needed to trade: {max(base, learned):.2f} (your minimum: {base:.2f})")
        n, filled = j.observation_count()
        need = self.ctx.settings["learning"]["shadow_min_observations"]
        q = j.get("signal_quality")
        text = (f"Shadow learning: {filled:,} of {n:,} scanned stocks checked against what happened next "
                f"(weights start adjusting at {need:,}).")
        if q:
            text += "\nLast measured signal quality (higher = more predictive): " + ", ".join(
                f"{k} {v:+.3f}" for k, v in q["ic"].items())
        self.shadow_lbl.setText(text)
        from ..learner import Learner
        st = self.ctx.settings
        rep = Learner(j, st["learning"], base).edge_report(st["edge_gate"], st["risk"]["min_score"], st["allow_short"])
        g = st["edge_gate"]
        ok = lambda b: f'<span style="color:{GREEN}">passed</span>' if b else \
            f'<span style="color:{RED}">not yet</span>'  # noqa: E731
        pf = "∞" if rep["paper_pf"] == float("inf") else f"{rep['paper_pf']:.2f}"
        status = ("UNLOCKED" if rep["passed"] else "LOCKED") if g["enabled"] else "lock switched OFF"
        color = GREEN if rep["passed"] else (RED if g["enabled"] else "#d29922")
        self.edge_lbl.setText(
            f"1. Paper trades (last {g['lookback_days']} days): {rep['paper_trades']}/{g['min_paper_trades']}, "
            f"profit factor {pf} (needs {g['min_profit_factor']:.2f}): {ok(rep['paper_ok'])}<br>"
            f"2. Signal edge after {g['cost_bps']} bps costs: {rep['shadow_bps']:+.1f} bps per trade over "
            f"{rep['shadow_days']} days, confidence t={rep['shadow_t']:.1f} (needs {g['min_t_stat']:.1f} "
            f"over {g['min_days']}+ days): {ok(rep['shadow_ok'])}<br>"
            f'<b>Real-money trading: <span style="color:{color}">{status}</span></b>'
            + ("" if rep["passed"] else "<br>Paper trading continues normally and builds the evidence."))
        self.lessons_list.clear()
        for l in j.active_lessons(100):
            it = QListWidgetItem(f"{l['created'][:10]}  {l['text']}")
            it.setData(Qt.UserRole, l["id"])
            self.lessons_list.addItem(it)

    def _load_saved_reports(self):
        for kind, box in (("briefing", self.briefing_box), ("review", self.review_box)):
            r = self.ctx.journal.latest_report(kind)
            box.setPlainText(r["text"] if r else f"No {kind} yet.")


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
