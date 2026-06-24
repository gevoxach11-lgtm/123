"""Session manager and stats tracker.

:class:`SessionManager` runs the core decision loop given pre-built engine,
scraper and executor dependencies. It polls the table, detects new hands /
streets, asks the engine for a decision, optionally executes it (auto-play),
tracks statistics and enforces session limits (max hands, max minutes,
stop-loss).

Browser / auth / lobby orchestration lives in :func:`prepare_session`, which
wires up all the dependencies and returns a ready :class:`SessionManager`
together with the :class:`BrowserManager` owning the page (so the caller can
close it). The Streamlit dashboard uses these pieces from a background thread.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime

import config as _config_module
from bot.auth import AuthManager
from bot.browser import BrowserManager
from bot.engine import PokerEngine
from bot.executor import ActionExecutor
from bot.lobby import LobbyNavigator
from bot.scraper import TableScraper
from poker.models import Action, GameState, SessionStats
from poker.montecarlo import monte_carlo_equity
from utils.errors import capture_page_error
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
    equity: float | None = None


@dataclass
class SessionConfig:
    """Per-run options chosen in the UI before a session starts."""

    headless: bool = field(default_factory=lambda: _config_module.HEADLESS)
    auto_play: bool = field(default_factory=lambda: _config_module.AUTO_PLAY)
    table_limit: str = field(default_factory=lambda: _config_module.TABLE_LIMIT)
    max_hands: int = field(default_factory=lambda: _config_module.SESSION["MAX_HANDS"])
    max_minutes: int = field(default_factory=lambda: _config_module.SESSION["MAX_MINUTES"])
    stop_loss_bb: int = field(default_factory=lambda: _config_module.SESSION["STOP_LOSS"])
    dry_run: bool = False  # if True, decide but don't click
    manual_login: bool = field(default_factory=lambda: _config_module.MANUAL_LOGIN)


class SessionManager:
    """Run and supervise the decision loop for a single play session."""

    def __init__(self, config, engine, scraper, executor) -> None:
        self.config = config
        self.engine = engine
        self.scraper = scraper
        self.executor = executor

        self.stats = SessionStats(
            hands_played=0,
            hands_won=0,
            profit_loss=0.0,
            session_start=datetime.now(),
            bb_per_100=0.0,
            big_blind=self._big_blind(),
        )

        self.running = False
        self.last_hand_id = None
        self.acted_this_hand = False
        self.last_stack = 0.0
        self.auto_play = False
        self.on_state_update = None  # callback(dict) for UI updates

        # Dashboard / introspection extras.
        self.history: list[DecisionRecord] = []
        self.last_state: GameState | None = None
        self.last_action: Action | None = None
        self.last_equity: float | None = None
        self.error: str | None = None
        self.stop_reason: str | None = None

    # ------------------------------------------------------------------ #
    # Config helpers
    # ------------------------------------------------------------------ #
    def _big_blind(self) -> float:
        getter = getattr(self.config, "big_blind_for", None)
        return getter() if callable(getter) else 0.10

    @property
    def max_hands(self) -> int:
        return getattr(self.config, "SESSION", {}).get("MAX_HANDS", 500)

    @property
    def max_minutes(self) -> int:
        return getattr(self.config, "SESSION", {}).get("MAX_MINUTES", 90)

    @property
    def stop_loss_bb(self) -> int:
        return getattr(self.config, "SESSION", {}).get("STOP_LOSS", 200)

    @property
    def poll_interval(self) -> float:
        return getattr(self.config, "TIMING", {}).get("POLL_INTERVAL", 0.4)

    @property
    def mc_display_sims(self) -> int:
        return int((getattr(self.config, "MC", {}) or {}).get("SESSION_DISPLAY_SIMS", 3000))

    async def _capture_error(self, label: str, exc: Exception) -> None:
        page = getattr(self.scraper, "page", None)
        await capture_page_error(page, self.config, label, exc)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        self.running = True
        self.error = None
        self.stop_reason = None
        logger.info("Session starting (auto_play={})", self.auto_play)
        try:
            await self._loop()
        except asyncio.CancelledError:
            logger.warning("Session cancelled")
            raise
        except Exception as exc:  # pragma: no cover - runtime safety net
            self.error = f"{type(exc).__name__}: {exc}"
            await self._capture_error("session_crash", exc)
        finally:
            self.running = False
            logger.info("Session ended ({}) | {}",
                        self.stop_reason or "stopped", self.stats.as_dict())

    async def stop(self) -> None:
        logger.info("Stop requested")
        self.running = False

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    async def _loop(self) -> None:
        poll = self.poll_interval
        while self.running:
            # --- Session limit checks ---
            elapsed_min = self.stats.elapsed_minutes
            if self.stats.hands_played >= self.max_hands:
                self.stop_reason = f"reached MAX_HANDS ({self.max_hands})"
                break
            if elapsed_min >= self.max_minutes:
                self.stop_reason = f"reached MAX_MINUTES ({self.max_minutes})"
                break
            if -self.stats.profit_in_bb >= self.stop_loss_bb:
                self.stop_reason = f"hit STOP_LOSS ({self.stop_loss_bb}bb)"
                break

            try:
                # --- Is it our turn? ---
                if not await self.scraper.is_my_turn():
                    await asyncio.sleep(poll)
                    continue

                state = await self.scraper.get_game_state()
                if not state or len(state.my_cards) < 2:
                    await asyncio.sleep(poll)
                    continue
                self.last_state = state

                # --- New hand / street detection ---
                hand_id = self._make_hand_id(state)
                if hand_id != self.last_hand_id:
                    self.last_hand_id = hand_id
                    self.acted_this_hand = False
                    self.stats.hands_played += 1
                    if self.last_stack > 0:
                        delta = state.my_stack - self.last_stack
                        self.stats.profit_loss += delta
                        if delta > 0:
                            self.stats.hands_won += 1
                    self.last_stack = state.my_stack
                    self.update_bb_per_100(state.big_blind or self.stats.big_blind)

                if self.acted_this_hand:
                    await asyncio.sleep(poll)
                    continue

                # --- Decision ---
                action = self.engine.decide(state)
                self.last_action = action

                # --- Equity for display (postflop only) ---
                equity = None
                if len(state.community_cards) >= 3:
                    try:
                        villains = max(1, state.num_active - 1)
                        equity = monte_carlo_equity(
                            list(state.my_cards), list(state.community_cards),
                            villain_count=villains, n_sims=self.mc_display_sims,
                        )
                    except Exception as exc:
                        await self._capture_error("session_equity", exc)
                        equity = None
                self.last_equity = equity
                self._record(state, action, equity)

                # --- UI callback ---
                if self.on_state_update:
                    try:
                        self.on_state_update({
                            "state": state,
                            "action": action,
                            "equity": equity,
                            "stats": self.stats,
                        })
                    except Exception as exc:  # pragma: no cover
                        logger.warning("on_state_update callback error: {}", exc)

                eq_str = f"{equity:.1%}" if equity is not None else "n/a"
                logger.info(
                    "Hand #{} | {} | Board: {} | Equity: {} | Decision: {} {}",
                    self.stats.hands_played,
                    [str(c) for c in state.my_cards],
                    [str(c) for c in state.community_cards],
                    eq_str, action.type.value, action.amount,
                )

                # --- Execute (auto-play) ---
                if self.auto_play:
                    ok = await self.executor.execute(action, state)
                    if ok:
                        self.acted_this_hand = True

                await asyncio.sleep(poll)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                await self._capture_error("session_loop", exc)
                await asyncio.sleep(poll)

        self.running = False
        logger.info("Loop exited: {}", self.stop_reason or "stop requested")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _make_hand_id(self, state: GameState) -> str:
        return "".join(
            c.rank + c.suit for c in list(state.my_cards) + list(state.community_cards)
        )

    def _record(self, state: GameState, action: Action, equity: float | None) -> None:
        self.history.append(
            DecisionRecord(
                timestamp=time.time(),
                hand_id=state.hand_id or self.last_hand_id or "",
                street=state.round.value,
                action=str(action),
                reason=action.reason,
                pot=state.pot,
                stack=state.my_stack,
                equity=equity,
            )
        )
        if len(self.history) > 1000:
            self.history = self.history[-1000:]

    def update_bb_per_100(self, bb_size: float) -> None:
        if bb_size and bb_size > 0:
            self.stats.big_blind = bb_size
            self.stats.bb_per_100 = (
                (self.stats.profit_loss / bb_size) / max(self.stats.hands_played, 1) * 100
            )

    def get_stats_dict(self) -> dict:
        """Return stats as a plain dict for UI rendering."""
        data = self.stats.as_dict()
        data["running"] = self.running
        data["stop_reason"] = self.stop_reason
        return data

    # ------------------------------------------------------------------ #
    # Dashboard snapshot
    # ------------------------------------------------------------------ #
    def status(self) -> dict:
        state = self.last_state
        return {
            "running": self.running,
            "error": self.error,
            "stop_reason": self.stop_reason,
            "auto_play": self.auto_play,
            "stats": self.stats.as_dict(),
            "last_action": str(self.last_action) if self.last_action else None,
            "last_equity": self.last_equity,
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


# --------------------------------------------------------------------------- #
# Orchestration: build a ready-to-run session (browser + auth + lobby + deps)
# --------------------------------------------------------------------------- #
async def prepare_session(
    config,
    cfg: SessionConfig,
    on_state_update=None,
    login_event=None,
    on_phase=None,
):
    """Launch the browser, log in, join a table and wire up a SessionManager."""
    headless = cfg.headless
    if cfg.manual_login and headless:
        logger.warning("Manual login requires a visible browser; forcing headless=False")
        headless = False

    browser = BrowserManager(config, headless=headless)
    page = None
    try:
        page = await browser.launch()
        auth = AuthManager(page, config)
        if on_phase:
            on_phase("waiting_login")
        if not await auth.ensure_logged_in(
            manual=cfg.manual_login,
            confirm_event=login_event,
        ):
            raise RuntimeError(
                "Login failed — log in manually in the browser or set MANUAL_LOGIN=false "
                "with credentials in .env"
            )
        if on_phase:
            on_phase("joining_table")

        lobby = LobbyNavigator(page, config)
        if not await lobby.find_and_join(cfg.table_limit):
            raise RuntimeError("Could not join a table")

        engine = PokerEngine(config)
        scraper = TableScraper(page, config)
        executor = ActionExecutor(page, config, dry_run=cfg.dry_run or not cfg.auto_play)

        manager = SessionManager(config, engine, scraper, executor)
        manager.auto_play = cfg.auto_play and not cfg.dry_run
        manager.on_state_update = on_state_update
        return manager, browser
    except Exception as exc:
        if page is not None:
            await capture_page_error(page, config, "prepare_session", exc)
        await browser.close()
        raise


__all__ = ["SessionManager", "SessionConfig", "DecisionRecord", "prepare_session"]
