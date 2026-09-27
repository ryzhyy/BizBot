import os
from datetime import datetime
from zoneinfo import ZoneInfo


# Booking dates/times are stored as the business's wall-clock time, but
# the server (Railway) runs in UTC, so plain datetime.now() is hours off.
BOT_TIMEZONE = ZoneInfo(os.getenv("BOT_TIMEZONE", "Europe/Kyiv"))


def now_local():
    """Current wall-clock time in BOT_TIMEZONE, as a naive datetime so it
    compares directly with the naive date/time strings in the database."""
    return datetime.now(BOT_TIMEZONE).replace(tzinfo=None)
