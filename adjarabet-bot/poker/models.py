"""Core data models for the AdjaraPoker Bot.

These dataclasses describe cards, players, the live game state, bot actions and
session statistics. They are deliberately framework-agnostic so they can be
shared between the scraper, engine, executor and dashboard.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

# --------------------------------------------------------------------------- #
# Card primitives
# --------------------------------------------------------------------------- #
RANKS = "23456789TJQKA"
SUITS = "cdhs"  # clubs, diamonds, hearts, spades

RANK_VALUES = {rank: index + 2 for index, rank in enumerate(RANKS)}  # 2..14

SUIT_SYMBOLS = {"c": "\u2663", "d": "\u2666", "h": "\u2665", "s": "\u2660"}


class Street(str, Enum):
    """Betting rounds of a Texas Hold'em hand."""

    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"


class ActionType(str, Enum):
    """All actions the bot can take at the table."""

    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"


class Position(str, Enum):
    """Standard table positions (6-max friendly, extendable to 9-max)."""

    SB = "SB"
    BB = "BB"
    UTG = "UTG"
    UTG1 = "UTG+1"
    MP = "MP"
    LJ = "LJ"
    HJ = "HJ"
    CO = "CO"
    BTN = "BTN"
    UNKNOWN = "?"


@dataclass(frozen=True)
class Card:
    """A single playing card.

    ``rank`` is one of ``RANKS`` (``2``-``9``, ``T``, ``J``, ``Q``, ``K``,
    ``A``) and ``suit`` is one of ``SUITS`` (``c``, ``d``, ``h``, ``s``).
    """

    rank: str
    suit: str

    def __post_init__(self) -> None:
        if self.rank not in RANKS:
            raise ValueError(f"Invalid card rank: {self.rank!r}")
        if self.suit not in SUITS:
            raise ValueError(f"Invalid card suit: {self.suit!r}")

    @property
    def value(self) -> int:
        """Numeric rank value (2-14)."""
        return RANK_VALUES[self.rank]

    @classmethod
    def from_str(cls, text: str) -> "Card":
        """Build a Card from a 2-character string such as ``'Ah'`` or ``'Tc'``.

        Suit is matched case-insensitively; rank uppercased.
        """
        cleaned = text.strip().replace("10", "T")
        if len(cleaned) != 2:
            raise ValueError(f"Cannot parse card from {text!r}")
        rank = cleaned[0].upper()
        suit = cleaned[1].lower()
        return cls(rank=rank, suit=suit)

    def __str__(self) -> str:  # e.g. "A\u2665"
        return f"{self.rank}{SUIT_SYMBOLS.get(self.suit, self.suit)}"

    def code(self) -> str:  # e.g. "Ah"
        return f"{self.rank}{self.suit}"


@dataclass
class Player:
    """A player seated at the table."""

    seat: int
    name: str = ""
    stack: float = 0.0
    bet: float = 0.0
    is_active: bool = True          # still in the current hand (not folded)
    position: Position = Position.UNKNOWN
    is_hero: bool = False           # True for the bot's own seat
    is_dealer: bool = False
    cards: list[Card] = field(default_factory=list)

    def __str__(self) -> str:
        tag = " (hero)" if self.is_hero else ""
        return f"Seat {self.seat} {self.name or '?'} ${self.stack:.2f}{tag}"


