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
