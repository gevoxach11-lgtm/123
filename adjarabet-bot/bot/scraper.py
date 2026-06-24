"""Table scraper: read the live DOM into a :class:`GameState`.

The scraper translates the poker client's HTML into the structured models the
engine understands. Selectors are placeholders in ``config.SELECTORS``; the
parsing logic is defensive so missing/garbled data degrades gracefully rather
than crashing the loop.
"""

from __future__ import annotations

import re

import config
from poker.models import (
    ActionType,
    Card,
    GameState,
    Player,
    Position,
    Street,
)
from utils.logger import get_logger

logger = get_logger()

_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def parse_amount(text: str | None) -> float:
    """Extract a numeric amount from messy UI text like ``'$1,234.50'``."""
    if not text:
        return 0.0
    match = _NUM_RE.search(text.replace(",", ""))
    if not match:
        return 0.0
    try:
        return float(match.group())
    except ValueError:
        return 0.0


def parse_card(text: str | None) -> Card | None:
    """Parse a card from text/attribute such as ``'Ah'`` or ``'10s'``."""
    if not text:
        return None
    try:
        return Card.from_str(text)
    except Exception:
        return None


class TableScraper:
    """Read the poker table DOM and build a GameState snapshot."""

    def __init__(self, page) -> None:
        self.page = page
        self.sel = config.SELECTORS
        self.big_blind = config.big_blind_for()

    async def scrape(self) -> GameState:
        """Return a best-effort snapshot of the current table state."""
        state = GameState(big_blind=self.big_blind)

        state.hand_id = await self._text(self.sel["hand_id"])
        state.pot = parse_amount(await self._text(self.sel["pot_size"]))
        state.my_stack = parse_amount(await self._text(self.sel["my_stack"]))
        state.my_bet = parse_amount(await self._text(self.sel["my_bet"]))

        state.my_cards = await self._cards(self.sel["my_hole_card"])
        state.community_cards = await self._cards(self.sel["community_card"])
        state.round = self._street_from_board(len(state.community_cards))

        state.players = await self._players()
        state.position = self._derive_hero_position(state.players)

        state.available_actions = await self._available_actions()
        state.call_amount = parse_amount(await self._text(self.sel["call_button"]))
        state.is_my_turn = len(state.available_actions) > 0
        state.time_left = parse_amount(await self._text(self.sel["turn_timer"]))

        logger.debug("Scraped state: {}", state)
        return state

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    async def _text(self, selector: str) -> str:
        try:
            locator = self.page.locator(selector).first
            if await locator.count() == 0:
                return ""
            return (await locator.inner_text()).strip()
        except Exception:
            return ""

    async def _cards(self, selector: str) -> list[Card]:
        cards: list[Card] = []
        try:
            locator = self.page.locator(selector)
            count = await locator.count()
        except Exception:
            return cards
        for i in range(count):
            el = locator.nth(i)
            # Card value may be in text or a data attribute.
            text = ""
            try:
                text = (await el.inner_text()).strip()
            except Exception:
                pass
            if not text:
                for attr in ("data-card", "data-value", "alt", "title"):
                    try:
                        val = await el.get_attribute(attr)
                    except Exception:
                        val = None
                    if val:
                        text = val
                        break
            card = parse_card(text)
            if card:
                cards.append(card)
        return cards

    async def _players(self) -> list[Player]:
        players: list[Player] = []
        try:
            seats = self.page.locator(self.sel["seat"])
            count = await seats.count()
        except Exception:
            return players
        for i in range(count):
            seat = seats.nth(i)
            name = await self._child_text(seat, self.sel["seat_name"])
            stack = parse_amount(await self._child_text(seat, self.sel["seat_stack"]))
            bet = parse_amount(await self._child_text(seat, self.sel["seat_bet"]))
            is_dealer = await self._child_exists(seat, self.sel["dealer_button"])
            klass = await self._attr(seat, "class")
            is_active = "folded" not in (klass or "").lower()
            is_hero = "hero" in (klass or "").lower()
            players.append(
                Player(
                    seat=i,
                    name=name,
                    stack=stack,
                    bet=bet,
                    is_active=is_active,
                    is_dealer=is_dealer,
                    is_hero=is_hero,
                )
            )
        return players

    async def _available_actions(self) -> list[ActionType]:
        actions: list[ActionType] = []
        mapping = {
            ActionType.FOLD: self.sel["fold_button"],
            ActionType.CHECK: self.sel["check_button"],
            ActionType.CALL: self.sel["call_button"],
            ActionType.BET: self.sel["bet_button"],
            ActionType.RAISE: self.sel["raise_button"],
        }
        for action, selector in mapping.items():
            if await self._is_actionable(selector):
                actions.append(action)
        return actions

    async def _is_actionable(self, selector: str) -> bool:
        try:
            locator = self.page.locator(selector).first
            if await locator.count() == 0:
                return False
            if not await locator.is_visible():
                return False
            return await locator.is_enabled()
        except Exception:
            return False

    def _derive_hero_position(self, players: list[Player]) -> Position:
        """Approximate hero position from seat offset relative to the button."""
        hero = next((p for p in players if p.is_hero), None)
        dealer = next((p for p in players if p.is_dealer), None)
        if not hero or not dealer or not players:
            return Position.UNKNOWN
        n = len(players)
        offset = (hero.seat - dealer.seat) % n
        # 6-max position mapping by seats after the button.
        order = [
            Position.BTN, Position.SB, Position.BB,
            Position.UTG, Position.MP, Position.CO,
        ]
        if offset < len(order):
            return order[offset]
        return Position.UNKNOWN

    @staticmethod
    def _street_from_board(num_board: int) -> Street:
        return {
            0: Street.PREFLOP,
            3: Street.FLOP,
            4: Street.TURN,
            5: Street.RIVER,
        }.get(num_board, Street.PREFLOP)

    @staticmethod
    async def _child_text(parent, selector: str) -> str:
        try:
            loc = parent.locator(selector).first
            if await loc.count() == 0:
                return ""
            return (await loc.inner_text()).strip()
        except Exception:
            return ""

    @staticmethod
    async def _child_exists(parent, selector: str) -> bool:
        try:
            return await parent.locator(selector).count() > 0
        except Exception:
            return False

    @staticmethod
    async def _attr(locator, name: str) -> str | None:
        try:
            return await locator.get_attribute(name)
        except Exception:
            return None


__all__ = ["TableScraper", "parse_amount", "parse_card"]
