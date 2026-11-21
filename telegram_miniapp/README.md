# Flasher Builder Macan • Telegram Mini App (TMA)

A Telegram Mini App to configure, trigger, and track [build_macan.yml](file:///e:/RIR18/Development/GitHub/Flasher_Builder/.github/workflows/build_macan.yml) GitHub Actions builds directly from Telegram.

---

## 🌟 Key Features

* **Mobile-Optimized UI:** Dark-mode native look & feel integrated with Telegram theme and haptic feedback.
* **Integrated OTA Version Catalog:** Fetches official OnePlus OTA releases dynamically (matching the Python GUI app's `ota_catalog.py`). Includes package size, release dates, and security patch levels.
* **Dynamic Patch Matching for Ace 6T:** Automatically checks whether a donor ColorOS Ace 6T CN OTA exists with an exact matching security patch date for OxygenOS (GLO/IN).
* **Smart Device & Region Validation:** Automatically disables unsupported combinations (e.g., Regional Flasher on Ace 6T or CN on 15R) according to `build_macan.yml` build rules.
* **Live Artifact Preview:** Calculates the exact artifact package name in real-time (e.g., `Super Flasher A6T GLO OOS+COS ODM latest.zip`).
* **Live Action Runs Monitor & Step-by-Step Tracker:** Real-time tracking of queued, running, succeeded, and failed runs. During active builds, automatically live-polls progress every 4 seconds, showing the current step, progress percentage bar, and an expandable checklist of all execution steps.
* **Direct Gofile Download Integration:** Automatically extracts the Gofile download URL from GitHub Actions annotations upon completion, providing instant `⬇️ Download Package` and `📋 Copy Link` buttons directly inside Telegram.
* **Secure Architecture:** GitHub Personal Access Token (PAT) is stored safely in serverless environment secrets; Telegram `initData` is verified with HMAC-SHA256 to whitelist authorized users.

---

## 📁 Project Structure

```
telegram_miniapp/
├── public/                 # Mini App Frontend
│   ├── index.html          # Responsive single-page UI (Tailwind CSS)
│   ├── app.js              # State management, validation & Telegram SDK
│   └── style.css           # Telegram theme overrides & active states
├── worker/                 # Serverless Cloudflare Worker Backend
│   ├── worker.js           # HMAC validator, GitHub API proxy & static asset server
│   └── wrangler.toml       # Cloudflare deployment config
├── bot/                    # Optional Standalone Bot Launcher
│   └── bot.py              # Lightweight polling bot for /start button
└── README.md
```

---

## 🚀 Setup & Deployment Guide

### Step 1: Create a Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/botfather).
2. Send `/newbot`, choose a name and username (e.g., `MacanFlasherBot`).
3. Copy the **HTTP API Token** (e.g., `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).

---

### Step 2: Create a GitHub Personal Access Token (PAT)

1. Go to [GitHub Settings → Developer Settings → Personal access tokens → Fine-grained tokens](https://github.com/settings/tokens?type=beta).
2. Click **Generate new token**.
3. Name: `Telegram-Flasher-Worker`.
4. Repository access: **Only select repositories** → choose `bt75gh48/Flasher_Builder`.
5. Permissions:
   * **Actions:** `Read and write` (needed to dispatch workflow and read runs).
6. Click **Generate token** and copy it.

---

### Step 3: Deploy to Cloudflare Workers (Serverless)

Cloudflare Workers can host the frontend UI, the backend API, and the entire **Telegram Bot Webhook & Authorization System** 100% serverless without needing any external server or Python process.

1. Install Wrangler CLI (if not already installed):
   ```bash
   npm install -g wrangler
   ```
2. Log in to Cloudflare:
   ```bash
   npx wrangler login
   ```
3. Navigate to the `worker` directory:
   ```bash
   cd telegram_miniapp/worker
   ```
4. **(Recommended)** Create a Cloudflare KV namespace for permanent authorized user storage:
   ```bash
   npx wrangler kv namespace create AUTH_KV
   ```
   Copy the output `id = "xxxxxx"` and uncomment it in `telegram_miniapp/worker/wrangler.toml`:
   ```toml
   [[kv_namespaces]]
   binding = "AUTH_KV"
   id = "your_auth_kv_namespace_id_here"
   ```
5. Deploy the worker and static frontend:
   ```bash
   npx wrangler deploy
   ```
6. Set your secrets in Cloudflare:
   ```bash
   # Add your GitHub PAT
   npx wrangler secret put GITHUB_PAT

   # Add your Telegram Bot Token
   npx wrangler secret put TELEGRAM_BOT_TOKEN

   # (Optional) Pre-authorize additional admin Telegram IDs (comma-separated, e.g. "1593626807")
   npx wrangler secret put ALLOWED_TELEGRAM_USER_IDS
   ```

7. **Activate Serverless Telegram Webhook (One-click):**
   Once deployed, simply visit this URL in your web browser (or run `curl`):
   ```text
   https://<your-worker-subdomain>.workers.dev/api/set-webhook
   ```
   Telegram will reply:
   ```json
   {
     "registered_url": "https://<your-worker>.workers.dev/api/telegram-webhook",
     "telegram_response": { "ok": true, "result": true, "description": "Webhook was set" }
   }
   ```
   Your Telegram bot is now completely live and managed serverless by your Cloudflare Worker!

---

### Step 4: Configure the Mini App in Telegram

You can attach the Mini App to your bot's **Menu Button** so users can open it with a single tap:

1. In [@BotFather](https://t.me/botfather), send `/setmenubutton`.
2. Select your bot.
3. Send the deployed URL (e.g., `https://flasher-builder.yourname.workers.dev`).
4. Set the button title to: `⚡ Build Flasher`.

*(Alternatively, run `/newapp` in @BotFather to create a direct short link like `t.me/your_bot/builder`).*

---

### Step 5: Telegram User Verification & Authorization Flow

The authorization system runs completely serverless inside the Cloudflare Worker:

#### 🛡️ User Verification Flow:
1. **Unauthorized User starts bot (`/start`):**
   The bot checks if the user is authorized. If not, it displays:
   - User's Telegram Name & User ID (e.g. `🆔 Your Telegram ID: 123456789`).
   - `⚠️ Status: Not Authorized`.
   - An inline button: **`[ 📩 Request Authorization ]`**.
2. **User taps `Request Authorization`:**
   - The bot forwards an authorization prompt directly to the Owner (`OWNER_TELEGRAM_ID = "1593626807"`):
     ```text
     🔔 Authorization Request
     👤 User: John Doe (@johndoe)
     🆔 User ID: 123456789
     [ ✅ Authorize ]   [ ❌ Reject ]
     ```
   - The user is notified: `⏳ Your authorization request has been sent to the administrator.`
3. **Owner approves or rejects:**
   - **Tapping `[ ✅ Authorize ]`:**
     - The user is stored in Cloudflare KV (`AUTH_KV`).
     - The owner's message updates to show approval details.
     - The user immediately receives a notification on Telegram:
       `🎉 Access Granted! You can now launch Flasher Builder.`
       with the **`[ 🚀 Launch Flasher Builder ]`** WebApp button.
   - **Tapping `[ ❌ Reject ]`:**
     - The user's request is declined and they are notified.

#### 👑 Owner / Admin Commands (Directly in Telegram):
* `/whitelist` — View all administrators and KV-authorized users.
* `/auth <user_id>` — Manually authorize a user ID.
* `/revoke <user_id>` — Revoke authorization for a user ID.
* `/help` — Show available admin commands.

---

### Step 6: (Alternative) Standalone Python Bot Launcher

If you prefer to run a local polling bot instead of the serverless Cloudflare Webhook:

```bash
cd telegram_miniapp/bot
export TELEGRAM_BOT_TOKEN="your_bot_token"
export WEBAPP_URL="https://flasher-builder.yourname.workers.dev"
export OWNER_TELEGRAM_ID="1593626807"
python bot.py
```

---

## 🧩 Supported Matrix

| Device | Flasher Type | Supported Regions | Output Format |
|---|---|---|---|
| **OnePlus 15R** | Super Flasher | `GLO`, `EU`, `IN` | `Super Flasher 15R <REGION> <VER>.zip` |
| **OnePlus 15R** | Regional Flasher | `GLO`, `EU`, `IN` | `Regional Flasher 15R <REGION> <VER>.zip` |
| **OnePlus Ace 6T** | Super Flasher | `CN` | `Super Flasher A6T COS <VER>.zip` |
| **OnePlus Ace 6T** | Super Flasher | `GLO`, `IN` | `Super Flasher A6T <REGION> OOS+COS ODM <VER>.zip` |
| **OnePlus Ace 6T** | Regional Flasher | *Not Supported* | *Disabled in UI* |
