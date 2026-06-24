"""Human-like interaction helpers.

Real players don't click instantly or move the mouse in straight teleporting
jumps. These helpers add randomised delays, curved mouse movement and
per-character typing so the bot's behaviour looks less mechanical.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Page

from config import TIMING


def _rng(rng: random.Random | None) -> random.Random:
    return rng or random


async def think_delay(
    min_delay: float | None = None,
    max_delay: float | None = None,
    rng: random.Random | None = None,
) -> float:
    """Sleep for a random "thinking" interval and return the slept duration."""
    rng = _rng(rng)
    lo = TIMING["MIN_DELAY"] if min_delay is None else min_delay
    hi = TIMING["MAX_DELAY"] if max_delay is None else max_delay
    delay = rng.uniform(lo, hi)
    await asyncio.sleep(delay)
    return delay


async def short_pause(rng: random.Random | None = None) -> float:
    """A brief sub-second pause used between micro-actions."""
    rng = _rng(rng)
    delay = rng.uniform(0.05, 0.35)
    await asyncio.sleep(delay)
    return delay


def _bezier_point(p0, p1, p2, p3, t):
    """Cubic Bezier interpolation between four 2D points."""
    mt = 1 - t
    x = (mt ** 3) * p0[0] + 3 * (mt ** 2) * t * p1[0] + 3 * mt * (t ** 2) * p2[0] + (t ** 3) * p3[0]
    y = (mt ** 3) * p0[1] + 3 * (mt ** 2) * t * p1[1] + 3 * mt * (t ** 2) * p2[1] + (t ** 3) * p3[1]
    return x, y


async def human_move(
    page: "Page",
    x: float,
    y: float,
    start: tuple[float, float] | None = None,
    steps: int = 24,
    rng: random.Random | None = None,
) -> None:
    """Move the mouse to (x, y) along a randomised curved path."""
    rng = _rng(rng)
    sx, sy = start if start is not None else (rng.uniform(0, 200), rng.uniform(0, 200))
    # Two random control points to bend the path.
    c1 = (sx + (x - sx) * rng.uniform(0.2, 0.4), sy + (y - sy) * rng.uniform(0.0, 0.5))
    c2 = (sx + (x - sx) * rng.uniform(0.6, 0.8), sy + (y - sy) * rng.uniform(0.5, 1.0))
    for i in range(1, steps + 1):
        t = i / steps
        px, py = _bezier_point((sx, sy), c1, c2, (x, y), t)
        # Tiny jitter for realism.
        px += rng.uniform(-1.5, 1.5)
        py += rng.uniform(-1.5, 1.5)
        await page.mouse.move(px, py)
        await asyncio.sleep(rng.uniform(0.004, 0.018))


async def human_click(
    page: "Page",
    selector: str,
    rng: random.Random | None = None,
    timeout: float | None = None,
) -> bool:
    """Move to an element and click it at a randomised in-bounds point.

    Returns ``True`` on success, ``False`` if the element was not found.
    """
    rng = _rng(rng)
    timeout_ms = int((timeout or TIMING["ACTION_TIMEOUT"]) * 1000)
    locator = page.locator(selector).first
    try:
        await locator.wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        return False

    box = await locator.bounding_box()
    if box:
        target_x = box["x"] + box["width"] * rng.uniform(0.3, 0.7)
        target_y = box["y"] + box["height"] * rng.uniform(0.3, 0.7)
        await human_move(page, target_x, target_y, rng=rng)
        await short_pause(rng)
        await page.mouse.click(target_x, target_y)
    else:
        # Fallback: let Playwright handle positioning.
        await locator.click()
    return True


async def human_type(
    page: "Page",
    selector: str,
    text: str,
    rng: random.Random | None = None,
    timeout: float | None = None,
) -> bool:
    """Type ``text`` into an element one character at a time with jitter."""
    rng = _rng(rng)
    timeout_ms = int((timeout or TIMING["ACTION_TIMEOUT"]) * 1000)
    locator = page.locator(selector).first
    try:
        await locator.wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        return False
    await locator.click()
    await short_pause(rng)
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(rng.uniform(0.04, 0.18))
    return True


__all__ = [
    "think_delay",
    "short_pause",
    "human_move",
    "human_click",
    "human_type",
]
