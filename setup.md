# Weekly Updates Bot — Setup Guide

A Telegram bot that tracks your daily accomplishments and syncs them to a Google Sheet every Sunday, organized into **Work**, **Personal**, and **Next Week Focus** tabs.

---

## How it works

- **Every day** at your configured time, the bot messages you asking what you accomplished at work and personally, plus what you want to focus on next week.
- If you **miss a day**, it reminds you and walks you through filling in each missing date before logging today.
- **Every Sunday**, it sends you a summary and updates your Google Sheet with three tabs: Work Accomplishments, Personal Accomplishments, and Next Week Focus.

---

## Step 1 — Python environment

```bash
cd "Weekly Updates"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Step 2 — Create a Telegram bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** you receive

**Get your Chat ID:**
1. Start your bot (search for it in Telegram and send `/start`)
2. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat":{"id":XXXXXXX}` — that number is your Chat ID

---

## Step 3 — Set up Google Sheets

### 3a. Create the spreadsheet
1. Go to [Google Sheets](https://sheets.google.com) and create a new spreadsheet
2. Name it something like **Weekly Accomplishments**
3. Copy the **Sheet ID** from the URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`

### 3b. Create a Service Account
1. Go to the [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or use an existing one)
3. Enable these two APIs:
   - **Google Sheets API**
   - **Google Drive API**
4. Go to **IAM & Admin → Service Accounts → Create Service Account**
5. Name it (e.g., `weekly-updates-bot`), click **Create and Continue**, then **Done**
6. Click on the service account → **Keys** tab → **Add Key → Create new key → JSON**
7. Download the JSON file and save it as `service_account.json` in this project folder

### 3c. Share the spreadsheet with the service account
1. Open your Google Sheet
2. Click **Share**
3. Enter the service account email (found in the JSON file as `client_email`, looks like `name@project.iam.gserviceaccount.com`)
4. Give it **Editor** access

---

## Step 4 — Configure environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```
TELEGRAM_BOT_TOKEN=123456789:ABCdef...
TELEGRAM_CHAT_ID=987654321
DAILY_PROMPT_HOUR=18
DAILY_PROMPT_MINUTE=0
WEEKLY_SUMMARY_HOUR=17
TIMEZONE=America/New_York
GOOGLE_SHEETS_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
DATABASE_PATH=weekly_updates.db
```

**Timezone options:** `America/New_York`, `America/Chicago`, `America/Denver`, `America/Los_Angeles`, `Europe/London`, etc.

---

## Step 5 — Run the bot

```bash
source venv/bin/activate
python main.py
```

The bot will start and wait for the scheduled times. Open Telegram, find your bot, and send `/start` to confirm it's working.

---

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Show welcome message and your chat ID |
| `/update` | Manually trigger today's update (with missed-day catch-up) |
| `/skip` | Skip the current question |
| `/status` | See which days this week you've logged |
| `/sync` | Push all data to Google Sheets right now |
| `/summary` | Generate and send this week's summary |

---

## Running continuously (macOS)

To keep the bot running in the background, use a Launch Agent.

Create `~/Library/LaunchAgents/com.weeklyupdates.bot.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.weeklyupdates.bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOUR_USERNAME/Desktop/Weekly Updates/venv/bin/python</string>
        <string>/Users/YOUR_USERNAME/Desktop/Weekly Updates/main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USERNAME/Desktop/Weekly Updates</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/Desktop/Weekly Updates/bot.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/Desktop/Weekly Updates/bot.log</string>
</dict>
</plist>
```

Replace `YOUR_USERNAME` with your macOS username, then:

```bash
launchctl load ~/Library/LaunchAgents/com.weeklyupdates.bot.plist
```

---

## Google Sheet structure

After your first Sunday sync (or `/sync`), your sheet will have three tabs:

- **Work Accomplishments** — Week-by-week work entries, newest first
- **Personal Accomplishments** — Week-by-week personal entries, newest first
- **Next Week Focus** — Your stated priorities for each upcoming week

Each sync rebuilds the sheets completely from your local database, so you can safely re-run `/sync` anytime.
