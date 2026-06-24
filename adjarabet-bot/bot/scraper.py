"""Table scraper: read the live Adjarabet poker DOM into a :class:`GameState`.

Adjarabet runs a browser-based client (Microgaming / Babelfish). Every game
element lives in HTML - cards, chips, player boxes and action buttons - but the
exact markup is unknown, so this scraper uses several fallback strategies per
field, combining CSS attribute selectors with viewport geometry (the hero seat
sits at the bottom-centre, the board at the vertical centre, etc.).

All public methods are defensive: missing or unparseable data degrades to sane
defaults instead of raising, so the polling loop never crashes on a bad frame.
"""

from __future__ import annotations

import re
from datetime import datetime

import config as _default_config
from poker.evaluator import parse_card
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

_NUM_RE = re.compile(r"[-+]?\d[\d ]*\.?\d*")

_RANK_WORDS = "ace|king|queen|jack|ten|nine|eight|seven|six|five|four|three|two|deuce"
_SUIT_WORDS = "heart|diamond|club|spade"
_SRC_WORD_OF = re.compile(rf"({_RANK_WORDS})\w*[ _\-]*of[ _\-]*({_SUIT_WORDS})")
_SRC_WORD_ADJ = re.compile(rf"({_RANK_WORDS})\w*[ _\-]+({_SUIT_WORDS})")
_SRC_COMPACT = re.compile(r"(?<![a-z])(10|[2-9tjqka])[ _\-]?([hdcs])(?![a-z])")

# Keyword -> canonical action (English + Georgian).
_ACTION_KEYWORDS = {
    "fold": ["fold", "\u10d3\u10d0\u10d9\u10d4\u10ea\u10d5\u10d0"],         # დაკეცვა
    "check": ["check", "\u10d2\u10d0\u10d5\u10da\u10d0"],                    # გავლა
    "call": ["call", "\u10d2\u10d0\u10d7\u10d0\u10dc\u10d0\u10d1\u10d0"],   # გათანაბება
    "bet": ["bet", "\u10e4\u10e1\u10d8"],                                    # ფსონი
    "raise": ["raise", "\u10db\u10d0\u10e2\u10d4\u10d1\u10d0"],             # მატება
}


# --------------------------------------------------------------------------- #
# Module-level parsing helpers (pure / unit-testable)
# --------------------------------------------------------------------------- #
def parse_amount(text: str | None) -> float:
    """Extract a numeric amount from messy UI text like ``'Call \u20be5.00'``."""
    if not text:
        return 0.0
    cleaned = text.replace(",", "").replace("\u00a0", " ")
    match = _NUM_RE.search(cleaned)
    if not match:
        return 0.0
    try:
        return float(match.group().replace(" ", ""))
    except ValueError:
        return 0.0


def parse_card_from_src(src: str | None) -> Card | None:
    """Parse a card from an image src / class / filename.

    Handles ``"card_ah.png"`` (-> A\u2665), ``"ace_of_hearts.svg"`` and
    ``"10h"``. Word forms are tried before the compact form so that strings
    like ``"hearts"`` are not mis-read as ``Ts``.
    """
    if not src:
        return None
    s = src.lower()
    m = _SRC_WORD_OF.search(s) or _SRC_WORD_ADJ.search(s)
    if m:
        card = parse_card(f"{m.group(1)} of {m.group(2)}s")
        if card:
            return card
    m = _SRC_COMPACT.search(s)
    if m:
        card = parse_card(m.group(1) + m.group(2))
        if card:
            return card
    return None


