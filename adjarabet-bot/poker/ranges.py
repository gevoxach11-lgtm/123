"""GTO-flavoured preflop range tables.

This module encodes a simplified, position-aware opening/calling range for
6-max No-Limit Hold'em. Ranges are expressed using the standard 169-combo
notation:

* Pocket pairs:        ``"AA"``, ``"KK"`` ... ``"22"``
* Suited hands:        ``"AKs"``, ``"T9s"`` ...
* Offsuit hands:       ``"AKo"``, ``"KQo"`` ...

The tables are intentionally compact and opinionated rather than a full solver
output - they give the engine a sensible default preflop policy that can later
be replaced with solver-derived ranges.
"""

from __future__ import annotations

from typing import Iterable

from .models import RANK_VALUES, Card, Position

# --------------------------------------------------------------------------- #
# Hand notation helpers
# --------------------------------------------------------------------------- #
def hand_notation(cards: Iterable[Card]) -> str:
    """Convert two hole cards into 169-combo notation (e.g. ``'AKs'``)."""
    cards = list(cards)
    if len(cards) != 2:
        raise ValueError("hand_notation requires exactly two cards")
    a, b = cards
    # Higher rank first.
    if a.value < b.value:
        a, b = b, a
    if a.rank == b.rank:
        return f"{a.rank}{b.rank}"  # pair, e.g. "AA"
    suffix = "s" if a.suit == b.suit else "o"
    return f"{a.rank}{b.rank}{suffix}"


# --------------------------------------------------------------------------- #
# Opening (raise-first-in) ranges per position - 6-max.
# Each set lists every combo that should open-raise from that seat.
# Ranges widen as position improves (UTG tightest, BTN widest).
# --------------------------------------------------------------------------- #
_UTG = {
    "AA", "KK", "QQ", "JJ", "TT", "99", "88", "77",
    "AKs", "AQs", "AJs", "ATs", "KQs", "KJs", "QJs", "JTs", "T9s",
    "AKo", "AQo",
}

_MP = _UTG | {
    "66", "55",
    "A9s", "KTs", "QTs", "J9s", "T8s", "98s",
    "AJo", "KQo",
}

_CO = _MP | {
    "44", "33", "22",
    "A8s", "A7s", "A6s", "A5s", "A4s", "A3s", "A2s",
    "K9s", "Q9s", "J8s", "T7s", "97s", "87s", "76s", "65s",
    "ATo", "KJo", "QJo", "JTo",
}

_BTN = _CO | {
    "K8s", "K7s", "K6s", "K5s", "Q8s", "J7s", "T6s", "96s", "86s", "75s",
    "64s", "54s", "53s", "43s",
    "A9o", "A8o", "A7o", "A5o", "KTo", "K9o", "QTo", "Q9o", "J9o", "T9o", "98o",
}

_SB = _CO | {
    "A9o", "KTo", "QTo", "JTo",
    "K8s", "Q8s", "J8s", "T8s",
}

OPENING_RANGES: dict[Position, set[str]] = {
    Position.UTG: _UTG,
    Position.UTG1: _UTG,
    Position.MP: _MP,
    Position.LJ: _MP,
    Position.HJ: _CO,
    Position.CO: _CO,
    Position.BTN: _BTN,
    Position.SB: _SB,
    Position.BB: _BTN,  # BB defends very wide; reuse the widest open set.
    Position.UNKNOWN: _MP,
}

# Premium hands worth re-raising / 3-betting for value from any position.
THREE_BET_VALUE = {"AA", "KK", "QQ", "JJ", "AKs", "AKo", "AQs"}

# Hands strong enough to call an open if not raising.
CALLING_RANGE = _CO | {"A9o", "KJo", "QJo", "JTo", "T9o"}


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def in_opening_range(cards: Iterable[Card], position: Position) -> bool:
    """Return True if the hand should open-raise from the given position."""
    return hand_notation(cards) in OPENING_RANGES.get(position, _MP)


def is_three_bet(cards: Iterable[Card]) -> bool:
    """Return True for premium 3-bet-for-value hands."""
    return hand_notation(cards) in THREE_BET_VALUE


def in_calling_range(cards: Iterable[Card]) -> bool:
    """Return True if the hand is strong enough to flat-call a raise."""
    return hand_notation(cards) in CALLING_RANGE


def hand_strength_score(cards: Iterable[Card]) -> float:
    """Rough 0..1 preflop strength heuristic (Chen-formula inspired).

    Useful as a tiebreaker / sizing input when a hand is in range.
    """
    cards = list(cards)
    a, b = (cards[0], cards[1]) if cards[0].value >= cards[1].value else (cards[1], cards[0])
    high = a.value
    # Base points from the high card (Chen formula style).
    base = {14: 10, 13: 8, 12: 7, 11: 6}.get(high, high / 2.0)
    score = base
    if a.rank == b.rank:  # pair
        score = max(base * 2, 5)
    if a.suit == b.suit:  # suited bonus
        score += 2
    gap = high - b.value
    penalty = {0: 0, 1: 0, 2: 1, 3: 2, 4: 4}.get(gap, 5)
    score -= penalty
    # Straight bonus for connectors with low cards.
    if gap <= 1 and high < 12:
        score += 1
    # Normalise to ~0..1 (AA scores 20 in Chen).
    return max(0.0, min(1.0, score / 20.0))


__all__ = [
    "hand_notation",
    "OPENING_RANGES",
    "THREE_BET_VALUE",
    "CALLING_RANGE",
    "in_opening_range",
    "is_three_bet",
    "in_calling_range",
    "hand_strength_score",
    "RANK_VALUES",
]
