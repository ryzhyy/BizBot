import logging
import os


def configure_logging():
    """Timestamps and levels on every line (Railway keeps stdout), and
    LOG_LEVEL=DEBUG to see per-message details like parsed AI intents."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every Telegram long-poll request at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
