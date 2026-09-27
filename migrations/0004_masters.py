"""Masters: who performs a service.

Owners already split work between people by service, but booking
conflicts were checked business-wide, so two masters could not take
clients at the same time. A service may now point at a master; bookings
only conflict with other bookings of the same master, and services with
no master share one common queue (a single-person business keeps
working exactly as before).

The business-wide "one confirmed booking per date/time" unique index
would still forbid two masters starting at 10:00, so it becomes
per-service. Overlap between services of one master is enforced by the
transactional check in bookings.create_booking().
"""


def up(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS masters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL
                REFERENCES businesses(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            -- name.casefold(): SQLite's NOCASE only folds ASCII, so
            -- "оля" and "Оля" would otherwise be two different masters.
            name_key TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (business_id, name_key)
        )
        """
    )

    cursor.execute("PRAGMA table_info(services)")
    if "master_id" not in {row[1] for row in cursor.fetchall()}:
        cursor.execute(
            "ALTER TABLE services ADD COLUMN master_id INTEGER "
            "REFERENCES masters(id) ON DELETE SET NULL"
        )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_services_master ON services(master_id)"
    )

    cursor.execute("DROP INDEX IF EXISTS idx_bookings_confirmed_slot_unique")
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_bookings_confirmed_service_slot_unique "
        "ON bookings(service_id, booking_date, booking_time) "
        "WHERE status = 'confirmed'"
    )
