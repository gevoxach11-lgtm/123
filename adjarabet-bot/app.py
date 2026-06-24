"""AdjaraPoker Bot - Streamlit control dashboard.

Start/stop the bot, watch the live hand, the engine's decision (with an equity
gauge), session statistics + P/L chart, and a scrolling log.

Run with::

    streamlit run app.py

The bot runs in a background thread with its own asyncio event loop (managed by
a cached :class:`SessionRunner`) so the Streamlit UI thread stays responsive and
state survives reruns.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

import config as cfg
from bot.session import SessionConfig, prepare_session
from poker.montecarlo import pot_odds
from utils.logger import setup_logger

setup_logger()

st.set_page_config(
    page_title="Adjarabet Poker Bot",
    page_icon="\U0001F0CF",  # playing card
    layout="wide",
    initial_sidebar_state="expanded",
)

# Action -> colour for the big decision text.
ACTION_COLORS = {
    "raise": "#21ba45",
    "bet": "#21ba45",
    "call": "#fbbd08",
    "check": "#00b5ad",
    "fold": "#db2828",
}


# --------------------------------------------------------------------------- #
# Shared singletons (survive Streamlit reruns)
# --------------------------------------------------------------------------- #
class SessionRunner:
    """Own the browser + SessionManager in a dedicated thread/event loop."""

    def __init__(self) -> None:
        self.manager = None
        self.browser = None
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.error: str | None = None
        self.stop_requested = False
        self.update_data: dict | None = None
        self.pl_history: list[tuple[int, float]] = []
        self.phase = "idle"
        self.login_event = threading.Event()

    def _on_update(self, data: dict) -> None:
        self.update_data = data
        stats = data.get("stats")
        if stats is not None:
            self.pl_history.append((stats.hands_played, round(stats.profit_loss, 2)))

    def start(self, sconf: SessionConfig) -> None:
        if self.is_running:
            return
        self.manager = None
        self.browser = None
        self.error = None
        self.stop_requested = False
        self.update_data = None
        self.pl_history = []
        self.phase = "starting"
        self.login_event.clear()

        def _run() -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._session(sconf))
            except Exception as exc:  # pragma: no cover - surfaced in UI
                self.error = f"{type(exc).__name__}: {exc}"
            finally:
                self.loop.close()

        self.thread = threading.Thread(target=_run, daemon=True, name="bot-session")
        self.thread.start()

    async def _session(self, sconf: SessionConfig) -> None:
        try:
            self.manager, self.browser = await prepare_session(
                cfg,
                sconf,
                on_state_update=self._on_update,
                login_event=self.login_event,
                on_phase=lambda phase: setattr(self, "phase", phase),
            )
            if self.stop_requested:
                return
            self.phase = "running"
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
    return SessionRunner()


@st.cache_resource
def get_log_buffer() -> deque:
    """A bounded log buffer fed by a loguru sink (registered once)."""
    buffer: deque = deque(maxlen=200)
    logger.add(
        lambda message: buffer.append(message.record["time"].strftime("%H:%M:%S")
                                      + f" | {message.record['level'].name:<7} | "
                                      + message.record["message"]),
        level="INFO",
        enqueue=True,
    )
    return buffer


runner = get_runner()
log_buffer = get_log_buffer()


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
def render_sidebar() -> SessionConfig | None:
    with st.sidebar:
        st.title("\U0001F0CF Poker Bot")
        st.divider()

        st.subheader("Login")
        st.info(
            "Click **Start** — a Chromium browser opens at **adjarabet.am**. "
            "Log in there manually, then click **Continue** below."
        )

        st.subheader("Table Settings")
        limits = ["NL2", "NL5", "NL10", "NL25", "NL50"]
        default_idx = limits.index(cfg.TABLE_LIMIT) if cfg.TABLE_LIMIT in limits else 2
        table_limit = st.selectbox("Limit", limits, index=default_idx)
        buyin_bb = st.slider("Buy-in (BB)", 40, 100, 100)
        stop_loss = st.number_input("Stop Loss (\u20be)", value=50.0, min_value=1.0)

        st.subheader("Bot Settings")
        auto_play = st.toggle("\U0001F916 Auto-Play", value=cfg.AUTO_PLAY)
        proxy = st.text_input("SOCKS5 Proxy", value=cfg.PROXY_SERVER)

        st.divider()
        col1, col2 = st.columns(2)
        start_btn = col1.button("\u25b6 Start", use_container_width=True, type="primary",
                                disabled=runner.is_running)
        stop_btn = col2.button("\u23f9 Stop", use_container_width=True,
                               disabled=not runner.is_running)

        if runner.is_running and runner.phase == "waiting_login":
            st.warning("Browser open — log in on adjarabet.am, then click Continue.")
            if st.button("\u2713 Continue after login", use_container_width=True, type="primary"):
                runner.login_event.set()
                st.toast("Continuing...")

    if start_btn and not runner.is_running:
        cfg.PROXY_SERVER = proxy
        cfg.TABLE_LIMIT = table_limit
        cfg.MANUAL_LOGIN = True
        bb = cfg.big_blind_for(table_limit) or 0.10
        cfg.SESSION["STOP_LOSS"] = max(1, int(stop_loss / bb))
        sconf = SessionConfig(
            headless=False,
            auto_play=auto_play,
            table_limit=table_limit,
            stop_loss_bb=cfg.SESSION["STOP_LOSS"],
            dry_run=not auto_play,
            manual_login=True,
        )
        runner.start(sconf)
        st.toast("Opening browser at adjarabet.am...")

    if stop_btn:
        runner.stop()
        st.toast("Stopping session...")

    return None


# --------------------------------------------------------------------------- #
# Snapshot helpers
# --------------------------------------------------------------------------- #
def current_snapshot():
    """Return (state, action, equity, stats) from the latest update/manager."""
    data = runner.update_data
    if data:
        return data.get("state"), data.get("action"), data.get("equity"), data.get("stats")
    manager = runner.manager
    if manager:
        return manager.last_state, manager.last_action, manager.last_equity, manager.stats
    return None, None, None, None


def status_label() -> str:
    if runner.error:
        return "\U0001F534 Error"
    if runner.is_running and runner.manager and runner.manager.running:
        return "\U0001F7E2 Running"
    if runner.phase == "waiting_login":
        return "\U0001F7E1 Waiting for login"
    if runner.is_running:
        return "\U0001F7E1 Connecting"
    return "\U0001F534 Stopped"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_status_bar(stats, equity) -> None:
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("Status", status_label())
    s2.metric("Hands", stats.hands_played if stats else 0)
    s3.metric("Profit", f"{stats.profit_loss:+.2f} \u20be" if stats else "0.00 \u20be")
    s4.metric("bb/100", f"{stats.bb_per_100:+.1f}" if stats else "0.0")
    s5.metric("Equity", f"{equity * 100:.1f}%" if equity is not None else "-")


def render_live_game(state, action, equity) -> None:
    col_game, col_decision = st.columns([2, 1])

    with col_game:
        st.subheader("Current Hand")
        if state is None:
            st.info("Waiting for a hand...")
        else:
            hole = "  ".join(str(c) for c in state.my_cards) or "?? ??"
            board = "  ".join(str(c) for c in state.community_cards) or "-"
            st.markdown(f"### Hole: `{hole}`")
            st.markdown(f"**Board:** `{board}`  ·  **Street:** {state.round.value}")
            st.markdown(
                f"**Pot:** {state.pot:.2f} \u20be  ·  **My stack:** {state.my_stack:.2f} \u20be"
                f"  ·  **To call:** {state.call_amount:.2f} \u20be"
            )
            if state.players:
                df = pd.DataFrame(
                    [
                        {
                            "Seat": p.seat,
                            "Name": p.name or "?",
                            "Stack": round(p.stack, 2),
                            "Bet": round(p.bet, 2),
                            "Active": p.is_active,
                            "Hero": p.is_hero,
                            "Dealer": p.is_dealer,
                        }
                        for p in state.players
                    ]
                )
                st.dataframe(df, use_container_width=True, hide_index=True)

    with col_decision:
        st.subheader("Bot Decision")
        # Equity gauge.
        gauge_val = (equity or 0.0) * 100
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=gauge_val,
            number={"suffix": "%"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "#2185d0"},
                "steps": [
                    {"range": [0, 40], "color": "#3b1f24"},
                    {"range": [40, 60], "color": "#3a371f"},
                    {"range": [60, 100], "color": "#1f3a24"},
                ],
            },
            title={"text": "Equity"},
        ))
        fig.update_layout(height=240, margin=dict(l=20, r=20, t=40, b=10))
        st.plotly_chart(fig, use_container_width=True)

        # Big colored action text.
        if action is not None:
            color = ACTION_COLORS.get(action.type.value, "#ffffff")
            amount = f" {action.amount:.2f}\u20be" if action.amount else ""
            st.markdown(
                f"<div style='font-size:2.2rem;font-weight:800;color:{color};"
                f"text-align:center;padding:10px 0'>{action.type.value.upper()}{amount}</div>",
                unsafe_allow_html=True,
            )
            if action.reason:
                st.caption(action.reason)
        else:
            st.markdown("<div style='text-align:center;color:#888'>No decision yet</div>",
                        unsafe_allow_html=True)

        # Pot odds.
        if state is not None and state.call_amount > 0:
            po = pot_odds(state.call_amount, state.pot)
            st.metric("Pot odds", f"{po * 100:.1f}%")


def render_stats(stats) -> None:
    if not runner.pl_history:
        st.caption("No hands recorded yet.")
    else:
        hands = [h for h, _ in runner.pl_history]
        pls = [p for _, p in runner.pl_history]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=hands, y=pls, mode="lines+markers", name="P/L",
                                 line=dict(color="#21ba45")))
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10),
                          xaxis_title="Hand #", yaxis_title="Profit / Loss (\u20be)")
        st.plotly_chart(fig, use_container_width=True)

    if stats is not None:
        st.dataframe(
            pd.DataFrame(stats.as_dict().items(), columns=["Metric", "Value"]),
            use_container_width=True, hide_index=True,
        )


def render_logs() -> None:
    lines = list(log_buffer)[-100:]
    st.code("\n".join(lines) if lines else "No logs yet.", language="log")


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
render_sidebar()

state, action, equity, stats = current_snapshot()

render_status_bar(stats, equity)
if runner.error:
    st.error(f"Session error: {runner.error}")

st.divider()

tab1, tab2, tab3 = st.tabs(["\U0001F4CA Live Game", "\U0001F4C8 Stats", "\U0001F4CB Logs"])
with tab1:
    render_live_game(state, action, equity)
with tab2:
    render_stats(stats)
with tab3:
    render_logs()

# Live auto-refresh while the bot is running.
if runner.is_running:
    time.sleep(1.5)
    st.rerun()
