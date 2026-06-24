"""Poker lobby navigation and table selection.

Navigates to the poker section, filters cash tables by the configured limit and
joins a suitable table. Selectors live in ``config.SELECTORS``.
"""

from __future__ import annotations

from dataclasses import dataclass

import config as _default_config
from utils.human import human_click, think_delay
from utils.logger import get_logger

logger = get_logger()


@dataclass
class TableInfo:
    """Lightweight description of a lobby table row."""

    name: str
    stakes: str
    players: str
    index: int


class LobbyNavigator:
    """Navigate the poker lobby and seat the bot at a table."""

    def __init__(self, page, config=None) -> None:
        self.page = page
        self.config = config or _default_config
        self.sel = getattr(self.config, "SELECTORS", {}) or _default_config.SELECTORS

    async def open_poker(self) -> bool:
        """Navigate to the poker lobby."""
        logger.info("Opening poker lobby")
        try:
            await self.page.goto(self.config.POKER_URL, wait_until="domcontentloaded")
        except Exception as exc:
            logger.warning("Direct poker URL nav failed ({}); trying menu", exc)
            if not await human_click(self.page, self.sel["poker_menu"]):
                return False
        await think_delay()
        # Try to switch to cash games.
        await human_click(self.page, self.sel["cash_games_tab"])
        await think_delay()
        return True

    async def list_tables(self) -> list[TableInfo]:
        """Read available cash-table rows from the lobby."""
        tables: list[TableInfo] = []
        rows = self.page.locator(self.sel["table_row"])
        try:
            count = await rows.count()
        except Exception:
            count = 0

        for i in range(count):
            row = rows.nth(i)
            name = await self._safe_text(row.locator(self.sel["table_name"]))
            stakes = await self._safe_text(row.locator(self.sel["table_stakes"]))
            players = await self._safe_text(row.locator(self.sel["table_players"]))
            tables.append(TableInfo(name=name, stakes=stakes, players=players, index=i))

        logger.info("Found {} tables in lobby", len(tables))
        return tables

    async def pick_table(self, limit: str | None = None) -> TableInfo | None:
        """Choose a table matching the configured stake limit.

        Falls back to the first available table if no exact match is found.
        """
        limit = (limit or self.config.TABLE_LIMIT).upper()
        tables = await self.list_tables()
        if not tables:
            logger.warning("No tables visible in lobby")
            return None

        for table in tables:
            if limit in table.stakes.upper() or limit in table.name.upper():
                logger.success("Selected table matching {}: {}", limit, table.name)
                return table

        logger.warning("No table matched {}; defaulting to first table", limit)
        return tables[0]

    async def navigate_to_poker(self) -> bool:
        """Alias for :meth:`open_poker` (spec-friendly name)."""
        return await self.open_poker()

    async def join_table(self, table: "TableInfo | str | None" = None) -> bool:
        """Join a table and take an open seat.

        ``table`` may be a :class:`TableInfo`, a stake-limit string (e.g.
        ``"NL10"``) or ``None`` (uses the configured limit). Strings/None are
        resolved to a concrete table via :meth:`pick_table`.
        """
        if not isinstance(table, TableInfo):
            table = await self.pick_table(table if isinstance(table, str) else None)
            if not table:
                logger.warning("No table available to join")
                return False
        logger.info("Joining table: {}", table.name)
        rows = self.page.locator(self.sel["table_row"])
        row = rows.nth(table.index)
        # Click the join button within the row, falling back to the row itself.
        join = row.locator(self.sel["join_table_button"])
        try:
            if await join.count() > 0:
                await join.first.click()
            else:
                await row.click()
        except Exception as exc:
            logger.error("Failed to click join: {}", exc)
            return False

        await think_delay(1.0, 2.5)
        # Take a seat if a seat-selection prompt appears.
        await human_click(self.page, self.sel["take_seat_button"])
        await think_delay()
        logger.success("Seated at table: {}", table.name)
        return True

    async def find_and_join(self, limit: str | None = None) -> bool:
        """Convenience: open poker, pick a table and sit down."""
        if not await self.open_poker():
            return False
        table = await self.pick_table(limit)
        if not table:
            return False
        return await self.join_table(table)

    @staticmethod
    async def _safe_text(locator) -> str:
        try:
            if await locator.count() == 0:
                return ""
            text = await locator.first.inner_text()
            return text.strip()
        except Exception:
            return ""


# Spec-friendly alias.
LobbyManager = LobbyNavigator


__all__ = ["LobbyNavigator", "LobbyManager", "TableInfo"]
