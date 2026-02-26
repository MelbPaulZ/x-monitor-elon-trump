import os
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright


DEFAULT_ACCOUNTS = ["elonmusk", "trumpchinese1"]
@dataclass
class AppConfig:
    accounts: List[str]
    poll_seconds: int
    db_path: str
    telegram_bot_token: Optional[str]
    telegram_chat_id: Optional[str]
    headless: bool
    browser_channel: str
    storage_state_path: Optional[str]
    user_data_dir: Optional[str]
    require_login: bool
    page_timeout_ms: int
    max_posts_per_check: int


def _parse_bool(value: str, default: bool) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> AppConfig:
    load_dotenv()

    accounts_raw = os.getenv("X_ACCOUNTS", ",".join(DEFAULT_ACCOUNTS))
    accounts = [a.strip().lstrip("@").lower() for a in accounts_raw.split(",") if a.strip()]
    if not accounts:
        raise ValueError("X_ACCOUNTS must contain at least one username")

    poll_seconds = int(os.getenv("POLL_SECONDS", "60"))
    db_path = os.getenv("DB_PATH", "monitor_state.db")

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip() or None

    headless = _parse_bool(os.getenv("HEADLESS", "true"), default=True)
    browser_channel = os.getenv("PLAYWRIGHT_BROWSER_CHANNEL", "chrome").strip() or "chrome"
    storage_state_path = os.getenv("PLAYWRIGHT_STORAGE_STATE", "").strip() or None
    user_data_dir = os.getenv("PLAYWRIGHT_USER_DATA_DIR", "").strip() or None
    require_login = _parse_bool(os.getenv("X_REQUIRE_LOGIN", "true"), default=True)
    page_timeout_ms = int(os.getenv("PAGE_TIMEOUT_MS", "30000"))
    max_posts_per_check = int(os.getenv("MAX_POSTS_PER_CHECK", "10"))

    return AppConfig(
        accounts=accounts,
        poll_seconds=poll_seconds,
        db_path=db_path,
        telegram_bot_token=tg_token,
        telegram_chat_id=tg_chat_id,
        headless=headless,
        browser_channel=browser_channel,
        storage_state_path=storage_state_path,
        user_data_dir=user_data_dir,
        require_login=require_login,
        page_timeout_ms=page_timeout_ms,
        max_posts_per_check=max(3, min(max_posts_per_check, 30)),
    )


