"""Session manager and stats tracker.

Orchestrates the full bot lifecycle:

1. Start the browser, log in, join a table.
2. Poll the table DOM (scraper) on an async loop.
3. When it's hero's turn, ask the engine for an action and execute it.
4. Track session statistics and enforce safety limits (max hands, max time,
   stop-loss).

The manager exposes a small status snapshot (:meth:`status`) so the Streamlit
dashboard can render live progress, and supports cooperative cancellation via
:meth:`stop`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import config
from bot.auth import Authenticator
from bot.browser import BrowserManager
from bot.engine import PokerEngine
from bot.executor import ActionExecutor
from bot.lobby import LobbyNavigator
from bot.scraper import TableScraper
from poker.models import Action, GameState, SessionStats
from utils.logger import get_logger

logger = get_logger()


@dataclass
class DecisionRecord:
    """A single logged decision for the dashboard / history view."""

    timestamp: float
    hand_id: str
    street: str
    action: str
    reason: str
    pot: float
    stack: float


@dataclass
class SessionConfig:
    """Per-run options for a session."""

    headless: bool = field(default_factory=lambda: config.HEADLESS)
    auto_play: bool = field(default_factory=lambda: config.AUTO_PLAY)
    table_limit: str = field(default_factory=lambda: config.TABLE_LIMIT)
    max_hands: int = field(default_factory=lambda: config.SESSION["MAX_HANDS"])
    max_minutes: int = field(default_factory=lambda: config.SESSION["MAX_MINUTES"])
    stop_loss_bb: int = field(default_factory=lambda: config.SESSION["STOP_LOSS"])
    dry_run: bool = False  # if True, decide but don't click


class SessionManager:
    """Run and supervise a single play session."""

    def __init__(self, cfg: SessionConfig | None = None) -> None:
        self.cfg = cfg or SessionConfig()
        self.stats = SessionStats(big_blind=config.big_blind_for(self.cfg.table_limit))
        self.browser = BrowserManager(headless=self.cfg.headless)
        self.running = False
        self._stop_requested = False
        self.last_state: GameState | None = None
        self.last_action: Action | None = None
        self.history: list[DecisionRecord] = []
        self.error: str | None = None
        self._last_hand_id: str | None = None
        self._last_stack: float | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def run(self) -> None:
        """Full session: connect, sit down and play until a limit is hit."""
        self.running = True
        self._stop_requested = False
        self.error = None
        logger.info("=== Session starting (limit={}, auto_play={}) ===",
                    self.cfg.table_limit, self.cfg.auto_play)
        try:
            page = await self.browser.start()
            auth = Authenticator(page)
            if not await auth.login():
                self.error = "Login failed - check credentials/selectors"
                logger.error(self.error)
                return

            lobby = LobbyNavigator(page)
            if not await lobby.find_and_join(self.cfg.table_limit):
                self.error = "Could not join a table"
                logger.error(self.error)
                return

            await self.play_loop(page)
        except asyncio.CancelledError:
            logger.warning("Session cancelled")
            raise
        except Exception as exc:  # pragma: no cover - runtime safety net
            self.error = f"{type(exc).__name__}: {exc}"
            logger.exception("Session crashed: {}", exc)
        finally:
            await self.browser.stop()
            self.running = False
            logger.info("=== Session ended === {}", self.stats.as_dict())

    async def play_loop(self, page) -> None:
        """Poll the table and act when it's hero's turn."""
        scraper = TableScraper(page)
        engine = PokerEngine()
        executor = ActionExecutor(page, dry_run=self.cfg.dry_run or not self.cfg.auto_play)

        poll = config.TIMING["POLL_INTERVAL"]
        logger.info("Entering play loop (poll={}s)", poll)

        while not self._should_stop():
            try:
                state = await scraper.scrape()
                self.last_state = state
                self._track_hand_transition(state)

                if state.is_my_turn:
                    action = engine.decide(state)
                    self.last_action = action
                    self._record(state, action)
                    await executor.execute(action, state)
                    # Give the table time to advance before re-polling.
                    await asyncio.sleep(poll * 2)
                else:
                    await asyncio.sleep(poll)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                logger.exception("Loop iteration error: {}", exc)
                await asyncio.sleep(poll)

        logger.info("Play loop exited: {}", self._stop_reason())

    # ------------------------------------------------------------------ #
    # Stats / bookkeeping
    # ------------------------------------------------------------------ #
    def _track_hand_transition(self, state: GameState) -> None:
        """Detect new hands and approximate profit/loss from stack deltas."""
        if not state.hand_id:
            return
        if self._last_hand_id is None:
            self._last_hand_id = state.hand_id
            self._last_stack = state.my_stack
            return
        if state.hand_id != self._last_hand_id:
            # Hand changed - compute stack delta vs previous hand.
            delta = 0.0
            if self._last_stack is not None and state.my_stack > 0:
                delta = state.my_stack - self._last_stack
            won = delta > 0
            self.stats.record_hand(won=won, delta=delta)
            logger.info("Hand {} complete: delta={:+.2f} ({} total hands)",
                        self._last_hand_id, delta, self.stats.hands_played)
            self._last_hand_id = state.hand_id
            self._last_stack = state.my_stack

    def _record(self, state: GameState, action: Action) -> None:
        if action.type.value in ("bet", "raise", "call"):
            self.stats.vpip_count += 0  # incremented per-hand below if desired
        self.history.append(
            DecisionRecord(
                timestamp=time.time(),
                hand_id=state.hand_id,
                street=state.round.value,
                action=str(action),
                reason=action.reason,
                pot=state.pot,
                stack=state.my_stack,
            )
        )
        # Keep history bounded.
        if len(self.history) > 1000:
            self.history = self.history[-1000:]

    # ------------------------------------------------------------------ #
    # Stop conditions
    # ------------------------------------------------------------------ #
    def _should_stop(self) -> bool:
        return self._stop_reason() is not None

    def _stop_reason(self) -> str | None:
        if self._stop_requested:
            return "stop requested"
        if self.stats.hands_played >= self.cfg.max_hands:
            return f"reached MAX_HANDS ({self.cfg.max_hands})"
        if self.stats.elapsed_minutes >= self.cfg.max_minutes:
            return f"reached MAX_MINUTES ({self.cfg.max_minutes})"
        if -self.stats.profit_in_bb >= self.cfg.stop_loss_bb:
            return f"hit STOP_LOSS ({self.cfg.stop_loss_bb}bb)"
        return None

    def stop(self) -> None:
        """Request a cooperative shutdown of the play loop."""
        logger.info("Stop requested")
        self._stop_requested = True

    # ------------------------------------------------------------------ #
    # Dashboard snapshot
    # ------------------------------------------------------------------ #
    def status(self) -> dict:
        """Return a JSON-serialisable snapshot for the dashboard."""
        state = self.last_state
        return {
            "running": self.running,
            "error": self.error,
            "stop_reason": self._stop_reason(),
            "stats": self.stats.as_dict(),
            "last_action": str(self.last_action) if self.last_action else None,
            "table": {
                "hand_id": state.hand_id if state else None,
                "street": state.round.value if state else None,
                "pot": state.pot if state else 0.0,
                "my_stack": state.my_stack if state else 0.0,
                "my_cards": [str(c) for c in state.my_cards] if state else [],
                "board": [str(c) for c in state.community_cards] if state else [],
                "is_my_turn": state.is_my_turn if state else False,
            },
            "history_len": len(self.history),
        }


__all__ = ["SessionManager", "SessionConfig", "DecisionRecord"]
