"""Action executor: translate an :class:`Action` into UI clicks.

Clicks the fold/check/call/bet/raise buttons using the human-like interaction
helpers so timing and mouse paths look natural. Bet/raise sizing is entered via
the bet amount input (or slider) when present.
"""

from __future__ import annotations

import config
from poker.models import Action, ActionType, GameState
from utils.human import human_click, human_type, short_pause, think_delay
from utils.logger import get_logger

logger = get_logger()


class ActionExecutor:
    """Execute engine decisions on the live table."""

    def __init__(self, page, dry_run: bool = False) -> None:
        self.page = page
        self.sel = config.SELECTORS
        self.dry_run = dry_run

    async def execute(self, action: Action, state: GameState) -> bool:
        """Perform the action. Returns True if a button was clicked."""
        # Human-like thinking time before acting.
        await think_delay()

        if self.dry_run:
            logger.info("[DRY-RUN] Would execute: {}", action)
            return True

        if action.type == ActionType.FOLD:
            return await self._click(self.sel["fold_button"], action)
        if action.type == ActionType.CHECK:
            return await self._click(self.sel["check_button"], action)
        if action.type == ActionType.CALL:
            return await self._click(self.sel["call_button"], action)
        if action.type in (ActionType.BET, ActionType.RAISE):
            return await self._bet_or_raise(action)

        logger.warning("Unknown action type: {}", action.type)
        return False

    async def _bet_or_raise(self, action: Action) -> bool:
        """Enter a bet/raise amount then confirm."""
        # Set the amount via the input field if available.
        amount_text = self._format_amount(action.amount)
        typed = await human_type(self.page, self.sel["bet_amount_input"], amount_text)
        if typed:
            await short_pause()
        else:
            logger.debug("Bet amount input not found; relying on default sizing")

        # Click the bet or raise button.
        button = self.sel["raise_button"] if action.type == ActionType.RAISE else self.sel["bet_button"]
        clicked = await human_click(self.page, button)
        if not clicked:
            logger.warning("{} button not found", action.type.value)
            return False

        # Some clients require a separate confirm click.
        await short_pause()
        await human_click(self.page, self.sel["bet_confirm_button"], timeout=2.0)
        logger.success("Executed: {}", action)
        return True

    async def _click(self, selector: str, action: Action) -> bool:
        clicked = await human_click(self.page, selector)
        if clicked:
            logger.success("Executed: {}", action)
        else:
            logger.warning("Could not click {} for action {}", selector, action)
        return clicked

    @staticmethod
    def _format_amount(amount: float) -> str:
        # Trim trailing zeros for cleaner input (e.g. "5" instead of "5.00").
        if amount == int(amount):
            return str(int(amount))
        return f"{amount:.2f}"


__all__ = ["ActionExecutor"]
