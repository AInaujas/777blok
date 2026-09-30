"""Crypto price ALERT bot.

Reads prices from CoinGecko and sends alerts. It only reads public prices:
it never places trades and never touches wallets or exchange API keys.

Usage:
    python bot.py                   run forever
    python bot.py --once            check prices once and exit
    python bot.py --test-telegram   send a test message and exit
    python bot.py --once --state state.json
                                    one check that remembers cooldowns, price
                                    history and the chat id between runs
                                    (this is how the GitHub Actions setup runs)
"""

import argparse
import json
import logging
import os
import sys
import time

from dotenv import load_dotenv

from alerts import AlertEngine, ConfigError, load_config
from coingecko import fetch_prices
from notifier import Notifier

log = logging.getLogger("bot")


def load_state(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        log.warning("Could not read %s, starting fresh", path)
        return {}


def save_state(path: str, data: dict) -> None:
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        log.warning("Could not save %s", path)


def connect_telegram(notifier: Notifier, state: dict) -> None:
    """Fill in the chat id from state, or find it from the latest message to the bot."""
    if not notifier.bot_token or notifier.chat_id:
        return
    notifier.chat_id = str(state.get("chat_id") or "")
    if notifier.chat_id:
        return
    notifier.chat_id = notifier.find_chat_id()
    if notifier.chat_id:
        state["chat_id"] = notifier.chat_id
        log.info("Found your Telegram chat automatically")
        notifier.send("✅ Crypto alert bot connected! You'll get your price alerts here.")
    else:
        log.warning("Telegram token is set but no chat found yet: open your bot in Telegram and send it any message")


def main() -> int:
    parser = argparse.ArgumentParser(description="Crypto price alert bot (alerts only, no trading)")
    parser.add_argument("--config", default="config.yaml", help="path to the alerts config (default: config.yaml)")
    parser.add_argument("--once", action="store_true", help="check prices once and exit")
    parser.add_argument("--state", default="", help="JSON file to remember cooldowns, price history and chat id between runs")
    parser.add_argument("--test-telegram", action="store_true", help="send a test Telegram message and exit")
    args = parser.parse_args()

    # Emoji-safe printing on older Windows consoles.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    load_dotenv()
    notifier = Notifier(os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", ""))
    state = load_state(args.state)
    connect_telegram(notifier, state)

    if args.test_telegram:
        if not notifier.telegram_enabled:
            log.error("Set TELEGRAM_BOT_TOKEN in .env and send your bot a message in Telegram first")
            return 1
        ok = notifier.send("✅ Test message from your crypto alert bot")
        log.info("Telegram test %s", "succeeded" if ok else "FAILED (see error above)")
        return 0 if ok else 1

    try:
        config = load_config(args.config)
    except FileNotFoundError:
        log.error("Config file not found: %s", args.config)
        return 1
    except ConfigError as exc:
        log.error("Problem in %s: %s", args.config, exc)
        return 1

    if not notifier.telegram_enabled:
        log.info("Telegram not configured - alerts will only be printed here")

    engine = AlertEngine(config.alerts, config.vs_currency)
    engine.load_dict(state.get("engine") or {})
    coins = [a.coin for a in config.alerts]
    log.info("Watching %s with %d alert(s), checking every %gs. Press Ctrl+C to stop.",
             ", ".join(sorted(set(coins))), len(config.alerts), config.poll_seconds)

    try:
        while True:
            started = time.time()
            try:
                prices = fetch_prices(coins, config.vs_currency)
                if prices:
                    log.info("Prices: %s", ", ".join(f"{c}={p:g}" for c, p in sorted(prices.items())))
                    for message in engine.check(prices, time.time()):
                        notifier.send(message)
            except Exception:  # keep the bot alive no matter what one poll does
                log.exception("Unexpected error during this poll; continuing")
            state["engine"] = engine.to_dict()
            save_state(args.state, state)
            if args.once:
                return 0
            time.sleep(max(0, config.poll_seconds - (time.time() - started)))
    except KeyboardInterrupt:
        log.info("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
