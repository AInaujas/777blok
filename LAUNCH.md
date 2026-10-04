# Launching the multi-user bot (`signalbot`)

A public Telegram bot anyone can use: people set their own price alerts, the
free plan gets 3 alerts, and **Pro** is a monthly subscription paid in
**Telegram Stars**. Alerts only: it never trades and never asks for keys.

## What users get

| | Free | ⭐ Pro (250 Stars / month) |
|---|---|---|
| Price alerts | 3 | 50 |
| How often checked | every 5 min | every minute |
| ⚡ Move alerts ("SOL ±5% in 1h") | – | ✓ |
| ☀️ Daily market digest | – | ✓ |
| `/price`, `/movers` | ✓ | ✓ |

New users get a **3-day Pro trial** automatically. **Referrals:** when someone
joins with your user's invite link (`/invite`) and pays, the inviter gets 30
days of Pro.

Users can type `btc` for a price or `btc 90000` for an alert, with no slash
needed. Level alerts fire **once when the price crosses** the level and re-arm
when it moves back, so there's no spam.

**Admin commands** (only for ids listed in `ADMIN_IDS`):
- `/stats`: users, Pro count and Stars revenue.
- `/broadcast text`: send a message to every user.
- `/grant <user_id> <days>`: give a user free Pro.
- `/refund <user_id> <charge_id>`: refund a payment.
- `/backup`: send yourself a copy of the database. Admins also get one
  automatically every day.

---

## Step 1: Create the product bot (5 min)

Use a **new** bot for the product, separate from your personal 777signal.

1. Open **@BotFather** → `/newbot` → pick a name, e.g. *Crypto Alerts 24/7*, and
   a username ending in `bot`. Copy the token.
2. Still in BotFather, `/mybots` → your bot → **Edit Bot**:
   - **Description**: what people see before pressing Start. For example:
     *"Get a Telegram message the moment Bitcoin, Ethereum or any of 10,000+
     coins hits your price. Free to start."*
   - **About**, then upload a **profile picture**.
3. Payments in Stars need **no setup**. They work for every bot.

## Step 2: Put it on a server (runs 24/7)

The bot must run all the time, so it can't live on GitHub Actions like the
personal bot. Pick one option. **Run only one copy at a time**: two copies
with the same token fight over messages.

### Option A — Railway (easiest, about $5/month, no terminal)

1. Sign up at railway.com with your GitHub account.
2. **New Project → Deploy from GitHub repo →** `AInaujas/777blok`. Railway
   finds the `Dockerfile` by itself.
3. Open the service → **Variables**, and add:
   - `BOT_TOKEN`: your new bot token
   - `ADMIN_IDS`: your Telegram id (`6977102608`)
   - `SUPPORT_CONTACT`: your @username
4. Right-click the service → **Attach volume**, with mount path `/app/data`.
   This keeps the database when the bot restarts. **Don't skip this step.**
5. Deploy. Message your bot `/start`.

### Option B — Your own server (about €4/month, most control)

Rent the smallest Ubuntu server, for example Hetzner's cheapest cloud server. Then, in its
terminal:

```bash
curl -fsSL https://get.docker.com | sh
git clone https://github.com/AInaujas/777blok.git && cd 777blok
cp .env.example .env && nano .env        # fill BOT_TOKEN, ADMIN_IDS, SUPPORT_CONTACT
docker compose up -d --build
docker compose logs -f                   # watch it run; Ctrl+C to stop watching
```

To update after new code is merged:
`git pull && docker compose up -d --build`

### Optional: a free CoinGecko key

The bot makes one batched price request per minute for all users, so the free
public API is usually enough. For more headroom, create a free **Demo** key at
coingecko.com/en/api and set `COINGECKO_API_KEY`.

## Step 3: Check everything works

1. Send `/start` → you get the welcome message and a 3-day trial.
2. Send `btc 200000` → you get "✅ Alert set".
3. Send `/pro` → tap Upgrade. Telegram shows a 250⭐ subscription. To test the
   payment cheaply, temporarily set `PRO_PRICE_STARS=1`.
4. Send `/stats` → your revenue appears.
5. The next morning, check that a database backup file arrived in your chat.

---

## Making money: an honest plan

**What 250 Stars is worth to you:** Telegram pays developers roughly
**$0.013 per Star**, so about **$3.25 per Pro user per month**. Check the
current rate before relying on it.
- Payouts go through **Fragment**, paid in TON, after a ~21-day hold.
- You need at least 1,000 Stars before you can withdraw.

So 100 paying users is about $325 a month, and 1,000 is about $3,250.
Typically 2–5% of active users pay, so you need traffic. The code is the easy
part; distribution is the job.

**Ways to get users:**
- Post your bot link in crypto communities (Reddit, X, Telegram groups),
  following each place's rules.
- Answer "how do I get price alerts" questions with a link to your bot.
- Make short videos: "I get a Telegram ping when BTC hits my price — free bot".
- Reward referrals; this is already built in with `/invite`.
- Make `/movers` and the daily digest good enough that people forward them.

**Ideas for later:** more languages, alerts on funding rates or volume spikes,
group or channel alerts for communities, a cheaper yearly plan.

## Legal: read this

- **Sell a tool, not "signals".** Telling people *what to buy or sell*,
  especially for money, can count as investment advice. Under the EU's MiCA
  rules (fully in force since 1 July 2026), advice on crypto-assets needs a
  licence. This bot lets users set **their own** alerts on public prices,
  which is a software tool. Keep it that way: never promise profits, never
  post "buy now" calls, and keep the *"not financial advice"* line.
- **Telegram's payment rules** require working `/terms` and `/paysupport`
  commands, and that you handle refund requests. Both commands are built in;
  refund with `/refund`.
- **Taxes:** Stars income is income. Where you live you'll likely need to
  register as self-employed or as a company. Ask an accountant once you're
  earning.
- **Privacy (GDPR):** the bot stores only what it needs (Telegram id, name,
  alerts, payments). `/terms` says so.
