"""Щоденний бекап бази власнику платформи в Telegram.

Файл бази живе на одному volume Railway, як і бекапи перед міграціями,
тож якщо з volume щось станеться, втрачено буде все разом. Копія в
Telegram лежить поза Railway і не залежить від нього.
"""

import gzip
import os
import shutil
import sqlite3
import tempfile
from datetime import time

from database import get_connection
from error_reporting import report_error
from platform_admin import OWNER_TELEGRAM_ID
from timeutils import BOT_TIMEZONE, now_local

BACKUP_TIME = time(3, 0, tzinfo=BOT_TIMEZONE)
# Ліміт Telegram Bot API на надсилання файлів.
TELEGRAM_FILE_LIMIT_BYTES = 50 * 1024 * 1024


def make_backup_file(directory):
    """Узгоджена копія бази (sqlite backup API, безпечно під WAL),
    стиснута gzip. Повертає шлях до .db.gz."""
    stamp = now_local().strftime("%Y-%m-%d_%H-%M")
    db_copy = os.path.join(directory, f"bizbot_{stamp}.db")

    source = get_connection()
    target = sqlite3.connect(db_copy)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    gz_path = db_copy + ".gz"
    with open(db_copy, "rb") as raw, gzip.open(gz_path, "wb") as packed:
        shutil.copyfileobj(raw, packed)

    return gz_path


async def send_daily_backup(context):
    if not OWNER_TELEGRAM_ID:
        return

    try:
        with tempfile.TemporaryDirectory() as directory:
            path = make_backup_file(directory)
            size = os.path.getsize(path)

            if size > TELEGRAM_FILE_LIMIT_BYTES:
                await context.bot.send_message(
                    chat_id=int(OWNER_TELEGRAM_ID),
                    text=(
                        f"⚠️ Бекап бази ({size // (1024 * 1024)} МБ) більший "
                        "за ліміт Telegram у 50 МБ, тож не надіслано. "
                        "Потрібне інше сховище для бекапів."
                    ),
                )
                return

            with open(path, "rb") as backup:
                await context.bot.send_document(
                    chat_id=int(OWNER_TELEGRAM_ID),
                    document=backup,
                    filename=os.path.basename(path),
                    caption=(
                        "🗄 Щоденний бекап бази BizBot.\n"
                        "Містить дані клієнтів — не пересилайте нікому."
                    ),
                )
    except Exception as error:
        await report_error(context.bot, error, where="Щоденний бекап бази")
