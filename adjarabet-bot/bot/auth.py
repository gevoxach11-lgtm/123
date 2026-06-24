"""Adjarabet authentication flow.

:class:`AuthManager` logs into the site using credentials from the supplied
config, with human-like typing and resilient multi-selector lookups (the live
markup is unknown, so several candidate selectors are tried for each element).
"""

from __future__ import annotations

import asyncio

import config as _default_config
from utils.human import human_click, human_type, think_delay
from utils.logger import get_logger

logger = get_logger()

# Candidate selectors - the first visible match wins.
LOGIN_BUTTON_SELECTORS = [
    'button:has-text("\u10e8\u10d4\u10e1\u10d5\u10da\u10d0")',  # "შესვლა" (log in)
    '[data-testid="login"]',
    '[class*="login" i] button',
    'button:has-text("Login")',
    'a:has-text("Login")',
    'button:has-text("Sign in")',
]

USERNAME_SELECTORS = [
    'input[name="username"]',
    'input[name="email"]',
    'input[name="login"]',
    'input[autocomplete="username"]',
    'input[type="text"]',
    "#username",
]

PASSWORD_SELECTORS = [
    'input[name="password"]',
    'input[autocomplete="current-password"]',
    'input[type="password"]',
    "#password",
]

SUBMIT_SELECTORS = [
    'button:has-text("\u10e8\u10d4\u10e1\u10d5\u10da\u10d0")',  # "შესვლა"
    'button[type="submit"]',
    '[data-testid="login-submit"]',
    'button:has-text("Login")',
    'button:has-text("Sign in")',
]

BALANCE_SELECTORS = [
    ".balance",
    '[class*="balance"]',
    '[class*="user-name"]',
    '[class*="profile"]',
]

ERROR_SELECTORS = [
    '[class*="error"]',
    '[role="alert"]',
    ".error",
    ".form-error",
    '[class*="invalid"]',
]

LOGIN_TIMEOUT_SECONDS = 30.0


class AuthManager:
    """Perform login and verify authenticated state."""

    def __init__(self, page, config=None) -> None:
        self.page = page
        self.config = config or _default_config

    def _cfg(self, name: str, default=None):
        return getattr(self.config, name, default)

    # ------------------------------------------------------------------ #
    # State checks
    # ------------------------------------------------------------------ #
    async def is_logged_in(self) -> bool:
        """Return True if a balance / profile element is visible."""
        return await self._any_visible(BALANCE_SELECTORS, timeout_ms=1500)

    async def _any_visible(self, selectors: list[str], timeout_ms: int = 1500) -> bool:
        for selector in selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() == 0:
                    continue
                await locator.wait_for(state="visible", timeout=timeout_ms)
                return True
            except Exception:
                continue
        return False

    async def _error_text(self) -> str | None:
        for selector in ERROR_SELECTORS:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() == 0:
                    continue
                if await locator.is_visible():
                    text = (await locator.inner_text()).strip()
                    if text:
                        return text
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    async def _click_first(self, selectors: list[str], timeout: float = 5.0) -> bool:
        for selector in selectors:
            if await human_click(self.page, selector, timeout=timeout):
                logger.debug("Clicked selector: {}", selector)
                return True
        return False

    async def _type_first(self, selectors: list[str], text: str, timeout: float = 5.0) -> bool:
        for selector in selectors:
            if await human_type(self.page, selector, text, timeout=timeout):
                logger.debug("Typed into selector: {}", selector)
                return True
        return False

    async def login(self) -> bool:
        """Log into Adjarabet. Returns True on verified success."""
        username = self._cfg("ADJARABET_USERNAME", "")
        password = self._cfg("ADJARABET_PASSWORD", "")
        if not username or not password:
            logger.error("Missing credentials - set ADJARABET_USERNAME / ADJARABET_PASSWORD in .env")
            return False

        base_url = self._cfg("BASE_URL", "https://www.adjarabet.am") or "https://www.adjarabet.am"
        logger.info("Navigating to {} for login", base_url)
        await self.page.goto(base_url, wait_until="domcontentloaded")
        await think_delay()

        if await self.is_logged_in():
            logger.success("Already logged in")
            return True

        # Open the login modal/form.
        if not await self._click_first(LOGIN_BUTTON_SELECTORS):
            logger.warning("Login button not found (selectors may need updating)")
        await think_delay()

        # Fill credentials (typed character-by-character by human_type).
        if not await self._type_first(USERNAME_SELECTORS, username):
            logger.error("Username field not found")
            return False
        if not await self._type_first(PASSWORD_SELECTORS, password):
            logger.error("Password field not found")
            return False
        await think_delay()

        # Submit.
        if not await self._click_first(SUBMIT_SELECTORS):
            logger.warning("Submit button not found; pressing Enter")
            try:
                await self.page.keyboard.press("Enter")
            except Exception:
                pass

        # Wait for balance (success) OR error, up to the timeout.
        loop = asyncio.get_event_loop()
        deadline = loop.time() + LOGIN_TIMEOUT_SECONDS
        while loop.time() < deadline:
            if await self.is_logged_in():
                logger.success("Login successful")
                return True
            error = await self._error_text()
            if error:
                logger.error("Login error: {}", error)
                return False
            await asyncio.sleep(0.5)

        logger.error("Login timed out after {}s", LOGIN_TIMEOUT_SECONDS)
        return False

    async def ensure_logged_in(self) -> bool:
        """Log in only if not already authenticated."""
        if await self.is_logged_in():
            logger.info("Session already authenticated")
            return True
        return await self.login()


# Backward-compatible alias.
Authenticator = AuthManager


__all__ = ["AuthManager", "Authenticator"]
