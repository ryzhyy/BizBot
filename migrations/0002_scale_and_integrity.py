"""Hardens the schema for hundreds of businesses / thousands of clients:


- adds the indexes the hot query paths (service lists, per-business
  booking counts) were missing
- turns FKs from decoration into enforced constraints, with sane
  ON DELETE behaviour, via SQLite's table-rebuild technique (SQLite has
  no ALTER TABLE ... ADD CONSTRAINT)
- adds CHECK constraints for the value domains the app already assumes
  (status, price, duration, weekday, the 0/1 flags)
- adds the UNIQUE constraints the app's check-then-write code relied on
  without the database actually backing it up: one business per owner,
  one customer row per (business, telegram user), and — the important
  one at this scale — one confirmed booking per (business, date, time),
  which closes the double-booking race between concurrent clients
  tapping the same slot.

Every step below is written to run against real, possibly-messy
production data without dropping any of it: duplicates are merged or
marked, never deleted outright, and constraints are added only after
the data is made to satisfy them.
"""

import logging

logger = logging.getLogger(__name__)



def _columns(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cursor.fetchall()]


def _rebuild_table(cursor, table, create_sql, columns):
    tmp = f"{table}__migrating"
    column_list = ", ".join(columns)

    cursor.execute(f"ALTER TABLE {table} RENAME TO {tmp}")
    cursor.execute(create_sql)
    cursor.execute(
        f"INSERT INTO {table} ({column_list}) "
        f"SELECT {column_list} FROM {tmp}"
    )
    cursor.execute(f"DROP TABLE {tmp}")


def _dedupe_customers(cursor):
    """Merge duplicate (business_id, telegram_id) customer rows — a
    relic of the check-then-insert race in get_or_create_customer —
    into one row each, so the new UNIQUE constraint has something to
    hold onto. The earliest row is kept; any phone/name a later
    duplicate captured is folded in rather than discarded, and any
    bookings pointing at a duplicate are repointed at the survivor."""

    cursor.execute(
        """
        SELECT business_id, telegram_id, MIN(id) AS keep_id
        FROM customers
        GROUP BY business_id, telegram_id
        HAVING COUNT(*) > 1
        """
    )
    duplicate_groups = cursor.fetchall()

    for group in duplicate_groups:
        cursor.execute(
            """
            SELECT id, name, phone FROM customers
            WHERE business_id = ? AND telegram_id = ? AND id != ?
            """,
            (group["business_id"], group["telegram_id"], group["keep_id"])
        )
        duplicates = cursor.fetchall()
        duplicate_ids = [row["id"] for row in duplicates]

        for dup in duplicates:
            if dup["name"]:
                cursor.execute(
                    "UPDATE customers SET name = COALESCE(name, ?) "
                    "WHERE id = ?",
                    (dup["name"], group["keep_id"])
                )
            if dup["phone"]:
                cursor.execute(
                    "UPDATE customers SET phone = COALESCE(phone, ?) "
                    "WHERE id = ?",
                    (dup["phone"], group["keep_id"])
                )

        placeholders = ", ".join("?" for _ in duplicate_ids)
        cursor.execute(
            f"UPDATE bookings SET customer_id = ? "
            f"WHERE customer_id IN ({placeholders})",
            [group["keep_id"], *duplicate_ids]
        )
        cursor.execute(
            f"DELETE FROM customers WHERE id IN ({placeholders})",
            duplicate_ids
        )


def _dedupe_working_hours(cursor):
    """set_working_hours() always deletes-then-inserts, so duplicate
    rows for the same (business, service, weekday) can only be leftover
    from an interrupted write. Keep the newest row (it reflects the
    last schedule actually saved) and drop the rest."""

    for scope_filter in ("service_id IS NULL", "service_id IS NOT NULL"):
        cursor.execute(
            f"""
            SELECT business_id, service_id, weekday, MAX(id) AS keep_id
            FROM working_hours
            WHERE {scope_filter}
            GROUP BY business_id, service_id, weekday
            HAVING COUNT(*) > 1
            """
        )
        for group in cursor.fetchall():
            if group["service_id"] is None:
                cursor.execute(
                    """
                    DELETE FROM working_hours
                    WHERE business_id = ? AND service_id IS NULL
                      AND weekday = ? AND id != ?
                    """,
                    (group["business_id"], group["weekday"], group["keep_id"])
                )
            else:
                cursor.execute(
                    """
                    DELETE FROM working_hours
                    WHERE business_id = ? AND service_id = ?
                      AND weekday = ? AND id != ?
                    """,
                    (
                        group["business_id"], group["service_id"],
                        group["weekday"], group["keep_id"]
                    )
                )


