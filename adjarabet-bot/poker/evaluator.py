"""7-card Texas Hold'em hand evaluator.

Public API (as requested):

* ``RANK_VALUES`` - mapping of rank char -> numeric value (2..14).
* ``SUIT_NAMES``  - mapping of suit char -> unicode pip.
* :func:`parse_card`   - lenient parser for many textual card formats.
* :func:`hand_key`     - two cards -> ``"AKs"`` / ``"AKo"`` / ``"22"``.
* :func:`score_five`   - score exactly five cards -> dict.
* :func:`evaluate_hand`- best 5-card hand from 5-7 cards -> dict.

Scores are single integers that are directly comparable *across* categories
using the scheme::

    score = category * 10**10 + primary_rank * 10**6 + encoded_kickers

so a larger integer always denotes a stronger hand. Hand categories run from
``1`` (high card) to ``9`` (straight flush). The wheel straight (A-2-3-4-5) is
handled with the ace playing low.

Backward-compatible helpers (:func:`evaluate`, :func:`hand_rank`,
:func:`describe`, :func:`compare`, :class:`HandRank`) are also provided.
"""

from __future__ import annotations

from enum import IntEnum
from itertools import combinations
from typing import Iterable, Sequence

from .models import Card

# --------------------------------------------------------------------------- #
# Rank / suit tables
# --------------------------------------------------------------------------- #
_RANK_ORDER = "23456789TJQKA"
RANK_VALUES: dict[str, int] = {rank: i + 2 for i, rank in enumerate(_RANK_ORDER)}

SUIT_NAMES: dict[str, str] = {"h": "\u2665", "d": "\u2666", "c": "\u2663", "s": "\u2660"}

# Word forms for the lenient parser.
_WORD_RANKS = {
    "two": "2", "deuce": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "T",
    "jack": "J", "queen": "Q", "king": "K", "ace": "A",
}
_WORD_SUITS = {
    "heart": "h", "hearts": "h",
    "diamond": "d", "diamonds": "d",
    "club": "c", "clubs": "c",
    "spade": "s", "spades": "s",
}
_SYMBOL_SUITS = {"\u2665": "h", "\u2666": "d", "\u2663": "c", "\u2660": "s"}


class HandRank(IntEnum):
    """Poker hand categories ordered from worst (1) to best (9)."""

    HIGH_CARD = 1
    PAIR = 2
    TWO_PAIR = 3
    THREE_OF_A_KIND = 4
    STRAIGHT = 5
    FLUSH = 6
    FULL_HOUSE = 7
    FOUR_OF_A_KIND = 8
    STRAIGHT_FLUSH = 9


HAND_NAMES: dict[int, str] = {
    1: "High Card",
    2: "Pair",
    3: "Two Pair",
    4: "Three of a Kind",
    5: "Straight",
    6: "Flush",
    7: "Full House",
    8: "Four of a Kind",
    9: "Straight Flush",
}

# Pre-computed 5-of-7 index combinations for the hot evaluation path.
_COMBOS_7 = list(combinations(range(7), 5))
_COMBOS_6 = list(combinations(range(6), 5))

_CATEGORY_MULT = 10 ** 10
_PRIMARY_MULT = 10 ** 6


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def parse_card(text: str | None) -> Card | None:
    """Parse a card from many textual representations.

    Accepts e.g. ``"Ah"``, ``"A\u2665"``, ``"Ace of Hearts"``, ``"A-h"``,
    ``"10h"`` (``10`` -> ``T``), ``"th"``. Returns ``None`` when the text
    cannot be parsed instead of raising.
    """
    if not text:
        return None
    raw = text.strip()
    if not raw:
        return None
    low = raw.lower()

    # --- Word form: "ace of hearts" / "ten of spades" ---
    if " of " in low:
        rank_word, _, suit_word = low.partition(" of ")
        rank = _WORD_RANKS.get(rank_word.strip())
        suit = _WORD_SUITS.get(suit_word.strip())
        if rank and suit:
            return _safe_card(rank, suit)
        return None

    # --- Compact form: normalise separators and symbols ---
    compact = low.replace("-", "").replace("_", "").replace(" ", "")
    for symbol, ch in _SYMBOL_SUITS.items():
        compact = compact.replace(symbol, ch)
    compact = compact.replace("10", "t")

    if len(compact) < 2:
        return None

    rank_char = compact[0].upper()
    suit_char = compact[-1].lower()

    if rank_char not in RANK_VALUES or suit_char not in SUIT_NAMES:
        return None
    return _safe_card(rank_char, suit_char)


def _safe_card(rank: str, suit: str) -> Card | None:
    try:
        return Card(rank=rank, suit=suit)
    except Exception:
        return None


def hand_key(c1: Card, c2: Card) -> str:
    """Return two cards in 169-combo notation: ``"AKs"``/``"AKo"``/``"22"``.

    The higher rank is written first; ``s`` denotes suited, ``o`` offsuit.
    """
    a, b = (c1, c2) if RANK_VALUES[c1.rank] >= RANK_VALUES[c2.rank] else (c2, c1)
    if a.rank == b.rank:
        return f"{a.rank}{b.rank}"
    return f"{a.rank}{b.rank}{'s' if a.suit == b.suit else 'o'}"


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _straight_high(values: Iterable[int]) -> int | None:
    """Return the high card of the best straight, handling the wheel."""
    vset = set(values)
    if 14 in vset:
        vset.add(1)  # ace can play low
    ordered = sorted(vset, reverse=True)
    run = 1
    for i in range(len(ordered) - 1):
        if ordered[i] - 1 == ordered[i + 1]:
            run += 1
            if run >= 5:
                return ordered[i - 3]
        else:
            run = 1
    return None


