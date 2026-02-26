# X Monitor (Playwright)

Monitors X posts for selected accounts using Playwright (no X API token required).

Current default targets:
- `@elonmusk`
- `@trumpchinese1`

It emits structured JSON logs and can also push `new_post` alerts to Telegram.

## Features
- Monitors latest posts from configured X accounts.
- Filters out pinned posts.
- Detects reposts and includes origin post metadata.
- Includes post metrics when available (views, likes).
- Deduplicates `new_post` alerts using SQLite.
- Deduplicates `status` logs when latest non-pinned post has not changed.
- Emits machine-friendly JSON logs (one JSON object per line).

## Requirements
- Python 3.10+
- macOS/Linux shell
- Google Chrome installed
- Optional: Telegram bot token + chat ID

## Setup
```bash
cd "/Users/paulz/Documents/New project/x-monitor"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

## Configure
Edit `.env`.

Important keys:
- `X_ACCOUNTS`: comma-separated handles to monitor
- `POLL_SECONDS`: check interval
- `PLAYWRIGHT_BROWSER_CHANNEL=chrome`: use local Chrome for login reliability
- `PLAYWRIGHT_USER_DATA_DIR`: persistent browser profile (recommended)
- `PLAYWRIGHT_STORAGE_STATE`: optional auth snapshot file
- `X_REQUIRE_LOGIN=true`: fail fast if session is not authenticated
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`: optional push alerts

## Login Once
Create a persistent logged-in session:

```bash
cd "/Users/paulz/Documents/New project/x-monitor"
source .venv/bin/activate
python src/save_login_state.py
```

When the browser opens:
1. Sign in to X.
2. Return to terminal and press Enter.

This stores:
- `auth_state.json`
- `x_user_data/`

## Run
```bash
cd "/Users/paulz/Documents/New project/x-monitor"
source .venv/bin/activate
python src/monitor.py
```

## JSON Log Output
The monitor prints JSON lines with `event` types:
- `heartbeat`: fetch and next-fetch times
- `status`: no-new-post state (only when latest visible non-pinned post changes)
- `new_post`: newly detected post payload
- `error`: per-account check failures

### Example
```json
{"ts_utc":"2026-02-26 06:21:18 UTC","event":"heartbeat","stage":"fetch","fetch_time_utc":"2026-02-26 06:21:18 UTC"}
{"ts_utc":"2026-02-26 06:21:20 UTC","event":"status","account":"elonmusk","state":"no_new_post","latest_non_pinned_post":{"id":"123","time":"2026-02-26T05:00:00.000Z","author_handle":"elonmusk","is_repost":false,"text":"...","url":"https://x.com/elonmusk/status/123"}}
{"ts_utc":"2026-02-26 06:21:40 UTC","event":"new_post","account":"elonmusk","author_name":"Elon Musk","author_handle":"elonmusk","is_repost":false,"post_time":"2026-02-26T06:21:35.000Z","views":"1.2M","likes":"45K","post_id":"456","post_url":"https://x.com/elonmusk/status/456","post_text":"..."}
{"ts_utc":"2026-02-26 06:22:28 UTC","event":"heartbeat","stage":"next_fetch","next_fetch_time_utc":"2026-02-26 06:22:28 UTC"}
```

## Data Storage
SQLite database file (`DB_PATH`, default `monitor_state.db`) stores:
- `seen_posts`: prevents duplicate new-post alerts
- `status_log_state`: prevents repeated unchanged status logs

## Troubleshooting
- `browser is not secure` on X login:
  - Ensure `PLAYWRIGHT_BROWSER_CHANNEL=chrome`
  - Re-run login: `python src/save_login_state.py`
- Login expired:
  - Re-run `python src/save_login_state.py`
- No posts found:
  - Confirm account handle is correct in `X_ACCOUNTS`
  - Confirm your X session is valid
- 24/7 operation:
  - Run under `launchd`, `pm2`, or `supervisord`

## Security Notes
- Do not commit `.env`, `auth_state.json`, or `x_user_data/`.
- Telegram credentials in `.env` should be treated as secrets.
