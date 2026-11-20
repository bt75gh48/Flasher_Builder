#!/usr/bin/env python3
"""
Flasher Builder Macan • Telegram Bot Launcher
A standalone Telegram Bot script (no third-party dependencies required, runs on standard Python 3.8+).
Listens for /start and replies with an inline button to launch the Telegram Mini App.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip()
ENABLE_PUBLIC_ACTION_RUNS = os.environ.get("ENABLE_PUBLIC_ACTION_RUNS", "").strip().lower() in ("true", "1", "yes")
ALLOWED_TELEGRAM_USER_IDS = [
    s.strip() for s in os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "").split(",") if s.strip()
]


def send_api_request(method: str, data: dict = None) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"[-] Telegram API Error ({e.code}): {err_body}", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"[-] Network Error: {e}", file=sys.stderr)
        return {}


def handle_start(chat_id: int, user_name: str, is_whitelisted: bool = False):
    text = (
        f"👋 <b>Welcome {user_name}!</b>\n\n"
        "⚡ <b>Flasher Builder Macan</b>\n"
        "Easily configure and trigger Action builds for OnePlus 15R and Ace 6T.\n\n"
        "Tap the button below to launch the Mini App:"
    )

    buttons = [
        [
            {
                "text": "🚀 Launch Flasher Builder",
                "web_app": {"url": WEBAPP_URL}
            }
        ]
    ]

    # Exclusively render GitHub Repository button for ALLOWED_TELEGRAM_USER_IDS
    if is_whitelisted:
        buttons.append([
            {
                "text": "📂 GitHub Repository",
                "url": "https://github.com/bt75gh48/Flasher_Builder"
            }
        ])

    keyboard = {"inline_keyboard": buttons}

    send_api_request("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": keyboard
    })


def poll_updates():
    if not BOT_TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN environment variable is not set.", file=sys.stderr)
        print("Usage: TELEGRAM_BOT_TOKEN='...' WEBAPP_URL='https://...' python bot.py")
        sys.exit(1)

    if not WEBAPP_URL:
        print("⚠️ Warning: WEBAPP_URL is not set. The Mini App button requires an HTTPS URL.", file=sys.stderr)
        print("Set WEBAPP_URL='https://your-worker.workers.dev' before running.")

    print(f"[*] Fetching bot info...")
    me = send_api_request("getMe")
    if not me.get("ok"):
        print("❌ Invalid BOT_TOKEN or unable to connect to Telegram API.", file=sys.stderr)
        sys.exit(1)

    bot_info = me["result"]
    print(f"[+] Bot online: @{bot_info.get('username')} ({bot_info.get('first_name')})")
    print(f"[+] Mini App Target: {WEBAPP_URL}")
    print("[*] Listening for /start commands (press Ctrl+C to stop)...")

    offset = 0
    while True:
        try:
            updates = send_api_request("getUpdates", {
                "offset": offset,
                "timeout": 20,
                "allowed_updates": ["message"]
            })

            if not updates.get("ok"):
                time.sleep(3)
                continue

            for item in updates.get("result", []):
                offset = item["update_id"] + 1
                msg = item.get("message")
                if not msg:
                    continue

                chat_id = msg.get("chat", {}).get("id")
                text = (msg.get("text") or "").strip()
                user = msg.get("from", {})
                user_id = str(user.get("id", ""))
                user_name = user.get("first_name") or user.get("username") or "there"

                # Check permissions
                is_whitelisted = bool(ALLOWED_TELEGRAM_USER_IDS and user_id in ALLOWED_TELEGRAM_USER_IDS)
                can_access = is_whitelisted or ENABLE_PUBLIC_ACTION_RUNS

                if not can_access:
                    print(f"[-] Blocked unauthorized access attempt by @{user.get('username')} (ID: {user_id})")
                    if text.startswith("/start"):
                        send_api_request("sendMessage", {
                            "chat_id": chat_id,
                            "text": (
                                f"⛔ <b>Access Restricted</b>\n\n"
                                f"Hello {user_name}, you are not authorized to use Flasher Builder.\n\n"
                                f"Your Telegram User ID: <code>{user_id}</code>\n"
                                f"Please ask the repository owner to add your ID to <code>ALLOWED_TELEGRAM_USER_IDS</code>."
                            ),
                            "parse_mode": "HTML"
                        })
                    continue

                if text.startswith("/start"):
                    print(f"[+] /start from @{user.get('username')} ({chat_id}) [Whitelisted: {is_whitelisted}]")
                    handle_start(chat_id, user_name, is_whitelisted=is_whitelisted)

        except KeyboardInterrupt:
            print("\n[!] Bot stopped.")
            break
        except Exception as e:
            print(f"[-] Loop error: {e}", file=sys.stderr)
            time.sleep(3)


if __name__ == "__main__":
    poll_updates()
