# 777blok — Crypto Price Alert Bots

> **Alerts only.** These bots only *read* public prices from CoinGecko. They
> never place trades, never connect to an exchange, and never ask for wallet or
> exchange keys.

This repo has two bots:

| | What it is | Where it runs | Guide |
|---|---|---|---|
| **signalbot** (`signalbot/`) | Public multi-user bot: anyone sets their own alerts; free plan + Pro subscription in Telegram Stars | A server, 24/7 | **[LAUNCH.md](LAUNCH.md)** |
| **Personal bot** (`bot.py`) | Alerts from `config.yaml` sent to just you | GitHub Actions, free | This README |

The rest of this README covers the personal bot.

## What's in here

| File | What it does |
| --- | --- |
| `bot.py` | Start here: the main loop you run |
| `config.yaml` | **Your alerts** — edit this |
| `.env.example` | Template for your Telegram settings (copy to `.env`) |
| `alerts.py` | Alert rules, percent-change and cooldown logic |
| `coingecko.py` | Gets prices from CoinGecko (free, no API key), with retries |
| `notifier.py` | Prints alerts and sends them to Telegram |
| `tests/` | Automated tests (`pytest`) |

---

## Easiest: run it on GitHub (no install, PC can be off)

GitHub can run the bot for you every ~5 minutes for free (this repo is public).
Everything below happens in your web browser and Telegram.

1. **Merge the pull request.** On GitHub open **Pull requests**, open the bot's pull
   request and click **Merge pull request**, then **Confirm merge**. (GitHub only
   runs the schedule from the `main` branch.)
2. **Get your bot token.** In Telegram, message **@BotFather**. Send `/newbot` to make
   a bot, or `/revoke` to get a fresh token for an existing one. Copy the token.
   Keep it secret.
3. **Give GitHub the token.** In your repo go to **Settings** → **Secrets and
   variables** → **Actions** → **New repository secret**.
   - Name: `TELEGRAM_BOT_TOKEN`
   - Secret: paste the token → **Add secret**
4. **Say hi to your bot.** In Telegram, open your bot, press **Start** and send it any
   message. The bot finds your chat from this, so you don't need a chat id.
5. **Start it.** Open the **Actions** tab → **Price alerts** → **Run workflow** →
   **Run workflow**. About a minute later your bot sends
   **"✅ Crypto alert bot connected!"** From then on it checks prices every ~5 minutes
   by itself.
