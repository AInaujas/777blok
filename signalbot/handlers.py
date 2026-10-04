"""Telegram glue: turns updates into Service calls and Replies into messages."""

import asyncio
import logging
import os
import tempfile
from datetime import datetime, time as dtime, timezone
from typing import Iterable, Tuple

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
                          PreCheckoutQueryHandler, filters)

from .service import Button, Reply, Service
from .texts import ALERT_USAGE

log = logging.getLogger(__name__)

COMMANDS = [
    ("price", "Price and 1h/24h change"),
    ("alert", "Set a price alert"),
    ("alerts", "Your alerts"),
    ("movers", "Top 24h gainers and losers"),
    ("pro", "Plans and upgrade"),
    ("digest", "Daily market digest on/off"),
    ("invite", "Invite friends, earn Pro"),
    ("help", "How to use the bot"),
]


def keyboard(reply: Reply):
    if not reply.buttons:
        return None
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(b.label, url=b.url) if b.url else InlineKeyboardButton(b.label, callback_data=b.data)
         for b in row]
        for row in reply.buttons
    ])


def svc(context: ContextTypes.DEFAULT_TYPE) -> Service:
    return context.application.bot_data["service"]


async def run(func, *args):
    """Service calls may hit the network or disk: keep them off the event loop."""
    return await asyncio.to_thread(func, *args)


async def send(update: Update, reply: Reply, edit: bool = False) -> None:
    kwargs = dict(parse_mode=ParseMode.HTML, reply_markup=keyboard(reply), disable_web_page_preview=True)
    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(reply.text, **kwargs)
            return
        except BadRequest:
            pass  # e.g. "message is not modified" or too old to edit
    await update.effective_chat.send_message(reply.text, **kwargs)


# --- commands -----------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    arg = context.args[0] if context.args else ""
    await send(update, await run(svc(context).start, u.id, u.username or "", u.first_name or "", arg))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send(update, svc(context).help())


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send(update, await run(svc(context).price, " ".join(context.args)))


async def cmd_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send(update, await run(svc(context).add_alert, update.effective_user.id, list(context.args)))


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send(update, await run(svc(context).list_alerts, update.effective_user.id))


async def cmd_movers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send(update, await run(svc(context).movers))


