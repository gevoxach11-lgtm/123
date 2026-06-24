"""Poker decision engine.

Turns a :class:`GameState` into an :class:`Action`. The strategy combines:

* Preflop: position-aware GTO-ish opening ranges (:mod:`poker.ranges`).
* Postflop: Monte-Carlo equity (:mod:`poker.montecarlo`) compared against pot
  odds, with simple value-betting and bluff-control heuristics.

The logic is deliberately transparent and conservative - it is a solid baseline
"TAG" (tight-aggressive) strategy, not a solver. Every decision carries a
``reason`` string for auditability in the logs and dashboard.
"""

from __future__ import annotations

import random

from poker.models import Action, ActionType, GameState, Street
from poker import ranges
from poker.montecarlo import estimate_equity
from utils.logger import get_logger

logger = get_logger()


class PokerEngine:
    """Compute the bot's action for a given game state."""

    def __init__(
        self,
        mc_iterations: int = 3000,
        aggression: float = 1.0,
        rng: random.Random | None = None,
    ) -> None:
        self.mc_iterations = mc_iterations
        self.aggression = aggression
        self.rng = rng or random.Random()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def decide(self, state: GameState) -> Action:
        """Return the engine's chosen action for the current state."""
        if not state.is_my_turn or not state.available_actions:
            return Action(ActionType.CHECK, reason="not my turn")

        if state.round == Street.PREFLOP:
            action = self._decide_preflop(state)
        else:
            action = self._decide_postflop(state)

        action = self._validate(action, state)
        logger.info("Decision: {} | {}", action, state)
        return action

    # ------------------------------------------------------------------ #
    # Preflop
    # ------------------------------------------------------------------ #
    def _decide_preflop(self, state: GameState) -> Action:
        if len(state.my_cards) < 2:
            return self._safe_passive(state, "no hole cards read")

        cards = state.my_cards
        facing_bet = state.call_amount > 0
        bb = state.big_blind or 0.10

        is_premium = ranges.is_three_bet(cards)
        in_open = ranges.in_opening_range(cards, state.position)
        can_call = ranges.in_calling_range(cards)
        strength = ranges.hand_strength_score(cards)

        if not facing_bet:
            # Unopened pot - open-raise in range, else check/fold.
            if in_open:
                size = self._raise_size(state, base_bb=3.0)
                return Action(ActionType.RAISE, size, confidence=strength,
                              reason=f"open-raise {ranges.hand_notation(cards)} from {state.position.value}")
            if state.can(ActionType.CHECK):
                return Action(ActionType.CHECK, reason="check option in BB")
            return Action(ActionType.FOLD, reason="out of opening range")

        # Facing a bet/raise.
        if is_premium:
            size = self._raise_size(state, base_bb=9.0)
            return Action(ActionType.RAISE, size, confidence=0.9,
                          reason=f"3-bet for value {ranges.hand_notation(cards)}")

        if in_open or can_call:
            # Reasonable hand - call if price is right.
            if self._price_ok(state, required_equity=0.35):
                return Action(ActionType.CALL, state.call_amount, confidence=strength,
                              reason=f"call {ranges.hand_notation(cards)} (price ok)")
        return Action(ActionType.FOLD, reason=f"fold {ranges.hand_notation(cards)} preflop")

    # ------------------------------------------------------------------ #
    # Postflop
    # ------------------------------------------------------------------ #
    def _decide_postflop(self, state: GameState) -> Action:
        if len(state.my_cards) < 2:
            return self._safe_passive(state, "no hole cards read")

        opponents = max(1, state.num_active - 1)
        result = estimate_equity(
            state.my_cards,
            state.community_cards,
            num_opponents=opponents,
            iterations=self.mc_iterations,
            rng=self.rng,
        )
        equity = result.equity
        facing_bet = state.call_amount > 0
        pot_odds = state.pot_odds

        logger.debug(
            "Postflop equity={:.3f} potOdds={:.3f} opp={} street={}",
            equity, pot_odds, opponents, state.round.value,
        )

        # Strong hand -> bet/raise for value.
        value_threshold = 0.66
        bluff_threshold = 0.30

        if not facing_bet:
            if equity >= value_threshold and state.can(ActionType.BET):
                size = self._bet_size(state, fraction=0.66)
                return Action(ActionType.BET, size, confidence=equity,
                              reason=f"value bet (eq {equity:.0%})")
            # Semi-bluff occasionally with low-equity hands when it's cheap.
            if equity < bluff_threshold and state.can(ActionType.BET) and self._bluff_now(0.20):
                size = self._bet_size(state, fraction=0.5)
                return Action(ActionType.BET, size, confidence=0.2,
                              reason=f"semi-bluff (eq {equity:.0%})")
            if state.can(ActionType.CHECK):
                return Action(ActionType.CHECK, confidence=equity,
                              reason=f"check (eq {equity:.0%})")
            return self._safe_passive(state, "no check available")

        # Facing a bet: compare equity to pot odds.
        if equity >= value_threshold and state.can(ActionType.RAISE):
            size = self._raise_size(state, base_bb=0.0, fraction=0.75)
            return Action(ActionType.RAISE, size, confidence=equity,
                          reason=f"raise for value (eq {equity:.0%})")

        if equity >= pot_odds + 0.02:  # small margin to cover rake/variance
            return Action(ActionType.CALL, state.call_amount, confidence=equity,
                          reason=f"call (eq {equity:.0%} > odds {pot_odds:.0%})")

        if state.can(ActionType.CHECK):
            return Action(ActionType.CHECK, confidence=equity, reason="check back")
        return Action(ActionType.FOLD, confidence=1 - equity,
                      reason=f"fold (eq {equity:.0%} < odds {pot_odds:.0%})")

    # ------------------------------------------------------------------ #
    # Sizing helpers
    # ------------------------------------------------------------------ #
    def _raise_size(self, state: GameState, base_bb: float = 3.0, fraction: float = 0.0) -> float:
        """Total raise size in chips."""
        bb = state.big_blind or 0.10
        if fraction > 0:
            target = state.call_amount + (state.pot + state.call_amount) * fraction
        else:
            target = base_bb * bb + state.call_amount
        target *= self.aggression
        return self._clamp_bet(state, target)

    def _bet_size(self, state: GameState, fraction: float = 0.66) -> float:
        target = max(state.big_blind, state.pot * fraction) * self.aggression
        return self._clamp_bet(state, target)

    def _clamp_bet(self, state: GameState, amount: float) -> float:
        """Never bet more than the stack; round to a sensible precision."""
        amount = max(state.big_blind, amount)
        amount = min(amount, state.my_stack) if state.my_stack > 0 else amount
        return round(amount, 2)

    # ------------------------------------------------------------------ #
    # Misc helpers
    # ------------------------------------------------------------------ #
    def _price_ok(self, state: GameState, required_equity: float) -> bool:
        """Quick pot-odds gate for preflop calls."""
        if state.call_amount <= 0:
            return True
        return state.pot_odds <= required_equity

    def _bluff_now(self, probability: float) -> bool:
        return self.rng.random() < probability

    def _safe_passive(self, state: GameState, reason: str) -> Action:
        if state.can(ActionType.CHECK):
            return Action(ActionType.CHECK, reason=reason)
        return Action(ActionType.FOLD, reason=reason)

    def _validate(self, action: Action, state: GameState) -> Action:
        """Ensure the chosen action is actually available; degrade if not."""
        if state.can(action.type):
            return action
        # Map unavailable actions to the safest legal alternative.
        if action.type == ActionType.RAISE and state.can(ActionType.BET):
            return Action(ActionType.BET, action.amount, action.confidence,
                          reason=action.reason + " (raise->bet)")
        if action.type == ActionType.BET and state.can(ActionType.RAISE):
            return Action(ActionType.RAISE, action.amount, action.confidence,
                          reason=action.reason + " (bet->raise)")
        if action.type in (ActionType.BET, ActionType.RAISE) and state.can(ActionType.CALL):
            return Action(ActionType.CALL, state.call_amount, action.confidence,
                          reason=action.reason + " (aggr->call)")
        return self._safe_passive(state, action.reason + " (fallback)")


__all__ = ["PokerEngine"]
