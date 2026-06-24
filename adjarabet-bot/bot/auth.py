"""Adjarabet authentication flow.

By default the bot opens a visible browser at adjarabet.am and waits for you
to log in manually on the site. Automated credential login (from ``.env``) is
optional when ``MANUAL_LOGIN=false``.

All CSS selectors are read from :mod:`config` — never hardcoded here.
"""

from __future__ import annotations

import asyncio

import config as _default_config
from utils.errors import capture_page_error
from utils.human import human_click, human_type, think_delay
from utils.logger import get_logger

logger = get_logger()


class AuthManager:
    """Perform login and verify authenticated state."""

    def __init__(self, page, config=None) -> None:
        self.page = page
        self.config = config or _default_config

    def _cfg(self, name: str, default=None):
        return getattr(self.config, name, default)

    def _selectors(self, list_key: str) -> list[str]:
        fn = getattr(self.config, "selector_list", None)
        if callable(fn):
            return fn(list_key)
        sel = getattr(self.config, "SELECTORS", {}) or {}
        return [sel.get(list_key, list_key)]

    def _timing(self, key: str, default: float) -> float:
        return (self._cfg("TIMING", {}) or {}).get(key, default)

    # ------------------------------------------------------------------ #
    # State checks
    # ------------------------------------------------------------------ #
    async def is_logged_in(self) -> bool:
        """Return True if a balance / profile element is visible."""
        try:
            return await self._any_visible(self._selectors("logged_in_marker"), timeout_ms=1500)
        except Exception as exc:
            await capture_page_error(self.page, self.config, "auth_is_logged_in", exc)
            return False

    async def _any_visible(self, selectors: list[str], timeout_ms: int = 1500) -> bool:
        for css in selectors:
            try:
                locator = self.page.locator(css).first
                if await locator.count() == 0:
                    continue
                await locator.wait_for(state="visible", timeout=timeout_ms)
                return True
            except Exception:
                continue
        return False

    async def _detect_2fa(self) -> bool:
        """Return True if a 2FA / OTP prompt appears to be visible."""
        try:
            return await self._any_visible(self._selectors("twofa_marker"), timeout_ms=800)
        except Exception:
            return False

    async def _detect_geo_block(self) -> bool:
        """Return True if the site shows a geo-restriction / unavailable page."""
        try:
            title = self.page.locator(".alert-container .title").first
            if await title.count() == 0:
                return False
            text = (await title.inner_text()).strip().lower()
            return "not available" in text or "unavailable" in text
        except Exception:
            return False

    async def _error_text(self) -> str | None:
        for css in self._selectors("login_error"):
            try:
                locator = self.page.locator(css).first
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
    async def _click_first(self, list_key: str, timeout: float | None = None) -> bool:
        timeout = timeout if timeout is not None else self._timing("ACTION_TIMEOUT", 10.0)
        for css in self._selectors(list_key):
            try:
                if await human_click(self.page, css, timeout=timeout):
                    logger.debug("Clicked selector: {}", css)
                    return True
            except Exception as exc:
                logger.debug("Click failed on {}: {}", css, exc)
        return False

    async def _type_first(self, list_key: str, text: str, timeout: float | None = None) -> bool:
        timeout = timeout if timeout is not None else self._timing("ACTION_TIMEOUT", 10.0)
        for css in self._selectors(list_key):
            try:
                if await human_type(self.page, css, text, timeout=timeout):
                    logger.debug("Typed into selector: {}", css)
                    return True
            except Exception as exc:
                logger.debug("Type failed on {}: {}", css, exc)
        return False

    async def login(self) -> bool:
        """Log into Adjarabet. Returns True on verified success."""
        username = self._cfg("ADJARABET_USERNAME", "")
        password = self._cfg("ADJARABET_PASSWORD", "")
        if not username or not password:
            logger.error("Missing credentials - set ADJARABET_USERNAME / ADJARABET_PASSWORD in .env")
            return False

        base_url = self._cfg("BASE_URL", "")
        login_timeout = self._timing("LOGIN_TIMEOUT", 30.0)

        try:
            logger.info("Navigating to {} for login", base_url)
            await self.page.goto(base_url, wait_until="domcontentloaded")
        except Exception as exc:
            await capture_page_error(self.page, self.config, "auth_goto", exc)
            return False

        await think_delay()

        if await self._detect_geo_block():
            logger.error(
                "Adjarabet geo-block page detected — site unavailable from this "
                "region/IP. Use a SOCKS5 proxy (PROXY_SERVER in .env) or run locally "
                "from an allowed country."
            )
            await capture_page_error(self.page, self.config, "auth_geo_blocked")
            return False

        if await self.is_logged_in():
            logger.success("Already logged in")
            return True

        try:
            if not await self._click_first("login_button"):
                logger.warning("Login button not found (selectors may need updating)")
            await think_delay()

            if not await self._type_first("username_input", username):
                logger.error("Username field not found")
                await capture_page_error(self.page, self.config, "auth_no_username_field")
                return False
            if not await self._type_first("password_input", password):
                logger.error("Password field not found")
                await capture_page_error(self.page, self.config, "auth_no_password_field")
                return False
            await think_delay()

            if not await self._click_first("submit_login"):
                logger.warning("Submit button not found; pressing Enter")
                try:
                    await self.page.keyboard.press("Enter")
                except Exception:
                    pass
        except Exception as exc:
            await capture_page_error(self.page, self.config, "auth_form", exc)
            return False

        loop = asyncio.get_event_loop()
        deadline = loop.time() + login_timeout
        while loop.time() < deadline:
            try:
                if await self.is_logged_in():
                    logger.success("Login successful")
                    return True
                if await self._detect_2fa():
                    logger.error("2FA prompt detected - manual login required")
                    await capture_page_error(self.page, self.config, "auth_2fa_required")
                    return False
                error = await self._error_text()
                if error:
                    logger.error("Login error (wrong credentials?): {}", error)
                    await capture_page_error(self.page, self.config, "auth_login_error")
                    return False
            except Exception as exc:
                await capture_page_error(self.page, self.config, "auth_wait_loop", exc)
            await asyncio.sleep(self._timing("TURN_WAIT_POLL", 0.5))

        logger.error("Login timed out after {}s", login_timeout)
        await capture_page_error(self.page, self.config, "auth_timeout")
        return False

    async def wait_for_manual_login(self, confirm_event=None) -> bool:
        """Open adjarabet.am in the browser and wait for the user to log in."""
        base_url = self._cfg("BASE_URL", "")
        timeout = self._timing("MANUAL_LOGIN_TIMEOUT", 300.0)
        poll = self._timing("TURN_WAIT_POLL", 0.5)

        try:
            logger.info("Opening {} — log in manually in the browser window", base_url)
            await self.page.goto(base_url, wait_until="domcontentloaded")
        except Exception as exc:
            await capture_page_error(self.page, self.config, "auth_goto", exc)
            return False

        if await self._detect_geo_block():
            logger.error(
                "Adjarabet geo-block page detected — site unavailable from this "
                "region/IP. Use a SOCKS5 proxy (PROXY_SERVER in .env) or run locally "
                "from an allowed country."
            )
            await capture_page_error(self.page, self.config, "auth_geo_blocked")
            return False

        if await self.is_logged_in():
            logger.success("Already logged in")
            return True

        logger.info(
            "Waiting for manual login (up to {:.0f}s). "
            "Use the Chromium window to sign in on adjarabet.am, "
            "then click Continue in the dashboard.",
            timeout,
        )

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                if confirm_event is not None and confirm_event.is_set():
                    logger.success("Manual login confirmed from dashboard")
                    return True
                if await self.is_logged_in():
                    logger.success("Manual login detected on page")
                    return True
            except Exception as exc:
                await capture_page_error(self.page, self.config, "auth_manual_wait", exc)
            await asyncio.sleep(poll)

        logger.error("Manual login timed out after {:.0f}s", timeout)
        await capture_page_error(self.page, self.config, "auth_manual_timeout")
        return False

    async def ensure_logged_in(
        self,
        manual: bool | None = None,
        confirm_event=None,
    ) -> bool:
        """Authenticate via manual browser login or automated .env credentials."""
        if await self.is_logged_in():
            logger.info("Session already authenticated")
            return True

        use_manual = self._cfg("MANUAL_LOGIN", True) if manual is None else manual
        if use_manual:
            return await self.wait_for_manual_login(confirm_event=confirm_event)
        return await self.login()


Authenticator = AuthManager

__all__ = ["AuthManager", "Authenticator"]
