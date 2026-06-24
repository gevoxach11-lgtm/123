"""Logging setup using loguru.

Provides:
* Coloured console output.
* Rotating file output to ``logs/session_{date}.log``.
* 50 MB rotation, 7-day retention, zip compression of old logs.

Call :func:`setup_logger` once at application start-up; subsequent imports can
simply ``from loguru import logger``.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

# Resolve the logs directory relative to the project root (parent of utils/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _PROJECT_ROOT / "logs"

_CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{name}:{function}:{line} - {message}"
)

_configured = False


def setup_logger(level: str = "INFO", log_dir: Path | None = None):
    """Configure loguru handlers (idempotent).

    Parameters
    ----------
    level:
        Minimum console log level (file always logs at DEBUG).
    log_dir:
        Optional override for the log directory.

    Returns
    -------
    The configured loguru ``logger`` instance.
    """
    global _configured

    target_dir = log_dir or _LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    # Remove loguru's default handler so we control all output.
    logger.remove()

    # Console (coloured).
    logger.add(
        sys.stderr,
        level=level,
        format=_CONSOLE_FORMAT,
        colorize=True,
        backtrace=True,
        diagnose=False,
        enqueue=True,
    )

    # File (rotating). Filename carries the start date.
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_file = target_dir / f"session_{date_str}.log"
    logger.add(
        str(log_file),
        level="DEBUG",
        format=_FILE_FORMAT,
        rotation="50 MB",
        retention="7 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )

    _configured = True
    logger.debug("Logger initialised -> {}", log_file)
    return logger


def get_logger():
    """Return the shared logger, configuring it on first use."""
    if not _configured:
        setup_logger()
    return logger


__all__ = ["setup_logger", "get_logger", "logger"]
