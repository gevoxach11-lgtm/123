"""Central configuration for the AdjaraPoker Bot.

All runtime settings are loaded from the environment (`.env`) via
``python-dotenv``. CSS selectors, timing parameters and session limits are
defined here so that the rest of the codebase has a single source of truth.

The selectors are intentionally placeholders - the real Adjarabet poker client
markup must be inspected and these values updated before live play.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Load variables from a local .env file if present.
load_dotenv(BASE_DIR / ".env")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _get_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable in a forgiving way."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None else default


# --------------------------------------------------------------------------- #
# Credentials & environment-driven settings
# --------------------------------------------------------------------------- #
ADJARABET_USERNAME: str = _get_str("ADJARABET_USERNAME")
ADJARABET_PASSWORD: str = _get_str("ADJARABET_PASSWORD")
# Short aliases (used by the dashboard UI).
USERNAME: str = ADJARABET_USERNAME
PASSWORD: str = ADJARABET_PASSWORD
TABLE_LIMIT: str = _get_str("TABLE_LIMIT", "NL10")
PROXY_SERVER: str = _get_str("PROXY_SERVER")
HEADLESS: bool = _get_bool("HEADLESS", False)
AUTO_PLAY: bool = _get_bool("AUTO_PLAY", False)
DEBUG: bool = _get_bool("DEBUG", False)

# Currency symbol used by the client (Georgian lari by default).
CURRENCY_SYMBOL: str = _get_str("CURRENCY_SYMBOL", "\u20be")

# Base site URLs (override via .env if Adjarabet changes domains).
BASE_URL: str = _get_str("ADJARABET_BASE_URL", "https://www.adjarabet.am")
POKER_URL: str = _get_str("ADJARABET_POKER_URL", "https://www.adjarabet.am/en/poker")


# --------------------------------------------------------------------------- #
# CSS / DOM selectors (PLACEHOLDERS - update after inspecting the real client)
# --------------------------------------------------------------------------- #
SELECTORS: dict[str, str] = {
    # --- Authentication ---
    "login_button": "button[data-testid='login']",
    "username_input": "input[name='username']",
    "password_input": "input[name='password']",
    "submit_login": "button[type='submit']",
    "logged_in_marker": ".user-balance",

    # --- Lobby ---
    "poker_menu": "a[href*='poker']",
    "cash_games_tab": "button[data-tab='cash']",
    "table_limit_filter": ".lobby-filter-limit",
    "table_row": ".lobby-table-row",
    "table_name": ".lobby-table-name",
    "table_stakes": ".lobby-table-stakes",
    "table_players": ".lobby-table-players",
    "join_table_button": ".lobby-table-join",
    "take_seat_button": ".seat.available",

    # --- Table / game state ---
    "table_container": ".poker-table",
    "hand_id": ".hand-id",
    "pot_size": ".pot-amount",
    "community_card": ".board .card",
    "my_hole_card": ".hero .card",
    "my_stack": ".hero .stack",
    "my_bet": ".hero .bet",
    "seat": ".seat",
    "seat_name": ".seat .player-name",
    "seat_stack": ".seat .stack",
    "seat_bet": ".seat .bet",
    "seat_active": ".seat.active",
    "dealer_button": ".dealer-button",
    "turn_timer": ".hero .timer",
    "betting_round": ".betting-round",

    # --- Action buttons ---
    "fold_button": "button[data-action='fold']",
    "check_button": "button[data-action='check']",
    "call_button": "button[data-action='call']",
    "bet_button": "button[data-action='bet']",
    "raise_button": "button[data-action='raise']",
    "bet_amount_input": "input.bet-amount",
    "bet_confirm_button": "button[data-action='confirm']",
    "bet_slider": "input.bet-slider",
}


# --------------------------------------------------------------------------- #
# Timing (seconds)
# --------------------------------------------------------------------------- #
TIMING: dict[str, float] = {
    "MIN_DELAY": 0.8,       # minimum human-like delay before an action
    "MAX_DELAY": 2.5,       # maximum human-like delay before an action
    "POLL_INTERVAL": 0.4,   # how often the scraper polls the table DOM
    "PAGE_TIMEOUT": 30.0,   # default Playwright navigation/selector timeout
    "ACTION_TIMEOUT": 10.0, # timeout when waiting for an action button
}


# --------------------------------------------------------------------------- #
# Session limits / safety guards
# --------------------------------------------------------------------------- #
SESSION: dict[str, int] = {
    "MAX_HANDS": 500,    # stop after this many hands
    "MAX_MINUTES": 90,   # stop after this many minutes
    "STOP_LOSS": 200,    # stop if losses (in big blinds) exceed this
}


# --------------------------------------------------------------------------- #
# Big-blind sizing per limit (used for stop-loss / bb-per-100 math)
# --------------------------------------------------------------------------- #
LIMIT_BIG_BLINDS: dict[str, float] = {
    "NL2": 0.02,
    "NL5": 0.05,
    "NL10": 0.10,
    "NL25": 0.25,
    "NL50": 0.50,
    "NL100": 1.00,
    "NL200": 2.00,
}


def big_blind_for(limit: str | None = None) -> float:
    """Return the big-blind size in currency units for a given limit string."""
    key = (limit or TABLE_LIMIT).upper()
    return LIMIT_BIG_BLINDS.get(key, 0.10)


def as_dict() -> dict:
    """Return a JSON-serialisable snapshot of the active configuration.

    Credentials are masked so this can be safely displayed in the dashboard.
    """
    return {
        "ADJARABET_USERNAME": ADJARABET_USERNAME or "(unset)",
        "ADJARABET_PASSWORD": "***" if ADJARABET_PASSWORD else "(unset)",
        "TABLE_LIMIT": TABLE_LIMIT,
        "PROXY_SERVER": PROXY_SERVER or "(none)",
        "HEADLESS": HEADLESS,
        "AUTO_PLAY": AUTO_PLAY,
        "BASE_URL": BASE_URL,
        "POKER_URL": POKER_URL,
        "TIMING": TIMING,
        "SESSION": SESSION,
        "BIG_BLIND": big_blind_for(),
    }
