"""The Backtest tab: test the strategy on past market data."""
from __future__ import annotations

import threading
import traceback
from datetime import date, datetime

from PySide6.QtCharts import QChart, QChartView, QDateTimeAxis, QLineSeries, QValueAxis
from PySide6.QtCore import QDate, QDateTime, QObject, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QFrame, QPlainTextEdit, QScrollArea, QComboBox, QDateEdit, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from ..backtest import Backtester, verdict
from ..swing import SwingBacktester, setup_breakdown
from ..indicators import TECH_SIGNALS


class _Bridge(QObject):
    progress = Signal(str)
    done = Signal(object, object)   # result, error text


class BacktestTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.result = None
        self.recommended = False      # set by the Getting Started checklist
        self._rec_run = False
        self.bridge = _Bridge()
        self.bridge.progress.connect(lambda m: self.status.setText(m))
        self.bridge.done.connect(self._on_done)

        from .main_window import MUTED, StatCard, make_table  # avoid circular import at load time
        self._make_table = make_table
        s = window.ctx.settings
        # Everything scrolls, so the chart and results never get squashed on smaller screens
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        lay = QVBoxLayout(inner)
        info = QLabel(
            "Replays past market data through the bot's rules, including trading costs. Swing: insider "
            "purchases from the SEC's records (2006 onward; needs your SEC contact in Settings) on daily prices. "
            "Day trading: 5-minute prices. S&P 500 late-day momentum is a fixed, pre-registered test: pick it, "
            "test 2019 to yesterday, and it says PASS or FAIL. The AI's news judgement can't be replayed, so it "
            "isn't part of any test.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED};")
        lay.addWidget(info)

        form = QHBoxLayout()
        f1, f2 = QFormLayout(), QFormLayout()
        self.symbols = QLineEdit(", ".join(s["watchlist"]))
        self.start = QDateEdit(QDate.currentDate().addMonths(-3), calendarPopup=True)
        self.end = QDateEdit(QDate.currentDate().addDays(-1), calendarPopup=True)
        for d in (self.start, self.end):
            d.setDisplayFormat("yyyy-MM-dd")
        self.score = QDoubleSpinBox(minimum=0.1, maximum=0.9, decimals=2, singleStep=0.05,
                                    value=s["backtest"]["entry_score"])
        self.equity = QDoubleSpinBox(minimum=1000, maximum=10_000_000, decimals=0, singleStep=1000,
                                     value=s["backtest"]["starting_equity"], prefix="$ ")
        self.slip = QDoubleSpinBox(minimum=0, maximum=1, decimals=3, singleStep=0.01, suffix=" %",
                                   value=s["backtest"]["slippage_pct"] * 100)
        self.strategy = QComboBox()
        self.strategy.addItems(["Swing trading (daily bars, hold days-weeks)",
                                "Day trading: chart signals (LOST money in testing)",
                                "Day trading: confirmed gaps (LOST money in testing)",
                                "Day trading: S&P 500 late-day momentum (SPY, 1 trade a day, fixed test)"])
        self.universe = QComboBox()
        self.universe.addItems(["My stock list", "My list + every stock insiders bought (slower)"])
        self.universe.setToolTip("Insider buying happens mostly in smaller companies. Testing only your list "
                                 "misses most insider trades.")
        self.strategy.currentIndexChanged.connect(self._strategy_changed)
        self.score.setEnabled(False)
        f1.addRow("Strategy", self.strategy)
        f1.addRow("Stocks", self.symbols)
        f1.addRow("Swing universe", self.universe)
        f1.addRow("From", self.start)
        f1.addRow("To", self.end)
        f2.addRow("Entry score needed", self.score)
        f2.addRow("Starting money", self.equity)
        f2.addRow("Cost per fill (slippage)", self.slip)
        form.addLayout(f1, 2)
        form.addLayout(f2, 1)
        lay.addLayout(form)

        row = QHBoxLayout()
        self.run_btn = QPushButton("Run backtest")
        self.run_btn.setObjectName("start")
        self.run_btn.clicked.connect(self.run)
        self.apply_btn = QPushButton("Use these signal weights in the bot")
        self.apply_btn.setEnabled(False)
        self.apply_btn.setToolTip("Sets the bot's signal weights to the ones suggested by this backtest")
        self.apply_btn.clicked.connect(self.apply_weights)
        self.status = QLabel("")
        self.copy_btn = QPushButton("Copy results")
        self.copy_btn.setToolTip("Copies the numbers and the summary as text, e.g. to paste into a chat")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self.copy_results)
        row.addWidget(self.run_btn)
        row.addWidget(self.copy_btn)
        row.addWidget(self.apply_btn)
        row.addWidget(self.status, 1)
        lay.addLayout(row)

        cards = QHBoxLayout()
        self.c_ret, self.c_pf, self.c_win = StatCard("Total return"), StatCard("Profit factor"), StatCard("Win rate")
        self.c_dd, self.c_trades, self.c_bench = StatCard("Worst drop"), StatCard("Trades"), StatCard("S&P 500 same period")
        for c in (self.c_ret, self.c_pf, self.c_win, self.c_dd, self.c_trades, self.c_bench):
            cards.addWidget(c)
        lay.addLayout(cards)
        self.verdict = QPlainTextEdit()               # scrolls instead of pushing the chart off the screen
        self.verdict.setReadOnly(True)
        self.verdict.setMinimumHeight(70)
        self.verdict.setMaximumHeight(130)
        self.verdict.setStyleSheet("font-size: 10.5pt;")
        lay.addWidget(self.verdict)

        split = QSplitter(Qt.Horizontal)
        self.chart = QChart()
        self.chart.setTheme(QChart.ChartThemeDark)
        self.chart.setBackgroundBrush(QColor("#0d1117"))
        self.chart.legend().hide()
        self.chart.setTitle("Account value")
        view = QChartView(self.chart)
        view.setRenderHint(QPainter.Antialiasing)
        split.addWidget(view)
        tabs = QTabWidget()
        self.result_tabs = tabs
        self.trades_table = make_table(["Entry", "Symbol", "Side", "Shares", "Entry $", "Exit $", "P&L $", "P&L %", "Exit reason"])
        self.sig_table = make_table(["Signal", "Quality (IC)", "Suggested weight", "Meaning"])
        self.edge_table = make_table(["Signal strength", "Trade", "1st half: avg after costs", "samples",
                                      "2nd half: avg after costs", "samples", "Edge?"])
        self.edge_table.setToolTip(
            "Every 5-minute bar, grouped by how strong the combined signal was. Shows the average result of "
            "trading in the signal's direction for 60 minutes, after costs, in basis points (100 bps = 1%).\n"
            "A real edge is positive in BOTH halves of the period.")
        tabs.addTab(self.edge_table, "Edge after costs")
        tabs.addTab(self.trades_table, "Trades")
        tabs.addTab(self.sig_table, "Signal quality")
        split.addWidget(tabs)
        split.setSizes([600, 600])
        split.setMinimumHeight(340)
        lay.addWidget(split, 1)

    # ------------------------------------------------------------------
    def _strategy_changed(self, i: int):
        self.universe.setEnabled(i in (0, 2))
        self.score.setEnabled(i == 1)
        self.apply_btn.setVisible(i == 1)                 # signal weights only exist for chart signals
        self.symbols.setEnabled(i != 3)                   # the momentum test always uses SPY
        if i == 3:                                        # the fair test: development years + unseen years
            self.start.setDate(QDate(2019, 1, 2))
            self.end.setDate(QDate.currentDate().addDays(-1))
        self.universe.setItemText(1, "My list + every stock insiders bought (slower)" if i == 0 else
                                  "My list + all liquid US stocks (slower)")

    def run(self):
        eng = self.win.engine
        if not eng:
            QMessageBox.warning(self, "Not connected", "Enter your Alpaca keys on the Settings tab first "
                                                       "(they're needed to download price history).")
            return
        syms = [x.strip().upper() for x in self.symbols.text().replace(";", ",").split(",") if x.strip()]
        start, end = self.start.date().toPython(), self.end.date().toPython()
        momentum = self.strategy.currentIndex() == 3
        if momentum:
            syms = ["SPY"]
        if not syms or end <= start:
            QMessageBox.warning(self, "Check inputs", "Enter some stocks and a valid date range.")
            return
        swing = self.strategy.currentIndex() == 0
        gap = self.strategy.currentIndex() == 2
        max_days = 365 * 8 + 5 if swing or momentum else 365 * 5 if gap else 750
        if (end - start).days > max_days or len(syms) > 80:
            QMessageBox.warning(self, "Too big", f"Please test at most {max_days // 365} years and 80 stocks "
                                                 "in your list at a time.")
            return
        settings = dict(self.win.ctx.settings)
        settings["backtest"] = {**settings["backtest"], "entry_score": self.score.value(),
                                "starting_equity": self.equity.value(), "slippage_pct": self.slip.value() / 100}
        weights = eng.learner.weights()
        self.run_btn.setEnabled(False)
        self.apply_btn.setEnabled(False)
        self.status.setText("Starting...")
        self._rec_run, self.recommended = self.recommended and swing, False
        if momentum:
            threading.Thread(target=self._work_momentum, args=(eng.broker, settings, start, end), daemon=True).start()
        elif gap:
            threading.Thread(target=self._work_gap, args=(eng.broker, settings, syms, start, end,
                                                          self.universe.currentIndex() == 1), daemon=True).start()
        elif swing:
            all_insiders = self.universe.currentIndex() == 1
            threading.Thread(target=self._work_swing, args=(eng.broker, settings, syms, start, end, all_insiders),
                             daemon=True).start()
        else:
            threading.Thread(target=self._work, args=(eng.broker, settings, weights, syms, start, end),
                             daemon=True).start()

    def _work_gap(self, broker, settings, syms, start: date, end: date, all_stocks: bool):
        from ..gap import GapBacktester
        from ..gap_data import load_gap_backtest
        try:
            events, intraday, spy, notes = load_gap_backtest(broker, settings, syms, start, end, all_stocks,
                                                             self.bridge.progress.emit)
            self.bridge.progress.emit(f"Simulating {len(events)} gaps...")
            res = GapBacktester(settings).run(events, intraday, spy, start, end)
            res.notes = notes + res.notes
            self.bridge.done.emit(res, None)
        except Exception as e:
            self.bridge.done.emit(None, f"{e}\n" + traceback.format_exc(limit=2))

    def _work_momentum(self, broker, settings, start: date, end: date):
        from ..spy_momentum import SpyMomentumBacktester, load_spy_bars
        try:
            bars = load_spy_bars(broker, start, end, settings["backtest"].get("data_feed", "sip"),
                                 self.bridge.progress.emit)
            self.bridge.progress.emit("Simulating...")
            self.bridge.done.emit(SpyMomentumBacktester(settings).run(bars, start, end), None)
        except Exception as e:
            self.bridge.done.emit(None, f"{e}\n" + traceback.format_exc(limit=2))

    def _work_swing(self, broker, settings, syms, start: date, end: date, all_insiders: bool):
        from ..swing_data import load_swing_signals
        try:
            emit = self.bridge.progress.emit
            events, px, spy, notes = load_swing_signals(broker, settings, syms, start, end, all_insiders, emit)
            emit(f"Simulating {len(events)} signals...")
            res = SwingBacktester(settings).run(events, px, spy, start, end)
            res.notes += notes + [f"{len(px)} stocks with price data, {len(events)} signals."]
            for b in setup_breakdown(res):
                res.notes.append(f"{b['setup']}: {b['trades']} trades, win rate {b['win_rate']:.0%}, "
                                 f"avg {b['avg_pct']:+.2%}, profit factor {b['profit_factor']:.2f}.")
            self.bridge.done.emit(res, None)
        except Exception as e:
            self.bridge.done.emit(None, f"{e}\n" + traceback.format_exc(limit=2))

    def _work(self, broker, settings, weights, syms, start: date, end: date):
        try:
            bt = Backtester(settings, weights)
            data = bt.load(broker, syms, start, end, progress=self.bridge.progress.emit)
            res = bt.run(data, start, end, syms, progress=self.bridge.progress.emit)
            self.bridge.done.emit(res, None)
        except Exception as e:
            msg = str(e)
            if "subscription" in msg.lower() or "sip" in msg.lower():
                msg += "\n\nTip: your data plan may not include full-market (SIP) history for this range."
            self.bridge.done.emit(None, msg + "\n" + traceback.format_exc(limit=2))

    def _on_done(self, res, err):
        from .main_window import GREEN, MUTED, RED, fill_table, money, pnl_color
        self.run_btn.setEnabled(True)
        if err:
            self.status.setText("Backtest failed.")
            QMessageBox.warning(self, "Backtest failed", err)
            return
        self.result = res
        self.copy_btn.setEnabled(True)
        s = res.stats
        if res.trades and res.trades[0].get("setup") in ("insider_buy", "news_gap"):   # swing: remember results
            from ..health import expectation_from_backtest
            exp = expectation_from_backtest(res, self.start.date().toPython(), self.end.date().toPython())
            if exp:
                self.win.ctx.journal.set("swing_expectation", exp)
        if self._rec_run and s:
            pf = s["profit_factor"]
            self.win.ctx.journal.set("recommended_backtest_done", {
                "trades": s["trades"], "ret": s["total_return"], "spy": res.benchmark_return,
                "pf": pf if pf != float("inf") else 99.0,
                "halves": " / ".join(f"PF {h['profit_factor']:.2f}" for h in res.halves)})
            self.win.getting_started.refresh()
        self.result_tabs.setCurrentIndex(0 if res.edge_table else 1)   # swing results have no edge table
        gap_result = bool(res.trades) and res.trades[0].get("setup") == "gap_confirmed" or \
            any("gap up" in r.get("band", "") or "gap down" in r.get("band", "") for r in res.edge_table)
        momentum_result = bool(res.trades) and res.trades[0].get("setup") == "spy_momentum"
        self.edge_table.horizontalHeaderItem(0).setText(
            "Rule" if momentum_result else "What happened in the first 30 min" if gap_result else "Signal strength")
        self.edge_table.horizontalHeaderItem(2).setText("Before 2024: avg after costs" if momentum_result
                                                        else "1st half: avg after costs")
        self.edge_table.horizontalHeaderItem(4).setText("2024 onward: avg after costs" if momentum_result
                                                        else "2nd half: avg after costs")
        self.status.setText("Done.")
        if res.notes:
            self.status.setToolTip("\n".join(res.notes))
        head = (res.headline + "\n") if res.headline else ""
        if not s:
            self.verdict.setPlainText(head + verdict(res))
            return
        pf = s["profit_factor"]
        self.c_ret.set(f"{s['total_return']:+.1%}", pnl_color(s["total_return"]))
        self.c_pf.set("∞" if pf == float("inf") else f"{pf:.2f}", GREEN if pf >= 1.3 else RED if pf < 1 else None)
        self.c_win.set(f"{s['win_rate']:.0%}")
        dd = s["max_drawdown"] if s["max_drawdown"] < -0.0005 else 0.0
        self.c_dd.set(f"{dd:.1%}", RED if dd < -0.1 else None)
        self.c_trades.set(str(s["trades"]))
        self.c_bench.set(f"{res.benchmark_return:+.1%}", pnl_color(res.benchmark_return))
        reasons = ", ".join(f"{k}: {v}" for k, v in s.get("exit_reasons", {}).items())
        halves = "   |   ".join(
            f"{h['name'].capitalize()} (from {h['from']}): {h['trades']} trades, P&L {money(h['pnl'])}, "
            f"profit factor {'∞' if h['profit_factor'] == float('inf') else format(h['profit_factor'], '.2f')}"
            for h in res.halves)
        self.verdict.setPlainText(head + verdict(res) + (f"\n{halves}" if halves else "") +
                             (f"\nHow trades ended: {reasons}" if reasons else "") +
                             ("\n" + " ".join(res.notes) if res.notes else ""))
        rows, colors = [], []
        for e in res.edge_table:
            rows.append([e["band"], e["side"], f"{e['bps1']:+.1f} bps", f"{e['n1']:,}", f"{e['bps2']:+.1f} bps",
                         f"{e['n2']:,}", "YES" if e["consistent"] else "no"])
            colors.append([None, None, pnl_color(e["bps1"]), None, pnl_color(e["bps2"]), None,
                           GREEN if e["consistent"] else MUTED])
        fill_table(self.edge_table, rows, colors)

        # Equity chart
        self.chart.removeAllSeries()
        for ax in self.chart.axes():
            self.chart.removeAxis(ax)
        series = QLineSeries()
        series.setPen(QPen(QColor(GREEN if s["total_return"] >= 0 else RED), 2))
        for ts, eq in res.equity_curve:
            series.append(QDateTime(ts.replace(tzinfo=None) if isinstance(ts, datetime) else ts.to_pydatetime()
                                    .replace(tzinfo=None)).toMSecsSinceEpoch(), eq)
        self.chart.addSeries(series)
        xa, ya = QDateTimeAxis(), QValueAxis()
        xa.setFormat("MMM yyyy")
        xa.setTickCount(5)
        ya.setLabelFormat("$%.0f")
        self.chart.addAxis(xa, Qt.AlignBottom)
        self.chart.addAxis(ya, Qt.AlignLeft)
        series.attachAxis(xa)
        series.attachAxis(ya)

        rows, colors = [], []
        for t in sorted(res.trades, key=lambda x: x["entry_time"], reverse=True)[:2000]:
            et = t["entry_time"]
            rows.append([f"{et:%Y-%m-%d}" if (et.hour, et.minute) == (0, 0) else f"{et:%Y-%m-%d %H:%M}", t["symbol"], t["side"], t["qty"], f"{t['entry']:.2f}",
                         f"{t['exit']:.2f}", money(t["pnl"]), f"{t['pnl_pct']:+.2%}",
                         t["exit_reason"] + (" (after partial)" if t["partial"] and t["exit_reason"] != "partial" else "")])
            c = pnl_color(t["pnl"])
            colors.append([None] * 6 + [c, c, None])
        fill_table(self.trades_table, rows, colors)

        meaning = lambda v: ("useful" if v >= 0.03 else "weak" if v >= 0.015 else  # noqa: E731
                             "no edge (noise)" if v > -0.015 else "works BACKWARDS here")
        rows = [[k, f"{res.signal_quality.get(k, 0):+.3f}", f"{res.suggested_weights.get(k, 1):.2f}",
                 meaning(res.signal_quality.get(k, 0))] for k in TECH_SIGNALS]
        fill_table(self.sig_table, rows, [[None, pnl_color(res.signal_quality.get(k, 0)), None,
                                           MUTED] for k in TECH_SIGNALS])
        self.apply_btn.setEnabled(bool(res.suggested_weights))

    def copy_results(self):
        cards = [("Total return", self.c_ret), ("Profit factor", self.c_pf), ("Win rate", self.c_win),
                 ("Worst drop", self.c_dd), ("Trades", self.c_trades), ("S&P 500 same period", self.c_bench)]
        text = (f"Eimeria backtest: {self.strategy.currentText()}, {self.start.date().toString('yyyy-MM-dd')} to "
                f"{self.end.date().toString('yyyy-MM-dd')}, {self.universe.currentText()}\n" +
                "\n".join(f"{name}: {card.value.text()}" for name, card in cards) +
                "\n\n" + self.verdict.toPlainText())
        QApplication.clipboard().setText(text)
        self.status.setText("Results copied: paste them anywhere with Ctrl+V.")

    def apply_weights(self):
        if not self.result or not self.win.engine:
            return
        if QMessageBox.question(self, "Use these weights?",
                                "Set the bot's signal weights to the ones suggested by this backtest?\n\n"
                                "Tip: only do this if the backtest covered several months and many stocks.") \
                != QMessageBox.Yes:
            return
        self.win.engine.learner.apply_weights(self.result.suggested_weights,
                                              note=f"from backtest {self.start.date().toString('yyyy-MM-dd')} to "
                                                   f"{self.end.date().toString('yyyy-MM-dd')}")
        self.win.refresh_learning()
        self.win.log("Applied signal weights from the backtest.")
