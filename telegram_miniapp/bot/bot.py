#!/usr/bin/env python3
"""
Flasher Builder Macan • Telegram Bot Launcher & Authorization Gatekeeper
A standalone Telegram Bot script (no third-party dependencies required, runs on standard Python 3.8+).

Features:
- Listens for /start and replies with an inline button to launch the Telegram Mini App.
- For unauthorized users, displays a "Request Authorization" button with their User ID.
- Forwards authorization requests directly to the owner/admin with [Approve] and [Reject] buttons.
- Automatically stores and persists authorized users in authorized_users.json.
- Supports owner commands: /whitelist, /auth <id>, /revoke <id>.
"""

from datetime import datetime
import html
import json
import os
from pathlib import Path
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

# Owner ID who receives access requests (defaults to first ID or '1593626807')
OWNER_TELEGRAM_ID = os.environ.get("OWNER_TELEGRAM_ID", "").strip()
if not OWNER_TELEGRAM_ID and ALLOWED_TELEGRAM_USER_IDS:
    OWNER_TELEGRAM_ID = ALLOWED_TELEGRAM_USER_IDS[0]
if not OWNER_TELEGRAM_ID:
    OWNER_TELEGRAM_ID = "1593626807"

# Persistent JSON files stored beside bot.py
DATA_DIR = Path(__file__).parent.resolve()
AUTH_STORE_FILE = DATA_DIR / "authorized_users.json"
PENDING_STORE_FILE = DATA_DIR / "pending_requests.json"


# =========================================================================
# JSON Persistence Helpers
# =========================================================================

def load_json_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[-] Error loading {path.name}: {e}", file=sys.stderr)
        return {}


def save_json_file(path: Path, data: dict):
    try:
        temp_file = path.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp_file.replace(path)
    except Exception as e:
        print(f"[-] Error saving {path.name}: {e}", file=sys.stderr)


# =========================================================================
# Authorization Checks
# =========================================================================

def is_user_admin(user_id: str) -> bool:
    uid = str(user_id).strip()
    return uid == str(OWNER_TELEGRAM_ID) or uid in ALLOWED_TELEGRAM_USER_IDS


def is_user_authorized(user_id: str) -> bool:
    uid = str(user_id).strip()
    if is_user_admin(uid):
        return True
    if ENABLE_PUBLIC_ACTION_RUNS:
        return True
    auth_data = load_json_file(AUTH_STORE_FILE)
    return uid in auth_data


def add_authorized_user(user_id: str, name: str, username: str = "", authorized_by: str = "owner"):
    uid = str(user_id).strip()
    auth_data = load_json_file(AUTH_STORE_FILE)
    auth_data[uid] = {
        "name": name,
        "username": username or "",
        "authorized_at": datetime.now().isoformat(timespec="seconds"),
        "authorized_by": str(authorized_by)
    }
    save_json_file(AUTH_STORE_FILE, auth_data)

    # Remove from pending requests if present
    pending = load_json_file(PENDING_STORE_FILE)
    if uid in pending:
        pending.pop(uid, None)
        save_json_file(PENDING_STORE_FILE, pending)


def remove_authorized_user(user_id: str) -> bool:
    uid = str(user_id).strip()
    auth_data = load_json_file(AUTH_STORE_FILE)
    if uid in auth_data:
        auth_data.pop(uid, None)
        save_json_file(AUTH_STORE_FILE, auth_data)
        return True
    return False


# =========================================================================
# Telegram API Request Helper
# =========================================================================

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


# =========================================================================
# UI Message Handlers
# =========================================================================

def handle_start(chat_id: int, user_name: str, is_admin: bool = False):
    text = (
        f"👋 <b>Welcome {html.escape(user_name)}!</b>\n\n"
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

    # Admin buttons
    if is_admin:
        buttons.append([
            {
                "text": "📂 GitHub Repository",
                "url": "https://github.com/bt75gh48/Flasher_Builder"
            }
        ])
        buttons.append([
            {
                "text": "👥 Whitelist Manager",
                "callback_data": "admin_whitelist"
            }
        ])

    keyboard = {"inline_keyboard": buttons}

    send_api_request("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": keyboard
    })