def _pack(category: int, key_values: Sequence[int]) -> int:
    """Pack a category and ordered rank values into a comparable integer."""
    primary = key_values[0] if key_values else 0
    kicker_code = 0
    for value in key_values[1:]:
        kicker_code = kicker_code * 15 + value
    return category * _CATEGORY_MULT + primary * _PRIMARY_MULT + kicker_code


def _category_and_key(values_desc: Sequence[int], is_flush: bool) -> tuple[int, list[int]]:
    """Determine (category, ordered key values) for five card values.

    ``values_desc`` must be the five rank values sorted in descending order.
    """
    straight_high = _straight_high(values_desc)

    counts: dict[int, int] = {}
    for v in values_desc:
        counts[v] = counts.get(v, 0) + 1
    by_count = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    pattern = [cnt for _, cnt in by_count]
    ordered = [val for val, _ in by_count]

    if is_flush and straight_high is not None:
        return 9, [straight_high]
    if pattern[0] == 4:
        return 8, ordered                      # [quad, kicker]
    if pattern[0] == 3 and len(pattern) > 1 and pattern[1] >= 2:
        return 7, [ordered[0], ordered[1]]     # [trips, pair]
    if is_flush:
        return 6, list(values_desc)
    if straight_high is not None:
        return 5, [straight_high]
    if pattern[0] == 3:
        return 4, ordered                      # [trips, k, k]
    if pattern[0] == 2 and len(pattern) > 1 and pattern[1] == 2:
        return 3, ordered                      # [high pair, low pair, kicker]
    if pattern[0] == 2:
        return 2, ordered                      # [pair, k, k, k]
    return 1, list(values_desc)


def score_five(cards: Sequence[Card]) -> dict:
    """Score exactly five cards.

    Returns ``{'name': str, 'score': int, 'cards': list[Card], 'category': int}``.
    """
    card_list = list(cards)
    if len(card_list) != 5:
        raise ValueError("score_five() requires exactly 5 cards")
    values = sorted((RANK_VALUES[c.rank] for c in card_list), reverse=True)
    is_flush = len({c.suit for c in card_list}) == 1
    category, key_values = _category_and_key(values, is_flush)
    return {
        "name": HAND_NAMES[category],
        "score": _pack(category, key_values),
        "cards": card_list,
        "category": category,
    }


def _score_five_int(values_desc: list[int], is_flush: bool) -> int:
    """Fast path: integer score from pre-extracted values/flush flag."""
    category, key_values = _category_and_key(values_desc, is_flush)
    return _pack(category, key_values)


def best_score(cards: Sequence[Card]) -> int:
    """Return just the best comparable integer score for 5-7 cards.

    This is the performance-critical helper used by the Monte-Carlo simulator;
    it avoids building intermediate dictionaries.
    """
    cards = list(cards)
    n = len(cards)
    if n < 5:
        raise ValueError("best_score() needs at least 5 cards")

    values = [RANK_VALUES[c.rank] for c in cards]
    suits = [c.suit for c in cards]

    if n == 5:
        return _score_five_int(sorted(values, reverse=True),
                               suits[0] == suits[1] == suits[2] == suits[3] == suits[4])

    combos = _COMBOS_7 if n == 7 else (_COMBOS_6 if n == 6 else list(combinations(range(n), 5)))
    best = 0
    for i0, i1, i2, i3, i4 in combos:
        vd = sorted((values[i0], values[i1], values[i2], values[i3], values[i4]), reverse=True)
        is_flush = suits[i0] == suits[i1] == suits[i2] == suits[i3] == suits[i4]
        s = _score_five_int(vd, is_flush)
        if s > best:
            best = s
    return best


def evaluate_hand(cards: Iterable[Card]) -> dict:
    """Return the best 5-card hand from 5, 6 or 7 cards as a dict.

    ``{'name': str, 'score': int, 'cards': list[Card], 'category': int}`` where
    ``cards`` are the five cards forming the best hand.
    """
    card_list = list(cards)
    if len(card_list) < 5:
        raise ValueError("evaluate_hand() needs at least 5 cards")
    if len(card_list) == 5:
        return score_five(card_list)
    best: dict | None = None
    for combo in combinations(card_list, 5):
        result = score_five(list(combo))
        if best is None or result["score"] > best["score"]:
            best = result
    assert best is not None
    return best


# --------------------------------------------------------------------------- #
# Backward-compatible helpers
# --------------------------------------------------------------------------- #
def evaluate(cards: Iterable[Card]) -> int:
    """Return the best comparable integer score (alias of :func:`best_score`)."""
    return best_score(list(cards))


def hand_rank(cards: Iterable[Card]) -> HandRank:
    """Return the :class:`HandRank` category of the best hand."""
    return HandRank(evaluate_hand(cards)["category"])


def describe(cards: Iterable[Card]) -> str:
    """Return a human-readable name for the best hand."""
    return evaluate_hand(cards)["name"]


def compare(hand_a: Iterable[Card], hand_b: Iterable[Card]) -> int:
    """Compare two hands: ``1`` if a wins, ``-1`` if b wins, ``0`` for a tie."""
    sa, sb = best_score(list(hand_a)), best_score(list(hand_b))
    return (sa > sb) - (sa < sb)


__all__ = [
    "RANK_VALUES",
    "SUIT_NAMES",
    "HandRank",
    "HAND_NAMES",
    "parse_card",
    "hand_key",
    "score_five",
    "evaluate_hand",
    "best_score",
    "evaluate",
    "hand_rank",
    "describe",
    "compare",
]
