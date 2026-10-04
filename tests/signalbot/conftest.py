import pytest

from signalbot.db import Database
from signalbot.market import Coin
from signalbot.service import Service
from signalbot.settings import Settings


class FakeMarket:
    """Stands in for CoinGecko. Set .table = {coin_id: price} to move the market."""

    def __init__(self):
        self.table = {"bitcoin": 85000.0, "ethereum": 2700.0, "solana": 120.0, "dogecoin": 0.15}
        self.meta = {
            "bitcoin": ("BTC", "Bitcoin", 1, 0.4, 2.1),
            "ethereum": ("ETH", "Ethereum", 2, -0.2, -1.5),
            "solana": ("SOL", "Solana", 5, 1.1, 6.3),
            "dogecoin": ("DOGE", "Dogecoin", 9, -0.5, -4.0),
        }
        self.price_calls = 0

    def top_coins(self, max_age=600):
        return [Coin(cid, s, n, self.table.get(cid), c1, c24, r) for cid, (s, n, r, c1, c24) in self.meta.items()]

    def find(self, query):
        q = query.lower().lstrip("$")
        return next((c for c in self.top_coins() if q in (c.id, c.symbol.lower(), c.name.lower())), None)

    def prices(self, ids, chunk=200):
        self.price_calls += 1
        return {i: self.table[i] for i in ids if i in self.table}

    def movers(self, count=5, min_rank=250):
        ranked = sorted(self.top_coins(), key=lambda c: c.change_24h, reverse=True)
        return ranked[:count], list(reversed(ranked[-count:]))


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def market():
    return FakeMarket()


@pytest.fixture
def settings():
    return Settings(bot_token="x", admin_ids=frozenset({999}), trial_days=0, support_contact="@owner")


@pytest.fixture
def service(market, settings, clock):
    return Service(Database(":memory:"), market, settings, clock=clock)