def handle_unauthorized_start(chat_id: int, user_id: str, user_name: str):
    pending = load_json_file(PENDING_STORE_FILE)
    if user_id in pending:
        text = (
            f"⏳ <b>Authorization Pending</b>\n\n"
            f"Hello {html.escape(user_name)}! Your authorization request is currently awaiting review by the administrator.\n\n"
            f"🆔 <b>Your Telegram User ID:</b> <code>{user_id}</code>\n\n"
            "You will be automatically notified here once your access is approved."
        )
        buttons = [
            [{"text": "🔄 Check Status", "callback_data": f"check_status:{user_id}"}]
        ]
    else:
        text = (
            f"⛔ <b>Access Required</b>\n\n"
            f"Hello {html.escape(user_name)}! You are not authorized to use Flasher Builder yet.\n\n"
            f"🆔 <b>Your Telegram User ID:</b> <code>{user_id}</code>\n\n"
            "To request access, tap the button below. Your ID will be sent directly to the owner for approval:"
        )
        buttons = [
            [{"text": "📩 Request Authorization", "callback_data": f"req_auth:{user_id}"}]
        ]

    send_api_request("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": buttons}
    })


# =========================================================================
# Callback Query Handlers (Button clicks)
# =========================================================================

def handle_request_auth_callback(cq: dict):
    cq_id = cq["id"]
    from_user = cq.get("from", {})
    user_id = str(from_user.get("id", ""))
    user_name = from_user.get("first_name") or from_user.get("username") or "User"
    username = from_user.get("username") or ""
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    msg_id = msg.get("message_id")

    if is_user_authorized(user_id):
        send_api_request("answerCallbackQuery", {
            "callback_query_id": cq_id,
            "text": "You are already authorized!"
        })
        handle_start(chat_id, user_name, is_admin=is_user_admin(user_id))
        return

    # Store in pending requests
    pending = load_json_file(PENDING_STORE_FILE)
    pending[user_id] = {
        "name": user_name,
        "username": username,
        "requested_at": datetime.now().isoformat(timespec="seconds")
    }
    save_json_file(PENDING_STORE_FILE, pending)

    send_api_request("answerCallbackQuery", {
        "callback_query_id": cq_id,
        "text": "Authorization request sent to the owner!"
    })

    # Update user's view
    user_text = (
        f"⏳ <b>Authorization Request Sent</b>\n\n"
        f"Hello {html.escape(user_name)}, your request has been forwarded to the owner with your Telegram ID: <code>{user_id}</code>.\n\n"
        "You will receive an instant notification here once reviewed."
    )
    send_api_request("editMessageText", {
        "chat_id": chat_id,
        "message_id": msg_id,
        "text": user_text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "🔄 Check Status", "callback_data": f"check_status:{user_id}"}]
            ]
        }
    })

    # Forward notification to Owner
    user_handle = f"@{username}" if username else "None"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    owner_text = (
        f"🔔 <b>New Access Request</b>\n\n"
        f"👤 <b>Name:</b> {html.escape(user_name)}\n"
        f"🏷️ <b>Username:</b> {html.escape(user_handle)}\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"📅 <b>Time:</b> {now_str}\n\n"
        "Do you want to authorize this user to use Flasher Builder?"
    )
    owner_buttons = [
        [
            {"text": "✅ Authorize", "callback_data": f"auth_appr:{user_id}"},
            {"text": "❌ Reject", "callback_data": f"auth_rejc:{user_id}"}
        ]
    ]
    send_api_request("sendMessage", {
        "chat_id": OWNER_TELEGRAM_ID,
        "text": owner_text,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": owner_buttons}
    })
    print(f"[+] Authorization request from @{username} ({user_id}) sent to owner {OWNER_TELEGRAM_ID}")


