"""Poker decision-making engine.

Turns a :class:`GameState` into an :class:`Action`:

* **Preflop** - position-aware open / 3-bet / 4-bet logic driven by the GTO
  range tables in :mod:`poker.ranges`.
* **Postflop** - Monte-Carlo equity (:mod:`poker.montecarlo`) compared with pot
  odds, combined with made-hand strength and draw detection.

The strategy is a solid, transparent tight-aggressive baseline (not a solver).
Bet sizes carry small random variation and the engine occasionally bluffs or
slow-plays so its behaviour is less predictable. Every decision records a
``reason`` for the logs / dashboard, and the final action is "legalised"
against the actions the table actually offers.
"""

from __future__ import annotations

import dataclasses
import random

from poker.evaluator import evaluate_hand, hand_key
from poker.montecarlo import (
    has_flush_draw,
    has_straight_draw,
    monte_carlo_equity,
    pot_odds,
)
from poker.ranges import (
    FOUR_BET_RANGE,
    OPEN_RANGES,
    THREE_BET_RANGES,
    is_in_range,
)
from poker.models import Action, ActionType, GameState, Position, Street
from utils.logger import get_logger

logger = get_logger()

# Map the rich Position enum onto the five range buckets used by the tables.
_POSITION_BUCKET = {
    Position.UTG: "UTG",
    Position.UTG1: "UTG",
    Position.MP: "MP",
    Position.LJ: "MP",
    Position.HJ: "CO",
    Position.CO: "CO",
    Position.BTN: "BTN",
    Position.SB: "SB",
    Position.BB: "BTN",       # BB defends wide; reuse the widest set.
    Position.UNKNOWN: "MP",
}

# Cold-calling range vs a 3-bet (premiums only).
_COLD_CALL_VS_3BET = {"JJ", "TT", "AKs", "AKo", "QQ"}

# Postflop equity simulation depth (5000 sims run in well under a second).
_POSTFLOP_SIMS = 4000


