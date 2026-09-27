import os
import re
import sqlite3


DB_NAME = os.getenv("DB_PATH", "bizbot_v06.db")

_TRANSLIT_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d",
    "е": "e", "є": "ie", "ж": "zh", "з": "z", "и": "y", "і": "i",
    "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ь": "", "ю": "iu", "я": "ia", "'": "", "’": "", "`": "",
}


DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]


def get_connection():
    # timeout: how long a write waits on a lock before raising, instead
    # of failing instantly under concurrent writers.
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row

    # WAL lets readers run concurrently with a writer instead of every
    # write locking the whole file; it's a one-time, persistent setting
    # on the database file, but harmless (and cheap) to (re)assert here.
    conn.execute("PRAGMA journal_mode = WAL")
    # busy_timeout is per-connection and mirrors the driver-level
    # `timeout` above at the SQLite level, so a write blocked by another
    # connection retries instead of raising "database is locked".
    conn.execute("PRAGMA busy_timeout = 30000")
    # SQLite enforces nothing declared as FOREIGN KEY unless this is set
    # on every connection — it's off by default for backward compat.
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


def slugify(name):
    lowered = (name or "").lower()
    transliterated = "".join(
        _TRANSLIT_MAP.get(char, char) for char in lowered
    )
    slug = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
    return slug[:40] or "business"


def generate_unique_slug(cursor, name):
    base_slug = slugify(name)
    slug = base_slug
    suffix = 2

    while True:
        cursor.execute(
            "SELECT 1 FROM businesses WHERE slug = ?",
            (slug,)
        )

        if not cursor.fetchone():
            return slug

        slug = f"{base_slug}-{suffix}"
        suffix += 1


def init_database():
    # Imported lazily: migrations/0001_baseline.py imports
    # generate_unique_slug back from this module, which would be a
    # circular import if this were a top-level import instead.
    from migrations import run_migrations

    conn = get_connection()
    run_migrations(conn)
    conn.close()


def set_working_hours(
    business_id,
    weekday,
    start_time,
    end_time,
    is_open=1,
    service_id=None
):
    conn = get_connection()
    cursor = conn.cursor()

    # Прибираємо старий графік цього дня (для цього ж service_id:
    # NULL — загальний графік бізнесу, число — графік конкретної послуги)
    if service_id is None:
        cursor.execute(
            """
            DELETE FROM working_hours
            WHERE business_id = ? AND weekday = ?
              AND service_id IS NULL
            """,
            (business_id, weekday)
        )
    else:
        cursor.execute(
            """
            DELETE FROM working_hours
            WHERE business_id = ? AND weekday = ?
              AND service_id = ?
            """,
            (business_id, weekday, service_id)
        )

    # Записуємо новий
    cursor.execute(
        """
        INSERT INTO working_hours (
            business_id,
            weekday,
            start_time,
            end_time,
            is_open,
            service_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            weekday,
            start_time,
            end_time,
            is_open,
            service_id
        )
    )

    conn.commit()
    conn.close()


def get_working_hours(business_id, service_id=None):
    conn = get_connection()
    cursor = conn.cursor()

    if service_id is not None:
        cursor.execute(
            """
            SELECT weekday, start_time, end_time, is_open
            FROM working_hours
            WHERE business_id = ? AND service_id = ?
            ORDER BY weekday
            """,
            (business_id, service_id)
        )

        rows = cursor.fetchall()

        if rows:
            conn.close()
            return rows

    # Немає власного графіка для цієї послуги (або service_id не
    # задано) — беремо загальний графік бізнесу.
    cursor.execute(
        """
        SELECT weekday, start_time, end_time, is_open
        FROM working_hours
        WHERE business_id = ? AND service_id IS NULL
        ORDER BY weekday
        """,
        (business_id,)
    )

    rows = cursor.fetchall()
    conn.close()

    return rows


def get_own_working_hours(business_id, service_id):
    """Графік, заданий САМЕ для цієї послуги, без fallback на
    загальний графік бізнесу. Порожній список = власного графіка
    немає (get_working_hours() для цієї послуги поверне fallback)."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT weekday, start_time, end_time, is_open
        FROM working_hours
        WHERE business_id = ? AND service_id = ?
        ORDER BY weekday
        """,
        (business_id, service_id)
    )

    rows = cursor.fetchall()
    conn.close()

    return rows


if __name__ == "__main__":
    init_database()
    print("✅ BizBot v0.6 database created!")
