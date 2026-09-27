"""Baseline schema: everything BizBot's ad-hoc init_database() used to
create by hand, frozen as migration 1. Runs once. On a brand-new database
it builds the full pre-existing schema; on a database that already has
these tables/columns (every production database, since this replaces the
old inline migration code) every step is a no-op because it is guarded
the same way the old code guarded it — so applying this migration never
touches, let alone loses, existing data.
"""

from database import generate_unique_slug


def _columns(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def up(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS businesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_telegram_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT,
            city TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
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
        """
    )

    cursor.execute(
        """
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
        """
    )

    cursor.execute(
        """
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
        """
    )

    cursor.execute(
        """
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
        """
    )

    if "active" not in _columns(cursor, "services"):
        cursor.execute(
            "ALTER TABLE services ADD COLUMN active INTEGER DEFAULT 1"
        )

    business_columns = _columns(cursor, "businesses")

    for column, ddl in (
        ("support_contact_mode",
         "ALTER TABLE businesses "
         "ADD COLUMN support_contact_mode TEXT DEFAULT 'auto'"),
        ("support_contact_value",
         "ALTER TABLE businesses "
         "ADD COLUMN support_contact_value TEXT DEFAULT NULL"),
        ("faq_text",
         "ALTER TABLE businesses ADD COLUMN faq_text TEXT DEFAULT NULL"),
        ("latitude",
         "ALTER TABLE businesses ADD COLUMN latitude REAL DEFAULT NULL"),
        ("longitude",
         "ALTER TABLE businesses ADD COLUMN longitude REAL DEFAULT NULL"),
        ("address_text",
         "ALTER TABLE businesses ADD COLUMN address_text TEXT DEFAULT NULL"),
        ("slug",
         "ALTER TABLE businesses ADD COLUMN slug TEXT DEFAULT NULL"),
    ):
        if column not in business_columns:
            cursor.execute(ddl)

    cursor.execute("SELECT id, name FROM businesses WHERE slug IS NULL")
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

    if "reminder_sent" not in _columns(cursor, "bookings"):
        cursor.execute(
            "ALTER TABLE bookings ADD COLUMN reminder_sent INTEGER DEFAULT 0"
        )

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

    if "service_id" not in _columns(cursor, "working_hours"):
        cursor.execute(
            "ALTER TABLE working_hours "
            "ADD COLUMN service_id INTEGER DEFAULT NULL"
        )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_working_hours_business_service "
        "ON working_hours(business_id, service_id, weekday)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS faq_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (business_id)
            REFERENCES businesses(id)
        )
        """
    )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_faq_items_business "
        "ON faq_items(business_id)"
    )

    cursor.execute(
        """
        SELECT id, faq_text FROM businesses
        WHERE faq_text IS NOT NULL AND TRIM(faq_text) != ''
        """
    )
    businesses_with_faq_text = cursor.fetchall()

    for row in businesses_with_faq_text:
        cursor.execute(
            "SELECT COUNT(*) AS count FROM faq_items WHERE business_id = ?",
            (row["id"],)
        )
        has_items = cursor.fetchone()["count"] > 0

        if not has_items:
            cursor.execute(
                """
                INSERT INTO faq_items (business_id, question, answer)
                VALUES (?, ?, ?)
                """,
                (row["id"], "Інформація", row["faq_text"])
            )

    business_columns = _columns(cursor, "businesses")

    for column in (
        "pro_until", "pro_warning_sent_for", "pro_expired_notified_for"
    ):
        if column not in business_columns:
            cursor.execute(
                f"ALTER TABLE businesses ADD COLUMN {column} TEXT DEFAULT NULL"
            )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_users (
            telegram_id INTEGER PRIMARY KEY,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            first_business_id INTEGER DEFAULT NULL
        )
        """
    )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bot_users_first_business "
        "ON bot_users(first_business_id)"
    )

    cursor.execute(
        """
        INSERT OR IGNORE INTO bot_users (telegram_id)
        SELECT owner_telegram_id FROM businesses
        """
    )
    cursor.execute(
        """
        INSERT OR IGNORE INTO bot_users (telegram_id, first_business_id)
        SELECT telegram_id, MIN(business_id) FROM customers
        GROUP BY telegram_id
        """
    )
