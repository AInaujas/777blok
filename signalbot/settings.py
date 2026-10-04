"""Settings, read from environment variables (or a .env file)."""

import os
from dataclasses import dataclass, field
from typing import FrozenSet


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: FrozenSet[int] = field(default_factory=frozenset)
    db_path: str = "data/signalbot.db"
    coingecko_api_key: str = ""
    support_contact: str = ""

    # Plans
    pro_price_stars: int = 250        # per 30 days
    free_max_alerts: int = 3
    pro_max_alerts: int = 50
    referral_bonus_days: int = 30     # given to the referrer when a referred user first pays
    trial_days: int = 3               # free Pro trial for new users (0 = off)

    # Timing
    pro_check_seconds: int = 60
    free_check_seconds: int = 300
    digest_hour_utc: int = 8

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise SystemExit("BOT_TOKEN is not set. Put it in .env (see .env.example).")
        admins = frozenset(int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x)
        return cls(
            bot_token=token,
            admin_ids=admins,
            db_path=os.getenv("DB_PATH", "data/signalbot.db"),
            coingecko_api_key=os.getenv("COINGECKO_API_KEY", "").strip(),
            support_contact=os.getenv("SUPPORT_CONTACT", "").strip(),
            pro_price_stars=_int("PRO_PRICE_STARS", 250),
            free_max_alerts=_int("FREE_MAX_ALERTS", 3),
            pro_max_alerts=_int("PRO_MAX_ALERTS", 50),
            referral_bonus_days=_int("REFERRAL_BONUS_DAYS", 30),
            trial_days=_int("TRIAL_DAYS", 3),
            pro_check_seconds=max(30, _int("PRO_CHECK_SECONDS", 60)),
            free_check_seconds=max(60, _int("FREE_CHECK_SECONDS", 300)),
            digest_hour_utc=_int("DIGEST_HOUR_UTC", 8) % 24,
        )
