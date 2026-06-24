"""Anti-detection helpers for Playwright.

Casino / poker sites often run bot-detection scripts that probe for automation
fingerprints (``navigator.webdriver``, headless Chrome quirks, missing plugins,
etc.). The JavaScript below patches the most common tells before any page
script runs.

These measures reduce trivial detection but are NOT a guarantee - use
responsibly and within the terms of service of any site you target.
"""

from __future__ import annotations

import random

# A small pool of realistic desktop user-agents.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

# Common desktop viewport sizes.
VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1600, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
]

# JS injected via add_init_script so it runs before page scripts.
STEALTH_JS = r"""
// --- navigator.webdriver ---
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// --- window.chrome ---
window.chrome = window.chrome || { runtime: {} };

// --- languages ---
Object.defineProperty(navigator, 'languages', {
  get: () => ['en-US', 'en'],
});

// --- plugins (non-empty array) ---
Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5],
});

// --- permissions query (notifications) ---
const originalQuery = window.navigator.permissions &&
  window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters && parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
}

// --- WebGL vendor/renderer spoofing ---
try {
  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (parameter) {
    if (parameter === 37445) { return 'Intel Inc.'; }          // UNMASKED_VENDOR_WEBGL
    if (parameter === 37446) { return 'Intel Iris OpenGL Engine'; } // UNMASKED_RENDERER_WEBGL
    return getParameter.call(this, parameter);
  };
} catch (e) { /* ignore */ }

// --- hardwareConcurrency / deviceMemory plausibility ---
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
"""


def random_user_agent(rng: random.Random | None = None) -> str:
    rng = rng or random
    return rng.choice(USER_AGENTS)


def random_viewport(rng: random.Random | None = None) -> dict[str, int]:
    rng = rng or random
    return dict(rng.choice(VIEWPORTS))


async def apply_stealth(context_or_page) -> None:
    """Inject the stealth script into a Playwright BrowserContext or Page.

    Works with both objects because each exposes ``add_init_script``.
    """
    await context_or_page.add_init_script(STEALTH_JS)


__all__ = [
    "USER_AGENTS",
    "VIEWPORTS",
    "STEALTH_JS",
    "random_user_agent",
    "random_viewport",
    "apply_stealth",
]
