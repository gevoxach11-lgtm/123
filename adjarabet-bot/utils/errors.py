"""Shared error-handling helpers (logging + debug screenshots)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from utils.logger import get_logger

logger = get_logger()


async def capture_page_error(page, config, label: str, exc: Exception | None = None) -> str | None:
    """Log an error and save a screenshot to ``logs/error_{label}_{ts}.png``.

    Returns the screenshot path on success, else ``None``. Never raises.
    """
    if exc is not None:
        logger.exception("{}: {}", label, exc)
    else:
        logger.error("{}", label)

    if page is None:
        return None

    log_dir = getattr(config, "LOG_DIR", None) or Path("logs")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40]
    path = log_dir / f"error_{safe_label}_{timestamp}.png"
    try:
        await page.screenshot(path=str(path), full_page=False)
        logger.debug("Saved error screenshot -> {}", path)
        return str(path)
    except Exception as shot_exc:  # pragma: no cover
        logger.warning("Could not save screenshot: {}", shot_exc)
        return None


__all__ = ["capture_page_error"]
