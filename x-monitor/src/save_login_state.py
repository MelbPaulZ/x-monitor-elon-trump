import os

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright


def main() -> None:
    load_dotenv()
    state_path = os.getenv("PLAYWRIGHT_STORAGE_STATE", "auth_state.json").strip() or "auth_state.json"
    user_data_dir = os.getenv("PLAYWRIGHT_USER_DATA_DIR", "x_user_data").strip() or "x_user_data"
    browser_channel = os.getenv("PLAYWRIGHT_BROWSER_CHANNEL", "chrome").strip() or "chrome"

    os.makedirs(user_data_dir, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            channel=browser_channel,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        page = context.new_page()
        page.goto("https://x.com/login", wait_until="domcontentloaded")

        print("Log in to X in the opened browser, then press Enter here to save session state/profile...")
        input()

        context.storage_state(path=state_path)
        context.close()

    print(f"Saved Playwright storage state to: {state_path}")
    print(f"Saved persistent browser profile to: {user_data_dir}")
    print(f"Browser channel used: {browser_channel}")


if __name__ == "__main__":
    main()
