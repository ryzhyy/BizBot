"""Make cross-business references impossible at the database level.

Every table carries its own business_id, but nothing stopped a row from
pointing at another business's row: a booking of business B could use
a service or customer of business A (this happened through stale
booking state when a client switched between businesses), a service
could be given another business's master, a per-service schedule could
name another business's service. SQLite can't express "the referenced
row has the same business_id" as a foreign key, so triggers enforce it
on every INSERT/UPDATE, whatever code path writes.

Existing rows are not touched: violations that pre-date this migration
are only reported, so nothing is silently rewritten or deleted.
"""

TRIGGERS = [
    ("bookings_service_same_business", "bookings", "INSERT", """
        SELECT RAISE(ABORT, 'bookings.service_id belongs to another business')
        WHERE (SELECT business_id FROM services WHERE id = NEW.service_id)
              IS NOT NEW.business_id;
    """),
    ("bookings_service_same_business_upd", "bookings",
     "UPDATE OF service_id, business_id", """
        SELECT RAISE(ABORT, 'bookings.service_id belongs to another business')
        WHERE (SELECT business_id FROM services WHERE id = NEW.service_id)
              IS NOT NEW.business_id;
    """),
    ("bookings_customer_same_business", "bookings", "INSERT", """
        SELECT RAISE(ABORT, 'bookings.customer_id belongs to another business')
        WHERE NEW.customer_id IS NOT NULL
          AND (SELECT business_id FROM customers WHERE id = NEW.customer_id)
              IS NOT NEW.business_id;
    """),
    ("bookings_customer_same_business_upd", "bookings",
     "UPDATE OF customer_id, business_id", """
        SELECT RAISE(ABORT, 'bookings.customer_id belongs to another business')
        WHERE NEW.customer_id IS NOT NULL
          AND (SELECT business_id FROM customers WHERE id = NEW.customer_id)
              IS NOT NEW.business_id;
    """),
    ("services_master_same_business", "services", "INSERT", """
        SELECT RAISE(ABORT, 'services.master_id belongs to another business')
        WHERE NEW.master_id IS NOT NULL
          AND (SELECT business_id FROM masters WHERE id = NEW.master_id)
              IS NOT NEW.business_id;
    """),
    ("services_master_same_business_upd", "services",
     "UPDATE OF master_id, business_id", """
        SELECT RAISE(ABORT, 'services.master_id belongs to another business')
        WHERE NEW.master_id IS NOT NULL
          AND (SELECT business_id FROM masters WHERE id = NEW.master_id)
              IS NOT NEW.business_id;
    """),
    ("working_hours_service_same_business", "working_hours", "INSERT", """
        SELECT RAISE(ABORT, 'working_hours.service_id belongs to another business')
        WHERE NEW.service_id IS NOT NULL
          AND (SELECT business_id FROM services WHERE id = NEW.service_id)
              IS NOT NEW.business_id;
    """),
    ("working_hours_service_same_business_upd", "working_hours",
     "UPDATE OF service_id, business_id", """
        SELECT RAISE(ABORT, 'working_hours.service_id belongs to another business')
        WHERE NEW.service_id IS NOT NULL
          AND (SELECT business_id FROM services WHERE id = NEW.service_id)
              IS NOT NEW.business_id;
    """),
]

EXISTING_VIOLATIONS = {
    "bookings with another business's service": """
        SELECT COUNT(*) FROM bookings JOIN services ON services.id = bookings.service_id
        WHERE services.business_id != bookings.business_id
    """,
    "bookings with another business's customer": """
        SELECT COUNT(*) FROM bookings JOIN customers ON customers.id = bookings.customer_id
        WHERE customers.business_id != bookings.business_id
    """,
    "services with another business's master": """
        SELECT COUNT(*) FROM services JOIN masters ON masters.id = services.master_id
        WHERE masters.business_id != services.business_id
    """,
    "schedules for another business's service": """
        SELECT COUNT(*) FROM working_hours
        JOIN services ON services.id = working_hours.service_id
        WHERE services.business_id != working_hours.business_id
    """,
}


def up(cursor):
    for name, table, event, body in TRIGGERS:
        cursor.execute(
            f"CREATE TRIGGER IF NOT EXISTS {name} "
            f"BEFORE {event} ON {table} FOR EACH ROW BEGIN {body} END"
        )

    for label, sql in EXISTING_VIOLATIONS.items():
        count = cursor.execute(sql).fetchone()[0]
        if count:
            print(
                f"WARNING: {count} pre-existing {label} (left as is, "
                "needs manual review)"
            )
