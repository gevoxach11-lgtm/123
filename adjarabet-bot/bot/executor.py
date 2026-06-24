"""Action executor: translate an :class:`Action` into UI clicks.

All CSS selectors are read from :mod:`config`. Clicks use human-like helpers.
"""

from __future__ import annotations

import asyncio

import config as _default_config
from poker.models import Action, ActionType
from utils.errors import capture_page_error
from utils.human import human_click, human_type, random_delay
from utils.logger import get_logger

logger = get_logger()

_FOLD_WORDS = ["fold", "pass", "\u10e1\u10e4\u10dd\u10da\u10d3\u10d8"]
_CHECK_WORDS = ["check", "\u10e8\u10d4\u10d0\u10db\u10dd\u10ec\u10db\u10d4"]
_CALL_WORDS = ["call", "\u10d2\u10d0\u10d7\u10d0\u10dc\u10d0\u10d1\u10d4\u10d1\u10d0"]
_BET_RAISE_WORDS = ["raise", "bet", "\u10db\u10d0\u10e2\u10d4\u10d1\u10d0", "\u10e4\u10e1\u10dd\u10dc\u10d8"]
_CONFIRM_WORDS = ["confirm", "submit", "ok", "\u10d3\u10d0\u10d3\u10d0\u10e1\u10e2\u10e3\u10e0\u10d4\u10d1\u10d0"]


class ActionExecutor:
    """Execute engine decisions on the live table."""

    def __init__(self, page, config=None, dry_run: bool = False) -> None:
        self.page = page
        self.config = config or _default_config
        self.dry_run = dry_run

    def _timing(self) -> dict:
        return getattr(self.config, "TIMING", {}) or {}

    def _sel(self, key: str) -> str:
        sel = getattr(self.config, "SELECTORS", {}) or {}
        fn = getattr(self.config, "selector", None)
        if callable(fn):
            return fn(key)
        return sel.get(key, key)

    def _selectors(self, list_key: str) -> list[str]:
        fn = getattr(self.config, "selector_list", None)
        if callable(fn):
            return fn(list_key)
        return [self._sel(list_key)]

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
        except Exception as exc:
            await capture_page_error(self.page, self.config, f"executor_{action.type.value}", exc)
            ok = False

        if ok:
            logger.success("Executed: {}", action)
        else:
            logger.warning("Failed to execute: {}", action)
            await capture_page_error(self.page, self.config, f"executor_failed_{action.type.value}")
        return ok

    async def fold(self) -> bool:
        return await self._click_first(_FOLD_WORDS)

    async def check(self) -> bool:
        return await self._click_first(_CHECK_WORDS)

    async def call(self) -> bool:
        return await self._click_first(_CALL_WORDS)

    async def bet_or_raise(self, amount: float) -> bool:
        timing = self._timing()
        buttons = await self.find_visible_buttons(_BET_RAISE_WORDS)
        if not buttons:
            logger.warning("No bet/raise button found")
            return False
        if not await human_click(self.page, buttons[0]):
            return False

        await random_delay(
            timing.get("BET_OPEN_DELAY_MIN", 0.4),
            timing.get("BET_OPEN_DELAY_MAX", 0.6),
        )

        amount_input = await self._find_bet_input()
        if amount_input is not None:
            await self._clear_field(amount_input)
            await human_type(self.page, amount_input, str(round(amount, 2)))
            await asyncio.sleep(timing.get("BET_CONFIRM_DELAY", 0.3))
        else:
            logger.debug("Bet amount input not found; using default sizing")

        confirm = await self.find_visible_buttons(_CONFIRM_WORDS)
        if confirm:
            await human_click(self.page, confirm[0])
        else:
            try:
                await self.page.keyboard.press("Enter")
            except Exception:
                pass
        return True

    async def find_visible_buttons(self, keywords: list[str]) -> list:
        results: list = []
        button_handles = await self._q(self._sel("action_buttons"))
        lowered = [k.lower() for k in keywords]
        for handle in button_handles:
            try:
                text = (await self._text(handle)).lower()
                if text and any(k in text for k in lowered):
                    if await self._usable(handle):
                        results.append(handle)
            except Exception:
                continue

        for keyword in keywords:
            safe = keyword.replace('"', "")
            for css in (
                f'[class*="{safe}" i]',
                f'[data-action="{safe}" i]',
                f'[data-action="{safe}"]',
            ):
                for handle in await self._q(css):
                    if await self._usable(handle):
                        results.append(handle)
        return results

    async def _click_first(self, keywords: list[str]) -> bool:
        buttons = await self.find_visible_buttons(keywords)
        if not buttons:
            logger.warning("No button found for keywords: {}", keywords)
            return False
        return await human_click(self.page, buttons[0])

    async def _find_bet_input(self):
        for css in self._selectors("bet_amount_input"):
            for handle in await self._q(css):
                if await self._usable(handle):
                    return handle
        return None

    async def _clear_field(self, handle) -> None:
        try:
            await handle.click(click_count=3)
            await asyncio.sleep(0.05)
            await self.page.keyboard.press("Backspace")
        except Exception as exc:
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
