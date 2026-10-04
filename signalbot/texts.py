"""Parsing what users type, and formatting what the bot sends."""

import html
import re
from dataclasses import dataclass
from typing import List, Optional

WINDOWS = {"15m": 15, "30m": 30, "1h": 60, "4h": 240, "12h": 720, "24h": 1440, "1d": 1440}
DISCLAIMER = "Price alerts only — not financial advice."


class ParseError(ValueError):
    """The user's input couldn't be understood. The message is shown to them."""


@dataclass
class AlertRequest:
    coin: str
    kind: Optional[str]     # above / below / change, or None = decide from current price
    threshold: float
    window_min: int = 60


def parse_number(text: str) -> float:
    t = text.strip().lower().replace(",", "").replace("$", "").replace("€", "")
    mult = 1.0
    if t.endswith("k"):
        mult, t = 1_000, t[:-1]
    elif t.endswith("m"):
        mult, t = 1_000_000, t[:-1]
    try:
        value = float(t) * mult
    except ValueError:
        raise ParseError(f"“{text}” isn't a number")
    if value <= 0:
        raise ParseError("The price must be above 0")
    return value


def parse_alert(args: List[str]) -> AlertRequest:
    """Understand /alert arguments.

    btc 90000 | btc >90k | btc above 90000 | eth < 2500 | sol 5% | sol 5% 4h
    """
    if len(args) < 2:
        raise ParseError(ALERT_USAGE)
    coin, rest = args[0], " ".join(args[1:]).strip().lower()

    move = re.fullmatch(r"[±+-]?\s*(\d+(?:\.\d+)?)\s*%(?:\s+(?:in\s+)?(\w+))?", rest)
    if move:
        pct = float(move.group(1))
        if not 0.5 <= pct <= 50:
            raise ParseError("Move alerts need a percent between 0.5% and 50%")
        window_key = move.group(2) or "1h"
        if window_key not in WINDOWS:
            raise ParseError("Window must be one of: " + ", ".join(k for k in WINDOWS if k != "1d"))
        return AlertRequest(coin, "change", pct, WINDOWS[window_key])

    level = re.fullmatch(r"(>=?|<=?|above|over|below|under)?\s*([\d.,]+[km]?|\$[\d.,]+[km]?)", rest)
    if not level:
        raise ParseError(ALERT_USAGE)
    word = level.group(1) or ""
    kind = "above" if word in (">", ">=", "above", "over") else "below" if word else None
    return AlertRequest(coin, kind, parse_number(level.group(2)))


ALERT_USAGE = (
    "How to set an alert:\n"
    "<code>/alert btc 90000</code> — when BTC reaches 90,000\n"
    "<code>/alert eth &lt;2500</code> — when ETH drops below 2,500\n"
    "<code>/alert sol 5%</code> — when SOL moves 5% within 1h ⭐\n"
    "<code>/alert sol 8% 4h</code> — 8% within 4 hours ⭐"
)


def esc(text) -> str:
    return html.escape(str(text), quote=False)


def money(value: Optional[float]) -> str:
    if value is None:
        return "?"
    if value >= 1000:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:,.2f}"
    if value >= 0.01:
        return f"${value:.4f}"
    return "$" + f"{value:.10f}".rstrip("0").rstrip(".")


def pct(value: Optional[float]) -> str:
    if value is None:
        return "–"
    arrow = "🟢" if value >= 0 else "🔴"
    return f"{arrow} {value:+.2f}%"


def window_label(minutes: int) -> str:
    return f"{minutes // 60}h" if minutes >= 60 and minutes % 60 == 0 else f"{minutes}m"


def describe_alert(kind: str, symbol: str, threshold: float, window_min: int = 60) -> str:
    if kind == "above":
        return f"{symbol} ≥ {money(threshold)}"
    if kind == "below":
        return f"{symbol} ≤ {money(threshold)}"
    return f"{symbol} moves ±{threshold:g}% in {window_label(window_min)}"


def fired_message(kind: str, symbol: str, threshold: float, price: float,
                  change: Optional[float] = None, window_min: int = 60) -> str:
    s = esc(symbol)
    if kind == "above":
        return f"🚀 <b>{s} broke {money(threshold)}</b>\nNow {money(price)}"
    if kind == "below":
        return f"🔻 <b>{s} fell below {money(threshold)}</b>\nNow {money(price)}"
    icon = "📈" if (change or 0) >= 0 else "📉"
    return (f"{icon} <b>{s} {change:+.2f}% in {window_label(window_min)}</b>\n"
            f"Now {money(price)}")
