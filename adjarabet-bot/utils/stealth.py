"""Anti-detection (stealth) helpers for Playwright.

Casino / poker sites probe for automation fingerprints. :func:`apply_stealth`
injects a single init script (runs before any page script on every navigation)
that patches the most common tells:

1.  ``navigator.webdriver`` -> ``false``
2.  ``navigator.plugins`` -> a non-empty fake plugin array (3 items)
3.  ``navigator.languages`` -> ``['ka-GE', 'ka', 'en-US', 'en']``
4.  ``navigator.hardwareConcurrency`` -> ``8``
5.  ``navigator.deviceMemory`` -> ``8``
6.  ``navigator.platform`` -> ``'Win32'``
7.  Removes the "Chrome is being controlled by automation" infobar artefacts
8.  ``chrome.runtime`` -> ``{}`` (fake chrome object)
9.  ``HTMLCanvasElement.toDataURL`` -> adds 1-bit LSB noise (fingerprint jitter)
10. WebGL ``getParameter`` -> spoofs Intel vendor/renderer
11. ``screen.colorDepth`` / ``pixelDepth`` -> ``24``
12. ``Intl.DateTimeFormat`` -> reports timezone ``'Asia/Tbilisi'``
13. ``window.chrome`` -> ``{ runtime: {} }``

These measures reduce trivial detection but are not a guarantee - use
responsibly and within the terms of service of any site you target.
"""

from __future__ import annotations

import random

# Pool of 5 realistic desktop Chrome user-agents.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

# Base desktop resolution; randomised per session with a small jitter.
BASE_VIEWPORT = {"width": 1920, "height": 1080}

# Single init script covering every stealth patch above.
STEALTH_JS = r"""
(() => {
  // (1) navigator.webdriver -> false
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
  } catch (e) {}

  // (2) navigator.plugins -> fake non-empty array (3 items)
  try {
    const makePlugin = (name, filename) => {
      const plugin = { name, filename, description: name, length: 1 };
      plugin[0] = { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: name };
      return plugin;
    };
    const plugins = [
      makePlugin('Chrome PDF Plugin', 'internal-pdf-viewer'),
      makePlugin('Chrome PDF Viewer', 'mhjfbmdgcfjbbpaeojofohoefgiehjai'),
      makePlugin('Native Client', 'internal-nacl-plugin'),
    ];
    plugins.item = (i) => plugins[i];
    plugins.namedItem = (n) => plugins.find((p) => p.name === n) || null;
    Object.defineProperty(navigator, 'plugins', { get: () => plugins });
    Object.defineProperty(navigator, 'mimeTypes', { get: () => [{ type: 'application/pdf' }] });
  } catch (e) {}

  // (3) navigator.languages
  try {
    Object.defineProperty(navigator, 'languages', { get: () => ['ka-GE', 'ka', 'en-US', 'en'] });
    Object.defineProperty(navigator, 'language', { get: () => 'ka-GE' });
  } catch (e) {}

  // (4) hardwareConcurrency
  try { Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 }); } catch (e) {}

  // (5) deviceMemory
  try { Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 }); } catch (e) {}

  // (6) platform
  try { Object.defineProperty(navigator, 'platform', { get: () => 'Win32' }); } catch (e) {}

  // (7) Hide automation infobar tells / permissions inconsistency
  try {
    const originalQuery = window.navigator.permissions &&
      window.navigator.permissions.query;
    if (originalQuery) {
      window.navigator.permissions.query = (parameters) => (
        parameters && parameters.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission })
          : originalQuery(parameters)
      );
    }
  } catch (e) {}

  // (8) + (13) window.chrome / chrome.runtime
  try {
    window.chrome = window.chrome || {};
    window.chrome.runtime = window.chrome.runtime || {};
  } catch (e) {}

  // (9) Canvas toDataURL -> 1-bit LSB noise
  try {
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function (...args) {
      try {
        const ctx = this.getContext('2d');
        if (ctx && this.width && this.height) {
          const img = ctx.getImageData(0, 0, this.width, this.height);
          const data = img.data;
          for (let i = 0; i < data.length; i += 4) {
            // Flip the least-significant bit of one channel at random.
            if (Math.random() < 0.5) { data[i] = data[i] ^ 1; }
          }
          ctx.putImageData(img, 0, 0);
        }
      } catch (e) {}
      return origToDataURL.apply(this, args);
    };
  } catch (e) {}

  // (10) WebGL vendor / renderer spoofing
  try {
    const spoof = (proto) => {
      if (!proto) return;
      const getParameter = proto.getParameter;
      proto.getParameter = function (parameter) {
        if (parameter === 37445) { return 'Intel Inc.'; }              // UNMASKED_VENDOR_WEBGL
        if (parameter === 37446) { return 'Intel Iris OpenGL Engine'; } // UNMASKED_RENDERER_WEBGL
        return getParameter.call(this, parameter);
      };
    };
    spoof(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
    spoof(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
  } catch (e) {}

  // (11) screen colour depth
  try {
    Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
    Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });
  } catch (e) {}

  // (12) Intl.DateTimeFormat timezone -> Asia/Tbilisi
  try {
    const DTF = Intl.DateTimeFormat;
    const origResolved = DTF.prototype.resolvedOptions;
    DTF.prototype.resolvedOptions = function () {
      const opts = origResolved.apply(this, arguments);
      opts.timeZone = 'Asia/Tbilisi';
      return opts;
    };
    const origOffset = Date.prototype.getTimezoneOffset;
    // Asia/Tbilisi is UTC+4 -> offset -240 minutes.
    Date.prototype.getTimezoneOffset = function () { return -240; };
  } catch (e) {}
})();
"""


def random_user_agent(rng: random.Random | None = None) -> str:
    """Return a random user-agent from the pool of 5."""
    rng = rng or random
    return rng.choice(USER_AGENTS)


def random_viewport(rng: random.Random | None = None) -> dict[str, int]:
    """Return a 1920x1080 viewport jittered by +/-80px on each axis."""
    rng = rng or random
    return {
        "width": BASE_VIEWPORT["width"] + rng.randint(-80, 80),
        "height": BASE_VIEWPORT["height"] + rng.randint(-80, 80),
    }


async def apply_stealth(page) -> None:
    """Inject the stealth init script into a Playwright Page (or Context).

    Must be called *before* navigating so the script runs ahead of page
    scripts. Both ``Page`` and ``BrowserContext`` expose ``add_init_script``.
    """
    await page.add_init_script(STEALTH_JS)


__all__ = [
    "USER_AGENTS",
    "BASE_VIEWPORT",
    "STEALTH_JS",
    "random_user_agent",
    "random_viewport",
    "apply_stealth",
]