def handle_approve_callback(cq: dict, target_uid: str):
    cq_id = cq["id"]
    from_user = cq.get("from", {})
    admin_id = str(from_user.get("id", ""))
    admin_name = from_user.get("first_name") or from_user.get("username") or "Owner"
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    msg_id = msg.get("message_id")

    if not is_user_admin(admin_id):
        send_api_request("answerCallbackQuery", {
            "callback_query_id": cq_id,
            "text": "⛔ Only the administrator can approve users.",
            "show_alert": True
        })
        return

    pending = load_json_file(PENDING_STORE_FILE)
    user_info = pending.get(target_uid, {})
    target_name = user_info.get("name", "User")
    target_username = user_info.get("username", "")

    add_authorized_user(target_uid, target_name, target_username, authorized_by=admin_id)

    send_api_request("answerCallbackQuery", {
        "callback_query_id": cq_id,
        "text": f"✅ User {target_name} ({target_uid}) authorized!"
    })

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_handle = f"@{target_username}" if target_username else "None"
    updated_owner_text = (
        f"✅ <b>User Authorized Successfully</b>\n\n"
        f"👤 <b>Name:</b> {html.escape(target_name)}\n"
        f"🏷️ <b>Username:</b> {html.escape(user_handle)}\n"
        f"🆔 <b>User ID:</b> <code>{target_uid}</code>\n"
        f"🛡️ <b>Action:</b> Approved by {html.escape(admin_name)} on {now_str}"
    )
    send_api_request("editMessageText", {
        "chat_id": chat_id,
        "message_id": msg_id,
        "text": updated_owner_text,
        "parse_mode": "HTML"
    })

    # Notify the approved user immediately
    user_welcome = (
        f"🎉 <b>Authorization Approved!</b>\n\n"
        f"Hello {html.escape(target_name)}, your access request has been approved by the administrator!\n"
        "You can now access Flasher Builder.\n\n"
        "Tap the button below to launch the Mini App:"
    )
    user_buttons = [
        [
            {
                "text": "🚀 Launch Flasher Builder",
                "web_app": {"url": WEBAPP_URL}
            }
        ]
    ]
    send_api_request("sendMessage", {
        "chat_id": int(target_uid),
        "text": user_welcome,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": user_buttons}
    })
    print(f"[+] User {target_uid} ({target_name}) authorized by {admin_id}")


def handle_reject_callback(cq: dict, target_uid: str):
    cq_id = cq["id"]
    from_user = cq.get("from", {})
    admin_id = str(from_user.get("id", ""))
    admin_name = from_user.get("first_name") or from_user.get("username") or "Owner"
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    msg_id = msg.get("message_id")

    if not is_user_admin(admin_id):
        send_api_request("answerCallbackQuery", {
            "callback_query_id": cq_id,
            "text": "⛔ Only the administrator can reject users.",
            "show_alert": True
        })
        return

    pending = load_json_file(PENDING_STORE_FILE)
    user_info = pending.pop(target_uid, {})
    save_json_file(PENDING_STORE_FILE, pending)

    target_name = user_info.get("name", "User")
    target_username = user_info.get("username", "")

    send_api_request("answerCallbackQuery", {
        "callback_query_id": cq_id,
        "text": f"❌ Request for {target_uid} declined."
    })

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_handle = f"@{target_username}" if target_username else "None"
    updated_owner_text = (
        f"❌ <b>User Request Declined</b>\n\n"
        f"👤 <b>Name:</b> {html.escape(target_name)}\n"
        f"🏷️ <b>Username:</b> {html.escape(user_handle)}\n"
        f"🆔 <b>User ID:</b> <code>{target_uid}</code>\n"
        f"🛡️ <b>Action:</b> Declined by {html.escape(admin_name)} on {now_str}"
    )
    send_api_request("editMessageText", {
        "chat_id": chat_id,
        "message_id": msg_id,
        "text": updated_owner_text,
        "parse_mode": "HTML"
    })

    # Notify user
    send_api_request("sendMessage", {
        "chat_id": int(target_uid),
        "text": (
            f"❌ <b>Authorization Declined</b>\n\n"
            f"Hello {html.escape(target_name)}, your request to access Flasher Builder was declined by the administrator."
        ),
        "parse_mode": "HTML"
    })
    print(f"[-] User {target_uid} request rejected by {admin_id}")


def handle_check_status_callback(cq: dict, target_uid: str):
    cq_id = cq["id"]
    from_user = cq.get("from", {})
    user_id = str(from_user.get("id", ""))
    user_name = from_user.get("first_name") or from_user.get("username") or "User"
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")

    if is_user_authorized(user_id):
        send_api_request("answerCallbackQuery", {
            "callback_query_id": cq_id,
            "text": "🎉 You have been approved! Loading Mini App..."
        })
        handle_start(chat_id, user_name, is_admin=is_user_admin(user_id))
        return

    pending = load_json_file(PENDING_STORE_FILE)
    if user_id in pending:
        send_api_request("answerCallbackQuery", {
            "callback_query_id": cq_id,
            "text": "⏳ Your request is still pending review by the owner.",
            "show_alert": True
        })
    else:
        send_api_request("answerCallbackQuery", {
            "callback_query_id": cq_id,
            "text": "No active request found. Submitting request...",
            "show_alert": False
        })
        handle_request_auth_callback(cq)


