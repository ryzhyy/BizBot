import sqlite3

import pytest

import database
from migrations import _discover_migrations


def test_fresh_database_applies_every_migration(db):
    conn = db.get_connection()
    applied = [row[0] for row in conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    )]
    conn.close()

    assert applied == [version for version, _, _ in _discover_migrations()]


def test_rerunning_migrations_is_a_noop(db):
    db.init_database()
    db.init_database()

    conn = db.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    conn.close()

    assert count == len(_discover_migrations())


@pytest.mark.parametrize("sql", [
    "INSERT INTO services (business_id, name, price, duration) "
    "VALUES (1, 'x', -1, 30)",
    "INSERT INTO services (business_id, name, price, duration) "
    "VALUES (1, 'x', 1, 0)",
    "INSERT INTO services (business_id, name, price, duration) "
    "VALUES (999, 'x', 1, 30)",
    "INSERT INTO working_hours (business_id, weekday, is_open) "
    "VALUES (1, 7, 1)",
    "INSERT INTO bookings (business_id, service_id, booking_date, "
    "booking_time, status) VALUES (1, 1, '2026-10-05', '10:00', 'bogus')",
    "INSERT INTO customers (business_id, telegram_id) VALUES (1, 5), (1, 5)",
    "INSERT INTO businesses (owner_telegram_id, name, slug) "
    "VALUES (100, 'Другий', 'second')",
])
def test_constraints_reject_bad_rows(business, sql):
    conn = database.get_connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)
    conn.close()


def test_service_with_bookings_cannot_be_deleted(business):
    conn = database.get_connection()
    conn.execute(
        "INSERT INTO bookings (business_id, service_id, booking_date, "
        "booking_time) VALUES (1, 1, '2026-10-05', '10:00')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM services WHERE id = 1")
    conn.close()


def _legacy_database(path):
    """The schema as the pre-migration init_database() left it, with
    the dirty data its races could produce."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE businesses (id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_telegram_id INTEGER NOT NULL, name TEXT NOT NULL,
            category TEXT, city TEXT, phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            pro_until TEXT DEFAULT NULL,
            pro_warning_sent_for TEXT DEFAULT NULL,
            pro_expired_notified_for TEXT DEFAULT NULL);
        CREATE TABLE services (id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL, name TEXT NOT NULL,
            price INTEGER NOT NULL, duration INTEGER NOT NULL,
            active INTEGER DEFAULT 1);
        CREATE TABLE working_hours (id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL, weekday INTEGER NOT NULL,
            start_time TEXT, end_time TEXT, is_open INTEGER DEFAULT 1,
            service_id INTEGER DEFAULT NULL);
        CREATE TABLE customers (id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL, telegram_id INTEGER NOT NULL,
            name TEXT, phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE bookings (id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL, customer_id INTEGER,
            service_id INTEGER NOT NULL, booking_date TEXT NOT NULL,
            booking_time TEXT NOT NULL, status TEXT DEFAULT 'confirmed',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reminder_sent INTEGER DEFAULT 0);

        INSERT INTO businesses (id, owner_telegram_id, name, pro_until,
            pro_warning_sent_for)
        VALUES (1, 111, 'Барбершоп', '2026-10-27 12:00:00',
            '2026-10-27 12:00:00');
        INSERT INTO services VALUES (1, 1, 'Стрижка', 300, 60, 1);
        INSERT INTO working_hours (business_id, weekday, start_time,
            end_time) VALUES (1, 0, '09:00', '18:00'),
            (1, 0, '10:00', '19:00');
        INSERT INTO customers (id, business_id, telegram_id, name, phone)
        VALUES (1, 1, 999, 'Іван', NULL), (2, 1, 999, NULL, '+380001');
        INSERT INTO bookings (id, business_id, customer_id, service_id,
            booking_date, booking_time)
        VALUES (1, 1, 1, 1, '2026-10-05', '10:00'),
            (2, 1, 2, 1, '2026-10-05', '10:00');
    """)
    conn.commit()
    conn.close()


def test_legacy_database_migrates_without_losing_data(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    _legacy_database(path)
    monkeypatch.setattr(database, "DB_NAME", str(path))

    database.init_database()

    conn = database.get_connection()
    customers = conn.execute("SELECT id, name, phone FROM customers").fetchall()
    assert [tuple(row) for row in customers] == [(1, "Іван", "+380001")]

    statuses = conn.execute(
        "SELECT id, customer_id, status FROM bookings ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in statuses] == [
        (1, 1, "confirmed"), (2, 1, "cancelled"),
    ]

    hours = conn.execute("SELECT start_time FROM working_hours").fetchall()
    assert [row[0] for row in hours] == ["10:00"]

    # Pro timestamps were stored in server UTC; late October is UTC+2.
    business = conn.execute(
        "SELECT pro_until, pro_warning_sent_for FROM businesses"
    ).fetchone()
    assert tuple(business) == ("2026-10-27 14:00:00", "2026-10-27 14:00:00")
    conn.close()

    backups = list(tmp_path.glob("legacy.db.pre-0001-*.bak"))
    assert len(backups) == 1
