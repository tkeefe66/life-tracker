import logging
import os

import database as db
from bot import create_application

logging.basicConfig(
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main():
    db.initialize_db()

    app = create_application()
    logger.info("Weekly Updates Bot is starting.")

    webhook_url = os.getenv("WEBHOOK_URL", "").rstrip("/")
    port = int(os.getenv("PORT", "8080"))

    if webhook_url:
        from config import TELEGRAM_BOT_TOKEN
        path = TELEGRAM_BOT_TOKEN
        full_url = f"{webhook_url}/{path}"
        logger.info("Webhook mode: %s (port %d)", full_url, port)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=path,
            webhook_url=full_url,
        )
    else:
        logger.info("Polling mode (local dev).")
        app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