def handle_admin_whitelist_callback(cq: dict):
    cq_id = cq["id"]
    from_user = cq.get("from", {})
    admin_id = str(from_user.get("id", ""))
    chat_id = cq.get("message", {}).get("chat", {}).get("id")

    if not is_user_admin(admin_id):
        send_api_request("answerCallbackQuery", {
            "callback_query_id": cq_id,
            "text": "⛔ Access denied.",
            "show_alert": True
        })
        return

    send_api_request("answerCallbackQuery", {"callback_query_id": cq_id})
    show_whitelist(chat_id)


def show_whitelist(chat_id: int):
    auth_data = load_json_file(AUTH_STORE_FILE)
    pending_data = load_json_file(PENDING_STORE_FILE)

    lines = ["👥 <b>Flasher Builder Whitelist</b>\n"]

    lines.append(f"👑 <b>Owner:</b> <code>{OWNER_TELEGRAM_ID}</code>")
    if ALLOWED_TELEGRAM_USER_IDS:
        env_admins = [f"<code>{uid}</code>" for uid in ALLOWED_TELEGRAM_USER_IDS if uid != str(OWNER_TELEGRAM_ID)]
        if env_admins:
            lines.append(f"🛡️ <b>Env Admins:</b> {', '.join(env_admins)}")

    lines.append(f"\n✅ <b>Dynamically Authorized Users ({len(auth_data)}):</b>")
    if not auth_data:
        lines.append("<i>None yet. Users approved via Telegram will appear here.</i>")
    else:
        for uid, info in auth_data.items():
            uname = f"@{info.get('username')}" if info.get('username') else info.get('name', 'User')
            lines.append(f"• <b>{html.escape(uname)}</b>: <code>{uid}</code> (<i>{info.get('authorized_at', '')[:10]}</i>)")

    if pending_data:
        lines.append(f"\n⏳ <b>Pending Requests ({len(pending_data)}):</b>")
        for uid, info in pending_data.items():
            uname = f"@{info.get('username')}" if info.get('username') else info.get('name', 'User')
            lines.append(f"• <b>{html.escape(uname)}</b>: <code>{uid}</code>")

    lines.append("\n💡 <i>Commands:</i>")
    lines.append("• <code>/auth &lt;id&gt;</code> — Authorize a user ID")
    lines.append("• <code>/revoke &lt;id&gt;</code> — Revoke a user's access")

    send_api_request("sendMessage", {
        "chat_id": chat_id,
        "text": "\n".join(lines),
        "parse_mode": "HTML"
    })


# =========================================================================
# Main Long-Polling Loop
# =========================================================================

