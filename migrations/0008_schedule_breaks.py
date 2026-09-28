"""Optional break inside a working day (e.g. lunch 13:00-14:00).

A day's schedule was a single start-end interval, so a master working
09:00-18:00 with a lunch break had no way to say so and clients could
book right into it. Both columns NULL means no break.
"""


def up(cursor):
    cursor.execute("PRAGMA table_info(working_hours)")
    columns = {row[1] for row in cursor.fetchall()}

    for column in ("break_start", "break_end"):
        if column not in columns:
            cursor.execute(
                f"ALTER TABLE working_hours ADD COLUMN {column} TEXT DEFAULT NULL"
            )
