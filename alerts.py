"""Alert rules and the logic that decides when an alert fires.

This module is pure logic: no network, no clock. The caller passes in prices
and the current time, which keeps it easy to test.
"""

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Optional

import yaml

VALID_CONDITIONS = ("above", "below", "change")
VALID_DIRECTIONS = ("up", "down", "any")


class ConfigError(ValueError):
    """Raised when config.yaml is missing something or has a bad value."""


@dataclass
class Alert:
    coin: str
    condition: str  # "above", "below" or "change"
    threshold: float  # a price for above/below, a percent for change
    name: str = ""
    window_minutes: float = 60  # only used by "change"
    direction: str = "any"  # only used by "change": up, down or any
    cooldown_minutes: float = 30

    def __post_init__(self):
        if not self.name:
            if self.condition == "change":
                self.name = f"{self.coin} {self.direction} {self.threshold}% in {self.window_minutes:g}m"
            else:
                self.name = f"{self.coin} {self.condition} {self.threshold:g}"

    @property
    def key(self) -> str:
        """Identifies this alert in the saved state, so cooldowns survive restarts."""
        return f"{self.coin}|{self.condition}|{self.threshold:g}|{self.direction}|{self.window_minutes:g}"


@dataclass
class Config:
    poll_seconds: float
    vs_currency: str
    alerts: List[Alert]


def parse_config(data: dict) -> Config:
    """Turn the dict loaded from config.yaml into a validated Config."""
    if not isinstance(data, dict):
        raise ConfigError("config.yaml is empty or not a mapping")

    poll_seconds = float(data.get("poll_seconds", 60))
    if poll_seconds < 10:
        raise ConfigError("poll_seconds must be at least 10 (CoinGecko's free API is rate limited)")

    vs_currency = str(data.get("vs_currency", "usd")).lower()
    default_cooldown = float(data.get("cooldown_minutes", 30))

    raw_alerts = data.get("alerts") or []
    if not isinstance(raw_alerts, list) or not raw_alerts:
        raise ConfigError("config.yaml needs at least one entry under 'alerts'")

    alerts = []
    for i, raw in enumerate(raw_alerts, start=1):
        where = f"alert #{i}"
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: must be a mapping with coin/condition/threshold")
        for key in ("coin", "condition", "threshold"):
            if key not in raw:
                raise ConfigError(f"{where}: missing '{key}'")

        condition = str(raw["condition"]).lower()
        if condition not in VALID_CONDITIONS:
            raise ConfigError(f"{where}: condition must be one of {', '.join(VALID_CONDITIONS)}")

        direction = str(raw.get("direction", "any")).lower()
        if direction not in VALID_DIRECTIONS:
            raise ConfigError(f"{where}: direction must be one of {', '.join(VALID_DIRECTIONS)}")

        try:
            threshold = float(raw["threshold"])
            window = float(raw.get("window_minutes", 60))
            cooldown = float(raw.get("cooldown_minutes", default_cooldown))
        except (TypeError, ValueError):
            raise ConfigError(f"{where}: threshold, window_minutes and cooldown_minutes must be numbers")

        if condition == "change" and threshold <= 0:
            raise ConfigError(f"{where}: change threshold must be a positive percent (use 'direction' for down moves)")
        if window <= 0 or cooldown < 0:
            raise ConfigError(f"{where}: window_minutes must be > 0 and cooldown_minutes >= 0")

        alerts.append(
            Alert(
                coin=str(raw["coin"]).strip().lower(),
                condition=condition,
                threshold=threshold,
                name=str(raw.get("name", "")),
                window_minutes=window,
                direction=direction,
                cooldown_minutes=cooldown,
            )
        )

    return Config(poll_seconds=poll_seconds, vs_currency=vs_currency, alerts=alerts)


def load_config(path: str) -> Config:
    with open(path, encoding="utf-8") as f:
        return parse_config(yaml.safe_load(f))


