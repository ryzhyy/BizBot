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


def _scope_filter(service_id, master_id):
    """SQL condition + params selecting one schedule scope: a service, a
    master, or (neither) the business-wide default."""
    if service_id is not None:
        return "service_id = ?", (service_id,)
    if master_id is not None:
        return "master_id = ?", (master_id,)
    return "service_id IS NULL AND master_id IS NULL", ()


def set_working_hours(
    business_id,
    weekday,
    start_time,
    end_time,
    is_open=1,
    service_id=None,
    master_id=None,
    break_start=None,
    break_end=None
):
    """Save one weekday of one schedule: a service's (service_id), a
    master's (master_id) or, with neither, the business default.
    break_start/break_end: optional break inside the day."""
    scope, scope_params = _scope_filter(service_id, master_id)

    conn = get_connection()
    cursor = conn.cursor()

    # Прибираємо старий графік цього дня в цьому ж графіку.
    cursor.execute(
        f"DELETE FROM working_hours "
        f"WHERE business_id = ? AND weekday = ? AND {scope}",
        (business_id, weekday, *scope_params)
    )

    cursor.execute(
        """
        INSERT INTO working_hours (
            business_id, weekday, start_time, end_time, is_open,
            service_id, master_id, break_start, break_end
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            business_id, weekday, start_time, end_time, is_open,
            service_id, master_id, break_start, break_end
        )
    )

    conn.commit()
    conn.close()


def _schedule_rows(cursor, business_id, service_id=None, master_id=None):
    scope, scope_params = _scope_filter(service_id, master_id)
    return cursor.execute(
        f"""
        SELECT weekday, start_time, end_time, is_open,
               break_start, break_end
        FROM working_hours
        WHERE business_id = ? AND {scope}
        ORDER BY weekday
        """,
        (business_id, *scope_params)
    ).fetchall()


def get_working_hours(business_id, service_id=None):
    """Effective schedule for a service: its own, else its master's,
    else the business default. Without service_id: the default."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        if service_id is not None:
            rows = _schedule_rows(cursor, business_id, service_id=service_id)
            if rows:
                return rows

            master = cursor.execute(
                "SELECT master_id FROM services WHERE id = ? AND business_id = ?",
                (service_id, business_id)
            ).fetchone()

            if master and master["master_id"] is not None:
                rows = _schedule_rows(
                    cursor, business_id, master_id=master["master_id"]
                )
                if rows:
                    return rows

        return _schedule_rows(cursor, business_id)
    finally:
        conn.close()


def get_master_working_hours(business_id, master_id):
    """Графік, заданий САМЕ для цього майстра (без fallback)."""
    conn = get_connection()
    try:
        return _schedule_rows(conn.cursor(), business_id, master_id=master_id)
    finally:
        conn.close()


def get_own_working_hours(business_id, service_id):
    """Графік, заданий САМЕ для цієї послуги, без fallback на
    загальний графік бізнесу. Порожній список = власного графіка
    немає (get_working_hours() для цієї послуги поверне fallback)."""
    conn = get_connection()
    try:
        return _schedule_rows(conn.cursor(), business_id, service_id=service_id)
    finally:
        conn.close()


def format_day_hours(row):
    """'09:00–18:00 (перерва 13:00–14:00)', '09:00–18:00' or 'вихідний'."""
    if not row["is_open"]:
        return "вихідний"

    text = f"{row['start_time']}–{row['end_time']}"

    if row["break_start"] and row["break_end"]:
        text += f" (перерва {row['break_start']}–{row['break_end']})"

    return text


if __name__ == "__main__":
    init_database()
    print("✅ BizBot v0.6 database created!")
