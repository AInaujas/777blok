"""Deliver alert messages: always to the console, and to Telegram if configured."""

import logging
from datetime import datetime

import requests

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, bot_token: str = "", chat_id: str = "", session=None):
        self.bot_token = (bot_token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.http = session or requests

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, message: str) -> bool:
        """Print the alert and forward it to Telegram. Returns True if Telegram accepted it."""
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{stamp}] ALERT {message}", flush=True)
        if not self.telegram_enabled:
            return False
        return self._send_telegram(message)

    def find_chat_id(self) -> str:
        """Look up the chat of the most recent message sent to the bot.

        Lets beginners skip finding their chat id by hand: just message the
        bot once. Returns "" if there's no token or no recent message
        (Telegram only keeps messages from the last 24 hours here).
        """
        if not self.bot_token:
            return ""
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        try:
            resp = self.http.get(url, timeout=15)
            updates = resp.json().get("result", []) if resp.status_code == 200 else []
        except (requests.RequestException, ValueError, AttributeError):
            log.error("Could not ask Telegram for the chat id")
            return ""
        for update in reversed(updates):
            for field in ("message", "channel_post", "my_chat_member"):
                chat_id = ((update.get(field) or {}).get("chat") or {}).get("id")
                if chat_id is not None:
                    return str(chat_id)
        return ""

    def _send_telegram(self, message: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            resp = self.http.post(url, json={"chat_id": self.chat_id, "text": message}, timeout=15)
        except requests.RequestException as exc:
            # Don't log str(exc): it can contain the URL, which contains the token.
            log.error("Could not reach Telegram (%s); alert was still printed above", type(exc).__name__)
            return False
        if resp.status_code != 200:
            try:
                detail = resp.json().get("description", "")
            except ValueError:
                detail = ""
            log.error("Telegram rejected the message (HTTP %d) %s", resp.status_code, detail)
            return False
        return True
