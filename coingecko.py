"""Read-only price lookups from CoinGecko's free public API (no API key)."""

import logging
import time
from typing import Dict, Iterable, Optional

import requests

API_URL = "https://api.coingecko.com/api/v3/simple/price"
log = logging.getLogger(__name__)


def fetch_prices(
    coins: Iterable[str],
    vs_currency: str = "usd",
    max_attempts: int = 4,
    base_delay: float = 5.0,
    session=None,
    sleep=time.sleep,
) -> Optional[Dict[str, float]]:
    """Return {coin_id: price}, or None if every attempt failed.

    Retries network errors, rate limits (HTTP 429) and server errors (5xx)
    with exponential backoff: base_delay, 2x, 4x, ... Never raises for API
    problems, so the caller can simply try again on the next poll.
    """
    coins = sorted(set(coins))
    http = session or requests
    params = {"ids": ",".join(coins), "vs_currencies": vs_currency}

    for attempt in range(1, max_attempts + 1):
        delay = base_delay * 2 ** (attempt - 1)
        try:
            resp = http.get(API_URL, params=params, timeout=15)
        except requests.RequestException as exc:
            log.warning("CoinGecko request failed (%s), attempt %d/%d", type(exc).__name__, attempt, max_attempts)
        else:
            if resp.status_code == 200:
                try:
                    return _parse(resp.json(), coins, vs_currency)
                except ValueError:
                    log.warning("CoinGecko sent a response that isn't valid JSON, attempt %d/%d", attempt, max_attempts)
            elif resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    delay = max(delay, min(int(retry_after), 300))
                log.warning("CoinGecko rate limit hit (429), attempt %d/%d", attempt, max_attempts)
            elif resp.status_code >= 500:
                log.warning("CoinGecko server error %d, attempt %d/%d", resp.status_code, attempt, max_attempts)
            else:
                # 4xx other than 429 won't fix itself by retrying right away.
                log.error("CoinGecko returned HTTP %d: %s", resp.status_code, resp.text[:200])
                return None

        if attempt < max_attempts:
            log.info("Retrying in %.0f seconds...", delay)
            sleep(delay)

    log.error("Giving up on CoinGecko for this poll; will try again next poll")
    return None


def _parse(data, coins, vs_currency) -> Dict[str, float]:
    prices = {}
    for coin in coins:
        value = (data.get(coin) or {}).get(vs_currency) if isinstance(data, dict) else None
        if isinstance(value, (int, float)):
            prices[coin] = float(value)
        else:
            log.warning("No price for '%s' - check the coin id on coingecko.com", coin)
    return prices
