"""Human-like interaction helpers.

Real players don't click instantly or move the mouse in straight teleporting
jumps. These helpers add randomised delays, curved (Bezier) mouse movement and
per-character typing so the bot's behaviour looks less mechanical.

The click/type helpers accept *either* a CSS selector string *or* a Playwright
``Locator`` / ``ElementHandle``, so they are convenient for both high-level
("click this selector") and low-level ("click this element I already found")
call sites.
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


# --------------------------------------------------------------------------- #
# Delays
# --------------------------------------------------------------------------- #
async def random_delay(min_s: float, max_s: float, rng: random.Random | None = None) -> float:
    """Sleep for a uniformly random duration in ``[min_s, max_s]``."""
    rng = _rng(rng)
    if max_s < min_s:
        min_s, max_s = max_s, min_s
    delay = min_s + rng.random() * (max_s - min_s)
    await asyncio.sleep(delay)
    return delay


async def think_delay(
    min_delay: float | None = None,
    max_delay: float | None = None,
    rng: random.Random | None = None,
) -> float:
    """Sleep a random "thinking" interval (defaults from ``config.TIMING``)."""
    lo = TIMING["MIN_DELAY"] if min_delay is None else min_delay
    hi = TIMING["MAX_DELAY"] if max_delay is None else max_delay
    return await random_delay(lo, hi, rng=rng)


async def short_pause(rng: random.Random | None = None) -> float:
    """A brief sub-second pause used between micro-actions."""
    return await random_delay(0.05, 0.35, rng=rng)


# --------------------------------------------------------------------------- #
# Mouse movement
# --------------------------------------------------------------------------- #
def _get_mouse_pos(page: "Page", rng: random.Random) -> tuple[float, float]:
    pos = getattr(page, "_bot_mouse_pos", None)
    if pos:
        return pos
    return (rng.uniform(0, 200), rng.uniform(0, 200))


def _set_mouse_pos(page: "Page", x: float, y: float) -> None:
    try:
        page._bot_mouse_pos = (x, y)
    except Exception:  # pragma: no cover - some objects disallow attrs
        pass


async def bezier_move(
    page: "Page",
    target_x: float,
    target_y: float,
    steps: int = 12,
    rng: random.Random | None = None,
) -> None:
    """Move the mouse to (target_x, target_y) along a quadratic Bezier curve.

    The control point is the midpoint offset by a random +/-100px, giving a
    natural arc. Mouse-move events are dispatched at each step with an 8-15ms
    pause between them.
    """
    rng = _rng(rng)
    sx, sy = _get_mouse_pos(page, rng)
    # Control point: midpoint with a random perpendicular-ish offset.
    cx = (sx + target_x) / 2 + rng.uniform(-100, 100)
    cy = (sy + target_y) / 2 + rng.uniform(-100, 100)

    steps = max(1, steps)
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1 - t
        x = mt * mt * sx + 2 * mt * t * cx + t * t * target_x
        y = mt * mt * sy + 2 * mt * t * cy + t * t * target_y
        await page.mouse.move(x, y)
        await asyncio.sleep(rng.uniform(0.008, 0.015))

    _set_mouse_pos(page, target_x, target_y)


# Backward-compatible alias used elsewhere in the codebase.
async def human_move(
    page: "Page",
    x: float,
    y: float,
    start: tuple[float, float] | None = None,
    steps: int = 12,
    rng: random.Random | None = None,
) -> None:
    if start is not None:
        _set_mouse_pos(page, *start)
    await bezier_move(page, x, y, steps=steps, rng=rng)


# --------------------------------------------------------------------------- #
# Element resolution
# --------------------------------------------------------------------------- #
def _as_target(page: "Page", target):
    """Return a Locator/ElementHandle for ``target`` (selector str or element)."""
    if isinstance(target, str):
        return page.locator(target).first
    return target


async def _wait_visible(target, timeout_ms: int) -> bool:
    waiter = getattr(target, "wait_for", None)
    if waiter is None:
        return True  # ElementHandles are assumed already attached/visible
    try:
        await waiter(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Click & type
# --------------------------------------------------------------------------- #
async def human_click(
    page: "Page",
    element,
    rng: random.Random | None = None,
    timeout: float | None = None,
) -> bool:
    """Move to an element and click it at a randomised in-bounds point.

    ``element`` may be a selector string, a Locator or an ElementHandle.
    Returns ``True`` on success, ``False`` if the element was unusable.
    """
    rng = _rng(rng)
    timeout_ms = int((timeout if timeout is not None else TIMING["ACTION_TIMEOUT"]) * 1000)
    target = _as_target(page, element)

    if not await _wait_visible(target, timeout_ms):
        return False

    box = None
    try:
        box = await target.bounding_box()
    except Exception:
        box = None

    if not box:
        # Fallback: let Playwright handle positioning/clicking.
        try:
            await target.click(timeout=timeout_ms)
            return True
        except Exception:
            return False

    x = box["x"] + box["width"] / 2 + rng.uniform(-4, 4)
    y = box["y"] + box["height"] / 2 + rng.uniform(-4, 4)

    await bezier_move(page, x, y, rng=rng)
    # mouseover/mouseenter are produced by the move above.
    await page.mouse.down()
    await random_delay(0.06, 0.12, rng=rng)
    await page.mouse.up()  # produces mouseup + click
    await asyncio.sleep(0.05)
    return True


async def human_type(
    page: "Page",
    element,
    text: str,
    rng: random.Random | None = None,
    timeout: float | None = None,
) -> bool:
    """Type ``text`` into an element one character at a time with jitter.

    ``element`` may be a selector string, a Locator or an ElementHandle.
    """
    rng = _rng(rng)
    timeout_ms = int((timeout if timeout is not None else TIMING["ACTION_TIMEOUT"]) * 1000)
    target = _as_target(page, element)

    if not await _wait_visible(target, timeout_ms):
        return False

    # Focus the field (click also focuses and looks human).
    try:
        await target.click()
    except Exception:
        focus = getattr(target, "focus", None)
        if focus:
            try:
                await focus()
            except Exception:
                return False
        else:
            return False

    await short_pause(rng)
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(rng.uniform(0.05, 0.14))
    return True


__all__ = [
    "random_delay",
    "think_delay",
    "short_pause",
    "bezier_move",
    "human_move",
    "human_click",
    "human_type",
]
