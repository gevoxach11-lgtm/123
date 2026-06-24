"""Monte-Carlo equity calculator and draw detection.

Public API (as requested):

* :func:`full_deck`          - the 52-card deck.
* :func:`remove_cards`       - filter known cards out of a deck.
* :func:`monte_carlo_equity` - hero win equity (0.0-1.0) by simulation.
* :func:`pot_odds`           - call / (pot + call).
* :func:`has_flush_draw`     - 4 cards of one suit.
* :func:`has_straight_draw`  - 4 cards to an open-ended/gutshot straight.

The simulator deals random runouts and villain hands, evaluates everyone with
the integer scorer from :mod:`poker.evaluator`, and returns
``(wins + 0.5 * ties) / n_sims``. It is tuned to complete 5000 single-villain
simulations in well under a second.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable, Sequence

from .evaluator import RANK_VALUES, best_score
from .models import RANKS, SUITS, Card


# --------------------------------------------------------------------------- #
# Deck helpers
# --------------------------------------------------------------------------- #
def full_deck() -> list[Card]:
    """Return a fresh, ordered 52-card deck."""
    return [Card(rank=r, suit=s) for r in RANKS for s in SUITS]


def remove_cards(deck: Iterable[Card], known_cards: Iterable[Card]) -> list[Card]:
    """Return ``deck`` with every card in ``known_cards`` removed."""
    known = set(known_cards)
    return [card for card in deck if card not in known]


# --------------------------------------------------------------------------- #
# Core simulation
# --------------------------------------------------------------------------- #
def _run_sims(
    my_cards: Sequence[Card],
    community: Sequence[Card],
    villain_count: int,
    n_sims: int,
    rng: random.Random,
) -> tuple[int, int, int]:
    """Run the simulation loop, returning (wins, ties, losses)."""
    if len(my_cards) != 2:
        raise ValueError("my_cards must contain exactly 2 cards")
    if villain_count < 1:
        raise ValueError("villain_count must be >= 1")

    community = list(community)
    deck = remove_cards(full_deck(), list(my_cards) + community)

    board_needed = 5 - len(community)
    draw_count = board_needed + 2 * villain_count
    if draw_count > len(deck):
        raise ValueError("Not enough cards left in the deck for this scenario")

    hero_base = list(my_cards)
    sample = rng.sample  # local binding for speed
    score = best_score

    wins = ties = losses = 0
    for _ in range(n_sims):
        drawn = sample(deck, draw_count)
        board = community + drawn[:board_needed]
        hero_score = score(hero_base + board)

        best_villain = 0
        idx = board_needed
        for _ in range(villain_count):
            villain_score = score(drawn[idx:idx + 2] + board)
            idx += 2
            if villain_score > best_villain:
                best_villain = villain_score

        if hero_score > best_villain:
            wins += 1
        elif hero_score == best_villain:
            ties += 1
        else:
            losses += 1

    return wins, ties, losses


def monte_carlo_equity(
    my_cards: list[Card],
    community: list[Card] | None = None,
    villain_count: int = 1,
    n_sims: int = 5000,
    rng: random.Random | None = None,
) -> float:
    """Estimate hero win equity in ``[0.0, 1.0]`` via Monte-Carlo simulation.

    Ties are split evenly: ``(wins + 0.5 * ties) / n_sims``.
    """
    rng = rng or random
    wins, ties, _ = _run_sims(my_cards, community or [], villain_count, n_sims, rng)
    return (wins + 0.5 * ties) / n_sims


# --------------------------------------------------------------------------- #
# Rich result wrapper (kept for the engine / dashboard)
# --------------------------------------------------------------------------- #
@dataclass
class EquityResult:
    """Outcome of an equity simulation with win/tie/lose breakdown."""

    win: float
    tie: float
    lose: float
    iterations: int

    @property
    def equity(self) -> float:
        return self.win + self.tie / 2.0

    def as_dict(self) -> dict:
        return {
            "win": round(self.win, 4),
            "tie": round(self.tie, 4),
            "lose": round(self.lose, 4),
            "equity": round(self.equity, 4),
            "iterations": self.iterations,
        }


def estimate_equity(
    hole_cards: Sequence[Card],
    community_cards: Sequence[Card] | None = None,
    num_opponents: int = 1,
    iterations: int = 5000,
    rng: random.Random | None = None,
) -> EquityResult:
    """Detailed equity estimate returning an :class:`EquityResult`."""
    rng = rng or random.Random()
    wins, ties, losses = _run_sims(
        hole_cards, community_cards or [], num_opponents, iterations, rng
    )
    total = float(iterations)
    return EquityResult(
        win=wins / total,
        tie=ties / total,
        lose=losses / total,
        iterations=iterations,
    )


def equity_pct(
    hole_cards: Sequence[Card],
    community_cards: Iterable[Card] | None = None,
    num_opponents: int = 1,
    iterations: int = 5000,
) -> float:
    """Convenience wrapper returning equity as a 0..100 percentage."""
    return monte_carlo_equity(
        list(hole_cards), list(community_cards or []),
        villain_count=num_opponents, n_sims=iterations,
    ) * 100.0


# --------------------------------------------------------------------------- #
# Pot odds & draws
# --------------------------------------------------------------------------- #
def pot_odds(call_amount: float, pot: float) -> float:
    """Return the pot odds for a call: ``call / (pot + call)`` (0 if nothing to call)."""
    if call_amount <= 0:
        return 0.0
    return call_amount / (pot + call_amount)


def has_flush_draw(
    my_cards: Iterable[Card],
    community: Iterable[Card] | None = None,
) -> bool:
    """Return True if hero holds exactly four cards of one suit (a flush draw)."""
    cards = list(my_cards) + list(community or [])
    counts: dict[str, int] = {}
    for card in cards:
        counts[card.suit] = counts.get(card.suit, 0) + 1
    return any(count == 4 for count in counts.values())


def has_straight_draw(
    my_cards: Iterable[Card],
    community: Iterable[Card] | None = None,
) -> bool:
    """Return True for an open-ended or gutshot straight draw.

    A draw exists when some five-rank window contains exactly four of our
    distinct ranks (one card short of a straight). The ace is considered for
    both the broadway (T-A) and wheel (A-5) straights.
    """
    cards = list(my_cards) + list(community or [])
    values = {RANK_VALUES[c.rank] for c in cards}
    if 14 in values:
        values.add(1)  # ace plays low for the wheel
    for low in range(1, 11):  # windows A-5 .. T-A
        window = set(range(low, low + 5))
        if len(window & values) == 4:
            return True
    return False


__all__ = [
    "full_deck",
    "remove_cards",
    "monte_carlo_equity",
    "pot_odds",
    "has_flush_draw",
    "has_straight_draw",
    "EquityResult",
    "estimate_equity",
    "equity_pct",
]
