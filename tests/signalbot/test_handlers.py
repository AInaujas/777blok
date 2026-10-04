"""Drive the real python-telegram-bot Application against a fake Telegram API."""

import asyncio
import json
import time

import pytest

pytest.importorskip("telegram")

from telegram import Update  # noqa: E402
from telegram.ext import Application  # noqa: E402
from telegram.request import BaseRequest  # noqa: E402

from signalbot.handlers import register  # noqa: E402

BOT = {"id": 42, "is_bot": True, "first_name": "Signal", "username": "signal_test_bot"}
USER = {"id": 7, "is_bot": False, "first_name": "Ann", "username": "ann"}
CHAT = {"id": 7, "type": "private", "first_name": "Ann"}


def _decode(value):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


class FakeTelegram(BaseRequest):
    def __init__(self):
        self.calls = []

    @property
    def read_timeout(self):
        return 5

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        name = url.rsplit("/", 1)[-1]
        params = {k: _decode(v) for k, v in (request_data.json_parameters if request_data else {}).items()}
        self.calls.append((name, params))
        if name == "getMe":
            result = BOT
        elif name in ("sendMessage", "editMessageText"):
            result = {"message_id": len(self.calls), "date": int(time.time()), "chat": CHAT,
                      "text": params.get("text", "")}
        elif name == "createInvoiceLink":
            result = "https://t.me/$invoice123"
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()

    def sent(self, name="sendMessage"):
        return [p for n, p in self.calls if n == name]


@pytest.fixture
def app_and_api(service):
    api = FakeTelegram()
    app = Application.builder().token("42:TEST").request(api).get_updates_request(FakeTelegram()).build()
    register(app, service)
    asyncio.run(app.initialize())
    return app, api


def message(text, update_id=1):
    msg = {"message_id": update_id, "date": int(time.time()), "chat": CHAT, "from": USER, "text": text}
    if text.startswith("/"):
        msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return {"update_id": update_id, "message": msg}


def feed(app, data):
    asyncio.run(app.process_update(Update.de_json(data, app.bot)))


def test_start_sends_welcome_with_menu(app_and_api, service):
    app, api = app_and_api
    feed(app, message("/start ref_123"))
    sent = api.sent()[-1]
    assert "Hi Ann" in sent["text"] and sent["parse_mode"] == "HTML"
    assert "inline_keyboard" in sent["reply_markup"]
    assert service.db.get_user(7).username == "ann"


def test_alert_command_and_plain_text(app_and_api, service):
    app, api = app_and_api
    feed(app, message("/start"))
    feed(app, message("/alert btc 90000", 2))
    assert "Alert set" in api.sent()[-1]["text"]
    feed(app, message("eth 3000", 3))  # no slash needed
    assert "ETH ≥ $3,000" in api.sent()[-1]["text"]
    feed(app, message("sol", 4))
    assert "Solana" in api.sent()[-1]["text"]
    assert service.db.count_alerts(7) == 2


def test_delete_button_edits_the_list(app_and_api, service):
    app, api = app_and_api
    feed(app, message("/start"))
    feed(app, message("/alert btc 90000", 2))
    alert_id = service.db.user_alerts(7)[0].id
    feed(app, {"update_id": 3, "callback_query": {
        "id": "cb1", "from": USER, "chat_instance": "x", "data": f"del:{alert_id}",
        "message": {"message_id": 9, "date": int(time.time()), "chat": CHAT, "text": "list"}}})
    assert service.db.count_alerts(7) == 0
    assert api.sent("answerCallbackQuery") and api.sent("editMessageText")


def test_pro_shows_stars_subscription_button(app_and_api, settings):
    app, api = app_and_api
    feed(app, message("/start"))
    feed(app, message("/pro", 2))
    invoice = api.sent("createInvoiceLink")[-1]
    assert invoice["currency"] == "XTR" and invoice["subscription_period"] == 2592000
    markup = api.sent()[-1]["reply_markup"]
    assert markup["inline_keyboard"][0][0]["url"] == "https://t.me/$invoice123"


def test_payment_flow(app_and_api, service, settings):
    app, api = app_and_api
    feed(app, message("/start"))
    feed(app, {"update_id": 2, "pre_checkout_query": {
        "id": "pc1", "from": USER, "currency": "XTR", "total_amount": settings.pro_price_stars,
        "invoice_payload": "pro30:7"}})
    assert api.sent("answerPreCheckoutQuery")[-1]["ok"] is True

    paid = message("", 3)
    paid["message"].pop("text")
    paid["message"]["successful_payment"] = {
        "currency": "XTR", "total_amount": settings.pro_price_stars, "invoice_payload": "pro30:7",
        "telegram_payment_charge_id": "tg-charge-1", "provider_payment_charge_id": "",
        "subscription_expiration_date": int(time.time()) + 30 * 86400, "is_first_recurring": True}
    feed(app, paid)
    assert service.db.get_user(7).is_pro()
    assert "Pro is active" in api.sent()[-1]["text"]


def test_bad_precheckout_is_rejected(app_and_api):
    app, api = app_and_api
    feed(app, {"update_id": 1, "pre_checkout_query": {
        "id": "pc1", "from": USER, "currency": "XTR", "total_amount": 1, "invoice_payload": "pro30:7"}})
    answer = api.sent("answerPreCheckoutQuery")[-1]
    assert answer["ok"] is False and answer["error_message"]


def test_admin_commands_are_hidden_from_users(app_and_api):
    app, api = app_and_api
    feed(app, message("/stats"))
    assert api.sent() == []  # silently ignored for non-admins


def test_admin_backup_sends_database(app_and_api, service):
    app, api = app_and_api
    service.start(999)
    admin = dict(USER, id=999)
    msg = message("/backup")
    msg["message"]["from"] = admin
    msg["message"]["chat"] = dict(CHAT, id=999)
    feed(app, msg)
    assert [n for n, _ in api.calls].count("sendDocument") == 1
