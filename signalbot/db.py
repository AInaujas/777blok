"""SQLite storage: users, alerts, payments and a short price history.

One file, no server to run. Comfortable for tens of thousands of users;
every call here is a quick indexed query.
"""

import functools
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

DAY = 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY,          -- Telegram user id
    username     TEXT DEFAULT '',
    first_name   TEXT DEFAULT '',
    created_at   REAL NOT NULL,
    last_seen    REAL NOT NULL,
    pro_until    REAL NOT NULL DEFAULT 0,
    trial_used   INTEGER NOT NULL DEFAULT 0,
    digest       INTEGER NOT NULL DEFAULT 1,
    blocked      INTEGER NOT NULL DEFAULT 0,   -- user blocked the bot
    referred_by  INTEGER
);
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    coin_id     TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('above', 'below', 'change')),
    threshold   REAL NOT NULL,
    window_min  INTEGER NOT NULL DEFAULT 60,
    armed       INTEGER NOT NULL DEFAULT 1,
    last_fired  REAL,
    created_at  REAL NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS alerts_user ON alerts(user_id, active);
CREATE TABLE IF NOT EXISTS payments (
    charge_id    TEXT PRIMARY KEY,             -- telegram_payment_charge_id
    user_id      INTEGER NOT NULL,
    amount       INTEGER NOT NULL,             -- Stars
    payload      TEXT NOT NULL,
    is_recurring INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    refunded     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS prices (
    coin_id TEXT NOT NULL,
    ts      REAL NOT NULL,
    price   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS prices_coin_ts ON prices(coin_id, ts);
"""


@dataclass
class User:
    id: int
    username: str
    first_name: str
    created_at: float
    pro_until: float
    trial_used: bool
    digest: bool
    blocked: bool
    referred_by: Optional[int]

    def is_pro(self, now: Optional[float] = None) -> bool:
        return self.pro_until > (now if now is not None else time.time())


@dataclass
class Alert:
    id: int
    user_id: int
    coin_id: str
    symbol: str
    kind: str
    threshold: float
    window_min: int
    armed: bool
    last_fired: Optional[float]
    created_at: float


def _serialized(cls):
    """Run every public method under one lock: alert checks and user commands
    use the database from different threads."""
    for name, func in list(vars(cls).items()):
        if callable(func) and not name.startswith("_"):
            def wrap(f):
                @functools.wraps(f)
                def locked(self, *args, **kwargs):
                    with self._lock:
                        return f(self, *args, **kwargs)
                return locked
            setattr(cls, name, wrap(func))
    return cls


@_serialized
class Database:
    def __init__(self, path: str):
        self._lock = threading.RLock()
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- users ---------------------------------------------------------------

    def upsert_user(self, user_id: int, username: str = "", first_name: str = "",
                    referred_by: Optional[int] = None, now: Optional[float] = None) -> bool:
        """Create or refresh a user. Returns True if the user is new."""
        now = now or time.time()
        with self.conn:
            cur = self.conn.execute(
                "UPDATE users SET username=?, first_name=?, last_seen=?, blocked=0 WHERE id=?",
                (username or "", first_name or "", now, user_id),
            )
            if cur.rowcount:
                return False
            if referred_by == user_id:
                referred_by = None
            self.conn.execute(
                "INSERT INTO users (id, username, first_name, created_at, last_seen, referred_by) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username or "", first_name or "", now, now, referred_by),
            )
            return True

    def get_user(self, user_id: int) -> Optional[User]:
        row = self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return _user(row) if row else None

    def extend_pro(self, user_id: int, days: float = 0, until: Optional[float] = None,
                   now: Optional[float] = None) -> float:
        """Add days of Pro (stacking on any time left), or set an exact end. Returns the new end."""
        now = now or time.time()
        user = self.get_user(user_id)
        current = max(user.pro_until if user else 0, now)
        new_until = max(until or 0, current + days * DAY if days else current)
        with self.conn:
            self.conn.execute("UPDATE users SET pro_until=? WHERE id=?", (new_until, user_id))
        return new_until

    def start_trial(self, user_id: int, days: int, now: Optional[float] = None) -> bool:
        user = self.get_user(user_id)
        if not user or user.trial_used or days <= 0:
            return False
        with self.conn:
            self.conn.execute("UPDATE users SET trial_used=1 WHERE id=?", (user_id,))
        self.extend_pro(user_id, days=days, now=now)
        return True

    def set_digest(self, user_id: int, on: bool) -> None:
        with self.conn:
            self.conn.execute("UPDATE users SET digest=? WHERE id=?", (int(on), user_id))

    def mark_blocked(self, user_id: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE users SET blocked=1 WHERE id=?", (user_id,))

    def reachable_user_ids(self) -> List[int]:
        return [r[0] for r in self.conn.execute("SELECT id FROM users WHERE blocked=0 ORDER BY id")]

    def pro_ended_between(self, start: float, end: float) -> List[int]:
        rows = self.conn.execute(
            "SELECT id FROM users WHERE blocked=0 AND pro_until>? AND pro_until<=?", (start, end)
        ).fetchall()
        return [r[0] for r in rows]

    def digest_users(self, now: Optional[float] = None) -> List[User]:
        now = now or time.time()
        rows = self.conn.execute(
            "SELECT * FROM users WHERE blocked=0 AND digest=1 AND pro_until>?", (now,)
        ).fetchall()
        return [_user(r) for r in rows]

    # --- alerts --------------------------------------------------------------

    def add_alert(self, user_id: int, coin_id: str, symbol: str, kind: str, threshold: float,
                  window_min: int = 60, now: Optional[float] = None) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO alerts (user_id, coin_id, symbol, kind, threshold, window_min, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, coin_id, symbol.upper(), kind, threshold, window_min, now or time.time()),
            )
            return cur.lastrowid

    def user_alerts(self, user_id: int) -> List[Alert]:
        rows = self.conn.execute(
            "SELECT * FROM alerts WHERE user_id=? AND active=1 ORDER BY id", (user_id,)
        ).fetchall()
        return [_alert(r) for r in rows]

    def count_alerts(self, user_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE user_id=? AND active=1", (user_id,)
        ).fetchone()[0]

    def delete_alert(self, user_id: int, alert_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE alerts SET active=0 WHERE id=? AND user_id=? AND active=1", (alert_id, user_id)
            )
            return cur.rowcount > 0

    def delete_all_alerts(self, user_id: int) -> int:
        with self.conn:
            return self.conn.execute(
                "UPDATE alerts SET active=0 WHERE user_id=? AND active=1", (user_id,)
            ).rowcount

    def live_alerts(self, now: Optional[float] = None) -> List[tuple]:
        """Every active alert of a reachable user, as (Alert, is_pro), oldest first."""
        now = now or time.time()
        rows = self.conn.execute(
            "SELECT a.*, u.pro_until > ? AS is_pro FROM alerts a JOIN users u ON u.id = a.user_id "
            "WHERE a.active=1 AND u.blocked=0 ORDER BY a.user_id, a.id",
            (now,),
        ).fetchall()
        return [(_alert(r), bool(r["is_pro"])) for r in rows]

    def update_alert_state(self, updates: Dict[int, dict]) -> None:
        with self.conn:
            for alert_id, fields in updates.items():
                self.conn.execute(
                    "UPDATE alerts SET armed=?, last_fired=? WHERE id=?",
                    (int(fields["armed"]), fields["last_fired"], alert_id),
                )

    # --- prices --------------------------------------------------------------

    def add_prices(self, prices: Dict[str, float], now: Optional[float] = None,
                   keep_seconds: float = 25 * 3600) -> None:
        now = now or time.time()
        with self.conn:
            self.conn.executemany(
                "INSERT INTO prices (coin_id, ts, price) VALUES (?, ?, ?)",
                [(coin, now, price) for coin, price in prices.items()],
            )
            self.conn.execute("DELETE FROM prices WHERE ts < ?", (now - keep_seconds,))

    def price_at_or_before(self, coin_id: str, ts: float) -> Optional[float]:
        """The newest stored price at or before ts, if it is no more than 15 minutes older."""
        row = self.conn.execute(
            "SELECT price FROM prices WHERE coin_id=? AND ts<=? AND ts>=? ORDER BY ts DESC LIMIT 1",
            (coin_id, ts, ts - 900),
        ).fetchone()
        return row[0] if row else None

    # --- payments ------------------------------------------------------------

    def record_payment(self, charge_id: str, user_id: int, amount: int, payload: str,
                       is_recurring: bool, now: Optional[float] = None) -> bool:
        """Store a payment. Returns False if this charge was already recorded."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO payments (charge_id, user_id, amount, payload, is_recurring, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (charge_id, user_id, amount, payload, int(is_recurring), now or time.time()),
            )
            return cur.rowcount > 0

    def payment_count(self, user_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM payments WHERE user_id=? AND refunded=0", (user_id,)
        ).fetchone()[0]

    def mark_refunded(self, charge_id: str) -> Optional[int]:
        """Mark a payment refunded and return its user id (None if unknown)."""
        row = self.conn.execute("SELECT user_id FROM payments WHERE charge_id=?", (charge_id,)).fetchone()
        if not row:
            return None
        with self.conn:
            self.conn.execute("UPDATE payments SET refunded=1 WHERE charge_id=?", (charge_id,))
            self.conn.execute("UPDATE users SET pro_until=? WHERE id=?", (time.time(), row[0]))
        return row[0]

    # --- admin ---------------------------------------------------------------

    def backup_to(self, path: str) -> str:
        """Write a consistent copy of the database (safe while the bot runs)."""
        target = sqlite3.connect(path)
        with target:
            self.conn.backup(target)
        target.close()
        return path

    def stats(self, now: Optional[float] = None) -> dict:
        now = now or time.time()
        q = lambda sql, *a: self.conn.execute(sql, a).fetchone()[0]  # noqa: E731
        return {
            "users": q("SELECT COUNT(*) FROM users"),
            "reachable": q("SELECT COUNT(*) FROM users WHERE blocked=0"),
            "new_7d": q("SELECT COUNT(*) FROM users WHERE created_at>?", now - 7 * DAY),
            "active_7d": q("SELECT COUNT(*) FROM users WHERE last_seen>?", now - 7 * DAY),
            "pro": q("SELECT COUNT(*) FROM users WHERE pro_until>?", now),
            "paying_30d": q("SELECT COUNT(DISTINCT user_id) FROM payments WHERE refunded=0 AND created_at>?",
                            now - 30 * DAY),
            "stars_30d": q("SELECT COALESCE(SUM(amount),0) FROM payments WHERE refunded=0 AND created_at>?",
                           now - 30 * DAY),
            "stars_total": q("SELECT COALESCE(SUM(amount),0) FROM payments WHERE refunded=0"),
            "alerts": q("SELECT COUNT(*) FROM alerts WHERE active=1"),
        }


def _user(r) -> User:
    return User(r["id"], r["username"], r["first_name"], r["created_at"], r["pro_until"],
                bool(r["trial_used"]), bool(r["digest"]), bool(r["blocked"]), r["referred_by"])


def _alert(r) -> Alert:
    return Alert(r["id"], r["user_id"], r["coin_id"], r["symbol"], r["kind"], r["threshold"],
                 r["window_min"], bool(r["armed"]), r["last_fired"], r["created_at"])
