"""CoinGecko market data: batched prices, coin lookup and top movers.

Every user shares the same price requests: one call per check covers all
coins anybody is watching, so the free API limits are not a problem.
A free CoinGecko "Demo" API key (COINGECKO_API_KEY) raises the limits.
"""

import logging
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional

import requests

log = logging.getLogger(__name__)
API = "https://api.coingecko.com/api/v3"


@dataclass
class Coin:
    id: str
    symbol: str
    name: str
    price: Optional[float] = None
    change_1h: Optional[float] = None
    change_24h: Optional[float] = None
    rank: Optional[int] = None


class Market:
    def __init__(self, api_key: str = "", session=None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time):
        self.http = session or requests.Session()
        self.headers = {"accept": "application/json"}
        if api_key:
            self.headers["x-cg-demo-api-key"] = api_key
        self.sleep = sleep
        self.clock = clock
        self._top: List[Coin] = []
        self._top_at = 0.0
        self._search_cache: Dict[str, Optional[Coin]] = {}

    # --- HTTP ----------------------------------------------------------------

    def _get(self, path: str, params: dict, attempts: int = 4, base_delay: float = 3.0):
        """GET with retries on network errors, 429 and 5xx. Returns parsed JSON or None."""
        for attempt in range(1, attempts + 1):
            delay = base_delay * 2 ** (attempt - 1)
            try:
                resp = self.http.get(f"{API}{path}", params=params, headers=self.headers, timeout=20)
            except requests.RequestException as exc:
                log.warning("CoinGecko %s failed (%s), attempt %d/%d", path, type(exc).__name__, attempt, attempts)
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError:
                        log.warning("CoinGecko %s sent invalid JSON", path)
                elif resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After", "")
                    if retry_after.isdigit():
                        delay = max(delay, min(int(retry_after), 120))
                    log.warning("CoinGecko rate limit on %s, attempt %d/%d", path, attempt, attempts)
                elif resp.status_code >= 500:
                    log.warning("CoinGecko %s server error %d", path, resp.status_code)
                else:
                    log.error("CoinGecko %s returned HTTP %d", path, resp.status_code)
                    return None
            if attempt < attempts:
                self.sleep(delay)
        return None

    # --- prices --------------------------------------------------------------

    def prices(self, coin_ids: Iterable[str], chunk: int = 200) -> Dict[str, float]:
        """USD prices for many coins, in as few requests as possible."""
        ids = sorted(set(coin_ids))
        out: Dict[str, float] = {}
        for i in range(0, len(ids), chunk):
            part = ids[i:i + chunk]
            data = self._get("/simple/price", {"ids": ",".join(part), "vs_currencies": "usd"})
            if not isinstance(data, dict):
                continue
            for coin in part:
                value = (data.get(coin) or {}).get("usd")
                if isinstance(value, (int, float)) and value > 0:
                    out[coin] = float(value)
        return out

    # --- top coins (lookup, movers, digest) ----------------------------------

    def top_coins(self, max_age: float = 120) -> List[Coin]:
        """Top 250 coins by market cap with 1h/24h change, cached for max_age seconds."""
        if self._top and self.clock() - self._top_at < max_age:
            return self._top
        data = self._get("/coins/markets", {
            "vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": 1,
            "price_change_percentage": "1h,24h",
        })
        if isinstance(data, list) and data:
            self._top = [
                Coin(
                    id=c["id"], symbol=str(c.get("symbol", "")).upper(), name=c.get("name", c["id"]),
                    price=c.get("current_price"),
                    change_1h=c.get("price_change_percentage_1h_in_currency"),
                    change_24h=c.get("price_change_percentage_24h_in_currency", c.get("price_change_percentage_24h")),
                    rank=c.get("market_cap_rank"),
                )
                for c in data if isinstance(c, dict) and c.get("id")
            ]
            self._top_at = self.clock()
        return self._top

    def find(self, query: str) -> Optional[Coin]:
        """Turn what a user typed (btc, Bitcoin, solana, pepe...) into a coin."""
        q = query.strip().lower().lstrip("$")
        if not q:
            return None
        top = self.top_coins()
        for match in (
            lambda c: c.id == q,
            lambda c: c.symbol.lower() == q,  # top list is sorted by market cap, so BTC wins over copycats
            lambda c: c.name.lower() == q,
        ):
            for coin in top:
                if match(coin):
                    return coin
        if q in self._search_cache:
            return self._search_cache[q]
        data = self._get("/search", {"query": q}, attempts=2)
        found = None
        for c in (data or {}).get("coins", []) if isinstance(data, dict) else []:
            if q in (str(c.get("id", "")).lower(), str(c.get("symbol", "")).lower(), str(c.get("name", "")).lower()):
                found = Coin(id=c["id"], symbol=str(c.get("symbol", "")).upper(), name=c.get("name", c["id"]),
                             rank=c.get("market_cap_rank"))
                break
        if len(self._search_cache) > 5000:
            self._search_cache.clear()
        self._search_cache[q] = found
        return found

    def movers(self, count: int = 5, min_rank: int = 250):
        """(gainers, losers) over 24h among the top coins."""
        coins = [c for c in self.top_coins() if c.change_24h is not None and (c.rank or 9999) <= min_rank]
        ranked = sorted(coins, key=lambda c: c.change_24h, reverse=True)
        return ranked[:count], list(reversed(ranked[-count:]))
