"""Poker domain logic: models, hand evaluation, equity and ranges."""

from .models import (
    Action,
    ActionType,
    Card,
    GameState,
    Player,
    Position,
    SessionStats,
    Street,
)
from .evaluator import (
    RANK_VALUES,
    SUIT_NAMES,
    HandRank,
    HAND_NAMES,
    best_score,
    compare,
    describe,
    evaluate,
    evaluate_hand,
    hand_key,
    hand_rank,
    parse_card,
    score_five,
)
from .montecarlo import (
    EquityResult,
    equity_pct,
    estimate_equity,
    full_deck,
    has_flush_draw,
    has_straight_draw,
    monte_carlo_equity,
    pot_odds,
    remove_cards,
)
from .ranges import (
    CALLING_RANGE,
    FOUR_BET_RANGE,
    OPEN_RANGES,
    THREE_BET_RANGES,
    hand_notation,
    hand_strength_score,
    in_calling_range,
    in_opening_range,
    is_four_bet,
    is_in_range,
    is_three_bet,
)

__all__ = [
    # models
    "Action", "ActionType", "Card", "GameState", "Player", "Position",
    "SessionStats", "Street",
    # evaluator
    "RANK_VALUES", "SUIT_NAMES", "HandRank", "HAND_NAMES", "best_score",
    "compare", "describe", "evaluate", "evaluate_hand", "hand_key",
    "hand_rank", "parse_card", "score_five",
    # montecarlo
    "EquityResult", "equity_pct", "estimate_equity", "full_deck",
    "has_flush_draw", "has_straight_draw", "monte_carlo_equity", "pot_odds",
    "remove_cards",
    # ranges
    "CALLING_RANGE", "FOUR_BET_RANGE", "OPEN_RANGES", "THREE_BET_RANGES",
    "hand_notation", "hand_strength_score", "in_calling_range",
    "in_opening_range", "is_four_bet", "is_in_range", "is_three_bet",
]
