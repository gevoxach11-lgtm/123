"""Monte-Carlo equity calculator.

Estimates hero's probability of winning (and tying) a hand by simulating many
random runouts and random opponent holdings. Used by the engine to make
+EV decisions when an exact equity table is unavailable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable, Sequence

from .evaluator import evaluate
from .models import RANKS, SUITS, Card


def full_deck() -> list[Card]:
    """Return a fresh, ordered 52-card deck."""
    return [Card(rank=r, suit=s) for r in RANKS for s in SUITS]


@dataclass
class EquityResult:
    """Outcome of an equity simulation."""

    win: float        # probability hero has the strict best hand
    tie: float        # probability hero ties for best
    lose: float       # probability hero loses
    iterations: int

    @property
    def equity(self) -> float:
        """Pot-share equity, splitting ties evenly (2-way approximation)."""
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
    """Estimate hero equity via Monte-Carlo simulation.

    Parameters
    ----------
    hole_cards:
        Hero's two hole cards.
    community_cards:
        Cards already on the board (0-5). Missing board cards are dealt
        randomly each iteration.
    num_opponents:
        Number of opposing players, each dealt two random cards.
    iterations:
        Number of simulated runouts. Higher == more accurate but slower.
    rng:
        Optional ``random.Random`` instance for deterministic testing.
    """
    if len(hole_cards) != 2:
        raise ValueError("hole_cards must contain exactly 2 cards")
    if num_opponents < 1:
        raise ValueError("num_opponents must be >= 1")

    rng = rng or random.Random()
    community = list(community_cards or [])

    known = set(hole_cards) | set(community)
    available = [c for c in full_deck() if c not in known]

    board_needed = 5 - len(community)
    cards_per_iter = board_needed + 2 * num_opponents

    if cards_per_iter > len(available):
        raise ValueError("Not enough cards left in the deck for this scenario")

    wins = ties = losses = 0
    hero_base = list(hole_cards)

    for _ in range(iterations):
        drawn = rng.sample(available, cards_per_iter)
        idx = 0
        sim_board = community + drawn[idx:idx + board_needed]
        idx += board_needed

        hero_score = evaluate(hero_base + sim_board)

        best_opp_score = None
        for _ in range(num_opponents):
            opp_hole = drawn[idx:idx + 2]
            idx += 2
            opp_score = evaluate(opp_hole + sim_board)
            if best_opp_score is None or opp_score > best_opp_score:
                best_opp_score = opp_score

        if hero_score > best_opp_score:
            wins += 1
        elif hero_score == best_opp_score:
            ties += 1
        else:
            losses += 1

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
    result = estimate_equity(
        hole_cards,
        list(community_cards or []),
        num_opponents=num_opponents,
        iterations=iterations,
    )
    return result.equity * 100.0


__all__ = ["EquityResult", "estimate_equity", "equity_pct", "full_deck"]
