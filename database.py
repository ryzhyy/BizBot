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
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
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
    conn = get_connection()
    cursor = conn.cursor()

    # -------------------------
    # BUSINESSES
    # -------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS businesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_telegram_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT,
            city TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # -------------------------
    # SERVICES
    # -------------------------

   
    # -------------------------
    # SERVICES
    # -------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            duration INTEGER NOT NULL,
            active INTEGER DEFAULT 1,
            FOREIGN KEY (business_id)
            REFERENCES businesses(id)
        )
    """)

    # -------------------------
    # WORKING HOURS
    # -------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS working_hours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            weekday INTEGER NOT NULL,
            start_time TEXT,
            end_time TEXT,
            is_open INTEGER DEFAULT 1,

            FOREIGN KEY (business_id)
            REFERENCES businesses(id)
        )
    """)

    # -------------------------
    # CUSTOMERS
    # -------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            telegram_id INTEGER NOT NULL,
            name TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (business_id)
            REFERENCES businesses(id)
        )
    """)

    # -------------------------
    # BOOKINGS
    # -------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            customer_id INTEGER,
            service_id INTEGER NOT NULL,
            booking_date TEXT NOT NULL,
            booking_time TEXT NOT NULL,
            status TEXT DEFAULT 'confirmed',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (business_id)
            REFERENCES businesses(id),

            FOREIGN KEY (customer_id)
            REFERENCES customers(id),

            FOREIGN KEY (service_id)
            REFERENCES services(id)
        )
    """)
    
    
    # Migration: add active column to existing services table
    cursor.execute("PRAGMA table_info(services)")
    columns = [row[1] for row in cursor.fetchall()]

    if "active" not in columns:
        cursor.execute(
            "ALTER TABLE services ADD COLUMN active INTEGER DEFAULT 1"
        )

    # Migration: add support contact / FAQ columns to businesses
    cursor.execute("PRAGMA table_info(businesses)")
    business_columns = [row[1] for row in cursor.fetchall()]

    if "support_contact_mode" not in business_columns:
        cursor.execute(
            "ALTER TABLE businesses "
            "ADD COLUMN support_contact_mode TEXT DEFAULT 'auto'"
        )

    if "support_contact_value" not in business_columns:
        cursor.execute(
            "ALTER TABLE businesses "
            "ADD COLUMN support_contact_value TEXT DEFAULT NULL"
        )

    if "faq_text" not in business_columns:
        cursor.execute(
            "ALTER TABLE businesses "
            "ADD COLUMN faq_text TEXT DEFAULT NULL"
        )

    if "latitude" not in business_columns:
        cursor.execute(
            "ALTER TABLE businesses "
            "ADD COLUMN latitude REAL DEFAULT NULL"
        )

    if "longitude" not in business_columns:
        cursor.execute(
            "ALTER TABLE businesses "
            "ADD COLUMN longitude REAL DEFAULT NULL"
        )

    if "address_text" not in business_columns:
        cursor.execute(
            "ALTER TABLE businesses "
            "ADD COLUMN address_text TEXT DEFAULT NULL"
        )

    if "slug" not in business_columns:
        cursor.execute(
            "ALTER TABLE businesses "
            "ADD COLUMN slug TEXT DEFAULT NULL"
        )

    # Backfill: generate a slug for businesses that don't have one yet
    cursor.execute(
        "SELECT id, name FROM businesses WHERE slug IS NULL"
    )
    businesses_missing_slug = cursor.fetchall()

    for row in businesses_missing_slug:
        slug = generate_unique_slug(cursor, row["name"])
        cursor.execute(
            "UPDATE businesses SET slug = ? WHERE id = ?",
            (slug, row["id"])
        )

    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_businesses_slug "
        "ON businesses(slug)"
    )

    # Migration: add reminder_sent column to bookings
    cursor.execute("PRAGMA table_info(bookings)")
    booking_columns = [row[1] for row in cursor.fetchall()]

    if "reminder_sent" not in booking_columns:
        cursor.execute(
            "ALTER TABLE bookings "
            "ADD COLUMN reminder_sent INTEGER DEFAULT 0"
        )

    # Indexes for the columns hit on every booking/AI-context lookup.
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_businesses_owner_telegram_id "
        "ON businesses(owner_telegram_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bookings_business_date_status "
        "ON bookings(business_id, booking_date, status)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bookings_business_customer "
        "ON bookings(business_id, customer_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_customers_business_telegram "
        "ON customers(business_id, telegram_id)"
    )

    # Migration: add service_id to working_hours (per-service schedules).
    # NULL service_id = the business-wide default schedule (existing rows).
    cursor.execute("PRAGMA table_info(working_hours)")
    working_hours_columns = [row[1] for row in cursor.fetchall()]

    if "service_id" not in working_hours_columns:
        cursor.execute(
            "ALTER TABLE working_hours "
            "ADD COLUMN service_id INTEGER DEFAULT NULL"
        )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_working_hours_business_service "
        "ON working_hours(business_id, service_id, weekday)"
    )

    conn.commit()
    conn.close()
def set_business_schedule(
    business_id,
    weekday,
    open_time,
    close_time,
    is_working=1
):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO business_schedule (
            business_id,
            weekday,
            open_time,
            close_time,
            is_working
        )
        VALUES (?, ?, ?, ?, ?)

        ON CONFLICT(business_id, weekday)
        DO UPDATE SET
            open_time = excluded.open_time,
            close_time = excluded.close_time,
            is_working = excluded.is_working
        """,
        (
            business_id,
            weekday,
            open_time,
            close_time,
            is_working
        )
    )

    conn.commit()
    conn.close()
def get_business_schedule(business_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT weekday, open_time, close_time, is_working
        FROM business_schedule
        WHERE business_id = ?
        ORDER BY weekday
        """,
        (business_id,)
    )

    schedule = cursor.fetchall()
    conn.close()

    return schedule

if __name__ == "__main__":
    init_database()
    print("✅ BizBot v0.6 database created!")

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