6. **Choose your alerts.** On GitHub, click `config.yaml` → the ✏️ pencil icon, edit
   the alerts (see [step 8 below](#8-set-your-alerts-in-configyaml) for the format)
   → **Commit changes**. The next run uses them.

Good to know:
- **Checks every 5–15 minutes**, not every minute. GitHub's timer is often late.
- **To stop it:** **Actions** tab → **Price alerts** → **⋯** → **Disable workflow**.
- **Run logs are public** because the repo is public. They show prices and alerts,
  never your token.
- **GitHub pauses the schedule if nothing in the repo changes for 60 days.** It
  emails you first, and one click on **Enable workflow** turns it back on.
- **If you never get the "connected" message:** open the latest run on the
  **Actions** tab and read the log. The usual fix is to message the bot again, then
  **Run workflow** again.

Prefer running it on your own PC? Follow the steps below instead.

---

## Setup on Windows (step by step)

### 1. Install Python

1. Go to <https://www.python.org/downloads/> and download the latest **Python 3** for Windows.
2. Run the installer. **On the first screen, tick "Add python.exe to PATH"**, then click *Install Now*.
3. Open **Command Prompt** (press the Windows key, type `cmd`, press Enter) and check it works:

   ```bat
   python --version
   ```

   You should see something like `Python 3.12.x`. If Windows opens the Microsoft Store
   instead, re-run the installer and make sure "Add python.exe to PATH" is ticked.

### 2. Download this project

Either click **Code → Download ZIP** on GitHub and unzip it (for example to
`C:\Users\YOU\777blok`), or if you have Git: `git clone <repo url>`.

Then in Command Prompt, go into the folder:

```bat
cd C:\Users\YOU\777blok
```

### 3. Install the bot's requirements

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

The first line creates a private "virtual environment" for this project; the
second switches it on (you'll see `(.venv)` at the start of the line). **Run
`.venv\Scripts\activate` again every time you open a new Command Prompt.**

> PowerShell user? Use `.venv\Scripts\Activate.ps1`. If it complains about
> scripts being disabled, just use Command Prompt (`cmd`) instead.

### 4. Try it without Telegram (optional)

The bot works right away and prints alerts in the window:

```bat
python bot.py
```

Press **Ctrl+C** to stop it. Now let's hook up Telegram so alerts reach your phone.

### 5. Create your Telegram bot with @BotFather

1. In Telegram, search for **@BotFather** (it has a blue check mark) and open the chat.
2. Send `/newbot`.
3. Give it a display name, e.g. `My Price Alerts`.
4. Give it a username that ends in `bot`, e.g. `myname_price_alert_bot`.
5. BotFather replies with a **token** that looks like
   `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`. Copy it. **Keep it secret** — anyone
   with it can control your bot.

### 6. Get your chat id

**Easy way:** open a chat with **your new bot** in Telegram, press **Start** and send
it any message. Then leave `TELEGRAM_CHAT_ID` empty in step 7. The bot finds your
chat by itself and sends you a "connected" message. (This works if you messaged the
bot in the last 24 hours. If it doesn't find you, send it another message.)

**Manual way** (if you want to fill in the chat id yourself):

1. In Telegram, open a chat with **your new bot** (search its username) and press
   **Start** / send it any message like `hi`. (This step is required — bots can't
   message you until you message them first.)
2. In your web browser, open this address, replacing `<TOKEN>` with your token:

   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```

3. Find the part that says `"chat":{"id":123456789,...`. That number is your
   **chat id**. (If you only see `{"ok":true,"result":[]}`, send the bot another
   message and refresh the page.)

Want alerts in a group instead? Add the bot to the group, send a message in the
group, and use the group's id from the same page (group ids start with `-`).

### 7. Fill in `.env`

Copy the template:

```bat
copy .env.example .env
notepad .env
```

Fill in your values and save:

```
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=123456789
```

`.env` is listed in `.gitignore`, so it won't be committed to git. Never share it.

Check that Telegram works:

```bat
python bot.py --test-telegram
```

You should get a "✅ Test message" in Telegram.

### 8. Set your alerts in `config.yaml`

Open `config.yaml` in Notepad and edit the list under `alerts:`. Examples:

```yaml
poll_seconds: 60        # how often to check (seconds, minimum 10)
vs_currency: usd        # usd, eur, gbp, ...
cooldown_minutes: 30    # don't repeat the same alert more often than this

alerts:
  - coin: bitcoin
    condition: above      # price goes above threshold
    threshold: 150000

  - coin: ethereum
    condition: below      # price goes below threshold
    threshold: 2000

  - coin: solana
    condition: change     # price moves by threshold percent...
    threshold: 5          # ...5%...
    window_minutes: 60    # ...within the last 60 minutes
    direction: any        # up, down or any
    name: SOL big move    # optional label
    cooldown_minutes: 120 # optional, overrides the default above
```

**Coin ids:** use CoinGecko's id, not the ticker symbol. Find it on
<https://www.coingecko.com> — open the coin's page and look for **"API ID"**
(e.g. `bitcoin`, `ethereum`, `solana`, `ripple` for XRP, `dogecoin`). If an id is
wrong, the bot prints `No price for '...'`.

### 9. Run it

```bat
python bot.py
```

You'll see the prices every poll, and any alerts both in the window and in
Telegram. Leave the window open while it runs; press **Ctrl+C** to stop.

---

## How it behaves

- **Cooldown:** once an alert fires, the same alert won't fire again until its
  cooldown has passed, even if the condition stays true on every poll.
- **Percent change** alerts compare the latest price with the price from
  `window_minutes` ago. Prices are kept in memory only, so after starting the bot
  these alerts need one full window of history before they can fire.
- **Errors and rate limits:** if CoinGecko is down or says "too many requests"
  (HTTP 429), the bot waits and retries (5s, 10s, 20s…, respecting CoinGecko's
  `Retry-After`), then skips that poll and keeps running. Telegram failures are
  logged and the alert is still printed. CoinGecko's free API is rate limited, so
  keep `poll_seconds` at 30–60 or more.
- Nothing is saved to disk: no database. Restarting resets cooldowns and history.

## Running the tests

```bat
pip install -r requirements.txt
python -m pytest
```

The tests use fake prices and fake HTTP responses, so they don't need internet
or a Telegram bot.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `'python' is not recognized` | Reinstall Python with "Add python.exe to PATH" ticked, then open a new Command Prompt. |
| `ModuleNotFoundError: No module named 'yaml'` (or `dotenv`, `requests`) | Activate the venv (`.venv\Scripts\activate`) and run `pip install -r requirements.txt`. |
| `Telegram rejected the message (HTTP 401)` | The token is wrong — copy it again from @BotFather. |
| `Telegram rejected the message (HTTP 400) Bad Request: chat not found` | Wrong chat id, or you haven't pressed **Start** in the bot's chat yet. |
| `CoinGecko rate limit hit (429)` now and then | Normal on the free API; the bot retries. If it's constant, raise `poll_seconds`. |
| `Problem in config.yaml: ...` | The message says which alert and what's wrong. Check spacing — YAML uses spaces, not tabs. |
