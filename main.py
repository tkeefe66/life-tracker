import logging
import os

import database as db
from bot import create_application

logging.basicConfig(
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _test_anthropic():
    try:
        import anthropic
        from config import ANTHROPIC_API_KEY
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        models = client.models.list()
        logger.info("Anthropic models available: %s", [m.id for m in models.data])
    except Exception as e:
        logger.error("Anthropic test failed: %s", e)


def _test_calendar():
    logger.info("Running calendar health check...")
    from services.calendar_service import is_configured
    if not is_configured():
        logger.info("Google Calendar: not configured (set GOOGLE_CALENDAR_* env vars to enable)")
        return
    try:
        from services.calendar_service import get_events_rolling_window
        events = get_events_rolling_window(days=1)
        logger.info("Google Calendar: connected — %d event(s) in next 24h", len(events))
    except Exception as e:
        logger.error("Google Calendar health check failed: %s", e)


def _purge_blocked_items():
    """Remove any birthday/holiday items that slipped in before filtering was added."""
    count = db.delete_later_items_matching(["birthday", "bday", "holiday"])
    if count:
        logger.info("Purged %d birthday/holiday item(s) from Later Items", count)


def main():
    db.initialize_db()
    _purge_blocked_items()
    _test_anthropic()
    _test_calendar()

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
