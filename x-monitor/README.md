# X Monitor (Elon + Trump) - Playwright Version

This project watches X posts for:
- `@elonmusk`
- `@trumpchinese1`

When a new post appears, it sends a notification (Telegram) or prints to terminal.

## 1) Requirements
- Python 3.10+
- Playwright Chromium browser
- Google Chrome installed locally (recommended for X login reliability)
- Optional: Telegram bot + chat id for push alerts

## 2) Setup
```bash
cd /Users/paulz/Documents/New\ project/x-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

## 3) Login once (recommended)
X often limits/changes timeline content for logged-out sessions.  
To reliably monitor latest posts, save a logged-in browser profile:

```bash
cd /Users/paulz/Documents/New\ project/x-monitor
source .venv/bin/activate
python src/save_login_state.py
```

After you log in in the opened browser and press Enter, it saves:
- `auth_state.json` (cookie/localStorage snapshot)
- `x_user_data/` (persistent browser profile)

Set in `.env`:

```env
PLAYWRIGHT_BROWSER_CHANNEL=chrome
PLAYWRIGHT_STORAGE_STATE=auth_state.json
PLAYWRIGHT_USER_DATA_DIR=x_user_data
X_REQUIRE_LOGIN=true
```

## 4) Run monitor
```bash
cd /Users/paulz/Documents/New\ project/x-monitor
source .venv/bin/activate
python src/monitor.py
```

On first run, it seeds recent posts (no spam for old posts). After that, every new post triggers an alert.

## Notes
- This version does not require X API tokens.
- If login expires, rerun `python src/save_login_state.py`.
- If X says \"browser is not secure\", make sure `PLAYWRIGHT_BROWSER_CHANNEL=chrome` and retry login.
- If still blocked, set `HEADLESS=false`, rerun login once, then switch back to `HEADLESS=true` for monitor.
- X page structure can change over time; if selectors break, scraping logic may need updates.
- For 24/7 use, run via `launchd`, `pm2`, or `supervisord`.