@dataclass
class GameState:
    """A snapshot of the table as read by the scraper."""

    hand_id: str = ""
    my_cards: list[Card] = field(default_factory=list)
    community_cards: list[Card] = field(default_factory=list)
    pot: float = 0.0
    my_stack: float = 0.0
    my_bet: float = 0.0
    call_amount: float = 0.0
    players: list[Player] = field(default_factory=list)
    round: Street = Street.PREFLOP
    position: Position = Position.UNKNOWN
    available_actions: list[ActionType] = field(default_factory=list)
    is_my_turn: bool = False
    time_left: float = 0.0
    big_blind: float = 0.10
    captured_at: float = field(default_factory=time.time)

    # --- Convenience derived properties ---
    @property
    def num_players(self) -> int:
        return len(self.players)

    @property
    def num_active(self) -> int:
        return sum(1 for p in self.players if p.is_active)

    @property
    def pot_odds(self) -> float:
        """Ratio of call cost to the resulting pot (0 when nothing to call)."""
        if self.call_amount <= 0:
            return 0.0
        return self.call_amount / (self.pot + self.call_amount)

    @property
    def effective_stack(self) -> float:
        """Smallest active stack among hero and opponents (in chips)."""
        active = [p.stack for p in self.players if p.is_active and not p.is_hero]
        if not active:
            return self.my_stack
        return min(self.my_stack, max(active))

    @property
    def spr(self) -> float:
        """Stack-to-pot ratio for the effective stack."""
        if self.pot <= 0:
            return float("inf")
        return self.effective_stack / self.pot

    def can(self, action: ActionType) -> bool:
        return action in self.available_actions

    def __str__(self) -> str:
        board = " ".join(str(c) for c in self.community_cards) or "-"
        hole = " ".join(str(c) for c in self.my_cards) or "??"
        return (
            f"Hand {self.hand_id or '?'} [{self.round.value}] "
            f"hole={hole} board={board} pot=${self.pot:.2f} "
            f"toCall=${self.call_amount:.2f} pos={self.position.value}"
        )


@dataclass
class Action:
    """A decision produced by the engine and executed by the executor."""

    type: ActionType
    amount: float = 0.0          # total bet/raise size in chips (0 for fold/check/call)
    confidence: float = 0.0      # 0..1 model confidence
    reason: str = ""             # human-readable rationale for logging/UI

    def __str__(self) -> str:
        if self.type in (ActionType.BET, ActionType.RAISE):
            return f"{self.type.value.upper()} ${self.amount:.2f} ({self.reason})"
        return f"{self.type.value.upper()} ({self.reason})"


@dataclass
class SessionStats:
    """Aggregate statistics for a play session."""

    hands_played: int = 0
    hands_won: int = 0
    profit_loss: float = 0.0          # net chips won/lost this session
    session_start: float = field(default_factory=time.time)
    bb_per_100: float = 0.0
    big_blind: float = 0.10
    vpip_count: int = 0               # hands where hero voluntarily put $ in pot
    pfr_count: int = 0                # hands where hero raised preflop

    def __post_init__(self) -> None:
        # Accept a datetime for convenience; store as epoch seconds internally.
        if isinstance(self.session_start, datetime):
            self.session_start = self.session_start.timestamp()

    # --- Derived metrics ---
    @property
    def elapsed_minutes(self) -> float:
        return (time.time() - self.session_start) / 60.0

    @property
    def win_rate(self) -> float:
        if self.hands_played == 0:
            return 0.0
        return self.hands_won / self.hands_played

    @property
    def vpip(self) -> float:
        if self.hands_played == 0:
            return 0.0
        return self.vpip_count / self.hands_played

    @property
    def pfr(self) -> float:
        if self.hands_played == 0:
            return 0.0
        return self.pfr_count / self.hands_played

    @property
    def profit_in_bb(self) -> float:
        if self.big_blind <= 0:
            return 0.0
        return self.profit_loss / self.big_blind

    def record_hand(self, won: bool, delta: float) -> None:
        """Update counters after a completed hand and recompute bb/100."""
        self.hands_played += 1
        if won:
            self.hands_won += 1
        self.profit_loss += delta
        self.recompute()

    def recompute(self) -> None:
        if self.hands_played > 0 and self.big_blind > 0:
            self.bb_per_100 = (self.profit_in_bb / self.hands_played) * 100.0
        else:
            self.bb_per_100 = 0.0

    def as_dict(self) -> dict:
        return {
            "hands_played": self.hands_played,
            "hands_won": self.hands_won,
            "win_rate": round(self.win_rate, 4),
            "profit_loss": round(self.profit_loss, 2),
            "profit_in_bb": round(self.profit_in_bb, 2),
            "bb_per_100": round(self.bb_per_100, 2),
            "vpip": round(self.vpip, 4),
            "pfr": round(self.pfr, 4),
            "elapsed_minutes": round(self.elapsed_minutes, 2),
        }
