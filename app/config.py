"""Settings loading/saving.

User-editable settings live in settings.yaml (next to the app).
Secret API keys live in .env (never shared, never committed).
Anything missing from settings.yaml falls back to DEFAULTS below.
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv, set_key

# Folder that holds settings.yaml, .env, the database and logs.
if os.environ.get("EIMERIA_HOME"):  # used by the build's smoke test (keeps test data out of the build)
    BASE_DIR = Path(os.environ["EIMERIA_HOME"]).resolve()
elif getattr(sys, "frozen", False):  # running as a packaged .exe
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

SETTINGS_PATH = BASE_DIR / "settings.yaml"
ENV_PATH = BASE_DIR / ".env"
DB_PATH = BASE_DIR / "trader.db"
LOG_DIR = BASE_DIR / "logs"

SETTINGS_PROBLEM: list[str] = []          # filled if settings.yaml was damaged (shown at startup)

DEFAULTS: dict = {
    # "paper" = fake money (safe). "live" = REAL money. Start with paper!
    "mode": "paper",
    # Stocks the bot always looks at. Pre-market news and top movers add more each day.
    "watchlist": ["AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "GOOGL", "SPY", "QQQ"],
    "add_top_movers": True,       # also scan the day's biggest movers / most active stocks
    "max_symbols_per_scan": 25,
    "scan_interval_minutes": 5,
    "allow_short": False,         # shorting = betting on a drop. Riskier; off by default.
    "schedule": {                 # all times are New York (market) time
        "premarket_briefing": "08:45",
        "post_close_review": "16:15",
        "no_new_trades_first_minutes": 5,
        "no_new_trades_last_minutes": 30,
        "close_all_minutes_before_close": 10,
    },
    "ai": {
        "ollama_url": "http://localhost:11434",
        "model": "llama3.1:8b",
        "temperature": 0.2,
        "timeout_seconds": 120,
        "require_ai_approval": True,  # if the AI is offline, take no new trades
    },
    "risk": {
        "max_position_pct": 0.10,     # max 10% of account in one stock
        "risk_per_trade_pct": 0.005,  # lose at most 0.5% of account if stop-loss hits
        "stop_loss_pct": 0.015,       # sell if price drops 1.5% below entry
        "reward_risk_ratio": 2.0,     # take profit at 2x the stop distance (3%)
        "max_open_positions": 5,
        "max_trades_per_day": 10,
        "max_daily_loss_pct": 0.02,   # stop trading for the day after -2%
        "min_confidence": 0.65,       # AI confidence needed to trade (0-1)
        "min_score": 0.25,            # signal score needed before asking the AI
        "min_price": 5.0,             # skip penny stocks
        "pdt_protection": True,       # respect pattern-day-trader limits on small accounts
        "max_spread_pct": 0.002,      # skip stocks whose bid/ask gap is wider than 0.2%
        "entry_limit_buffer_pct": 0.0005,  # pay at most 0.05% above the ask (limit order, not market)
        "entry_timeout_seconds": 120, # cancel an entry that hasn't filled after 2 minutes
    },
    "news": {
        "rss_feeds": [
            "https://feeds.content.dowjones.io/public/rss/mw_topstories",
            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        ],
        "lookback_hours": 18,
        "max_headlines_per_symbol": 8,
    },
    "smart_money": {
        "enabled": True,
        # The SEC requires a name + email in requests. Put YOUR OWN here, e.g. "Jane Doe jane@example.com"
        "sec_user_agent": "",
        "min_purchase_usd": 100000,
        "lookback_days": 14,
        "refresh_minutes": 60,
    },
    "exits": {
        "partial_enabled": True,
        "partial_at_r": 1.0,          # when up 1x the stop distance...
        "partial_fraction": 0.5,      # ...sell half and move the stop to break-even
        "trailing_enabled": True,
        "trail_atr_mult": 1.5,        # then trail the stop 1.5 x ATR behind the best price
        "time_stop_minutes": 90,      # close trades that go nowhere after 90 minutes...
        "time_stop_min_r": 0.3,       # ...if they're up less than 0.3x the stop distance
    },
    "market_filter": {
        "enabled": True,              # be pickier (or stop buying) when the whole market is falling
    },
    "data": {
        # "iex" = free (one exchange). "sip" = all exchanges, needs Alpaca's paid market data plan.
        "live_feed": "iex",
    },
    "alerts": {
        "desktop": True,              # Windows pop-up notifications
        "ntfy_topic": "",             # phone alerts via the free ntfy app; pick a long random name
        "ntfy_server": "https://ntfy.sh",
        "on_trade": True,
        "on_problem": True,
        "on_report": True,
    },
    "backtest": {
        "starting_equity": 10000,
        "entry_score": 0.40,          # score needed to enter (stands in for AI approval)
        "slippage_pct": 0.0005,       # 0.05% worse price on every fill
        "data_feed": "sip",           # free for historical data older than 15 minutes
    },
    # Intraday scans + trades (closed before 4 PM). Off by default: on real data the
    # day-trading signals did not beat trading costs. Switch on in Settings > Strategies.
    "day_trading": {"enabled": False},
    # Swing trading: hold for days/weeks. Entries at the next open after a signal, a stop-loss
    # at 4 x the daily ATR, and an exit after `hold_days` trading days.
    # Defaults come from testing 2019-2026 SEC insider data: purchases of $1M+ by officers/directors
    # in stocks above their 50-day average beat small-cap stocks in both 2019-22 and 2023-26.
    # The news-gap setup did NOT beat the market in that test, so it's off by default.
    "swing": {
        "enabled": True,
        "setups": {"insider_buy": True, "news_gap": False},
        "hold_days": 40,
        "stop_atr_mult": 4.0,
        "risk_per_trade_pct": 0.005,      # lose at most 0.5% of the account if the stop hits
        "max_position_pct": 0.10,
        "max_positions": 8,
        "min_price": 5.0,
        "min_dollar_volume": 5_000_000,   # average $ traded per day (avoid illiquid stocks)
        "require_uptrend": True,          # only buy stocks above their 50-day average
        "entry_time": "09:45",            # New York time; after the opening rush
        "exit_time": "15:45",
        "use_ai_review": True,            # the AI reads the news and can veto a candidate
        "plan_time": "16:20",             # build tomorrow's plan after the close
        # Keep money that isn't in swing trades in an S&P 500 fund instead of cash. In 2019-2026 tests this
        # beat holding the index alone, but the account then rises AND FALLS with the market (-35% in 2020).
        "idle_cash_in_index": False,
        "index_symbol": "SPY",
        "index_cash_buffer_pct": 0.02,    # keep 2% as cash
        "index_time": "15:50",            # invest spare cash after the day's planned exits
        # No new swing buys while the S&P 500 is below its 200-day average. Tested: helped in 2019-22,
        # slightly hurt in 2023-26, so it's optional.
        "market_filter": False,
        "trail_atr_mult": 0,              # backtest-only option; a trailing exit did worse in testing
        # Crash protection (10-month trend rule on the S&P 500): "off", "step_aside" or "short".
        # When the S&P 500 closes a month below its 10-month average: no new swing buys, and the
        # index fund is sold to cash ("step_aside") or switched to an inverse fund ("short").
        "crash_protection": "off",
        "inverse_symbol": "SH",           # -1x S&P 500 fund (no leverage, can't lose more than invested)
        "news_gap": {"min_gap": 0.04, "min_volume_ratio": 3.0, "min_close_position": 0.0},
        "insider": {"min_value": 1_000_000, "min_insiders": 1, "require_ceo_cfo": False},
    },
    # Updates are published to this GitHub repository (update.json + version tags).
    "updates": {"repo": "catsandlights-ops/Eimeria", "branch": "claude/releases", "check_on_start": True},
    # Real-money trading stays locked until the bot has shown an edge after costs:
    # enough profitable paper trades AND a statistically meaningful edge in recent scans.
    "edge_gate": {
        "enabled": True,
        "min_paper_trades": 50,
        "min_profit_factor": 1.2,
        "min_days": 10,               # days of shadow observations needed
        "min_t_stat": 2.0,            # how sure we need to be the edge isn't luck (2 = ~95%)
        "lookback_days": 30,
        "cost_bps": 10,               # assumed round-trip trading cost (0.10%)
        "swing_min_paper_trades": 20, # swing trades are slower: judged on closed paper swing trades
        "swing_lookback_days": 180,
    },
    "learning": {
        "enabled": True,
        "min_trades_before_adjusting": 20,
        "learning_rate": 0.05,
        "weight_min": 0.2,
        "weight_max": 2.0,
        "max_lessons_in_prompt": 8,
        # Shadow learning: every scanned stock is recorded and checked 30/60 minutes later,
        # so the bot learns from thousands of observations, not just the trades it took.
        "shadow_enabled": True,
        "shadow_min_observations": 500,
        "shadow_lookback_days": 30,
        "shadow_learning_rate": 0.2,
    },
}


OLD_FOLDER_NAMES = ["ai-trader"]          # the app was called "AI Day Trader" before it became Eimeria
CARRY_OVER = ["settings.yaml", ".env", "trader.db", "cache"]


def migrate_from_old_folder() -> list[str]:
    """First start of Eimeria: copy settings, keys, trade history and caches from the old
    'ai-trader' folder next to this one, if found. The old folder is left untouched."""
    import shutil
    if any((BASE_DIR / name).exists() for name in ("settings.yaml", ".env", "trader.db")):
        return []
    for old_name in OLD_FOLDER_NAMES:
        old = BASE_DIR.parent / old_name
        if old.resolve() == BASE_DIR.resolve() or not old.is_dir():
            continue
        copied = []
        for name in CARRY_OVER:
            src = old / name
            if src.is_dir():
                shutil.copytree(src, BASE_DIR / name, dirs_exist_ok=True)
                copied.append(name)
            elif src.exists():
                shutil.copy2(src, BASE_DIR / name)
                copied.append(name)
        if copied:
            return [str(old / n) for n in copied]
    return []


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict:
    """Read settings.yaml merged over defaults. Creates the file on first run."""
    user = {}
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                user = yaml.safe_load(f) or {}
            if not isinstance(user, dict):
                raise ValueError("settings.yaml doesn't contain settings")
        except Exception as e:
            # A damaged settings file must not stop the app: keep it aside and start from defaults
            from datetime import datetime
            broken = SETTINGS_PATH.with_name(f"settings.broken-{datetime.now():%Y%m%d-%H%M%S}.yaml")
            SETTINGS_PATH.rename(broken)
            SETTINGS_PROBLEM.append(f"settings.yaml couldn't be read ({e}). It was renamed to {broken.name} and "
                                    f"default settings are being used. Your keys (.env) and trades are unaffected.")
            user = {}
    settings = _deep_merge(DEFAULTS, user)
    if not SETTINGS_PATH.exists():
        save_settings(settings)
    return settings


def save_settings(settings: dict) -> None:
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(settings, f, sort_keys=False, allow_unicode=True)


def load_keys() -> tuple[str, str]:
    load_dotenv(ENV_PATH, override=True)
    return os.getenv("ALPACA_API_KEY", "").strip(), os.getenv("ALPACA_SECRET_KEY", "").strip()


def save_keys(api_key: str, secret_key: str) -> None:
    ENV_PATH.touch(exist_ok=True)
    set_key(str(ENV_PATH), "ALPACA_API_KEY", api_key.strip())
    set_key(str(ENV_PATH), "ALPACA_SECRET_KEY", secret_key.strip())
