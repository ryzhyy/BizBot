"""Index for the reminder job.

Every 15 minutes the job looks for confirmed bookings in the next two
hours that haven't had a reminder yet. It used to filter on
booking_date || ' ' || booking_time, an expression no index can serve,
so each run scanned the whole bookings table, which only grows. This
partial index holds just the bookings still waiting for a reminder, so
it stays small however large the table gets.
"""


def up(cursor):
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bookings_pending_reminder "
        "ON bookings(booking_date, booking_time) "
        "WHERE status = 'confirmed' AND reminder_sent = 0"
    )
