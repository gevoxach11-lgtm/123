"""Utility helpers: logging, stealth and human-like interaction."""

from .logger import get_logger, logger, setup_logger
from .errors import capture_page_error
from .stealth import (
    apply_stealth,
    random_user_agent,
    random_viewport,
)
from .human import (
    bezier_move,
    human_click,
    human_move,
    human_type,
    random_delay,
    short_pause,
    think_delay,
)

__all__ = [
    "get_logger",
    "logger",
    "setup_logger",
    "capture_page_error",
    "apply_stealth",
    "random_user_agent",
    "random_viewport",
    "bezier_move",
    "human_click",
    "human_move",
    "human_type",
    "random_delay",
    "short_pause",
    "think_delay",
]