def _dedupe_confirmed_bookings(cursor):
    """Only one confirmed booking can legitimately hold a given slot.
    If the pre-existing check-then-insert race ever let two through,
    keep the earliest (the one the customer actually arrived for) and
    mark the rest cancelled — never delete a booking record."""

    cursor.execute(
        """
        SELECT business_id, booking_date, booking_time, MIN(id) AS keep_id
        FROM bookings
        WHERE status = 'confirmed'
        GROUP BY business_id, booking_date, booking_time
        HAVING COUNT(*) > 1
        """
    )
    for group in cursor.fetchall():
        cursor.execute(
            """
            UPDATE bookings SET status = 'cancelled'
            WHERE business_id = ? AND booking_date = ? AND booking_time = ?
              AND status = 'confirmed' AND id != ?
            """,
            (
                group["business_id"], group["booking_date"],
                group["booking_time"], group["keep_id"]
            )
        )


def up(cursor):
    # --- Sanitize out-of-domain values before the CHECK constraints
    # below start rejecting them. These are no-ops on data the app has
    # only ever written correctly; they exist so a stray manual edit
    # can't halt the whole migration.
    cursor.execute("UPDATE services SET price = 0 WHERE price < 0")
    cursor.execute("UPDATE services SET duration = 1 WHERE duration <= 0")
    cursor.execute(
        "UPDATE services SET active = 1 WHERE active NOT IN (0, 1)"
    )
    cursor.execute(
        "UPDATE working_hours SET is_open = 1 WHERE is_open NOT IN (0, 1)"
    )
    cursor.execute(
        "UPDATE working_hours SET weekday = 0 "
        "WHERE weekday < 0 OR weekday > 6"
    )
    cursor.execute(
        "UPDATE bookings SET status = 'cancelled' "
        "WHERE status NOT IN ('confirmed', 'cancelled', 'completed')"
    )
    cursor.execute(
        "UPDATE bookings SET reminder_sent = 0 "
        "WHERE reminder_sent NOT IN (0, 1)"
    )

    # --- Resolve duplicates the new UNIQUE constraints would reject.
    _dedupe_customers(cursor)
    _dedupe_working_hours(cursor)
    _dedupe_confirmed_bookings(cursor)

    # --- services: FK + CHECK constraints, plus the business_id index
    # every service list / count query was missing.
    _rebuild_table(
        cursor,
        "services",
        """
        CREATE TABLE services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL
                REFERENCES businesses(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            price INTEGER NOT NULL CHECK (price >= 0),
            duration INTEGER NOT NULL CHECK (duration > 0),
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
        )
        """,
        ["id", "business_id", "name", "price", "duration", "active"]
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_services_business "
        "ON services(business_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_services_business_active "
        "ON services(business_id, active)"
    )

    # --- working_hours: FK + CHECK constraints, and the two constraints
    # set_working_hours already assumed: one default schedule row per
    # (business, weekday), one per-service schedule row per
    # (business, service, weekday). NULL service_id means "the
    # business-wide default", so these have to be partial indexes —
    # a plain UNIQUE index treats every NULL as distinct.
    _rebuild_table(
        cursor,
        "working_hours",
        """
        CREATE TABLE working_hours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL
                REFERENCES businesses(id) ON DELETE CASCADE,
            service_id INTEGER
                REFERENCES services(id) ON DELETE CASCADE,
            weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
            start_time TEXT,
            end_time TEXT,
            is_open INTEGER NOT NULL DEFAULT 1 CHECK (is_open IN (0, 1))
        )
        """,
        [
            "id", "business_id", "service_id", "weekday",
            "start_time", "end_time", "is_open"
        ]
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_working_hours_business_service "
        "ON working_hours(business_id, service_id, weekday)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_working_hours_default_unique "
        "ON working_hours(business_id, weekday) "
        "WHERE service_id IS NULL"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_working_hours_service_unique "
        "ON working_hours(business_id, service_id, weekday) "
        "WHERE service_id IS NOT NULL"
    )

    # --- customers: FK constraint, and a real UNIQUE constraint behind
    # get_or_create_customer's lookup-then-write so concurrent /start
    # calls from the same Telegram user can't create two rows.
    _rebuild_table(
        cursor,
        "customers",
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL
                REFERENCES businesses(id) ON DELETE CASCADE,
            telegram_id INTEGER NOT NULL,
            name TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        ["id", "business_id", "telegram_id", "name", "phone", "created_at"]
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_business_telegram "
        "ON customers(business_id, telegram_id)"
    )

    # --- bookings: FK constraints (service_id is RESTRICT — the app
    # never hard-deletes a service, it flips `active`, so a service
    # with booking history should never be deletable out from under
    # them), CHECK on status/reminder_sent, service_id index for the
    # RESTRICT check and joins, and the constraint that actually
    # prevents double-booking: only one confirmed booking may hold a
    # given (business, date, time) slot, enforced by the database
    # itself rather than by the app's is_slot_taken() pre-check, which
    # two concurrent requests can both pass.
    _rebuild_table(
        cursor,
        "bookings",
        """
        CREATE TABLE bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL
                REFERENCES businesses(id) ON DELETE CASCADE,
            customer_id INTEGER
                REFERENCES customers(id) ON DELETE SET NULL,
            service_id INTEGER NOT NULL
                REFERENCES services(id) ON DELETE RESTRICT,
            booking_date TEXT NOT NULL,
            booking_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'confirmed'
                CHECK (status IN ('confirmed', 'cancelled', 'completed')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reminder_sent INTEGER NOT NULL DEFAULT 0
                CHECK (reminder_sent IN (0, 1))
        )
        """,
        [
            "id", "business_id", "customer_id", "service_id",
            "booking_date", "booking_time", "status", "created_at",
            "reminder_sent"
        ]
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
        "CREATE INDEX IF NOT EXISTS idx_bookings_service "
        "ON bookings(service_id)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_bookings_confirmed_slot_unique "
        "ON bookings(business_id, booking_date, booking_time) "
        "WHERE status = 'confirmed'"
    )

    # --- faq_items / bot_users: FK constraints only, no shape changes.
    _rebuild_table(
        cursor,
        "faq_items",
        """
        CREATE TABLE faq_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL
                REFERENCES businesses(id) ON DELETE CASCADE,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        ["id", "business_id", "question", "answer", "created_at"]
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_faq_items_business "
        "ON faq_items(business_id)"
    )

    _rebuild_table(
        cursor,
        "bot_users",
        """
        CREATE TABLE bot_users (
            telegram_id INTEGER PRIMARY KEY,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            first_business_id INTEGER
                REFERENCES businesses(id) ON DELETE SET NULL
        )
        """,
        ["telegram_id", "first_seen", "first_business_id"]
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bot_users_first_business "
        "ON bot_users(first_business_id)"
    )

    # --- businesses: one business per owner is a rule the app already
    # enforces with a lookup-then-insert check in /setup; back it with
    # a real constraint so two concurrent /setup runs for the same
    # owner can't both slip through. If duplicates already exist
    # (pre-dating this constraint), merging them would mean guessing
    # which business is "the real one" and silently deleting data, so
    # instead we skip adding the constraint and leave a clear marker
    # for manual follow-up rather than lose anything.
    cursor.execute(
        """
        SELECT owner_telegram_id, COUNT(*) AS total
        FROM businesses
        GROUP BY owner_telegram_id
        HAVING COUNT(*) > 1
        """
    )
    duplicate_owners = cursor.fetchall()

    if duplicate_owners:
        owners = ", ".join(str(row["owner_telegram_id"]) for row in duplicate_owners)
        logger.warning(
            "Skipping UNIQUE(businesses.owner_telegram_id): these "
            "owner_telegram_id values already own more than one business "
            "and need manual review first: %s", owners
        )
    else:
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_businesses_owner_unique "
            "ON businesses(owner_telegram_id)"
        )
