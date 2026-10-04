import pytest
import requests

from signalbot.market import Market
from signalbot.texts import ParseError, money, parse_alert, parse_number


@pytest.mark.parametrize("text, kind, threshold, window", [
    ("btc 90000", None, 90000, 60),
    ("btc >90k", "above", 90000, 60),
    ("btc > 90,000", "above", 90000, 60),
    ("btc above $90000", "above", 90000, 60),
    ("eth <2500", "below", 2500, 60),
    ("eth under 2.5k", "below", 2500, 60),
    ("sol 5%", "change", 5, 60),
    ("sol ±8% 4h", "change", 8, 240),
    ("sol -3% in 15m", "change", 3, 15),
    ("pepe 0.0000123", None, 0.0000123, 60),
])
def test_parse_alert(text, kind, threshold, window):
    req = parse_alert(text.split())
    assert (req.kind, req.window_min) == (kind, window)
    assert req.threshold == pytest.approx(threshold)


@pytest.mark.parametrize("text", ["btc", "btc abc", "sol 5% 2h", "sol 90%", "btc 0", "btc -5"])
def test_parse_alert_errors(text):
    with pytest.raises(ParseError):
        parse_alert(text.split())


def test_parse_number():
    assert parse_number("1.5m") == 1_500_000
    with pytest.raises(ParseError):
        parse_number("lots")


def test_money_formats():
    assert money(91234.5) == "$91,234"
    assert money(12.3456) == "$12.35"
    assert money(0.1234) == "$0.1234"
    assert money(0.0000123) == "$0.0000123"


class Resp:
    def __init__(self, status, data=None, headers=None):
        self.status_code, self._data, self.headers = status, data, headers or {}

    def json(self):
        return self._data


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


MARKETS = [
    {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 85000, "market_cap_rank": 1,
     "price_change_percentage_24h_in_currency": 2.0, "price_change_percentage_1h_in_currency": 0.1},
    {"id": "fake-btc", "symbol": "btc", "name": "Fake BTC", "current_price": 1, "market_cap_rank": 200,
     "price_change_percentage_24h_in_currency": 50.0},
    {"id": "solana", "symbol": "sol", "name": "Solana", "current_price": 120, "market_cap_rank": 5,
     "price_change_percentage_24h_in_currency": -3.0},
]


def test_find_prefers_biggest_coin_and_caches_markets():
    session = Session(Resp(200, MARKETS))
    m = Market(session=session, sleep=lambda s: None)
    assert m.find("BTC").id == "bitcoin"
    assert m.find("solana").symbol == "SOL"
    assert len(session.calls) == 1


def test_find_falls_back_to_search():
    session = Session(Resp(200, MARKETS), Resp(200, {"coins": [{"id": "pepe", "symbol": "PEPE", "name": "Pepe"}]}))
    m = Market(session=session, sleep=lambda s: None)
    assert m.find("pepe").id == "pepe"
    assert m.find("pepe").id == "pepe"  # cached
    assert len(session.calls) == 2


def test_prices_batches_and_retries():
    sleeps = []
    session = Session(requests.ConnectionError(), Resp(429, headers={"Retry-After": "7"}),
                      Resp(200, {"bitcoin": {"usd": 85000}, "solana": {"usd": 0}}))
    m = Market(session=session, sleep=sleeps.append)
    assert m.prices(["bitcoin", "solana", "bitcoin"]) == {"bitcoin": 85000.0}
    assert sleeps == [3.0, 7]


def test_prices_chunks_many_coins():
    session = Session(Resp(200, {}), Resp(200, {}), Resp(200, {}))
    Market(session=session).prices([f"coin{i}" for i in range(450)], chunk=200)
    assert len(session.calls) == 3


def test_api_key_header():
    session = Session(Resp(200, {}))
    Market(api_key="demo-key", session=session).prices(["bitcoin"])
    assert session.calls[0][2]["x-cg-demo-api-key"] == "demo-key"


def test_movers():
    m = Market(session=Session(Resp(200, MARKETS)))
    gainers, losers = m.movers(1)
    assert gainers[0].id == "fake-btc" and losers[0].id == "solana"
