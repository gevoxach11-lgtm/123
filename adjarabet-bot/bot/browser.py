"""Playwright browser lifecycle management.

Wraps launching Chromium with stealth + proxy settings, creating a context and
page, opening additional tabs, taking screenshots and clean teardown. The rest
of the bot interacts with :class:`BrowserManager` rather than Playwright
directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

import config as _default_config
from utils.logger import get_logger
from utils.stealth import apply_stealth, random_user_agent, random_viewport

logger = get_logger()

# Chromium launch flags that reduce automation fingerprints.
_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-infobars",
    "--start-maximized",
]


class BrowserManager:
    """Manage the Playwright instance, browser, context and page(s)."""

    def __init__(
        self,
        config=None,
        headless: Optional[bool] = None,
        proxy_server: Optional[str] = None,
    ) -> None:
        # ``config`` may be the project config module or any object exposing
        # HEADLESS / PROXY_SERVER / LOG_DIR / TIMING. Defaults to the module.
        self.config = config or _default_config
        self.headless = self._cfg("HEADLESS", False) if headless is None else headless
        self.proxy_server = self._cfg("PROXY_SERVER", "") if proxy_server is None else proxy_server

        self._playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def _cfg(self, name: str, default=None):
        return getattr(self.config, name, default)

    async def launch(self) -> Page:
        """Launch Chromium with stealth + proxy and return a ready page."""
        logger.info("Launching Chromium (headless={})", self.headless)
        self._playwright = await async_playwright().start()

        launch_kwargs: dict = {"headless": self.headless, "args": list(_LAUNCH_ARGS)}
        if self.proxy_server:
            launch_kwargs["proxy"] = {"server": self.proxy_server}
            logger.info("Using proxy: {}", self.proxy_server)

        self.browser = await self._playwright.chromium.launch(**launch_kwargs)

        viewport = random_viewport()
        user_agent = random_user_agent()
        self.context = await self.browser.new_context(
            user_agent=user_agent,
            viewport=viewport,
            locale="ka-GE",
            timezone_id="Asia/Tbilisi",
            ignore_https_errors=True,
        )
        timing = self._cfg("TIMING", {}) or {}
        self.context.set_default_timeout(timing.get("PAGE_TIMEOUT", 30.0) * 1000)

        # Apply stealth at the context level BEFORE creating the page so the
        # init script runs ahead of page scripts on every document (including
        # each page's initial document and any future tabs).
        await apply_stealth(self.context)

        self.page = await self.context.new_page()
        logger.success("Browser ready (viewport={}, ua=...{})", viewport, user_agent[-25:])
        return self.page

    async def new_page(self) -> Page:
        """Open a new tab in the same context.

        Stealth is already registered at the context level, so it applies to
        this tab automatically.
        """
        if not self.context:
            raise RuntimeError("Browser not launched; call launch() first")
        page = await self.context.new_page()
        logger.debug("Opened new page/tab")
        return page

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        if not self.page:
            raise RuntimeError("Browser not launched; call launch() first")
        logger.info("Navigating to {}", url)
        await self.page.goto(url, wait_until=wait_until)

    async def screenshot(self, name: str, page: Optional[Page] = None) -> Optional[str]:
        """Save a screenshot to ``logs/{name}_{timestamp}.png`` and return path."""
        target = page or self.page
        if not target:
            return None
        log_dir = self._cfg("LOG_DIR", None)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{name}_{timestamp}.png"
        path = str(log_dir / filename) if log_dir is not None else filename
        try:
            await target.screenshot(path=path, full_page=False)
            logger.debug("Saved screenshot -> {}", path)
            return path
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("Screenshot failed: {}", exc)
            return None

    async def close(self) -> None:
        """Tear everything down, ignoring teardown errors."""
        logger.info("Closing browser")
        for closer in (
            getattr(self.context, "close", None),
            getattr(self.browser, "close", None),
        ):
            if closer:
                try:
                    await closer()
                except Exception as exc:  # pragma: no cover - best-effort cleanup
                    logger.warning("Error during browser teardown: {}", exc)
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as exc:  # pragma: no cover
                logger.warning("Error stopping playwright: {}", exc)
        self.page = self.context = self.browser = self._playwright = None

    # Backward-compatible aliases.
    async def start(self) -> Page:
        return await self.launch()

    async def stop(self) -> None:
        await self.close()

    async def __aenter__(self) -> "BrowserManager":
        await self.launch()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()


__all__ = ["BrowserManager"]
