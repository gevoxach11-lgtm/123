# AdjaraPoker Bot

Automated 6-max No-Limit Hold'em assistant for Adjarabet. Uses Playwright for browser automation, a Python poker engine for decisions, and a Streamlit dashboard for control and live stats.

> **Disclaimer:** For educational purposes only. Automating play on real-money sites may violate their terms of service. Use responsibly and at your own risk.

## Requirements

- Python 3.11+
- Chromium (installed via Playwright)

## Install

```bash
git clone <your-repo-url>
cd adjarabet-bot
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# Edit .env with your credentials
streamlit run app.py
```

## Configuration

Copy `.env.example` to `.env` and set:

| Variable | Description |
|----------|-------------|
| `ADJARABET_USERNAME` | Account username |
| `ADJARABET_PASSWORD` | Account password |
| `TABLE_LIMIT` | Stake limit (e.g. `NL10`) |
| `PROXY_SERVER` | Optional SOCKS5 proxy |
| `HEADLESS` | Run browser headless (`true`/`false`) |
| `AUTO_PLAY` | Click buttons automatically (`true`/`false`) |

All CSS selectors, timing, session limits and geometry constants live in `config.py`. Update `SELECTORS` and `SELECTOR_LISTS` after inspecting the live poker client markup.

## Project layout

```
adjarabet-bot/
├── app.py              # Streamlit dashboard
├── config.py           # Settings, selectors, timing
├── bot/                # Browser automation + session loop
├── poker/              # Hand evaluator, equity, ranges
├── utils/              # Logging, stealth, human input
└── logs/               # Session logs + error screenshots
```

## Usage

1. Start the dashboard: `streamlit run app.py`
2. Enter credentials and table settings in the sidebar
3. Click **Start** — the bot logs in, joins a table, and begins polling
4. Enable **Auto-Play** to execute decisions (off by default = analyse/log only)
5. Click **Stop** to end the session

## Logs & debugging

- Session logs: `logs/session_YYYY-MM-DD.log`
- Error screenshots: `logs/error_*.png` (saved automatically on failures)
- Set `DEBUG=true` in `.env` for HTML dumps when the scraper cannot detect a table
