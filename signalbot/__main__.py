"""Run the bot:  python -m signalbot"""

import logging

from dotenv import load_dotenv
from telegram.ext import AIORateLimiter, Application

from .db import Database
from .handlers import post_init, register
from .market import Market
from .service import Service
from .settings import Settings


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its request logs contain the bot token
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    settings = Settings.from_env()
    service = Service(Database(settings.db_path), Market(settings.coingecko_api_key), settings)
    app = (Application.builder().token(settings.bot_token)
           .rate_limiter(AIORateLimiter(max_retries=3)).post_init(post_init).build())
    register(app, service)
    logging.getLogger(__name__).info("Bot starting (alerts only, never trades)")
    app.run_polling(allowed_updates=["message", "callback_query", "pre_checkout_query"])


if __name__ == "__main__":
    main()
