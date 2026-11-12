"""SQLite trade journal: every trade, its reasons, results, AI reports, lessons and learned weights."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,              -- 'long' or 'short'
    qty REAL NOT NULL,
    entry_price REAL,
    entry_time TEXT,
    stop_price REAL,
    target_price REAL,
    exit_price REAL,
    exit_time TEXT,
    pnl REAL,
    pnl_pct REAL,
    status TEXT NOT NULL DEFAULT 'open',   -- open / closed
    scores_json TEXT,
    confidence REAL,
    reasoning TEXT,
    order_id TEXT,
    learned INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    kind TEXT NOT NULL,              -- 'briefing' or 'review'
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    text TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS weights (
    signal TEXT PRIMARY KEY,
    weight REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS weight_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    weights_json TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS insider_buys (
    accession TEXT PRIMARY KEY,
    symbol TEXT,
    owner TEXT,
    title TEXT,
    trade_date TEXT,
    shares REAL,
    price REAL,
    value REAL,
    filed TEXT
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,                -- UTC time of the scan
    symbol TEXT NOT NULL,
    price REAL NOT NULL,
    scores_json TEXT NOT NULL,
    score REAL,
    fwd30 REAL,                      -- % move 30 minutes later
    fwd60 REAL,                      -- % move 60 minutes later
    filled INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS obs_filled ON observations (filled, ts);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Journal:
    def __init__(self, path: Path | str):
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.commit()

    def _migrate(self):
        """Add columns introduced in later versions to older databases."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(trades)")}
        for name, typ in [("partial_taken", "INTEGER NOT NULL DEFAULT 0"), ("current_stop", "REAL"),
                          ("best_price", "REAL"), ("initial_qty", "REAL"), ("exit_reason", "TEXT"),
                          ("mode", "TEXT NOT NULL DEFAULT 'paper'"),
                          ("style", "TEXT NOT NULL DEFAULT 'day'"),        # 'day' or 'swing'
                          ("hold_days", "INTEGER"), ("setup", "TEXT")]:
            if name not in cols:
                self.conn.execute(f"ALTER TABLE trades ADD COLUMN {name} {typ}")

    def _exec(self, sql: str, params=()):
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def _all(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # ---- trades ----
    def open_trade(self, symbol, side, qty, entry_price, stop_price, target_price,
                   scores: dict, confidence: float, reasoning: str, order_id: str, mode: str = "paper",
                   style: str = "day", hold_days: int | None = None, setup: str | None = None) -> int:
        cur = self._exec(
            "INSERT INTO trades (symbol, side, qty, entry_price, entry_time, stop_price, target_price,"
            " scores_json, confidence, reasoning, order_id, initial_qty, current_stop, best_price, mode,"
            " style, hold_days, setup) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, side, qty, entry_price, now_iso(), stop_price, target_price,
             json.dumps(scores), confidence, reasoning, order_id, qty, stop_price, entry_price, mode,
             style, hold_days, setup),
        )
        return cur.lastrowid

    def update_trade(self, trade_id: int, **fields):
        allowed = {"qty", "current_stop", "best_price", "partial_taken", "exit_reason", "entry_price"}
        for k, v in fields.items():
            if k in allowed:
                self._exec(f"UPDATE trades SET {k}=? WHERE id=?", (v, trade_id))

    def update_entry_price(self, trade_id: int, price: float):
        self._exec("UPDATE trades SET entry_price=? WHERE id=?", (price, trade_id))

    def close_trade(self, trade_id: int, exit_price: float):
        t = self._all("SELECT * FROM trades WHERE id=?", (trade_id,))[0]
        direction = 1 if t["side"] == "long" else -1
        entry = t["entry_price"] or exit_price
        qty = t.get("initial_qty") or t["qty"]
        pnl = (exit_price - entry) * qty * direction
        pnl_pct = (exit_price - entry) / entry * direction if entry else 0.0
        self._exec(
            "UPDATE trades SET exit_price=?, exit_time=?, pnl=?, pnl_pct=?, status='closed', qty=? WHERE id=?",
            (exit_price, now_iso(), pnl, pnl_pct, qty, trade_id),
        )

    def delete_trade(self, trade_id: int):
        self._exec("DELETE FROM trades WHERE id=?", (trade_id,))

    def open_trades(self) -> list[dict]:
        return self._all("SELECT * FROM trades WHERE status='open' ORDER BY id")

    def closed_trades(self, limit: int = 500) -> list[dict]:
        return self._all("SELECT * FROM trades WHERE status='closed' ORDER BY id DESC LIMIT ?", (limit,))

    def closed_trades_since(self, since_iso: str, mode: str | None = None, style: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM trades WHERE status='closed' AND exit_time >= ?", [since_iso]
        if mode:
            sql, params = sql + " AND mode=?", params + [mode]
        if style:
            sql, params = sql + " AND style=?", params + [style]
        return self._all(sql + " ORDER BY id", tuple(params))

    def unlearned_trades(self) -> list[dict]:
        return self._all("SELECT * FROM trades WHERE status='closed' AND learned=0 ORDER BY id")

    def mark_learned(self, ids: list[int]):
        for i in ids:
            self._exec("UPDATE trades SET learned=1 WHERE id=?", (i,))

    def trades_today_count(self, day_prefix: str) -> int:
        """day_prefix is a UTC 'YYYY-MM-DD' string; counts entries made that day."""
        rows = self._all("SELECT COUNT(*) AS n FROM trades WHERE entry_time LIKE ?", (day_prefix + "%",))
        return rows[0]["n"]

    def stats(self) -> dict:
        rows = self._all("SELECT pnl, pnl_pct FROM trades WHERE status='closed'")
        n = len(rows)
        if not n:
            return {"trades": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl_pct": 0.0, "profit_factor": 0.0}
        wins = [r["pnl"] for r in rows if r["pnl"] > 0]
        losses = [-r["pnl"] for r in rows if r["pnl"] <= 0]
        return {
            "trades": n,
            "win_rate": len(wins) / n,
            "total_pnl": sum(r["pnl"] for r in rows),
            "avg_pnl_pct": sum(r["pnl_pct"] for r in rows) / n,
            "profit_factor": (sum(wins) / sum(losses)) if sum(losses) > 0 else float("inf") if wins else 0.0,
        }

    # ---- reports & lessons ----
    def add_report(self, kind: str, text: str):
        self._exec("INSERT INTO reports (created, kind, text) VALUES (?,?,?)", (now_iso(), kind, text))

    def latest_report(self, kind: str) -> dict | None:
        rows = self._all("SELECT * FROM reports WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,))
        return rows[0] if rows else None

    def add_lesson(self, text: str):
        self._exec("INSERT INTO lessons (created, text) VALUES (?,?)", (now_iso(), text))

    def active_lessons(self, limit: int) -> list[dict]:
        return self._all("SELECT * FROM lessons WHERE active=1 ORDER BY id DESC LIMIT ?", (limit,))

    def deactivate_lesson(self, lesson_id: int):
        self._exec("UPDATE lessons SET active=0 WHERE id=?", (lesson_id,))

    # ---- learned weights ----
    def get_weights(self, defaults: dict[str, float]) -> dict[str, float]:
        stored = {r["signal"]: r["weight"] for r in self._all("SELECT * FROM weights")}
        return {k: stored.get(k, v) for k, v in defaults.items()}

    def set_weights(self, weights: dict[str, float], note: str = ""):
        for k, v in weights.items():
            self._exec("INSERT OR REPLACE INTO weights (signal, weight) VALUES (?,?)", (k, float(v)))
        self._exec("INSERT INTO weight_history (created, weights_json, note) VALUES (?,?,?)",
                   (now_iso(), json.dumps(weights), note))

    # ---- insider buys ----
    def has_filing(self, accession: str) -> bool:
        return bool(self._all("SELECT 1 FROM insider_buys WHERE accession=?", (accession,)))

    def add_insider_buy(self, row: dict):
        self._exec(
            "INSERT OR IGNORE INTO insider_buys (accession, symbol, owner, title, trade_date, shares, price, value, filed)"
            " VALUES (:accession,:symbol,:owner,:title,:trade_date,:shares,:price,:value,:filed)", row)

    def recent_insider_buys(self, since_date: str) -> list[dict]:
        return self._all("SELECT * FROM insider_buys WHERE trade_date >= ? AND value > 0 ORDER BY value DESC",
                         (since_date,))

    # ---- shadow observations ----
    def add_observations(self, rows: list[dict]):
        with self._lock:
            self.conn.executemany(
                "INSERT INTO observations (ts, symbol, price, scores_json, score) VALUES (?,?,?,?,?)",
                [(now_iso(), r["symbol"], r["price"], json.dumps(r["scores"]), r["score"]) for r in rows])
            self.conn.commit()

    def pending_observations(self, older_than_iso: str) -> list[dict]:
        return self._all("SELECT * FROM observations WHERE filled=0 AND ts <= ? ORDER BY ts", (older_than_iso,))

    def fill_observation(self, obs_id: int, fwd30: float | None, fwd60: float | None):
        self._exec("UPDATE observations SET fwd30=?, fwd60=?, filled=1 WHERE id=?", (fwd30, fwd60, obs_id))

    def learning_observations(self, since_iso: str) -> list[dict]:
        return self._all("SELECT ts, score, scores_json, fwd30, fwd60 FROM observations WHERE filled=1"
                         " AND fwd60 IS NOT NULL AND ts >= ?", (since_iso,))

    def observation_count(self) -> tuple[int, int]:
        r = self._all("SELECT COUNT(*) AS n, SUM(filled) AS f FROM observations")[0]
        return r["n"] or 0, r["f"] or 0

    # ---- key/value ----
    def get(self, key: str, default=None):
        rows = self._all("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set(self, key: str, value):
        self._exec("INSERT OR REPLACE INTO kv (key, value) VALUES (?,?)", (key, json.dumps(value)))
