"""AdjaraPoker Bot - Streamlit dashboard.

Provides a control panel to start/stop the bot, live session statistics,
profit charts, a decision history table, and a standalone equity calculator
for ad-hoc analysis.

Run with::

    streamlit run app.py

The bot itself runs in a background thread with its own asyncio event loop so
the Streamlit UI thread stays responsive.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from bot.session import SessionConfig, SessionManager, prepare_session
from poker.models import Card
from poker.montecarlo import estimate_equity
from poker.evaluator import describe
from utils.logger import setup_logger

setup_logger()

st.set_page_config(
    page_title="AdjaraPoker Bot",
    page_icon="\u2660",
    layout="wide",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------------------- #
# Background session runner
# --------------------------------------------------------------------------- #
class SessionRunner:
    """Own the browser + :class:`SessionManager` in a dedicated thread/loop."""

    def __init__(self) -> None:
        self.manager: SessionManager | None = None
        self.browser = None
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.error: str | None = None
        self.stop_requested = False

    def start(self, cfg: SessionConfig) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.manager = None
        self.browser = None
        self.error = None
        self.stop_requested = False

        def _run() -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._session(cfg))
            except Exception as exc:  # pragma: no cover - surfaced in UI
                self.error = f"{type(exc).__name__}: {exc}"
            finally:
                self.loop.close()

        self.thread = threading.Thread(target=_run, daemon=True, name="bot-session")
        self.thread.start()

    async def _session(self, cfg: SessionConfig) -> None:
        try:
            self.manager, self.browser = await prepare_session(config, cfg)
            if self.stop_requested:  # stop pressed during setup
                return
            await self.manager.start()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            if self.manager:
                self.manager.error = self.error
        finally:
            if self.browser is not None:
                try:
                    await self.browser.close()
                except Exception:
                    pass

    def stop(self) -> None:
        self.stop_requested = True
        if self.manager:
            self.manager.running = False

    @property
    def is_running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())


@st.cache_resource
def get_runner() -> SessionRunner:
    """A single runner shared across Streamlit reruns."""
    return SessionRunner()


runner = get_runner()


# --------------------------------------------------------------------------- #
# Sidebar - configuration & controls
# --------------------------------------------------------------------------- #
def render_sidebar() -> SessionConfig:
    st.sidebar.title("\u2660 AdjaraPoker Bot")
    st.sidebar.caption("Automated 6-max NLHE assistant")

    st.sidebar.subheader("Session settings")
    table_limit = st.sidebar.selectbox(
        "Table limit",
        options=list(config.LIMIT_BIG_BLINDS.keys()),
        index=list(config.LIMIT_BIG_BLINDS.keys()).index(config.TABLE_LIMIT)
        if config.TABLE_LIMIT in config.LIMIT_BIG_BLINDS else 2,
    )
    headless = st.sidebar.checkbox("Headless browser", value=config.HEADLESS)
    auto_play = st.sidebar.checkbox(
        "Auto-play (click buttons)", value=config.AUTO_PLAY,
        help="When off, the bot analyses and logs decisions but does not click.",
    )
    dry_run = st.sidebar.checkbox(
        "Dry run", value=not config.AUTO_PLAY,
        help="Decide without executing any clicks (overrides auto-play).",
    )

    st.sidebar.subheader("Safety limits")
    max_hands = st.sidebar.number_input(
        "Max hands", min_value=1, max_value=100000, value=config.SESSION["MAX_HANDS"], step=10)
    max_minutes = st.sidebar.number_input(
        "Max minutes", min_value=1, max_value=1440, value=config.SESSION["MAX_MINUTES"], step=5)
    stop_loss = st.sidebar.number_input(
        "Stop loss (bb)", min_value=1, max_value=100000, value=config.SESSION["STOP_LOSS"], step=10)

    cfg = SessionConfig(
        headless=headless,
        auto_play=auto_play,
        table_limit=table_limit,
        max_hands=int(max_hands),
        max_minutes=int(max_minutes),
        stop_loss_bb=int(stop_loss),
        dry_run=dry_run,
    )

    st.sidebar.subheader("Controls")
    c1, c2 = st.sidebar.columns(2)
    if c1.button("\u25b6 Start", use_container_width=True, disabled=runner.is_running):
        runner.start(cfg)
        st.sidebar.success("Session started")
    if c2.button("\u23f9 Stop", use_container_width=True, disabled=not runner.is_running):
        runner.stop()
        st.sidebar.warning("Stopping...")

    auto_refresh = st.sidebar.checkbox("Auto-refresh (2s)", value=True)
    st.session_state["_auto_refresh"] = auto_refresh

    with st.sidebar.expander("Effective config"):
        st.json(config.as_dict())

    return cfg


# --------------------------------------------------------------------------- #
# Main dashboard
# --------------------------------------------------------------------------- #
def render_status() -> None:
    st.header("Live session")
    manager = runner.manager

    if not manager:
        if runner.error:
            st.error(f"Could not start session: {runner.error}")
        elif runner.is_running:
            st.info("Connecting: launching browser, logging in and joining a table...")
        else:
            st.info("No session yet. Configure options in the sidebar and press **Start**.")
        return

    status = manager.status()

    # Status banner.
    if status["running"]:
        st.success("Bot is running")
    elif status["error"]:
        st.error(f"Stopped: {status['error']}")
    elif status["stop_reason"]:
        st.warning(f"Stopped: {status['stop_reason']}")
    else:
        st.info("Idle")

    stats = status["stats"]
    table = status["table"]

    # Key metrics.
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Hands", stats["hands_played"])
    m2.metric("Win rate", f"{stats['win_rate'] * 100:.1f}%")
    m3.metric("Profit", f"{stats['profit_loss']:+.2f}")
    m4.metric("bb/100", f"{stats['bb_per_100']:+.1f}")
    m5.metric("Elapsed", f"{stats['elapsed_minutes']:.1f} min")

    # Current table view.
    st.subheader("Table")
    t1, t2 = st.columns(2)
    with t1:
        st.write(f"**Hand:** {table['hand_id'] or '-'}")
        st.write(f"**Street:** {table['street'] or '-'}")
        st.write(f"**My turn:** {'yes' if table['is_my_turn'] else 'no'}")
    with t2:
        st.write(f"**Pot:** {table['pot']:.2f}")
        st.write(f"**My stack:** {table['my_stack']:.2f}")
        hole = " ".join(table["my_cards"]) or "??"
        board = " ".join(table["board"]) or "-"
        st.write(f"**Hole:** {hole}  |  **Board:** {board}")

    if status["last_action"]:
        st.info(f"Last decision: {status['last_action']}")

    # Profit chart from history (stack over time).
    if manager.history:
        df = pd.DataFrame(
            [
                {
                    "time": time.strftime("%H:%M:%S", time.localtime(r.timestamp)),
                    "stack": r.stack,
                    "pot": r.pot,
                    "hand_id": r.hand_id,
                    "street": r.street,
                    "action": r.action,
                    "reason": r.reason,
                }
                for r in manager.history
            ]
        )
        st.subheader("Stack over time")
        fig = go.Figure()
        fig.add_trace(go.Scatter(y=df["stack"], mode="lines+markers", name="Stack"))
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                          yaxis_title="Stack", xaxis_title="Decision #")
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Decision history")
        st.dataframe(df[::-1], use_container_width=True, height=320)
    else:
        st.caption("No decisions recorded yet.")


# --------------------------------------------------------------------------- #
# Equity calculator tool
# --------------------------------------------------------------------------- #
def _parse_cards(text: str) -> list[Card]:
    cards: list[Card] = []
    for token in text.replace(",", " ").split():
        cards.append(Card.from_str(token))
    return cards


def render_equity_tool() -> None:
    st.header("Equity calculator")
    st.caption("Estimate hero equity via Monte-Carlo simulation. "
               "Enter cards like `Ah Kd`, board like `Qs Jc 2h`.")

    c1, c2, c3 = st.columns(3)
    hole_text = c1.text_input("Hole cards", value="Ah Kd")
    board_text = c2.text_input("Board (0-5)", value="")
    opponents = c3.number_input("Opponents", min_value=1, max_value=8, value=1)
    iterations = st.slider("Iterations", 500, 20000, 5000, step=500)

    if st.button("Calculate equity"):
        try:
            hole = _parse_cards(hole_text)
            board = _parse_cards(board_text) if board_text.strip() else []
            if len(hole) != 2:
                st.error("Enter exactly two hole cards.")
                return
            with st.spinner("Simulating..."):
                result = estimate_equity(
                    hole, board, num_opponents=int(opponents), iterations=int(iterations)
                )
            e1, e2, e3, e4 = st.columns(4)
            e1.metric("Equity", f"{result.equity * 100:.1f}%")
            e2.metric("Win", f"{result.win * 100:.1f}%")
            e3.metric("Tie", f"{result.tie * 100:.1f}%")
            e4.metric("Lose", f"{result.lose * 100:.1f}%")
            if board:
                st.write(f"Current made hand: **{describe(hole + board)}**")
        except Exception as exc:
            st.error(f"Could not parse input: {exc}")


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
def main() -> None:
    render_sidebar()
    tab_live, tab_equity, tab_about = st.tabs(["Live session", "Equity tool", "About"])

    with tab_live:
        render_status()
    with tab_equity:
        render_equity_tool()
    with tab_about:
        st.markdown(
            """
            ### AdjaraPoker Bot

            A Playwright-driven poker automation framework with a Streamlit
            control panel.

            **Components**
            - `bot/` - browser, auth, lobby, scraper, engine, executor, session
            - `poker/` - models, 7-card evaluator, Monte-Carlo equity, GTO ranges
            - `utils/` - logging, stealth, human-like interaction

            > **Disclaimer:** This project is for educational purposes. Automating
            > play on real-money sites may violate their terms of service and/or
            > local law. Use responsibly and at your own risk. The CSS selectors
            > in `config.py` are placeholders and must be updated for the live
            > client before any real use.
            """
        )

    # Lightweight auto-refresh while running.
    if st.session_state.get("_auto_refresh") and runner.is_running:
        time.sleep(2)
        st.rerun()


if __name__ == "__main__":
    main()
