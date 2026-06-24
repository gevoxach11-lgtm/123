"""Adjarabet authentication flow.

Handles logging in with credentials from :mod:`config`. Selectors are
placeholders defined in ``config.SELECTORS`` and must be verified against the
live site.
"""

from __future__ import annotations

import config
from utils.human import human_click, human_type, think_delay
from utils.logger import get_logger

logger = get_logger()


class Authenticator:
    """Perform login and verify authenticated state."""

    def __init__(self, page) -> None:
        self.page = page
        self.sel = config.SELECTORS

    async def is_logged_in(self) -> bool:
        """Return True if the logged-in marker is present."""
        try:
            locator = self.page.locator(self.sel["logged_in_marker"]).first
            await locator.wait_for(state="visible", timeout=4000)
            return True
        except Exception:
            return False

    async def login(
        self,
        username: str | None = None,
        password: str | None = None,
    ) -> bool:
        """Log into Adjarabet. Returns True on apparent success."""
        username = username or config.ADJARABET_USERNAME
        password = password or config.ADJARABET_PASSWORD

        if not username or not password:
            logger.error("Missing credentials - set ADJARABET_USERNAME / ADJARABET_PASSWORD in .env")
            return False

        logger.info("Navigating to base site for login")
        await self.page.goto(config.BASE_URL, wait_until="domcontentloaded")
        await think_delay()

        if await self.is_logged_in():
            logger.success("Already logged in")
            return True

        logger.info("Opening login form")
        if not await human_click(self.page, self.sel["login_button"]):
            logger.warning("Login button not found (selector may need updating)")

        await think_delay()

        if not await human_type(self.page, self.sel["username_input"], username):
            logger.error("Username field not found")
            return False
        if not await human_type(self.page, self.sel["password_input"], password):
            logger.error("Password field not found")
            return False

        await think_delay()
        logger.info("Submitting login")
        await human_click(self.page, self.sel["submit_login"])

        # Wait for navigation / marker.
        await think_delay(1.5, 3.0)
        success = await self.is_logged_in()
        if success:
            logger.success("Login successful")
        else:
            logger.error("Login could not be verified")
        return success


__all__ = ["Authenticator"]
