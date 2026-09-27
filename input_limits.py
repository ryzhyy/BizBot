"""Length limits for text owners type in.

Nothing capped these before, so a pasted essay as a business or service
name would later break every message and button that shows it
(Telegram caps a message at 4096 characters).
"""

BUSINESS_NAME = 60
BUSINESS_CATEGORY = 40
BUSINESS_CITY = 40
SERVICE_NAME = 60
CONTACT_VALUE = 100
ADDRESS_TEXT = 200
FAQ_QUESTION = 200
FAQ_ANSWER = 1000

MAX_PRICE_UAH = 1_000_000
MAX_DURATION_MINUTES = 12 * 60


def clean_text(text, max_length):
    """Trimmed text, or None if it is empty or longer than max_length."""
    value = (text or "").strip()
    if not value or len(value) > max_length:
        return None
    return value


def too_long_text(max_length):
    return f"⚠️ Надто довго — до {max_length} символів. Напишіть коротше."