class PokerEngine:
    """Compute the bot's action for a given game state."""

    def __init__(self, config=None, rng: random.Random | None = None) -> None:
        self.config = config
        self.rng = rng or random.Random()

    # ------------------------------------------------------------------ #
    # Routing
    # ------------------------------------------------------------------ #
    def decide(self, state: GameState) -> Action:
        """Route to the preflop or postflop engine and return a legal action."""
        if len(state.my_cards) < 2:
            return self._legalize(self._passive(state, "no hole cards"), state)

        if state.round == Street.PREFLOP:
            action = self.preflop_decide(state)
        else:
            action = self.postflop_decide(state)

        action = self._legalize(action, state)
        logger.info("Decision: {} | {}", action, state)
        return action

    # ------------------------------------------------------------------ #
    # Preflop
    # ------------------------------------------------------------------ #
    def preflop_decide(self, state: GameState) -> Action:
        c1, c2 = state.my_cards[0], state.my_cards[1]
        pos = state.position
        call_amount = state.call_amount
        stack = state.my_stack

        bucket = _POSITION_BUCKET.get(pos, "MP")
        open_range = OPEN_RANGES.get(bucket, OPEN_RANGES["MP"])
        three_bet_range = THREE_BET_RANGES.get(bucket, THREE_BET_RANGES["default"])

        # Big blind estimate: prefer the real value, fall back to the spec rule.
        bb = state.big_blind if getattr(state, "big_blind", 0) else 0.0
        if bb <= 0:
            bb = max(0.02 * stack, 1.0)

        key = hand_key(c1, c2)

        # --- Unopened pot (no meaningful bet to call) ---
        if call_amount == 0 or call_amount <= bb:
            if is_in_range(c1, c2, open_range):
                mult = 2.5 if bucket in ("BTN", "CO") else 3.0
                size = self._vary(mult * bb, 0.15)
                return self._raise(state, size, confidence=0.7,
                                   reason=f"open-raise {key} from {bucket} ({mult}bb)")
            # Not in opening range.
            if pos == Position.BB and call_amount <= bb:
                if self._chance(0.08):
                    size = self._vary(2.5 * bb, 0.15)
                    return self._raise(state, size, confidence=0.2,
                                       reason=f"bluff open {key} from BB")
                return Action(ActionType.CHECK, reason=f"check BB with {key}")
            if self._chance(0.08):
                size = self._vary((2.5 if bucket in ("BTN", "CO") else 3.0) * bb, 0.15)
                return self._raise(state, size, confidence=0.2,
                                   reason=f"bluff open {key} from {bucket}")
            return Action(ActionType.FOLD, reason=f"fold {key} (out of range)")

        # --- Facing a single raise ---
        if bb < call_amount < 8 * bb:
            if is_in_range(c1, c2, three_bet_range):
                size = self._vary(call_amount * 3.0, 0.10)
                return self._raise(state, size, confidence=0.85,
                                   reason=f"3-bet for value {key}")
            if is_in_range(c1, c2, open_range):
                return Action(ActionType.CALL, call_amount, confidence=0.5,
                              reason=f"call raise with {key}")
            return Action(ActionType.FOLD, reason=f"fold {key} vs raise")

        # --- Facing a 3-bet or larger ---
        if is_in_range(c1, c2, FOUR_BET_RANGE):
            size = self._vary(call_amount * 2.5, 0.10)
            return self._raise(state, size, confidence=0.95,
                               reason=f"4-bet for value {key}")
        if key in _COLD_CALL_VS_3BET:
            return Action(ActionType.CALL, call_amount, confidence=0.7,
                          reason=f"cold-call 3-bet with {key}")
        return Action(ActionType.FOLD, reason=f"fold {key} vs 3-bet")

    # ------------------------------------------------------------------ #
    # Postflop
    # ------------------------------------------------------------------ #
    def postflop_decide(self, state: GameState) -> Action:
        c1, c2 = state.my_cards[0], state.my_cards[1]
        community = state.community_cards
        pot = state.pot
        call_amount = state.call_amount

        equity = monte_carlo_equity(
            [c1, c2], community, villain_count=1, n_sims=_POSTFLOP_SIMS, rng=self.rng
        )
        p_odds = pot_odds(call_amount, pot) if call_amount > 0 else 0.0
        hand = evaluate_hand([c1, c2] + list(community)) if community else None
        category = hand["category"] if hand else 1

        # Category buckets: strong = straight+, medium = trips/two-pair.
        strong = category >= 5
        medium = category in (3, 4)

        flush_draw = has_flush_draw([c1, c2], community)
        straight_draw = has_straight_draw([c1, c2], community)
        is_draw = flush_draw or straight_draw

        logger.debug(
            "Postflop eq={:.3f} odds={:.3f} cat={} strong={} medium={} draw={}",
            equity, p_odds, category, strong, medium, is_draw,
        )

        # --- No bet facing us ---
        if call_amount == 0:
            # Occasional slow-play with a strong hand for deception.
            if strong and self._chance(0.05):
                return Action(ActionType.CHECK, confidence=equity,
                              reason=f"slow-play strong hand (eq {equity:.0%})")

            if equity > 0.70 and strong:
                return self._bet(state, pot * 0.75, confidence=equity,
                                 reason=f"value bet 75% (eq {equity:.0%})")
            if equity > 0.60:
                return self._bet(state, pot * 0.60, confidence=equity,
                                 reason=f"value bet 60% (eq {equity:.0%})")
            if equity > 0.50 and medium:
                return self._bet(state, pot * 0.50, confidence=equity,
                                 reason=f"bet 50% medium (eq {equity:.0%})")
            if is_draw and equity > 0.35 and self._chance(0.40):
                return self._bet(state, pot * 0.55, confidence=equity,
                                 reason=f"semi-bluff 55% (eq {equity:.0%})")
            return Action(ActionType.CHECK, confidence=equity,
                          reason=f"check (eq {equity:.0%})")

        # --- Facing a bet ---
        if equity > p_odds * 1.4 and (strong or medium):
            return self._raise(state, call_amount * 2.2, confidence=equity,
                               reason=f"raise for value 2.2x (eq {equity:.0%})")
        if equity > p_odds * 1.1:
            return Action(ActionType.CALL, call_amount, confidence=equity,
                          reason=f"call (eq {equity:.0%} > odds {p_odds:.0%})")
        if is_draw and equity > p_odds * 0.9:
            return Action(ActionType.CALL, call_amount, confidence=equity,
                          reason=f"call draw (eq {equity:.0%})")
        if equity < p_odds * 0.75:
            return Action(ActionType.FOLD, confidence=1 - equity,
                          reason=f"fold (eq {equity:.0%} << odds {p_odds:.0%})")
        # Marginal spot: small bluff-raise frequency, otherwise fold.
        if self._chance(0.08):
            return self._raise(state, call_amount * 2.2, confidence=0.15,
                               reason="bluff raise (marginal)")
        return Action(ActionType.FOLD, reason=f"fold marginal (eq {equity:.0%})")

    # ------------------------------------------------------------------ #
    # Action builders
    # ------------------------------------------------------------------ #
    def _raise(self, state: GameState, amount: float, confidence: float, reason: str) -> Action:
        return Action(ActionType.RAISE, self._clamp(state, amount), confidence, reason)

    def _bet(self, state: GameState, amount: float, confidence: float, reason: str) -> Action:
        amount = self._vary(amount, 0.10)
        return Action(ActionType.BET, self._clamp(state, amount), confidence, reason)

    def _clamp(self, state: GameState, amount: float) -> float:
        floor = state.big_blind if getattr(state, "big_blind", 0) else 0.0
        amount = max(amount, floor)
        if state.my_stack > 0:
            amount = min(amount, state.my_stack)
        return round(amount, 2)

    def _vary(self, amount: float, pct: float) -> float:
        return max(0.0, amount * (1 + self.rng.uniform(-pct, pct)))

    def _chance(self, probability: float) -> bool:
        return self.rng.random() < probability

    # ------------------------------------------------------------------ #
    # Safety / legalisation
    # ------------------------------------------------------------------ #
    def _passive(self, state: GameState, reason: str) -> Action:
        if state.can(ActionType.CHECK):
            return Action(ActionType.CHECK, reason=reason)
        return Action(ActionType.FOLD, reason=reason)

    def _legalize(self, action: Action, state: GameState) -> Action:
        """Map a decision onto an action the table actually offers."""
        avail = state.available_actions
        if not avail or action.type in avail:
            return action

        t = action.type
        replace = dataclasses.replace

        # Aggressive action: swap BET<->RAISE, else downgrade to call/check/fold.
        if t == ActionType.RAISE and ActionType.BET in avail:
            return replace(action, type=ActionType.BET, reason=action.reason + " (raise->bet)")
        if t == ActionType.BET and ActionType.RAISE in avail:
            return replace(action, type=ActionType.RAISE, reason=action.reason + " (bet->raise)")
        if t in (ActionType.BET, ActionType.RAISE):
            if ActionType.CALL in avail and state.call_amount > 0:
                return replace(action, type=ActionType.CALL, amount=state.call_amount,
                               reason=action.reason + " (aggr->call)")
            if ActionType.CHECK in avail:
                return replace(action, type=ActionType.CHECK, reason=action.reason + " (aggr->check)")
            return replace(action, type=ActionType.FOLD, reason=action.reason + " (aggr->fold)")

        if t == ActionType.CHECK:
            if ActionType.CALL in avail and state.call_amount == 0:
                return replace(action, type=ActionType.CALL, reason=action.reason + " (check->call0)")
            return replace(action, type=ActionType.FOLD, reason=action.reason + " (check->fold)")

        if t == ActionType.CALL:
            if ActionType.CHECK in avail and state.call_amount == 0:
                return replace(action, type=ActionType.CHECK, reason=action.reason + " (call->check)")
            return replace(action, type=ActionType.FOLD, reason=action.reason + " (call->fold)")

        if t == ActionType.FOLD and ActionType.CHECK in avail:
            # Never fold when checking is free.
            return replace(action, type=ActionType.CHECK, reason=action.reason + " (fold->check)")

        return action


__all__ = ["PokerEngine"]