async def cmd_pro(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    user_id = update.effective_user.id
    reply, upgrade = await run(svc(context).pro, user_id)
    if upgrade:
        inv = svc(context).invoice(user_id)
        try:
            link = await context.bot.create_invoice_link(
                title=inv["title"], description=inv["description"], payload=inv["payload"],
                currency=inv["currency"], prices=[LabeledPrice(l, a) for l, a in inv["prices"]],
                subscription_period=inv["subscription_period"],
            )
            reply.buttons.insert(0, [Button(f"⭐ Upgrade — {inv['prices'][0][1]} Stars/month", url=link)])
        except TelegramError as exc:
            log.error("Could not create invoice link: %s", exc)
    await send(update, reply, edit=edit)


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arg = context.args[0].lower() if context.args else ""
    await send(update, await run(svc(context).set_digest, update.effective_user.id, arg))


async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send(update, svc(context).invite(update.effective_user.id, context.bot.username))


async def cmd_terms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send(update, svc(context).terms())


async def cmd_paysupport(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send(update, svc(context).paysupport())


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Plain text like 'btc' or 'btc 90000' works without the slash command."""
    words = (update.message.text or "").split()
    if len(words) == 1:
        await send(update, await run(svc(context).price, words[0]))
    elif len(words) >= 2:
        await send(update, await run(svc(context).add_alert, update.effective_user.id, words))


# --- buttons ------------------------------------------------------------------

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = q.data or ""
    user_id = update.effective_user.id
    s = svc(context)
    await q.answer()
    if data == "menu:new":
        await send(update, Reply(ALERT_USAGE))
    elif data == "menu:alerts":
        await send(update, await run(s.list_alerts, user_id))
    elif data == "menu:movers":
        await send(update, await run(s.movers))
    elif data == "menu:pro":
        await cmd_pro(update, context)
    elif data.startswith("del:") and data[4:].isdigit():
        await send(update, await run(s.delete_alert, user_id, int(data[4:])), edit=True)
    elif data == "delall":
        await send(update, await run(s.delete_all, user_id), edit=True)
    elif data.startswith("qa:"):
        await send(update, await run(s.quick_alert, user_id, data))


# --- payments -----------------------------------------------------------------

async def on_precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.pre_checkout_query
    error = svc(context).check_precheckout(q.from_user.id, q.invoice_payload, q.currency, q.total_amount)
    await q.answer(ok=error is None, error_message=error)


async def on_paid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    p = update.message.successful_payment
    expires = p.subscription_expiration_date.timestamp() if p.subscription_expiration_date else None
    messages = await run(svc(context).on_payment, update.effective_user.id, p.telegram_payment_charge_id,
                         p.total_amount, p.invoice_payload, bool(p.is_recurring), expires)
    await deliver(context, messages)


# --- admin --------------------------------------------------------------------

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if svc(context).is_admin(update.effective_user.id):
            await func(update, context)
    return wrapper


@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send(update, await run(svc(context).stats))


@admin_only
async def cmd_grant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send(update, await run(svc(context).grant, list(context.args)))


@admin_only
async def cmd_refund(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id, charge_id = int(context.args[0]), context.args[1]
        await context.bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge_id)
    except (IndexError, ValueError):
        await send(update, Reply("Usage: /refund <user_id> <telegram_payment_charge_id>"))
        return
    except TelegramError as exc:
        await send(update, Reply(f"Refund failed: {exc}"))
        return
    svc(context).db.mark_refunded(charge_id)
    await send(update, Reply("Refunded and Pro removed."))


@admin_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.partition(" ")[2].strip()
    if not text:
        await send(update, Reply("Usage: /broadcast your message (HTML allowed)"))
        return
    ids = svc(context).db.reachable_user_ids()
    await send(update, Reply(f"Sending to {len(ids)} users in the background…"))

    async def job():
        ok = await deliver(context, [(uid, text) for uid in ids])
        await context.bot.send_message(update.effective_chat.id, f"Broadcast done: {ok}/{len(ids)} delivered.")

    context.application.create_task(job())


# --- delivery & jobs --------------------------------------------------------------

async def deliver(context: ContextTypes.DEFAULT_TYPE, messages: Iterable[Tuple[int, str]]) -> int:
    """Send messages; the rate limiter paces them and retries flood errors.

    Users who blocked the bot are marked so we stop messaging them.
    """
    sent = 0
    db = svc(context).db
    for chat_id, text in messages:
        try:
            await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            sent += 1
        except Forbidden:
            db.mark_blocked(chat_id)
        except TelegramError as exc:
            log.warning("Send to %s failed: %s", chat_id, exc)
    return sent


async def job_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        messages = await run(svc(context).check_round)
    except Exception:
        log.exception("Alert check failed; will retry next round")
        return
    if messages:
        log.info("Sending %d alert(s)", len(messages))
        await deliver(context, messages)


async def job_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        messages = await run(svc(context).digest_messages)
    except Exception:
        log.exception("Digest failed")
        return
    await deliver(context, messages)


async def job_backup(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send each admin a copy of the database once a day."""
    s = svc(context)
    if not s.s.admin_ids:
        return
    with tempfile.TemporaryDirectory() as tmp:
        name = f"signalbot-{datetime.now(timezone.utc):%Y%m%d}.db"
        path = await run(s.db.backup_to, os.path.join(tmp, name))
        for admin in s.s.admin_ids:
            try:
                with open(path, "rb") as f:
                    await context.bot.send_document(admin, f, filename=name, caption="Daily database backup")
            except TelegramError as exc:
                log.warning("Backup to admin %s failed: %s", admin, exc)


@admin_only
async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await job_backup(context)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled error", exc_info=context.error)


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])


def register(app: Application, service: Service) -> None:
    app.bot_data["service"] = service
    for name, func in [
        ("start", cmd_start), ("help", cmd_help), ("price", cmd_price), ("p", cmd_price),
        ("alert", cmd_alert), ("alerts", cmd_alerts), ("movers", cmd_movers), ("pro", cmd_pro),
        ("digest", cmd_digest), ("invite", cmd_invite), ("terms", cmd_terms), ("paysupport", cmd_paysupport),
        ("stats", cmd_stats), ("grant", cmd_grant), ("refund", cmd_refund), ("broadcast", cmd_broadcast),
        ("backup", cmd_backup),
    ]:
        app.add_handler(CommandHandler(name, func))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(PreCheckoutQueryHandler(on_precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, on_paid))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_text))
    app.add_error_handler(on_error)

    s = service.s
    app.job_queue.run_repeating(job_check, interval=s.pro_check_seconds, first=10, name="check")
    app.job_queue.run_daily(job_digest, time=dtime(hour=s.digest_hour_utc, tzinfo=timezone.utc), name="digest")
    app.job_queue.run_daily(job_backup, time=dtime(hour=3, minute=17, tzinfo=timezone.utc), name="backup")