class TableScraper:
    """Read the poker table DOM and build a :class:`GameState` snapshot."""

    def __init__(self, page, config=None) -> None:
        self.page = page
        self.config = config or _default_config
        self._vw = 0
        self._vh = 0

    def _cfg(self, name: str, default=None):
        return getattr(self.config, name, default)

    def _sel(self, key: str) -> str:
        sel = self._cfg("SELECTORS", {}) or {}
        fn = getattr(self.config, "selector", None)
        if callable(fn):
            return fn(key)
        return sel.get(key, key)

    def _selectors(self, list_key: str) -> list[str]:
        fn = getattr(self.config, "selector_list", None)
        if callable(fn):
            return fn(list_key)
        return [self._sel(list_key)]

    def _geo(self, key: str, default: float) -> float:
        return float((self._cfg("GEOMETRY", {}) or {}).get(key, default))

    async def _capture(self, label: str, exc: Exception) -> None:
        from utils.errors import capture_page_error
        await capture_page_error(self.page, self.config, label, exc)

    # ------------------------------------------------------------------ #
    # Top-level collector
    # ------------------------------------------------------------------ #
    async def get_game_state(self) -> GameState | None:
        """Collect all state into a :class:`GameState`, or ``None`` if no table."""
        try:
            await self._refresh_viewport()

            hole = await self.find_hole_cards()
            community = await self.find_community_cards()
            players = await self.find_players()
            pot = await self.find_pot()
            my_stack = await self.find_my_stack()
            call_amount = await self.find_call_amount()
            actions = await self.find_action_buttons()
            my_turn = len(actions) > 0
            position = await self.detect_position()
            round_name = await self.detect_current_round(community)

            if not any([hole, community, players, pot, actions]):
                if not await self._table_present():
                    logger.debug("No poker table detected")
                    if self._cfg("DEBUG", False):
                        await self.dump_debug()
                    return None

            hero = next((p for p in players if p.is_hero), None)
            my_bet = hero.bet if hero else 0.0
            if my_stack <= 0 and hero:
                my_stack = hero.stack

            big_blind = self._big_blind()
            state = GameState(
                hand_id=await self._find_hand_id(),
                my_cards=hole,
                community_cards=community,
                pot=pot,
                my_stack=my_stack,
                my_bet=my_bet,
                call_amount=call_amount,
                players=players,
                round=self._street(round_name),
                position=self._position(position),
                available_actions=[self._action(a) for a in actions if self._action(a)],
                is_my_turn=my_turn,
                time_left=await self._find_time_left(),
                big_blind=big_blind,
            )
            logger.debug("Scraped: {}", state)
            return state
        except Exception as exc:
            await self._capture("scraper_get_game_state", exc)
            return None

    # Backward-compatible alias.
    async def scrape(self) -> GameState | None:
        return await self.get_game_state()

    # ------------------------------------------------------------------ #
    # Cards
    # ------------------------------------------------------------------ #
    async def find_hole_cards(self) -> list[Card]:
        """Find the hero's two hole cards using several fallbacks."""
        await self._ensure_viewport()
        cx = self._vw / 2 if self._vw else 0
        hero_bottom = self._geo("HERO_BOTTOM_RATIO", 0.6)
        hero_x_off = self._geo("HERO_CENTER_X_OFFSET", 300)

        bottom: list[Card] = []
        for handle in await self._els(self._sel("card_generic")):
            if not await self._is_visible(handle):
                continue
            box = await self._box(handle)
            if not box:
                continue
            yc = box["y"] + box["height"] / 2
            xc = box["x"] + box["width"] / 2
            if self._vh and yc >= self._vh * hero_bottom and abs(xc - cx) <= hero_x_off:
                card = await self._card_from_element(handle)
                if card:
                    bottom.append(card)
        cards = self._dedupe(bottom)
        if len(cards) >= 2:
            return cards[:2]

        imgs: list[Card] = []
        for handle in await self._els(self._sel("card_img")):
            if not await self._is_visible(handle):
                continue
            src = await self._attr(handle, "src")
            card = parse_card_from_src(src)
            if card:
                imgs.append(card)
        cards = self._dedupe(imgs)
        if len(cards) >= 2:
            return cards[:2]

        # Strategy 3: explicit data attributes.
        data_cards: list[Card] = []
        for handle in await self._els(self._sel("card_data")):
            card = await self._card_from_data(handle)
            if card:
                data_cards.append(card)
        cards = self._dedupe(data_cards)
        if len(cards) >= 2:
            return cards[:2]

        # Strategy 4: explicit "hole" container.
        hole_cards: list[Card] = []
        for handle in await self._els(self._sel("hole_cards")):
            card = await self._card_from_element(handle)
            if card:
                hole_cards.append(card)
        cards = self._dedupe(hole_cards)
        return cards[:2]

    async def find_community_cards(self) -> list[Card]:
        """Find the 0/3/4/5 community (board) cards."""
        await self._ensure_viewport()
        for css in self._selectors("community_card"):
            found: list[Card] = []
            for handle in await self._els(css):
                if not await self._is_visible(handle):
                    continue
                card = await self._card_from_element(handle)
                if card:
                    found.append(card)
            cards = self._dedupe(found)
            if cards:
                return cards[:5]

        # Geometry fallback: cards near the vertical centre (and not the hero's
        # bottom cards).
        if not self._vh:
            return []
        center = self._vh / 2
        board_y_off = self._geo("BOARD_CENTER_Y_OFFSET", 200)
        hero_bottom = self._geo("HERO_BOTTOM_RATIO", 0.6)
        center_cards: list[Card] = []
        for handle in await self._els(self._sel("card_generic")):
            if not await self._is_visible(handle):
                continue
            box = await self._box(handle)
            if not box:
                continue
            yc = box["y"] + box["height"] / 2
            if abs(yc - center) <= board_y_off and yc < self._vh * hero_bottom:
                card = await self._card_from_element(handle)
                if card:
                    center_cards.append(card)
        return self._dedupe(center_cards)[:5]

    # ------------------------------------------------------------------ #
    # Money
    # ------------------------------------------------------------------ #
    async def find_pot(self) -> float:
        """Read the pot size."""
        await self._ensure_viewport()
        for css in self._selectors("pot_display"):
            for handle in await self._els(css):
                if not await self._is_visible(handle):
                    continue
                amount = parse_amount(await self._text(handle))
                if amount > 0:
                    return amount

        cx, cy = self._vw / 2, self._vh / 2
        pot_x_off = self._geo("POT_CENTER_X_OFFSET", 400)
        pot_y_off = self._geo("POT_CENTER_Y_OFFSET", 300)
        best = 0.0
        for handle in await self._els(self._sel("total_generic")):
            box = await self._box(handle)
            if not box:
                continue
            xc, yc = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            if abs(xc - cx) <= pot_x_off and abs(yc - cy) <= pot_y_off:
                best = max(best, parse_amount(await self._text(handle)))
        if best > 0:
            return best

        # Fallback: any element containing the currency symbol near the centre.
        symbol = self._cfg("CURRENCY_SYMBOL", "\u20be")
        for handle in await self._els(f"xpath=//*[contains(text(), '{symbol}')]"):
            box = await self._box(handle)
            if not box:
                continue
            yc = box["y"] + box["height"] / 2
            if self._vh and abs(yc - self._vh / 2) <= self._geo("BOARD_CENTER_Y_OFFSET", 200):
                amount = parse_amount(await self._text(handle))
                if amount > 0:
                    return amount
        return 0.0

    async def find_my_stack(self) -> float:
        """Read the hero's stack from the bottom of the viewport."""
        await self._ensure_viewport()
        stack_bottom = self._geo("STACK_BOTTOM_RATIO", 0.5)
        for css in self._selectors("stack_display"):
            best = 0.0
            for handle in await self._els(css):
                if not await self._is_visible(handle):
                    continue
                box = await self._box(handle)
                if not box:
                    continue
                yc = box["y"] + box["height"] / 2
                if self._vh and yc >= self._vh * stack_bottom:
                    best = max(best, parse_amount(await self._text(handle)))
            if best > 0:
                return best
        return 0.0

    async def find_call_amount(self) -> float:
        """Read the call amount from the Call button text (0 if none)."""
        for handle in await self._action_button_handles():
            text = (await self._text(handle)).lower()
            if any(kw in text for kw in _ACTION_KEYWORDS["call"]):
                if await self._is_visible(handle) and await self._is_enabled(handle):
                    return parse_amount(text)
        return 0.0

    # ------------------------------------------------------------------ #
    # Players
    # ------------------------------------------------------------------ #
    async def find_players(self) -> list[Player]:
        """Find seated players and their name/stack/bet/active status."""
        await self._ensure_viewport()
        players: list[Player] = []
        seats = await self._els(self._sel("seat"))
        if not seats:
            seats = await self._els(self._sel("player_generic"))

        cx = self._vw / 2 if self._vw else 0
        hero_bottom = self._geo("HERO_BOTTOM_RATIO", 0.6)
        hero_x_off = self._geo("HERO_SEAT_X_OFFSET", 350)
        for i, seat in enumerate(seats):
            klass = (await self._attr(seat, "class") or "").lower()
            if any(tag in klass for tag in ("empty", "vacant", "available")):
                continue
            name = await self._child_text(seat, self._sel("seat_name"))
            stack = parse_amount(
                await self._child_text(seat, self._sel("seat_stack"))
                or await self._child_text(seat, self._sel("seat_chips"))
            )
            bet = parse_amount(await self._child_text(seat, self._sel("seat_bet")))
            is_active = not any(tag in klass for tag in ("fold", "inactive", "sitout"))
            is_dealer = await self._child_exists(seat, self._sel("dealer_generic"))

            is_hero = any(tag in klass for tag in ("hero", "self", "me", "active-player"))
            if not is_hero:
                box = await self._box(seat)
                if box and self._vh:
                    yc = box["y"] + box["height"] / 2
                    xc = box["x"] + box["width"] / 2
                    if yc >= self._vh * hero_bottom and abs(xc - cx) <= hero_x_off:
                        is_hero = True

            players.append(
                Player(
                    seat=i,
                    name=name,
                    stack=stack,
                    bet=bet,
                    is_active=is_active,
                    is_hero=is_hero,
                    is_dealer=is_dealer,
                )
            )
        return players

    # ------------------------------------------------------------------ #
    # Actions / turn
    # ------------------------------------------------------------------ #
    async def find_action_buttons(self) -> list[str]:
        """Return canonical actions whose buttons are visible AND enabled."""
        found: set[str] = set()
        for handle in await self._action_button_handles():
            if not await self._is_visible(handle) or not await self._is_enabled(handle):
                continue
            text = (await self._text(handle)).lower()
            for action, keywords in _ACTION_KEYWORDS.items():
                if any(kw in text for kw in keywords):
                    found.add(action)
        # Preserve a stable, meaningful order.
        order = ["fold", "check", "call", "bet", "raise"]
        return [a for a in order if a in found]

    async def is_my_turn(self) -> bool:
        """True if any fold/check/call action button is visible & enabled."""
        actions = await self.find_action_buttons()
        return any(a in actions for a in ("fold", "check", "call"))

    # ------------------------------------------------------------------ #
    # Position / round
    # ------------------------------------------------------------------ #
    async def detect_position(self) -> str:
        """Estimate the hero's position relative to the dealer button."""
        await self._ensure_viewport()
        seats = await self._els(self._sel("seat"))
        if not seats:
            seats = await self._els(self._sel("player_generic"))
        centers: list[tuple[float, float]] = []
        hero_idx = -1
        dealer_idx = -1
        cx = self._vw / 2 if self._vw else 0
        hero_bottom = self._geo("HERO_BOTTOM_RATIO", 0.6)
        hero_x_off = self._geo("HERO_SEAT_X_OFFSET", 350)

        for i, seat in enumerate(seats):
            box = await self._box(seat)
            if not box:
                centers.append((0.0, 0.0))
                continue
            xc = box["x"] + box["width"] / 2
            yc = box["y"] + box["height"] / 2
            centers.append((xc, yc))
            klass = (await self._attr(seat, "class") or "").lower()
            if hero_idx < 0 and (
                any(t in klass for t in ("hero", "self", "me"))
                or (self._vh and yc >= self._vh * hero_bottom and abs(xc - cx) <= hero_x_off)
            ):
                hero_idx = i
            if await self._child_exists(seat, self._sel("dealer_generic")):
                dealer_idx = i

        n = len(seats)
        if n == 0 or hero_idx < 0 or dealer_idx < 0:
            return "MP"

        # Order seats clockwise around the table centre.
        tcx = sum(c[0] for c in centers) / n
        tcy = sum(c[1] for c in centers) / n
        import math
        angles = sorted(
            range(n),
            key=lambda i: math.atan2(centers[i][1] - tcy, centers[i][0] - tcx),
        )
        order = angles  # clockwise-ish ordering of seat indices
        try:
            d_pos = order.index(dealer_idx)
            h_pos = order.index(hero_idx)
        except ValueError:
            return "MP"
        offset = (h_pos - d_pos) % n

        # Names for seats after the button, 6-max style.
        names = ["BTN", "SB", "BB", "UTG", "MP", "CO"]
        if offset < len(names):
            return names[offset]
        return "MP"

    async def detect_current_round(self, community: list) -> str:
        """Map the number of board cards to the betting round."""
        return {0: "preflop", 3: "flop", 4: "turn", 5: "river"}.get(len(community), "preflop")

    # ------------------------------------------------------------------ #
    # Waiting / debug
    # ------------------------------------------------------------------ #
    async def wait_for_my_turn(self, timeout: int = 120) -> GameState | None:
        """Poll :meth:`is_my_turn` every 0.5s; return state when it's our turn."""
        import asyncio

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        poll = (self._cfg("TIMING", {}) or {}).get("TURN_WAIT_POLL", 0.5)
        while loop.time() < deadline:
            if await self.is_my_turn():
                return await self.get_game_state()
            await asyncio.sleep(poll)
        logger.warning("wait_for_my_turn timed out after {}s", timeout)
        return None

    async def dump_debug(self) -> None:
        """Dump cards/buttons/HTML/screenshot when DEBUG mode is enabled."""
        if not self._cfg("DEBUG", False):
            return
        logger.debug("--- DEBUG DUMP ---")
        try:
            for handle in await self._els(self._sel("card_generic")):
                logger.debug("card el: class={} text={!r}",
                             await self._attr(handle, "class"), await self._text(handle))
            for handle in await self._action_button_handles():
                if await self._is_visible(handle):
                    logger.debug("button: text={!r} enabled={}",
                                 await self._text(handle), await self._is_enabled(handle))
        except Exception as exc:  # pragma: no cover
            logger.warning("debug element dump failed: {}", exc)

        log_dir = self._cfg("LOG_DIR", None)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            html = await self.page.content()
            if log_dir is not None:
                html_path = log_dir / f"debug_{timestamp}.html"
                html_path.write_text(html, encoding="utf-8")
                logger.debug("Saved page HTML -> {}", html_path)
                await self.page.screenshot(path=str(log_dir / f"debug_{timestamp}.png"))
        except Exception as exc:  # pragma: no cover
            logger.warning("debug dump failed: {}", exc)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    async def _refresh_viewport(self) -> None:
        vp = getattr(self.page, "viewport_size", None)
        if isinstance(vp, dict) and vp.get("width") and vp.get("height"):
            self._vw, self._vh = vp["width"], vp["height"]
            return
        try:
            size = await self.page.evaluate(
                "() => ({w: window.innerWidth, h: window.innerHeight})"
            )
            self._vw, self._vh = size["w"], size["h"]
        except Exception:
            self._vw, self._vh = self._vw or 1920, self._vh or 1080

    async def _ensure_viewport(self) -> None:
        """Fetch viewport dimensions lazily if not already known."""
        if not self._vw or not self._vh:
            await self._refresh_viewport()

    def _big_blind(self) -> float:
        getter = getattr(self.config, "big_blind_for", None)
        if callable(getter):
            return getter()
        return 0.10

    async def _table_present(self) -> bool:
        for css in self._selectors("table_present"):
            try:
                if await self.page.locator(css).first.count() > 0:
                    return True
            except Exception:
                continue
        return False

    async def _find_hand_id(self) -> str:
        for css in self._selectors("hand_id"):
            text = await self._first_text(css)
            if text:
                return text.strip()
        return ""

    async def _find_time_left(self) -> float:
        for css in self._selectors("turn_timer"):
            text = await self._first_text(css)
            if text:
                return parse_amount(text)
        return 0.0

    async def _action_button_handles(self) -> list:
        return await self._els(self._sel("action_buttons"))

    async def _card_from_element(self, handle) -> Card | None:
        card = await self._card_from_data(handle)
        if card:
            return card
        # Nested img or self src.
        img = await self._query(handle, "img")
        src = await self._attr(img, "src") if img else await self._attr(handle, "src")
        card = parse_card_from_src(src)
        if card:
            return card
        # Class name often encodes the card, e.g. "card rank-a suit-h".
        card = parse_card_from_src(await self._attr(handle, "class"))
        if card:
            return card
        # Text / aria-label.
        for value in (await self._text(handle), await self._attr(handle, "aria-label"),
                      await self._attr(handle, "title"), await self._attr(handle, "alt")):
            if value:
                card = parse_card(value) or parse_card_from_src(value)
                if card:
                    return card
        return None

    async def _card_from_data(self, handle) -> Card | None:
        rank = await self._attr(handle, "data-rank")
        suit = await self._attr(handle, "data-suit")
        if not rank or not suit:
            return None
        rank = rank.strip().upper()
        if rank == "10":
            rank = "T"
        suit_map = {
            "h": "h", "hearts": "h", "heart": "h",
            "d": "d", "diamonds": "d", "diamond": "d",
            "c": "c", "clubs": "c", "club": "c",
            "s": "s", "spades": "s", "spade": "s",
        }
        suit_char = suit_map.get(suit.strip().lower(), suit.strip().lower()[:1])
        return parse_card(f"{rank}{suit_char}")

    @staticmethod
    def _dedupe(cards: list[Card]) -> list[Card]:
        seen: set[str] = set()
        out: list[Card] = []
        for card in cards:
            code = card.code()
            if code not in seen:
                seen.add(code)
                out.append(card)
        return out

    # --- Enum mapping helpers ---
    @staticmethod
    def _street(name: str) -> Street:
        try:
            return Street(name)
        except ValueError:
            return Street.PREFLOP

    @staticmethod
    def _position(name: str) -> Position:
        try:
            return Position(name)
        except ValueError:
            return Position.UNKNOWN

    @staticmethod
    def _action(name: str) -> ActionType | None:
        try:
            return ActionType(name)
        except ValueError:
            return None

    # --- Low-level Playwright wrappers (never raise) ---
    async def _els(self, selector: str) -> list:
        try:
            return await self.page.query_selector_all(selector)
        except Exception:
            return []

    async def _query(self, handle, selector: str):
        try:
            return await handle.query_selector(selector)
        except Exception:
            return None

    async def _box(self, handle):
        try:
            return await handle.bounding_box()
        except Exception:
            return None

    async def _is_visible(self, handle) -> bool:
        try:
            return await handle.is_visible()
        except Exception:
            return False

    async def _is_enabled(self, handle) -> bool:
        try:
            return await handle.is_enabled()
        except Exception:
            return True

    async def _attr(self, handle, name: str) -> str | None:
        if handle is None:
            return None
        try:
            return await handle.get_attribute(name)
        except Exception:
            return None

    async def _text(self, handle) -> str:
        if handle is None:
            return ""
        try:
            return (await handle.inner_text()).strip()
        except Exception:
            return ""

    async def _first_text(self, selector: str) -> str:
        try:
            locator = self.page.locator(selector).first
            if await locator.count() == 0:
                return ""
            return (await locator.inner_text()).strip()
        except Exception:
            return ""

    async def _child_text(self, parent, selector: str) -> str:
        child = await self._query(parent, selector)
        return await self._text(child)

    async def _child_exists(self, parent, selector: str) -> bool:
        try:
            return await parent.query_selector(selector) is not None
        except Exception:
            return False


__all__ = ["TableScraper", "parse_amount", "parse_card", "parse_card_from_src"]
