"""Action executor: translate an :class:`Action` into UI clicks.

Clicks the fold / check / call / bet / raise controls using the human-like
interaction helpers (curved mouse paths, randomised timing, per-character
typing). Bet/raise sizing is entered into the amount input and confirmed.

Buttons are located by hunting for matching text, class fragments or
``data-action`` attributes, since the live markup is unknown.
"""

from __future__ import annotations

import asyncio

import config as _default_config
from poker.models import Action, ActionType
from utils.human import human_click, human_type, random_delay
from utils.logger import get_logger

logger = get_logger()

# Keyword sets per action (English + Georgian).
_FOLD_WORDS = ["fold", "pass", "\u10e1\u10e4\u10dd\u10da\u10d3\u10d8"]          # სფოლდი
_CHECK_WORDS = ["check", "\u10e8\u10d4\u10d0\u10db\u10dd\u10ec\u10db\u10d4"]    # შეამოწმე
_CALL_WORDS = ["call", "\u10d2\u10d0\u10d7\u10d0\u10dc\u10d0\u10d1\u10d4\u10d1\u10d0"]
_BET_RAISE_WORDS = ["raise", "bet", "\u10db\u10d0\u10e2\u10d4\u10d1\u10d0", "\u10e4\u10e1\u10dd\u10dc\u10d8"]
_CONFIRM_WORDS = ["confirm", "submit", "ok", "\u10d3\u10d0\u10d3\u10d0\u10e1\u10e2\u10e3\u10e0\u10d4\u10d1\u10d0"]

_BET_INPUT_SELECTORS = [
    'input[type="number"]',
    'input[type="text"]',
    '[class*="amount" i] input',
    '[class*="bet-input" i]',
    'input[class*="amount" i]',
    'input[class*="bet" i]',
]


class ActionExecutor:
    """Execute engine decisions on the live table."""

    def __init__(self, page, config=None, dry_run: bool = False) -> None:
        self.page = page
        self.config = config or _default_config
        self.dry_run = dry_run

    def _timing(self) -> dict:
        return getattr(self.config, "TIMING", {}) or {}

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    async def execute(self, action: Action, state=None) -> bool:
        """Perform the action after a human-like think delay."""
        timing = self._timing()
        await random_delay(timing.get("MIN_DELAY", 0.8), timing.get("MAX_DELAY", 2.5))

        if self.dry_run:
            logger.info("[DRY-RUN] Would execute: {}", action)
            return True

        try:
            if action.type == ActionType.FOLD:
                ok = await self.fold()
            elif action.type == ActionType.CHECK:
                ok = await self.check()
            elif action.type == ActionType.CALL:
                ok = await self.call()
            elif action.type in (ActionType.BET, ActionType.RAISE):
                ok = await self.bet_or_raise(action.amount)
            else:
                logger.warning("Unknown action type: {}", action.type)
                ok = False
        except Exception as exc:  # pragma: no cover - runtime safety
            logger.exception("Executor error for {}: {}", action, exc)
            ok = False

        if ok:
            logger.success("Executed: {}", action)
        else:
            logger.warning("Failed to execute: {}", action)
        return ok

    # ------------------------------------------------------------------ #
    # Simple actions
    # ------------------------------------------------------------------ #
    async def fold(self) -> bool:
        return await self._click_first(_FOLD_WORDS)

    async def check(self) -> bool:
        return await self._click_first(_CHECK_WORDS)

    async def call(self) -> bool:
        return await self._click_first(_CALL_WORDS)

    async def bet_or_raise(self, amount: float) -> bool:
        """Click bet/raise, enter the amount, then confirm."""
        buttons = await self.find_visible_buttons(_BET_RAISE_WORDS)
        if not buttons:
            logger.warning("No bet/raise button found")
            return False
        if not await human_click(self.page, buttons[0]):
            return False

        await random_delay(0.4, 0.6)

        amount_input = await self._find_bet_input()
        if amount_input is not None:
            await self._clear_field(amount_input)
            await human_type(self.page, amount_input, str(round(amount, 2)))
            await asyncio.sleep(0.3)
        else:
            logger.debug("Bet amount input not found; using default sizing")

        # Confirm via a confirm/submit button, else press Enter.
        confirm = await self.find_visible_buttons(_CONFIRM_WORDS)
        if confirm:
            await human_click(self.page, confirm[0])
        else:
            try:
                await self.page.keyboard.press("Enter")
            except Exception:
                pass
        return True

    # ------------------------------------------------------------------ #
    # Button hunting
    # ------------------------------------------------------------------ #
    async def find_visible_buttons(self, keywords: list[str]) -> list:
        """Return visible+enabled elements matching any keyword.

        Searches by (1) button text, (2) ``[class*=keyword]`` and
        (3) ``[data-action=keyword]``.
        """
        results: list = []

        # (1) Buttons whose text matches a keyword.
        button_handles = await self._q(
            'button, [role="button"], [class*="btn" i], [class*="action" i] button'
        )
        lowered = [k.lower() for k in keywords]
        for handle in button_handles:
            text = (await self._text(handle)).lower()
            if text and any(k in text for k in lowered):
                if await self._usable(handle):
                    results.append(handle)

        # (2) + (3) Class fragment / data-action selectors.
        for keyword in keywords:
            safe = keyword.replace('"', "")
            for selector in (
                f'[class*="{safe}" i]',
                f'[data-action="{safe}" i]',
                f'[data-action="{safe}"]',
            ):
                for handle in await self._q(selector):
                    if await self._usable(handle):
                        results.append(handle)

        return results

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    async def _click_first(self, keywords: list[str]) -> bool:
        buttons = await self.find_visible_buttons(keywords)
        if not buttons:
            logger.warning("No button found for keywords: {}", keywords)
            return False
        return await human_click(self.page, buttons[0])

    async def _find_bet_input(self):
        for selector in _BET_INPUT_SELECTORS:
            for handle in await self._q(selector):
                if await self._usable(handle):
                    return handle
        return None

    async def _clear_field(self, handle) -> None:
        """Select all (triple-click) and delete existing content."""
        try:
            await handle.click(click_count=3)
            await asyncio.sleep(0.05)
            await self.page.keyboard.press("Backspace")
        except Exception as exc:  # pragma: no cover
            logger.debug("Field clear failed: {}", exc)

    async def _q(self, selector: str) -> list:
        try:
            return await self.page.query_selector_all(selector)
        except Exception:
            return []

    async def _text(self, handle) -> str:
        try:
            return (await handle.inner_text()).strip()
        except Exception:
            return ""

    async def _usable(self, handle) -> bool:
        try:
            return await handle.is_visible() and await handle.is_enabled()
        except Exception:
            return False


__all__ = ["ActionExecutor"]