def db_connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_posts (
            post_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            posted_at TEXT NOT NULL,
            seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS status_log_state (
            account TEXT PRIMARY KEY,
            signature TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def was_seen(conn: sqlite3.Connection, post_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen_posts WHERE post_id = ?", (post_id,)).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, post_id: str, username: str, posted_at: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO seen_posts(post_id, username, posted_at, seen_at) VALUES (?, ?, ?, ?)",
        (post_id, username, posted_at, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def get_last_status_signature(conn: sqlite3.Connection, account: str) -> Optional[str]:
    row = conn.execute(
        "SELECT signature FROM status_log_state WHERE account = ?",
        (account,),
    ).fetchone()
    if row is None:
        return None
    return str(row[0])


def set_last_status_signature(conn: sqlite3.Connection, account: str, signature: str) -> None:
    conn.execute(
        """
        INSERT INTO status_log_state(account, signature, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(account) DO UPDATE SET
            signature = excluded.signature,
            updated_at = excluded.updated_at
        """,
        (account, signature, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def telegram_notify(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=30,
    )
    resp.raise_for_status()


def log_json(event: str, **fields: object) -> None:
    payload = {
        "ts_utc": utc_ts(datetime.now(timezone.utc)),
        "event": event,
        **fields,
    }
    print(json.dumps(payload, ensure_ascii=False))


def notify(config: AppConfig, text: str, payload: Dict) -> None:
    if config.telegram_bot_token and config.telegram_chat_id:
        telegram_notify(config.telegram_bot_token, config.telegram_chat_id, text)
    log_json("new_post", **payload)


def utc_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_message(username: str, post: Dict) -> str:
    author_handle = post.get("author_handle", username)
    author_name = post.get("author_name", "") or f"@{author_handle}"
    is_repost = bool(post.get("is_repost", False))
    repost_str = "Yes" if is_repost else "No"
    created_str = post.get("created_at", "") or "unknown time"
    text = (post.get("text", "") or "").replace("\n", " ").strip()
    views = post.get("view_count", "unknown")
    likes = post.get("like_count", "unknown")
    post_url = post.get("url", f"https://x.com/{username}")

    lines = [
        "========================================",
        "NEW X POST DETECTED",
        "----------------------------------------",
        f"Monitored Account: @{username}",
        f"Author: {author_name} (@{author_handle})",
        f"Repost: {repost_str}",
        f"Post Time: {created_str}",
        f"Views: {views}",
        f"Likes: {likes}",
        f"Post URL: {post_url}",
        "----------------------------------------",
        "Post Text:",
        text or "(no text)",
    ]

    if is_repost:
        origin_author_name = post.get("origin_author_name", "") or "unknown"
        origin_author_handle = post.get("origin_author_handle", "") or "unknown"
        origin_url = post.get("origin_url", post_url)
        origin_text = (post.get("origin_text", "") or text or "(no text)").replace("\n", " ").strip()
        lines.extend(
            [
                "----------------------------------------",
                "Origin Post:",
                f"Author: {origin_author_name} (@{origin_author_handle})",
                f"URL: {origin_url}",
                f"Text: {origin_text or '(no text)'}",
            ]
        )

    lines.append("========================================")
    return "\n".join(lines)


def latest_status_payload(username: str, post: Optional[Dict]) -> Dict:
    if not post:
        return {
            "account": username,
            "state": "no_visible_non_pinned_posts",
        }

    author_handle = post.get("author_handle", username)
    created_str = post.get("created_at", "") or "unknown time"
    post_id = post.get("id", "unknown")
    is_repost = bool(post.get("is_repost"))
    post_url = post.get("url", f"https://x.com/{username}")
    text = (post.get("text", "") or "").replace("\n", " ").strip()
    text = (text[:120] + "...") if len(text) > 120 else text
    return {
        "account": username,
        "state": "no_new_post",
        "latest_non_pinned_post": {
            "id": post_id,
            "time": created_str,
            "author_handle": author_handle,
            "is_repost": is_repost,
            "text": text,
            "url": post_url,
        },
    }


def latest_status_signature(payload: Dict) -> str:
    state = str(payload.get("state", "unknown"))
    latest = payload.get("latest_non_pinned_post")
    if isinstance(latest, dict):
        post_id = str(latest.get("id", "unknown"))
        return f"{state}:{post_id}"
    return state


def log_status_if_changed(conn: sqlite3.Connection, payload: Dict) -> None:
    account = str(payload.get("account", ""))
    if not account:
        return
    signature = latest_status_signature(payload)
    previous = get_last_status_signature(conn, account)
    if previous == signature:
        return
    log_json("status", **payload)
    set_last_status_signature(conn, account, signature)


def new_post_payload(username: str, post: Dict) -> Dict:
    payload = {
        "account": username,
        "author_name": post.get("author_name", ""),
        "author_handle": post.get("author_handle", username),
        "is_repost": bool(post.get("is_repost", False)),
        "post_time": post.get("created_at", "") or "unknown time",
        "views": post.get("view_count", "unknown"),
        "likes": post.get("like_count", "unknown"),
        "post_id": post.get("id", "unknown"),
        "post_url": post.get("url", f"https://x.com/{username}"),
        "post_text": (post.get("text", "") or "").replace("\n", " ").strip() or "(no text)",
    }
    if payload["is_repost"]:
        payload["origin_post"] = {
            "author_name": post.get("origin_author_name", "") or "unknown",
            "author_handle": post.get("origin_author_handle", "") or "unknown",
            "url": post.get("origin_url", payload["post_url"]),
            "text": (post.get("origin_text", "") or payload["post_text"]).replace("\n", " ").strip(),
        }
    return payload


def scrape_latest_posts(page: Page, username: str, max_posts: int, timeout_ms: int) -> List[Dict]:
    page.goto(f"https://x.com/{username}", wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(3000)

    if "flow/login" in page.url:
        raise RuntimeError(
            "X redirected to login. Configure PLAYWRIGHT_USER_DATA_DIR or PLAYWRIGHT_STORAGE_STATE with a logged-in session."
        )

    # Ensure we scrape the "Posts" tab, not "Replies" / "Media".
    posts_tab = page.locator(f"a[href='/{username}']").first
    if posts_tab.count() > 0:
        posts_tab.click()
        page.wait_for_timeout(1200)

    page.wait_for_selector("article", timeout=timeout_ms)

    posts = page.evaluate(
        """
        ({ username, maxPosts }) => {
          const parseCount = (value) => {
            const raw = (value || '').trim();
            if (!raw) return 'unknown';
            const m = raw.match(/([\\d,.]+\\s*[KMB]?)/i);
            return m ? m[1].replace(/\\s+/g, '') : raw;
          };

          const parseHandleFromHref = (href) => {
            const m = (href || '').match(/^\\/([^\\/?#]+)/);
            return m ? m[1] : '';
          };

          const out = [];
          const seen = new Set();
          const articles = Array.from(document.querySelectorAll('article'));

          for (const article of articles) {
            const socialContextEl = article.querySelector('[data-testid="socialContext"]');
            const socialContextText = (socialContextEl ? socialContextEl.innerText : '').trim();
            const isPinned = /pinned/i.test(socialContextText);
            const isRepost = /reposted/i.test(socialContextText);
            if (isPinned) continue;

            const userNameBlock = article.querySelector('[data-testid="User-Name"]');
            const authorAnchor = userNameBlock ? userNameBlock.querySelector('a[href^="/"]') : null;
            const authorHref = authorAnchor ? (authorAnchor.getAttribute('href') || '') : '';
            const authorHandle = parseHandleFromHref(authorHref) || username;
            const authorNameNode = userNameBlock ? userNameBlock.querySelector('span') : null;
            const authorName = (authorNameNode ? authorNameNode.textContent : '').trim() || authorHandle;

            const links = Array.from(article.querySelectorAll('a[href*="/status/"]'));
            let postId = null;
            let handle = username;
            let iWebPostId = null;
            let ownCandidate = null;
            let externalCandidate = null;

            const timeNode = article.querySelector('time');
            const timeLink = timeNode ? timeNode.closest('a[href*="/status/"]') : null;
            const candidates = [];
            if (timeLink) candidates.push(timeLink);
            for (const l of links) candidates.push(l);

            for (const a of candidates) {
              const href = a.getAttribute('href') || '';
              let m = href.match(/^\/?([^\/]+)\/status\/(\d+)/);
              if (m) {
                if (m[1].toLowerCase() === username.toLowerCase()) {
                  ownCandidate = ownCandidate || { handle: m[1], id: m[2] };
                } else {
                  externalCandidate = externalCandidate || { handle: m[1], id: m[2] };
                }
              }
              m = href.match(/\/i\/web\/status\/(\d+)/);
              if (m) {
                iWebPostId = iWebPostId || m[1];
              }
            }

            // Normal post: prefer monitored account's own status link.
            if (ownCandidate) {
              handle = ownCandidate.handle;
              postId = ownCandidate.id;
            } else if (isRepost && externalCandidate) {
              // Repost: origin status often belongs to another handle.
              handle = externalCandidate.handle;
              postId = externalCandidate.id;
            } else if (iWebPostId) {
              // Fallback when X only exposes /i/web/status links in this card.
              handle = authorHandle || username;
              postId = iWebPostId;
            }

            if (!isRepost && (!handle || handle.toLowerCase() !== username.toLowerCase())) continue;
            if (!postId || seen.has(postId)) continue;
            seen.add(postId);

            const textNode = article.querySelector('[data-testid="tweetText"]');
            const text = (textNode ? textNode.innerText : article.innerText || '').trim();
            const createdAt = timeNode ? (timeNode.getAttribute('datetime') || '') : '';
            const likeEl = article.querySelector('[data-testid="like"],[data-testid="unlike"]');
            const likeLabel = likeEl ? (likeEl.getAttribute('aria-label') || likeEl.textContent || '') : '';
            const viewsEl =
              article.querySelector('a[href*="/analytics"]') ||
              article.querySelector('[aria-label*="View"]') ||
              article.querySelector('[aria-label*="view"]');
            const viewsLabel = viewsEl ? (viewsEl.getAttribute('aria-label') || viewsEl.textContent || '') : '';
            const likeCount = parseCount(likeLabel);
            const viewCount = parseCount(viewsLabel);
            const originHandle = isRepost ? (externalCandidate ? externalCandidate.handle : authorHandle) : '';
            const originUrl = isRepost && postId ? `https://x.com/${handle}/status/${postId}` : '';

            out.push({
              id: postId,
              text,
              created_at: createdAt,
              url: `https://x.com/${handle}/status/${postId}`,
              author_name: authorName,
              author_handle: authorHandle,
              is_repost: isRepost,
              origin_author_name: isRepost ? authorName : '',
              origin_author_handle: originHandle,
              origin_url: originUrl,
              origin_text: isRepost ? text : '',
              like_count: likeCount,
              view_count: viewCount,
            });

            if (out.length >= maxPosts) break;
          }

          return out;
        }
        """,
        {"username": username, "maxPosts": max_posts},
    )

    if not isinstance(posts, list):
        return []
    return [p for p in posts if isinstance(p, dict) and p.get("id")]


def bootstrap_seen(conn: sqlite3.Connection, username: str, posts: List[Dict]) -> None:
    for post in posts:
        post_id = post.get("id")
        posted_at = post.get("created_at", "")
        if post_id:
            mark_seen(conn, post_id, username, posted_at)


def create_context(config: AppConfig, playwright: Playwright) -> tuple[BrowserContext, Optional[Browser]]:
    common_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
    ]

    if config.user_data_dir:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=config.user_data_dir,
            headless=config.headless,
            channel=config.browser_channel,
            args=common_args,
        )
        return context, None

    browser = playwright.chromium.launch(
        headless=config.headless,
        channel=config.browser_channel,
        args=common_args,
    )
    if config.storage_state_path:
        return browser.new_context(storage_state=config.storage_state_path), browser
    return browser.new_context(), browser


def assert_logged_in(context: BrowserContext, timeout_ms: int) -> None:
    page = context.new_page()
    try:
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2000)
        if "flow/login" in page.url or "/i/flow/login" in page.url:
            raise RuntimeError(
                "Not logged in to X. Run login setup and provide PLAYWRIGHT_USER_DATA_DIR or PLAYWRIGHT_STORAGE_STATE."
            )
    finally:
        page.close()


def monitor_loop(config: AppConfig) -> None:
    conn = db_connect(config.db_path)

    seeded_users = set()

    with sync_playwright() as p:
        context, browser = create_context(config, p)
        try:
            if config.require_login:
                assert_logged_in(context, config.page_timeout_ms)
            while True:
                fetched_at = datetime.now(timezone.utc)
                log_json("heartbeat", stage="fetch", fetch_time_utc=utc_ts(fetched_at))
                for username in config.accounts:
                    page = context.new_page()
                    try:
                        posts = scrape_latest_posts(
                            page,
                            username,
                            max_posts=config.max_posts_per_check,
                            timeout_ms=config.page_timeout_ms,
                        )

                        if username not in seeded_users:
                            bootstrap_seen(conn, username, posts)
                            seeded_users.add(username)
                            latest_post = posts[0] if posts else None
                            log_status_if_changed(conn, latest_status_payload(username, latest_post))
                            continue

                        new_posts = [p for p in reversed(posts) if p.get("id") and not was_seen(conn, p["id"])]

                        if new_posts:
                            for post in new_posts:
                                msg = format_message(username, post)
                                notify(config, msg, new_post_payload(username, post))
                                mark_seen(conn, post["id"], username, post.get("created_at", ""))
                            if posts and posts[0].get("id"):
                                set_last_status_signature(conn, username, f"no_new_post:{posts[0]['id']}")
                        else:
                            latest_post = posts[0] if posts else None
                            log_status_if_changed(conn, latest_status_payload(username, latest_post))
                    except Exception as e:
                        log_json("error", account=username, message=str(e))
                    finally:
                        page.close()

                next_fetch = datetime.now(timezone.utc) + timedelta(seconds=config.poll_seconds)
                log_json("heartbeat", stage="next_fetch", next_fetch_time_utc=utc_ts(next_fetch))
                time.sleep(config.poll_seconds)
        finally:
            context.close()
            if browser is not None:
                browser.close()


def main() -> None:
    config = load_config()
    monitor_loop(config)


if __name__ == "__main__":
    main()
