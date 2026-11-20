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

### Step 3: Deploy to Cloudflare Workers (Free)

Cloudflare Workers can host both the frontend UI and the backend API on a single global URL for free.

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
4. Deploy the worker and assets:
   ```bash
   npx wrangler deploy
   ```
5. Set your secrets and configuration in Cloudflare:
   ```bash
   # Add your GitHub PAT
   npx wrangler secret put GITHUB_PAT

   # Add your Telegram Bot Token
   npx wrangler secret put TELEGRAM_BOT_TOKEN

   # Whitelist admin user IDs (comma-separated, e.g. "1593626807")
   npx wrangler secret put ALLOWED_TELEGRAM_USER_IDS
   ```

   > **Security & Access Control Model:**
   > - **`ENABLE_PUBLIC_ACTION_RUNS="true"`** (Configured in `wrangler.toml` under `[vars]`): Allows all users (both via Telegram Mini App and direct web browser visits on the Cloudflare Worker URL) to trigger workflow builds and monitor the "Action Runs" tab. Set to `"false"` to restrict both triggering and monitoring exclusively to whitelisted Telegram users in `ALLOWED_TELEGRAM_USER_IDS`.
   > - **Direct Worker Link (Web Browser):** When public access is enabled, anyone opening the worker URL directly in a browser can build and track progress as a web visitor. The **"View GitHub"** button remains strictly hidden since web visitors have no Telegram identity in `ALLOWED_TELEGRAM_USER_IDS`.
   > - **Repository Privacy:** Regardless of whether public builds/monitoring are enabled or whether accessed via Telegram or direct web link, the **"View GitHub"** button and repository URL are strictly rendered and exposed **only** to users whose Telegram ID is in `ALLOWED_TELEGRAM_USER_IDS`.

6. Note down the deployed URL (e.g. `https://flasher-builder-miniapp.yourname.workers.dev`).

---

### Step 4: Configure the Mini App in Telegram

You can attach the Mini App to your bot's **Menu Button** so users can open it with a single tap:

1. In [@BotFather](https://t.me/botfather), send `/setmenubutton`.
2. Select your bot.
3. Send the deployed URL (e.g., `https://flasher-builder-miniapp.yourname.workers.dev`).
4. Set the button title to: `⚡ Build Flasher`.

*(Alternatively, run `/newapp` in @BotFather to create a short link like `t.me/your_bot/builder`).*

---

### Step 5: (Optional) Run the Bot Launcher

If you want the bot to also respond to `/start` in private chat with an inline launch button:

```bash
cd telegram_miniapp/bot
export TELEGRAM_BOT_TOKEN="your_bot_token"
export WEBAPP_URL="https://flasher-builder-miniapp.yourname.workers.dev"
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
