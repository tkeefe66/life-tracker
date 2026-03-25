import logging

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
    logger.info("Weekly Updates Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
