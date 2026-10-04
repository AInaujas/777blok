"""CoinGecko and Telegram handling, with fake HTTP responses (no real network)."""

import requests

from coingecko import fetch_prices
from notifier import Notifier


class FakeResponse:
    def __init__(self, status_code=200, data=None, headers=None):
        self.status_code = status_code
        self._data = data
        self.headers = headers or {}
        self.text = str(data)

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeSession:
    """Returns (or raises) the queued responses in order and records calls."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    get = post = _next


def test_fetch_prices_success():
    session = FakeSession(FakeResponse(200, {"bitcoin": {"usd": 65000}, "ethereum": {"usd": 3000.5}}))
    prices = fetch_prices(["bitcoin", "ethereum", "bitcoin"], session=session, sleep=lambda s: None)
    assert prices == {"bitcoin": 65000.0, "ethereum": 3000.5}
    assert session.calls[0][1]["params"] == {"ids": "bitcoin,ethereum", "vs_currencies": "usd"}


def test_fetch_prices_skips_unknown_coin():
    session = FakeSession(FakeResponse(200, {"bitcoin": {"usd": 1}}))
    assert fetch_prices(["bitcoin", "notacoin"], session=session, sleep=lambda s: None) == {"bitcoin": 1.0}


def test_fetch_prices_retries_with_backoff_then_succeeds():
    sleeps = []
    session = FakeSession(
        requests.ConnectionError("boom"),
        FakeResponse(500),
        FakeResponse(429),
        FakeResponse(200, {"bitcoin": {"usd": 5}}),
    )
    prices = fetch_prices(["bitcoin"], session=session, base_delay=1, sleep=sleeps.append)
    assert prices == {"bitcoin": 5.0}
    assert sleeps == [1, 2, 4]


def test_fetch_prices_honours_retry_after():
    sleeps = []
    session = FakeSession(FakeResponse(429, headers={"Retry-After": "42"}), FakeResponse(200, {"bitcoin": {"usd": 5}}))
    fetch_prices(["bitcoin"], session=session, base_delay=1, sleep=sleeps.append)
    assert sleeps == [42]


def test_fetch_prices_gives_up_without_raising():
    session = FakeSession(*[FakeResponse(429)] * 3)
    assert fetch_prices(["bitcoin"], session=session, max_attempts=3, sleep=lambda s: None) is None


def test_fetch_prices_bad_json_is_retried():
    session = FakeSession(FakeResponse(200, ValueError("bad")), FakeResponse(200, {"bitcoin": {"usd": 5}}))
    assert fetch_prices(["bitcoin"], session=session, sleep=lambda s: None) == {"bitcoin": 5.0}


def test_fetch_prices_does_not_retry_client_errors():
    session = FakeSession(FakeResponse(400, {"error": "bad"}))
    assert fetch_prices(["bitcoin"], session=session, sleep=lambda s: None) is None
    assert len(session.calls) == 1


def test_notifier_console_only_without_telegram(capsys):
    session = FakeSession()
    notifier = Notifier("", "", session=session)
    assert notifier.send("hello") is False
    assert "ALERT hello" in capsys.readouterr().out
    assert session.calls == []


def test_notifier_sends_to_telegram(capsys):
    session = FakeSession(FakeResponse(200, {"ok": True}))
    notifier = Notifier("123:ABC", "999", session=session)
    assert notifier.send("hello") is True
    args, kwargs = session.calls[0]
    assert args[0] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert kwargs["json"] == {"chat_id": "999", "text": "hello"}
    assert "ALERT hello" in capsys.readouterr().out


def test_notifier_survives_telegram_failure_and_hides_token(caplog):
    session = FakeSession(requests.ConnectionError("https://api.telegram.org/botSECRET/sendMessage"))
    notifier = Notifier("SECRET", "999", session=session)
    assert notifier.send("hello") is False
    assert "SECRET" not in caplog.text


def test_find_chat_id_uses_latest_message():
    updates = {"ok": True, "result": [
        {"message": {"chat": {"id": 111}}},
        {"message": {"chat": {"id": 222}}},
        {"edited_message": {"chat": {"id": 333}}},
    ]}
    notifier = Notifier("123:ABC", "", session=FakeSession(FakeResponse(200, updates)))
    assert notifier.find_chat_id() == "222"


def test_find_chat_id_handles_no_messages_and_errors():
    empty = Notifier("123:ABC", "", session=FakeSession(FakeResponse(200, {"ok": True, "result": []})))
    assert empty.find_chat_id() == ""
    down = Notifier("123:ABC", "", session=FakeSession(requests.ConnectionError("x")))
    assert down.find_chat_id() == ""
    assert Notifier("", "").find_chat_id() == ""


def test_connect_telegram_discovers_saves_and_reuses_chat_id(capsys):
    from bot import connect_telegram
    session = FakeSession(
        FakeResponse(200, {"ok": True, "result": [{"message": {"chat": {"id": 42}}}]}),
        FakeResponse(200, {"ok": True}),  # the "connected" message
    )
    state = {}
    notifier = Notifier("123:ABC", "", session=session)
    connect_telegram(notifier, state)
    assert notifier.chat_id == "42" and state["chat_id"] == "42"
    assert "connected" in session.calls[1][1]["json"]["text"]

    # Next run: chat id comes from the saved state, no Telegram lookup.
    again = Notifier("123:ABC", "", session=FakeSession())
    connect_telegram(again, state)
    assert again.chat_id == "42"


def test_connect_telegram_greets_once_when_chat_id_is_set_by_hand():
    from bot import connect_telegram
    state = {}
    first = FakeSession(FakeResponse(200, {"ok": True}))
    connect_telegram(Notifier("123:ABC", "777", session=first), state)
    assert len(first.calls) == 1 and "connected" in first.calls[0][1]["json"]["text"]
    assert state["greeted_chat"] == "777"

    later = FakeSession()
    connect_telegram(Notifier("123:ABC", "777", session=later), state)
    assert later.calls == []  # no repeat hello


def test_connect_telegram_retries_hello_if_it_failed():
    from bot import connect_telegram
    state = {}
    connect_telegram(Notifier("123:ABC", "777", session=FakeSession(FakeResponse(401, {"description": "Unauthorized"}))), state)
    assert "greeted_chat" not in state
    retry = FakeSession(FakeResponse(200, {"ok": True}))
    connect_telegram(Notifier("123:ABC", "777", session=retry), state)
    assert state["greeted_chat"] == "777"


def test_find_chat_id_logs_telegram_reason(caplog):
    session = FakeSession(FakeResponse(409, {"ok": False, "description": "Conflict: webhook is active"}))
    assert Notifier("123:ABC", "", session=session).find_chat_id() == ""
    assert "webhook is active" in caplog.text