def poll_updates():
    if not BOT_TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN environment variable is not set.", file=sys.stderr)
        print("Usage: TELEGRAM_BOT_TOKEN='...' WEBAPP_URL='https://...' python bot.py")
        sys.exit(1)

    if not WEBAPP_URL:
        print("⚠️ Warning: WEBAPP_URL is not set. The Mini App button requires an HTTPS URL.", file=sys.stderr)
        print("Set WEBAPP_URL='https://your-worker.workers.dev' before running.")

    print("[*] Fetching bot info...")
    me = send_api_request("getMe")
    if not me.get("ok"):
        print("❌ Invalid BOT_TOKEN or unable to connect to Telegram API.", file=sys.stderr)
        sys.exit(1)

    bot_info = me["result"]
    print(f"[+] Bot online: @{bot_info.get('username')} ({bot_info.get('first_name')})")
    print(f"[+] Owner ID: {OWNER_TELEGRAM_ID}")
    print(f"[+] Mini App Target: {WEBAPP_URL}")
    print(f"[+] Public Actions Enabled: {ENABLE_PUBLIC_ACTION_RUNS}")
    print("[*] Listening for updates & authorization requests (press Ctrl+C to stop)...")

    offset = 0
    while True:
        try:
            updates = send_api_request("getUpdates", {
                "offset": offset,
                "timeout": 20,
                "allowed_updates": ["message", "callback_query"]
            })

            if not updates.get("ok"):
                time.sleep(3)
                continue

            for item in updates.get("result", []):
                offset = item["update_id"] + 1

                # ---------------------------------------------------------
                # 1. Handle Inline Button Callback Queries
                # ---------------------------------------------------------
                cq = item.get("callback_query")
                if cq:
                    data = cq.get("data", "")
                    if data.startswith("req_auth:"):
                        handle_request_auth_callback(cq)
                    elif data.startswith("auth_appr:"):
                        target_uid = data.split("auth_appr:")[1]
                        handle_approve_callback(cq, target_uid)
                    elif data.startswith("auth_rejc:"):
                        target_uid = data.split("auth_rejc:")[1]
                        handle_reject_callback(cq, target_uid)
                    elif data.startswith("check_status:"):
                        target_uid = data.split("check_status:")[1]
                        handle_check_status_callback(cq, target_uid)
                    elif data == "admin_whitelist":
                        handle_admin_whitelist_callback(cq)
                    continue

                # ---------------------------------------------------------
                # 2. Handle Text Messages
                # ---------------------------------------------------------
                msg = item.get("message")
                if not msg:
                    continue

                chat_id = msg.get("chat", {}).get("id")
                text = (msg.get("text") or "").strip()
                user = msg.get("from", {})
                user_id = str(user.get("id", ""))
                user_name = user.get("first_name") or user.get("username") or "there"

                # Admin commands
                if is_user_admin(user_id):
                    if text in ("/whitelist", "/users"):
                        show_whitelist(chat_id)
                        continue
                    elif text.startswith("/auth"):
                        parts = text.split()
                        if len(parts) >= 2:
                            target_id = parts[1].strip()
                            add_authorized_user(target_id, name="Manual Auth", authorized_by=user_id)
                            send_api_request("sendMessage", {
                                "chat_id": chat_id,
                                "text": f"✅ User ID <code>{target_id}</code> has been authorized.",
                                "parse_mode": "HTML"
                            })
                            # Try notifying the user
                            send_api_request("sendMessage", {
                                "chat_id": int(target_id),
                                "text": (
                                    "🎉 <b>Access Granted!</b>\n\n"
                                    "You have been authorized by the administrator. Tap below to launch:"
                                ),
                                "parse_mode": "HTML",
                                "reply_markup": {
                                    "inline_keyboard": [[{
                                        "text": "🚀 Launch Flasher Builder",
                                        "web_app": {"url": WEBAPP_URL}
                                    }]]
                                }
                            })
                        else:
                            send_api_request("sendMessage", {
                                "chat_id": chat_id,
                                "text": "Usage: <code>/auth &lt;telegram_user_id&gt;</code>",
                                "parse_mode": "HTML"
                            })
                        continue
                    elif text.startswith("/revoke"):
                        parts = text.split()
                        if len(parts) >= 2:
                            target_id = parts[1].strip()
                            if remove_authorized_user(target_id):
                                send_api_request("sendMessage", {
                                    "chat_id": chat_id,
                                    "text": f"🗑️ User ID <code>{target_id}</code> has been removed from authorized users.",
                                    "parse_mode": "HTML"
                                })
                            else:
                                send_api_request("sendMessage", {
                                    "chat_id": chat_id,
                                    "text": f"ℹ️ User ID <code>{target_id}</code> was not in the dynamic authorized list.",
                                    "parse_mode": "HTML"
                                })
                        else:
                            send_api_request("sendMessage", {
                                "chat_id": chat_id,
                                "text": "Usage: <code>/revoke &lt;telegram_user_id&gt;</code>",
                                "parse_mode": "HTML"
                            })
                        continue

                # Normal /start handling
                if text.startswith("/start"):
                    if is_user_authorized(user_id):
                        print(f"[+] /start from @{user.get('username')} ({chat_id}) [Authorized]")
                        handle_start(chat_id, user_name, is_admin=is_user_admin(user_id))
                    else:
                        print(f"[-] Unauthorized /start from @{user.get('username')} ({user_id})")
                        handle_unauthorized_start(chat_id, user_id, user_name)

        except KeyboardInterrupt:
            print("\n[!] Bot stopped.")
            break
        except Exception as e:
            print(f"[-] Loop error: {e}", file=sys.stderr)
            time.sleep(3)


if __name__ == "__main__":
    poll_updates()
