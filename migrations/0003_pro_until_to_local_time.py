"""Re-express stored Pro timestamps in the bot's local time zone.

pro_until and the two notification markers used to be written with the
server's datetime.now(), and the production server runs in UTC. The app
now reads and writes them as BOT_TIMEZONE wall-clock time, so without
this shift every Pro subscription would end 2-3 hours early. The markers
are converted with the same function as pro_until so that the
"already notified for this pro_until" equality checks keep matching.
"""

from datetime import datetime, timezone

from timeutils import BOT_TIMEZONE

TS_FORMAT = "%Y-%m-%d %H:%M:%S"
COLUMNS = ("pro_until", "pro_warning_sent_for", "pro_expired_notified_for")


def _utc_to_local(value):
    try:
        parsed = datetime.strptime(value, TS_FORMAT)
    except (TypeError, ValueError):
        return value

    local = parsed.replace(tzinfo=timezone.utc).astimezone(BOT_TIMEZONE)
    return local.replace(tzinfo=None).strftime(TS_FORMAT)


def up(cursor):
    cursor.execute(
        f"SELECT id, {', '.join(COLUMNS)} FROM businesses "
        "WHERE pro_until IS NOT NULL "
        "OR pro_warning_sent_for IS NOT NULL "
        "OR pro_expired_notified_for IS NOT NULL"
    )

    for row in cursor.fetchall():
        cursor.execute(
            f"UPDATE businesses SET {' = ?, '.join(COLUMNS)} = ? WHERE id = ?",
            (*(_utc_to_local(row[column]) for column in COLUMNS), row["id"])
        )
