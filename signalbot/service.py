"""Everything the bot says and decides, without any Telegram code.

Handlers (handlers.py) only translate between Telegram and these methods,
which keeps the product logic easy to test.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .db import DAY, Database, User
from .engine import Fired, evaluate, initial_kind, select_checkable
from .market import Market
from .settings import Settings
from .texts import (ALERT_USAGE, DISCLAIMER, ParseError, describe_alert, esc, fired_message, money,
                    parse_alert, pct)

log = logging.getLogger(__name__)


@dataclass
class Button:
    label: str
    data: Optional[str] = None   # callback data
    url: Optional[str] = None


@dataclass
class Reply:
    text: str
    buttons: List[List[Button]] = field(default_factory=list)


MAIN_MENU = [
    [Button("➕ New alert", "menu:new"), Button("📋 My alerts", "menu:alerts")],
    [Button("📈 Top movers", "menu:movers"), Button("⭐ Pro", "menu:pro")],
]


class Service:
    def __init__(self, db: Database, market: Market, settings: Settings, clock=time.time):
        self.db = db
        self.market = market
        self.s = settings
        self.clock = clock
        self._last_free_round = 0.0

    # --- helpers -------------------------------------------------------------

    def _user(self, user_id: int) -> User:
        user = self.db.get_user(user_id)
        if user is None:
            self.db.upsert_user(user_id, now=self.clock())
            user = self.db.get_user(user_id)
        return user

    def limit(self, user: User) -> int:
        return self.s.pro_max_alerts if user.is_pro(self.clock()) else self.s.free_max_alerts

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.s.admin_ids

    # --- onboarding ----------------------------------------------------------

    def start(self, user_id: int, username: str = "", first_name: str = "", arg: str = "") -> Reply:
        referred_by = None
        if arg.startswith("ref_") and arg[4:].isdigit():
            referred_by = int(arg[4:])
            if not self.db.get_user(referred_by):
                referred_by = None
        is_new = self.db.upsert_user(user_id, username, first_name, referred_by, now=self.clock())
        trial = is_new and self.db.start_trial(user_id, self.s.trial_days, now=self.clock())
        hello = f"👋 Hi {esc(first_name) or 'there'}!" if first_name else "👋 Hi!"
        text = (
            f"{hello}\n\nI watch crypto prices 24/7 and message you the moment they hit your levels.\n\n"
            "Try it now:\n<code>/alert btc 100000</code>\n<code>/price eth</code>\n\n"
        )
        if trial:
            text += f"🎁 You have <b>{self.s.trial_days} days of Pro free</b> — every feature unlocked.\n\n"
        text += f"<i>{DISCLAIMER}</i>"
        return Reply(text, MAIN_MENU)

    def help(self) -> Reply:
        return Reply(
            "<b>Commands</b>\n"
            "/price <i>coin</i> — price and 1h/24h change\n"
            "/alert <i>coin level</i> — set an alert\n"
            "/alerts — your alerts (tap ❌ to delete)\n"
            "/movers — biggest 24h gainers and losers\n"
            "/pro — plans and upgrade\n"
            "/digest — daily market summary on/off ⭐\n"
            "/invite — invite friends, earn free Pro\n"
            "/terms · /paysupport\n\n" + ALERT_USAGE + f"\n\n<i>{DISCLAIMER}</i>",
            MAIN_MENU,
        )

    # --- prices --------------------------------------------------------------

    def price(self, query: str) -> Reply:
        if not query:
            return Reply("Which coin? For example: <code>/price btc</code>")
        coin = self.market.find(query)
        if not coin:
            return Reply(f"I couldn't find “{esc(query)}”. Try the symbol (btc) or full name (bitcoin).")
        price = coin.price
        if price is None:
            price = self.market.prices([coin.id]).get(coin.id)
        if price is None:
            return Reply("Prices are temporarily unavailable, please try again in a minute.")
        rank = f" · #{coin.rank}" if coin.rank else ""
        text = (f"<b>{esc(coin.name)} ({esc(coin.symbol)})</b>{rank}\n"
                f"{money(price)}\n1h {pct(coin.change_1h)}   24h {pct(coin.change_24h)}")
        buttons = []
        if len(coin.id) <= 40:
            buttons = [[Button("🔔 +5%", f"qa:{coin.id}:up:5"), Button("🔔 −5%", f"qa:{coin.id}:down:5"),
                        Button("⚡ ±3% move", f"qa:{coin.id}:move:3")]]
        return Reply(text, buttons)

    def movers(self) -> Reply:
        gainers, losers = self.market.movers(5)
        if not gainers:
            return Reply("Market data is temporarily unavailable, please try again in a minute.")
        line = lambda c: f"{esc(c.symbol):<6} {pct(c.change_24h)}  {money(c.price)}"  # noqa: E731
        return Reply(
            "<b>24h top gainers</b>\n" + "\n".join(map(line, gainers)) +
            "\n\n<b>24h top losers</b>\n" + "\n".join(map(line, losers)) +
            "\n\n<i>Top 250 coins by market cap.</i>"
        )

    # --- alerts --------------------------------------------------------------

    def add_alert(self, user_id: int, args: List[str]) -> Reply:
        try:
            req = parse_alert(args)
        except ParseError as exc:
            return Reply(str(exc))
        coin = self.market.find(req.coin)
        if not coin:
            return Reply(f"I couldn't find “{esc(req.coin)}”. Try the symbol (btc) or name (bitcoin).")
        price = coin.price if coin.price is not None else self.market.prices([coin.id]).get(coin.id)
        return self._create(user_id, coin.id, coin.symbol, req.kind, req.threshold, req.window_min, price)

    def quick_alert(self, user_id: int, data: str) -> Reply:
        """Buttons under /price: qa:<coin_id>:up|down|move:<percent>."""
        try:
            _, coin_id, mode, value = data.split(":")
            value = float(value)
        except ValueError:
            return Reply("That button has expired, please try again.")
        price = self.market.prices([coin_id]).get(coin_id)
        coin = next((c for c in self.market.top_coins() if c.id == coin_id), None)
        symbol = coin.symbol if coin else coin_id.upper()
        if mode == "move":
            return self._create(user_id, coin_id, symbol, "change", value, 60, price)
        if price is None:
            return Reply("Prices are temporarily unavailable, please try again in a minute.")
        level = price * (1 + value / 100) if mode == "up" else price * (1 - value / 100)
        return self._create(user_id, coin_id, symbol, "above" if mode == "up" else "below",
                            _round_level(level), 60, price)

    def _create(self, user_id, coin_id, symbol, kind, threshold, window_min, price) -> Reply:
        user = self._user(user_id)
        pro = user.is_pro(self.clock())
        if kind == "change" and not pro:
            return Reply("⚡ Move alerts (e.g. “SOL moves 5% in 1h”) are a <b>Pro</b> feature.",
                         [[Button("⭐ See Pro", "menu:pro")]])
        if self.db.count_alerts(user_id) >= self.limit(user):
            if pro:
                return Reply(f"You've reached the maximum of {self.s.pro_max_alerts} alerts. Delete one in /alerts.")
            return Reply(f"Free plan: up to {self.s.free_max_alerts} alerts. Delete one in /alerts, "
                         f"or go <b>Pro</b> for {self.s.pro_max_alerts}.", [[Button("⭐ Go Pro", "menu:pro")]])
        if kind is None:
            kind = initial_kind(price, threshold)
        if price is not None and kind in ("above", "below"):
            if (kind == "above" and price >= threshold) or (kind == "below" and price <= threshold):
                side = "above" if kind == "above" else "below"
                return Reply(f"{esc(symbol)} is already {side} {money(threshold)} (now {money(price)}).")
        self.db.add_alert(user_id, coin_id, symbol, kind, threshold, window_min, now=self.clock())
        text = f"✅ Alert set: <b>{esc(describe_alert(kind, symbol.upper(), threshold, window_min))}</b>"
        if price is not None and kind != "change":
            text += f"\nNow {money(price)} ({(threshold - price) / price * 100:+.1f}% away)"
        if not pro:
            text += f"\n<i>Free plan checks every {self.s.free_check_seconds // 60} min. Pro checks every minute.</i>"
        return Reply(text, [[Button("📋 My alerts", "menu:alerts")]])

    def list_alerts(self, user_id: int) -> Reply:
        user = self._user(user_id)
        alerts = self.db.user_alerts(user_id)
        if not alerts:
            return Reply("You have no alerts yet.\n\n" + ALERT_USAGE)
        pro = user.is_pro(self.clock())
        limit = self.limit(user)
        lines, buttons = [], []
        for n, a in enumerate(alerts):
            paused = n >= limit or (a.kind == "change" and not pro)
            mark = "⏸" if paused else ("🔔" if a.armed or a.kind == "change" else "✔️")
            label = describe_alert(a.kind, a.symbol, a.threshold, a.window_min)
            lines.append(f"{mark} {esc(label)}")
            buttons.append([Button(f"❌ {label}"[:60], f"del:{a.id}")])
        if len(alerts) > 1:
            buttons.append([Button("🗑 Delete all", "delall")])
        text = f"<b>Your alerts</b> ({len(alerts)}/{limit})\n" + "\n".join(lines)
        if any(l.startswith("⏸") for l in lines):
            text += "\n\n⏸ = paused on the free plan. /pro to resume."
        text += "\n✔️ = already triggered; re-arms when the price moves back."
        return Reply(text, buttons)

    def delete_alert(self, user_id: int, alert_id: int) -> Reply:
        self.db.delete_alert(user_id, alert_id)
        return self.list_alerts(user_id)

    def delete_all(self, user_id: int) -> Reply:
        n = self.db.delete_all_alerts(user_id)
        return Reply(f"Deleted {n} alert(s).")

    # --- Pro, payments, referrals -------------------------------------------

    def pro(self, user_id: int) -> Tuple[Reply, bool]:
        """The plans screen. Second value: whether to attach an upgrade (invoice) button."""
        user = self._user(user_id)
        now = self.clock()
        s = self.s
        text = (
            "<b>Free</b>\n"
            f"• {s.free_max_alerts} price alerts\n• checked every {s.free_check_seconds // 60} minutes\n"
            "• /price and /movers\n\n"
            f"<b>⭐ Pro — {s.pro_price_stars} Stars / month</b>\n"
            f"• {s.pro_max_alerts} alerts\n• checked <b>every minute</b>\n"
            "• ⚡ move alerts: “SOL ±5% in 1h”\n"
            "• ☀️ daily market digest\n\n"
        )
        if user.is_pro(now):
            text += f"✅ You're Pro until <b>{_date(user.pro_until)}</b>."
            if self.db.payment_count(user_id):
                text += "\nManage or cancel your subscription in Telegram: Settings → My Stars."
            return Reply(text), not self.db.payment_count(user_id)
        text += "Pay with Telegram Stars — cancel anytime in Settings → My Stars."
        return Reply(text), True

    def invoice(self, user_id: int) -> dict:
        return {
            "title": "Pro — 30 days",
            "description": f"{self.s.pro_max_alerts} alerts, 1-minute checks, move alerts and daily digest.",
            "payload": f"pro30:{user_id}",
            "currency": "XTR",
            "prices": [("Pro", self.s.pro_price_stars)],
            "subscription_period": 30 * DAY,
        }

    def check_precheckout(self, user_id: int, payload: str, currency: str, amount: int) -> Optional[str]:
        """None if the payment may go ahead, otherwise an error shown to the user."""
        if currency != "XTR" or payload != f"pro30:{user_id}":
            return "This invoice is no longer valid. Please open /pro and try again."
        if amount != self.s.pro_price_stars:
            return "The price has changed. Please open /pro and try again."
        return None

    def on_payment(self, user_id: int, charge_id: str, amount: int, payload: str,
                   is_recurring: bool = False, expires_at: Optional[float] = None) -> List[Tuple[int, str]]:
        """Record a successful payment. Returns messages to send as (chat_id, text)."""
        first = self.db.payment_count(user_id) == 0
        if not self.db.record_payment(charge_id, user_id, amount, payload, is_recurring, now=self.clock()):
            return []  # duplicate delivery
        until = self.db.extend_pro(user_id, until=expires_at, now=self.clock()) if expires_at \
            else self.db.extend_pro(user_id, days=30, now=self.clock())
        out = [(user_id, f"🎉 Thank you! <b>Pro is active until {_date(until)}</b>.\n"
                         "All your alerts are now checked every minute.")]
        user = self.db.get_user(user_id)
        if first and user and user.referred_by and self.s.referral_bonus_days:
            ref_until = self.db.extend_pro(user.referred_by, days=self.s.referral_bonus_days, now=self.clock())
            out.append((user.referred_by,
                        f"🎁 A friend you invited just went Pro — you get <b>{self.s.referral_bonus_days} days "
                        f"of Pro</b> free (until {_date(ref_until)}). Thanks!"))
        return out

    def invite(self, user_id: int, bot_username: str) -> Reply:
        link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        return Reply(
            "<b>Invite friends, get Pro free</b>\n\n"
            f"Share your link. When someone joins with it and upgrades, you get "
            f"<b>{self.s.referral_bonus_days} days of Pro</b>.\n\n{esc(link)}",
            [[Button("📤 Share", url=f"https://t.me/share/url?url={link}")]],
        )

    def set_digest(self, user_id: int, arg: str) -> Reply:
        user = self._user(user_id)
        if arg in ("on", "off"):
            self.db.set_digest(user_id, arg == "on")
            user = self.db.get_user(user_id)
        state = "on" if user.digest else "off"
        text = (f"☀️ Daily digest is <b>{state}</b> (sent {self.s.digest_hour_utc:02d}:00 UTC).\n"
                "Turn on/off: <code>/digest on</code> · <code>/digest off</code>")
        if not user.is_pro(self.clock()):
            text += "\n\nThe digest is a ⭐ Pro feature."
        return Reply(text)

    def terms(self) -> Reply:
        return Reply(
            "<b>Terms</b>\n"
            "• This bot sends price alerts and market data. It is not financial, investment or trading "
            "advice, and it never trades or touches your funds.\n"
            "• Prices come from CoinGecko and can be delayed or wrong. Alerts can arrive late or not at "
            "all (network or API outages). Don't rely on them for time-critical decisions.\n"
            "• Pro is a monthly Telegram Stars subscription. Cancel anytime in Telegram → Settings → My "
            "Stars; it then runs until the end of the paid period.\n"
            "• Refunds: contact /paysupport within 7 days of a payment.\n"
            "• We store your Telegram id, name, alerts and payment records only to run the service."
        )

    def paysupport(self) -> Reply:
        contact = self.s.support_contact or "the bot owner"
        return Reply(f"For payment questions or refunds, message {esc(contact)} with the date of your payment. "
                     "We reply within 48 hours.")

    # --- background jobs -----------------------------------------------------

    def check_round(self) -> List[Tuple[int, str]]:
        """One alert check. Returns messages (chat_id, text) to send. Blocking: run in a thread."""
        now = self.clock()
        include_free = now - self._last_free_round >= self.s.free_check_seconds - 5
        rows = self.db.live_alerts(now)
        if not rows:
            return []
        prices = self.market.prices({a.coin_id for a, _ in rows})
        if not prices:
            log.warning("No prices this round")
            return []
        self.db.add_prices(prices, now)
        if include_free:
            self._last_free_round = now
        alerts = select_checkable(rows, self.s.free_max_alerts, self.s.pro_max_alerts, include_free)
        fired, updates = evaluate(alerts, prices, now, self.db.price_at_or_before)
        self.db.update_alert_state(updates)
        return [(f.alert.user_id, _fired_text(f)) for f in fired]

    def digest_messages(self) -> List[Tuple[int, str]]:
        now = self.clock()
        out = []
        top = self.market.top_coins()
        if top:
            majors = [c for c in top if c.symbol in ("BTC", "ETH", "SOL")]
            gainers, losers = self.market.movers(3)
            body = "\n".join(f"{esc(c.symbol)} {money(c.price)} {pct(c.change_24h)}" for c in majors)
            body += "\n\n<b>Top gainers</b>\n" + "\n".join(f"{esc(c.symbol)} {pct(c.change_24h)}" for c in gainers)
            body += "\n<b>Top losers</b>\n" + "\n".join(f"{esc(c.symbol)} {pct(c.change_24h)}" for c in losers)
            for user in self.db.digest_users(now):
                out.append((user.id, f"☀️ <b>Daily crypto digest</b>\n\n{body}\n\n<i>{DISCLAIMER}</i>"))
        # Pro ended in the last day (trial or cancelled subscription): tell the user once.
        for uid in self.db.pro_ended_between(now - DAY, now):
            n = self.db.count_alerts(uid)
            extra = max(0, n - self.s.free_max_alerts)
            text = "Your ⭐ Pro has ended. You're on the free plan now."
            if extra:
                text += f" {extra} alert(s) are paused until you upgrade."
            out.append((uid, text + "\n/pro to continue."))
        return out

    # --- admin ---------------------------------------------------------------

    def stats(self) -> Reply:
        s = self.db.stats(self.clock())
        return Reply(
            "<b>📊 Stats</b>\n"
            f"Users: {s['users']} (reachable {s['reachable']})\n"
            f"New 7d: {s['new_7d']} · Active 7d: {s['active_7d']}\n"
            f"Pro now: {s['pro']} · Paying 30d: {s['paying_30d']}\n"
            f"Stars 30d: {s['stars_30d']}⭐ · All time: {s['stars_total']}⭐\n"
            f"Active alerts: {s['alerts']}"
        )

    def grant(self, args: List[str]) -> Reply:
        try:
            user_id, days = int(args[0]), float(args[1])
        except (IndexError, ValueError):
            return Reply("Usage: /grant <user_id> <days>")
        if not self.db.get_user(user_id):
            return Reply("Unknown user.")
        return Reply(f"User {user_id} is Pro until {_date(self.db.extend_pro(user_id, days=days, now=self.clock()))}.")


def _fired_text(f: Fired) -> str:
    a = f.alert
    return fired_message(a.kind, a.symbol, a.threshold, f.price, f.change, a.window_min)


def _round_level(value: float) -> float:
    """Pleasant round numbers for button-made alerts: 91,234.5 -> 91,200."""
    if value >= 1000:
        return round(value, -2)
    if value >= 10:
        return round(value, 1)
    if value >= 1:
        return round(value, 3)
    return float(f"{value:.4g}")


def _date(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %Y")
