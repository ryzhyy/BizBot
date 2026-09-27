"""Working hours per master.

A working_hours row now belongs to exactly one scope: the business
default (service_id and master_id both NULL), one service, or one
master. For a service the effective schedule is its own, else its
master's, else the business default (database.get_working_hours).

The old "one default row per weekday" unique index only looked at
service_id IS NULL, which master rows also satisfy, so it would reject
a master's Monday next to the business's Monday; it now excludes
master rows, and masters get their own per-weekday uniqueness.
"""


def up(cursor):
    cursor.execute("PRAGMA table_info(working_hours)")
    if "master_id" not in {row[1] for row in cursor.fetchall()}:
        cursor.execute(
            "ALTER TABLE working_hours ADD COLUMN master_id INTEGER "
            "REFERENCES masters(id) ON DELETE CASCADE"
        )

    cursor.execute("DROP INDEX IF EXISTS idx_working_hours_default_unique")
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_working_hours_default_unique "
        "ON working_hours(business_id, weekday) "
        "WHERE service_id IS NULL AND master_id IS NULL"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_working_hours_master_unique "
        "ON working_hours(business_id, master_id, weekday) "
        "WHERE master_id IS NOT NULL"
    )

    for event, suffix in (("INSERT", ""), ("UPDATE", "_upd")):
        cursor.execute(
            f"CREATE TRIGGER IF NOT EXISTS working_hours_single_scope{suffix} "
            f"BEFORE {event} ON working_hours FOR EACH ROW BEGIN "
            "SELECT RAISE(ABORT, 'working_hours row is for a service or a "
            "master, not both') "
            "WHERE NEW.service_id IS NOT NULL AND NEW.master_id IS NOT NULL; "
            "END"
        )
        cursor.execute(
            f"CREATE TRIGGER IF NOT EXISTS working_hours_master_same_business{suffix} "
            f"BEFORE {event} ON working_hours FOR EACH ROW BEGIN "
            "SELECT RAISE(ABORT, 'working_hours.master_id belongs to another "
            "business') "
            "WHERE NEW.master_id IS NOT NULL "
            "AND (SELECT business_id FROM masters WHERE id = NEW.master_id) "
            "IS NOT NEW.business_id; "
            "END"
        )