class AlertEngine:
    """Keeps a short price history and decides which alerts to send."""

    def __init__(self, alerts: List[Alert], vs_currency: str = "usd"):
        self.alerts = alerts
        self.vs_currency = vs_currency
        self._history: Dict[str, deque] = defaultdict(deque)  # coin -> (time, price)
        self._last_fired: Dict[str, float] = {}  # alert key -> time it last fired
        longest = max([a.window_minutes for a in alerts if a.condition == "change"], default=0)
        self._keep_seconds = longest * 60

    def record(self, prices: Dict[str, float], now: float) -> None:
        """Store the latest prices so percent-change alerts can look back."""
        for coin, price in prices.items():
            history = self._history[coin]
            history.append((now, price))
            # Drop old samples, but keep the newest one that is older than the
            # longest window: it's the reference point for that window.
            cutoff = now - self._keep_seconds
            while len(history) >= 2 and history[1][0] <= cutoff:
                history.popleft()

    def percent_change(self, coin: str, window_minutes: float, now: float) -> Optional[float]:
        """Percent change from the price ~window_minutes ago to the latest price.

        Returns None until the bot has been running long enough to have a
        price from at least window_minutes ago.
        """
        history = self._history.get(coin)
        if not history:
            return None
        cutoff = now - window_minutes * 60
        reference = None
        for ts, price in history:
            if ts <= cutoff:
                reference = price
            else:
                break
        if reference is None or reference == 0:
            return None
        latest = history[-1][1]
        return (latest - reference) / reference * 100

    def check(self, prices: Dict[str, float], now: float) -> List[str]:
        """Record prices and return a message for every alert that should fire now."""
        self.record(prices, now)
        messages = []
        for alert in self.alerts:
            price = prices.get(alert.coin)
            if price is None:
                continue
            message = self._evaluate(alert, price, now)
            if message is None:
                continue
            last = self._last_fired.get(alert.key)
            if last is not None and now - last < alert.cooldown_minutes * 60:
                continue  # still cooling down, don't spam
            self._last_fired[alert.key] = now
            messages.append(message)
        return messages

    def to_dict(self) -> dict:
        """Everything the engine remembers, as plain JSON-friendly data."""
        return {
            "history": {coin: [list(sample) for sample in samples] for coin, samples in self._history.items()},
            "last_fired": dict(self._last_fired),
        }

    def load_dict(self, data: dict) -> None:
        """Restore what to_dict() saved. Bad or missing data is simply ignored."""
        try:
            for coin, samples in (data.get("history") or {}).items():
                self._history[coin] = deque((float(ts), float(price)) for ts, price in samples)
            self._last_fired.update({str(k): float(v) for k, v in (data.get("last_fired") or {}).items()})
        except (AttributeError, TypeError, ValueError):
            self._history.clear()
            self._last_fired.clear()

    def _evaluate(self, alert: Alert, price: float, now: float) -> Optional[str]:
        cur = self.vs_currency.upper()
        if alert.condition == "above" and price > alert.threshold:
            return f"🚀 {alert.name}: {alert.coin} is {fmt(price)} {cur} (above {fmt(alert.threshold)})"
        if alert.condition == "below" and price < alert.threshold:
            return f"🔻 {alert.name}: {alert.coin} is {fmt(price)} {cur} (below {fmt(alert.threshold)})"
        if alert.condition == "change":
            change = self.percent_change(alert.coin, alert.window_minutes, now)
            if change is None:
                return None
            hit = (
                (alert.direction in ("up", "any") and change >= alert.threshold)
                or (alert.direction in ("down", "any") and change <= -alert.threshold)
            )
            if hit:
                icon = "📈" if change >= 0 else "📉"
                return (
                    f"{icon} {alert.name}: {alert.coin} moved {change:+.2f}% in the last "
                    f"{alert.window_minutes:g} min, now {fmt(price)} {cur}"
                )
        return None


def fmt(value: float) -> str:
    """Readable price: commas for big numbers, more decimals for tiny ones."""
    if abs(value) >= 1:
        return f"{value:,.2f}"
    return f"{value:.8f}".rstrip("0").rstrip(".")
