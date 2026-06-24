"""7-card Texas Hold'em hand evaluator.

Given up to seven cards (2 hole + 5 board) this module finds the best 5-card
poker hand and returns a comparable score. The score is a tuple where the first
element is the hand category (see :class:`HandRank`) and the remaining elements
are kicker ranks in descending importance, so two scores can be compared
directly with normal tuple comparison (higher == better).

The implementation is pure-Python and dependency-free; it is fast enough for
the Monte-Carlo equity simulations used elsewhere in the bot.
"""

from __future__ import annotations

from enum import IntEnum
from itertools import combinations
from typing import Iterable, Sequence

from .models import Card, RANK_VALUES


class HandRank(IntEnum):
    """Poker hand categories ordered from worst (0) to best (8)."""

    HIGH_CARD = 0
    PAIR = 1
    TWO_PAIR = 2
    THREE_OF_A_KIND = 3
    STRAIGHT = 4
    FLUSH = 5
    FULL_HOUSE = 6
    FOUR_OF_A_KIND = 7
    STRAIGHT_FLUSH = 8


HAND_NAMES = {
    HandRank.HIGH_CARD: "High Card",
    HandRank.PAIR: "Pair",
    HandRank.TWO_PAIR: "Two Pair",
    HandRank.THREE_OF_A_KIND: "Three of a Kind",
    HandRank.STRAIGHT: "Straight",
    HandRank.FLUSH: "Flush",
    HandRank.FULL_HOUSE: "Full House",
    HandRank.FOUR_OF_A_KIND: "Four of a Kind",
    HandRank.STRAIGHT_FLUSH: "Straight Flush",
}

HandScore = tuple[int, ...]


def _straight_high(unique_values: Sequence[int]) -> int | None:
    """Return the high card of the best straight, or ``None`` if no straight.

    ``unique_values`` must be a set/sequence of distinct rank values. Handles
    the wheel (A-2-3-4-5) where the ace plays low.
    """
    values = set(unique_values)
    # Ace can play low for the wheel.
    if 14 in values:
        values.add(1)
    ordered = sorted(values, reverse=True)
    run = 1
    for i in range(len(ordered) - 1):
        if ordered[i] - 1 == ordered[i + 1]:
            run += 1
            if run >= 5:
                return ordered[i - 3]
        else:
            run = 1
    return None


def _score_five(cards: Sequence[Card]) -> HandScore:
    """Score exactly five cards."""
    values = sorted((c.value for c in cards), reverse=True)
    suits = [c.suit for c in cards]

    is_flush = len(set(suits)) == 1
    straight_high = _straight_high(values)

    # Count occurrences of each rank value.
    counts: dict[int, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    # Sort by (count, value) descending so the most frequent / highest ranks come first.
    by_count = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    count_pattern = [cnt for _, cnt in by_count]
    ordered_values = [val for val, _ in by_count]

    if is_flush and straight_high is not None:
        return (HandRank.STRAIGHT_FLUSH, straight_high)
    if count_pattern[0] == 4:
        return (HandRank.FOUR_OF_A_KIND, *ordered_values)
    if count_pattern[0] == 3 and count_pattern[1] >= 2:
        return (HandRank.FULL_HOUSE, ordered_values[0], ordered_values[1])
    if is_flush:
        return (HandRank.FLUSH, *values)
    if straight_high is not None:
        return (HandRank.STRAIGHT, straight_high)
    if count_pattern[0] == 3:
        return (HandRank.THREE_OF_A_KIND, *ordered_values)
    if count_pattern[0] == 2 and count_pattern[1] == 2:
        return (HandRank.TWO_PAIR, *ordered_values)
    if count_pattern[0] == 2:
        return (HandRank.PAIR, *ordered_values)
    return (HandRank.HIGH_CARD, *values)


def evaluate(cards: Iterable[Card]) -> HandScore:
    """Return the best 5-card score from 5, 6 or 7 cards.

    The returned tuple is directly comparable - a larger tuple denotes a
    stronger hand.
    """
    card_list = list(cards)
    if len(card_list) < 5:
        raise ValueError("evaluate() needs at least 5 cards")
    if len(card_list) == 5:
        return _score_five(card_list)
    best: HandScore | None = None
    for combo in combinations(card_list, 5):
        score = _score_five(combo)
        if best is None or score > best:
            best = score
    assert best is not None
    return best


def hand_rank(cards: Iterable[Card]) -> HandRank:
    """Return just the :class:`HandRank` category for the given cards."""
    return HandRank(evaluate(cards)[0])


def describe(cards: Iterable[Card]) -> str:
    """Return a human-readable name for the best hand."""
    return HAND_NAMES[hand_rank(cards)]


def compare(hand_a: Iterable[Card], hand_b: Iterable[Card]) -> int:
    """Compare two hands.

    Returns ``1`` if ``hand_a`` wins, ``-1`` if ``hand_b`` wins, ``0`` for a tie.
    """
    score_a = evaluate(hand_a)
    score_b = evaluate(hand_b)
    if score_a > score_b:
        return 1
    if score_a < score_b:
        return -1
    return 0


__all__ = [
    "HandRank",
    "HAND_NAMES",
    "HandScore",
    "evaluate",
    "hand_rank",
    "describe",
    "compare",
    "RANK_VALUES",
]
