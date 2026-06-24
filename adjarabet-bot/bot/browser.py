"""Playwright browser lifecycle management.

Wraps launching Chromium with stealth + proxy settings, creating a context and
page, and clean teardown. The rest of the bot interacts with the
:class:`BrowserManager` rather than Playwright directly.
"""

from __future__ import annotations

from typing import Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

import config
from utils.logger import get_logger
from utils.stealth import apply_stealth, random_user_agent, random_viewport

logger = get_logger()


class BrowserManager:
    """Manage the Playwright instance, browser, context and page."""

    def __init__(
        self,
        headless: Optional[bool] = None,
        proxy_server: Optional[str] = None,
    ) -> None:
        self.headless = config.HEADLESS if headless is None else headless
        self.proxy_server = config.PROXY_SERVER if proxy_server is None else proxy_server
        self._playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def start(self) -> Page:
        """Launch the browser and return a ready-to-use page."""
        logger.info("Starting browser (headless={})", self.headless)
        self._playwright = await async_playwright().start()

        launch_kwargs: dict = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--start-maximized",
            ],
        }
        if self.proxy_server:
            launch_kwargs["proxy"] = {"server": self.proxy_server}
            logger.info("Using proxy: {}", self.proxy_server)

        self.browser = await self._playwright.chromium.launch(**launch_kwargs)

        viewport = random_viewport()
        self.context = await self.browser.new_context(
            user_agent=random_user_agent(),
            viewport=viewport,
            locale="en-US",
            timezone_id="Asia/Tbilisi",
            ignore_https_errors=True,
        )
        self.context.set_default_timeout(config.TIMING["PAGE_TIMEOUT"] * 1000)

        # Inject anti-detection script before any page loads.
        await apply_stealth(self.context)

        self.page = await self.context.new_page()
        logger.success("Browser ready (viewport={})", viewport)
        return self.page

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        if not self.page:
            raise RuntimeError("Browser not started; call start() first")
        logger.info("Navigating to {}", url)
        await self.page.goto(url, wait_until=wait_until)

    async def screenshot(self, path: str) -> None:
        if self.page:
            await self.page.screenshot(path=path, full_page=False)
            logger.debug("Saved screenshot -> {}", path)

    async def stop(self) -> None:
        """Tear everything down, ignoring teardown errors."""
        logger.info("Stopping browser")
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

    async def __aenter__(self) -> "BrowserManager":
        await self.start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.stop()


__all__ = ["BrowserManager"]
