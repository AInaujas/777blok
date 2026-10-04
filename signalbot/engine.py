"""Decide which alerts fire. Pure logic: prices and time are passed in.

Level alerts (above/below) fire when the price *crosses* the level, then
re-arm only after the price moves back by REARM_GAP. That gives one clean
message per breakout instead of a message every few minutes.

Move alerts (change) fire when the price moved threshold % within the
window, at most once per window.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .db import Alert

REARM_GAP = 0.005  # 0.5%


@dataclass
class Fired:
    alert: Alert
    price: float
    change: Optional[float] = None  # for move alerts


def select_checkable(rows: Sequence[Tuple[Alert, bool]], free_max: int, pro_max: int,
                     include_free: bool) -> List[Alert]:
    """Which alerts to evaluate this round.

    Pro users: every alert up to pro_max. Free users: only on free rounds,
    only their oldest free_max alerts, and no move alerts (a Pro feature).
    Extra alerts left over from an expired Pro plan stay saved but paused.
    """
    out, seen = [], {}
    for alert, is_pro in rows:
        n = seen.get(alert.user_id, 0)
        if is_pro:
            if n < pro_max:
                out.append(alert)
        elif include_free and n < free_max and alert.kind != "change":
            out.append(alert)
        seen[alert.user_id] = n + 1
    return out


def evaluate(alerts: Sequence[Alert], prices: Dict[str, float], now: float,
             price_at: Callable[[str, float], Optional[float]]) -> Tuple[List[Fired], Dict[int, dict]]:
    """Return (alerts that fire now, state changes {alert_id: {armed, last_fired}})."""
    fired: List[Fired] = []
    updates: Dict[int, dict] = {}
    for a in alerts:
        price = prices.get(a.coin_id)
        if price is None:
            continue
        if a.kind in ("above", "below"):
            crossed = price >= a.threshold if a.kind == "above" else price <= a.threshold
            back = (price < a.threshold * (1 - REARM_GAP) if a.kind == "above"
                    else price > a.threshold * (1 + REARM_GAP))
            if a.armed and crossed:
                fired.append(Fired(a, price))
                updates[a.id] = {"armed": False, "last_fired": now}
            elif not a.armed and back:
                updates[a.id] = {"armed": True, "last_fired": a.last_fired}
        elif a.kind == "change":
            window = a.window_min * 60
            if a.last_fired and now - a.last_fired < window:
                continue
            ref = price_at(a.coin_id, now - window)
            if not ref:
                continue
            change = (price - ref) / ref * 100
            if abs(change) >= a.threshold:
                fired.append(Fired(a, price, change))
                updates[a.id] = {"armed": a.armed, "last_fired": now}
    return fired, updates


def initial_kind(price: Optional[float], threshold: float) -> str:
    """For '/alert btc 90000' with no direction: alert on the side the price must move to."""
    if price is None:
        return "above"
    return "above" if threshold > price else "below"
