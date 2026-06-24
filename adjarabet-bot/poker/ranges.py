"""GTO 6-max preflop range tables.

Public API (as requested):

* ``OPEN_RANGES``       - opening (raise-first-in) ranges per position.
* ``THREE_BET_RANGES``  - value 3-bet ranges per position (plus ``'default'``).
* ``FOUR_BET_RANGE``    - the nutted 4-bet range.
* :func:`is_in_range`   - membership test accepting two cards.

Ranges use the standard 169-combo notation (``"AKs"``, ``"AKo"``, ``"22"``).
Each later position inherits the earlier (tighter) ranges and adds more combos.

Backward-compatible helpers used by the engine (:func:`hand_notation`,
:func:`in_opening_range`, :func:`is_three_bet`, :func:`in_calling_range`,
:func:`hand_strength_score`) are also provided.
"""

from __future__ import annotations

from typing import Iterable

from .evaluator import RANK_VALUES, hand_key
from .models import Card, Position

# --------------------------------------------------------------------------- #
# Opening ranges (6-max). Each widens as position improves.
# --------------------------------------------------------------------------- #
_UTG = {
    "AA", "KK", "QQ", "JJ", "TT", "99", "88", "77",
    "AKs", "AQs", "AJs", "ATs", "A9s",
    "AKo", "AQo", "KQs", "KJs", "QJs", "JTs", "T9s", "98s",
}

_MP = _UTG | {
    "66", "55", "A8s", "A5s", "A4s", "KTs", "QTs", "J9s", "AJo",
}

_CO = _MP | {
    "44", "33", "22", "A3s", "A2s", "K9s", "Q9s", "T8s",
    "97s", "87s", "76s", "KJo", "QJo", "JTo", "ATo",
}

_BTN = _CO | {
    "K8s", "Q8s", "J8s", "T7s", "86s", "75s", "65s", "54s",
    "KTo", "QTo", "A9o", "A8o",
}

_SB = {
    "AA", "KK", "QQ", "JJ", "TT", "99", "88", "77", "66",
    "AKs", "AQs", "AJs", "ATs", "A9s", "A8s", "A5s",
    "AKo", "AQo", "AJo", "KQs", "KJs", "KTs", "QJs", "JTs", "T9s", "98s",
}

OPEN_RANGES: dict[str, set[str]] = {
    "UTG": _UTG,
    "MP": _MP,
    "CO": _CO,
    "BTN": _BTN,
    "SB": _SB,
}

THREE_BET_RANGES: dict[str, set[str]] = {
    "BTN": {"AA", "KK", "QQ", "JJ", "AKs", "AQs", "AKo"},
    "CO": {"AA", "KK", "QQ", "AKs", "AKo"},
    "default": {"AA", "KK", "AKs"},
}

FOUR_BET_RANGE: set[str] = {"AA", "KK"}

# Hands worth flat-calling an open when not 3-betting (roughly a CO open width).
CALLING_RANGE: set[str] = _CO | {"A9o", "KJo", "QJo", "JTo", "T9o"}

# Map the richer Position enum onto the five spec range buckets.
_POSITION_TO_BUCKET: dict[Position, str] = {
    Position.UTG: "UTG",
    Position.UTG1: "UTG",
    Position.MP: "MP",
    Position.LJ: "MP",
    Position.HJ: "CO",
    Position.CO: "CO",
    Position.BTN: "BTN",
    Position.SB: "SB",
    Position.BB: "BTN",       # BB defends very wide; reuse the widest set.
    Position.UNKNOWN: "MP",
}


# --------------------------------------------------------------------------- #
# Membership
# --------------------------------------------------------------------------- #
def is_in_range(c1: Card, c2: Card, range_set: Iterable[str]) -> bool:
    """Return True if the two-card hand is in ``range_set``.

    Matches both the full ``"AKs"`` / ``"AKo"`` / ``"22"`` notation and a
    suit-agnostic ``"AK"`` / ``"22"`` form (which covers both suited and
    offsuit combos).
    """
    members = range_set if isinstance(range_set, (set, frozenset)) else set(range_set)
    key = hand_key(c1, c2)
    if key in members:
        return True
    # Suit-agnostic form, e.g. "AK" matches AKs and AKo.
    ranks_only = key[:2]
    return ranks_only in members


# --------------------------------------------------------------------------- #
# Backward-compatible helpers (cards-as-list style)
# --------------------------------------------------------------------------- #
def hand_notation(cards: Iterable[Card]) -> str:
    """Convert two hole cards into 169-combo notation (e.g. ``'AKs'``)."""
    card_list = list(cards)
    if len(card_list) != 2:
        raise ValueError("hand_notation requires exactly two cards")
    return hand_key(card_list[0], card_list[1])


def _bucket_for(position: Position) -> str:
    return _POSITION_TO_BUCKET.get(position, "MP")


def in_opening_range(cards: Iterable[Card], position: Position) -> bool:
    """Return True if the hand should open-raise from the given position."""
    card_list = list(cards)
    if len(card_list) != 2:
        return False
    bucket = _bucket_for(position)
    return is_in_range(card_list[0], card_list[1], OPEN_RANGES[bucket])


def is_three_bet(cards: Iterable[Card], position: Position | None = None) -> bool:
    """Return True for value 3-bet hands (uses the position's range or default)."""
    card_list = list(cards)
    if len(card_list) != 2:
        return False
    if position is not None:
        range_set = THREE_BET_RANGES.get(_bucket_for(position), THREE_BET_RANGES["default"])
    else:
        range_set = THREE_BET_RANGES["default"]
    return is_in_range(card_list[0], card_list[1], range_set)


def is_four_bet(cards: Iterable[Card]) -> bool:
    """Return True for the nutted 4-bet range (AA, KK)."""
    card_list = list(cards)
    if len(card_list) != 2:
        return False
    return is_in_range(card_list[0], card_list[1], FOUR_BET_RANGE)


def in_calling_range(cards: Iterable[Card]) -> bool:
    """Return True if the hand is strong enough to flat-call a raise."""
    card_list = list(cards)
    if len(card_list) != 2:
        return False
    return is_in_range(card_list[0], card_list[1], CALLING_RANGE)


def hand_strength_score(cards: Iterable[Card]) -> float:
    """Rough 0..1 preflop strength heuristic (Chen-formula inspired)."""
    card_list = list(cards)
    a, b = (card_list[0], card_list[1]) \
        if RANK_VALUES[card_list[0].rank] >= RANK_VALUES[card_list[1].rank] \
        else (card_list[1], card_list[0])
    high = RANK_VALUES[a.rank]
    base = {14: 10, 13: 8, 12: 7, 11: 6}.get(high, high / 2.0)
    score = base
    if a.rank == b.rank:
        score = max(base * 2, 5)
    if a.suit == b.suit:
        score += 2
    gap = high - RANK_VALUES[b.rank]
    score -= {0: 0, 1: 0, 2: 1, 3: 2, 4: 4}.get(gap, 5)
    if gap <= 1 and high < 12:
        score += 1
    return max(0.0, min(1.0, score / 20.0))


__all__ = [
    "OPEN_RANGES",
    "THREE_BET_RANGES",
    "FOUR_BET_RANGE",
    "CALLING_RANGE",
    "is_in_range",
    "hand_key",
    "hand_notation",
    "in_opening_range",
    "is_three_bet",
    "is_four_bet",
    "in_calling_range",
    "hand_strength_score",
]
