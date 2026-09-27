from datetime import datetime, timedelta

from timeutils import now_local
from database import get_connection, get_working_hours
from plans import daily_limit_reached


def is_daily_free_limit_reached(business_id, booking_date):
    # Ліміт діє лише для тарифу Free (1 запис на день); для Pro —
    # без обмежень. Уся логіка тарифів — у plans.py.
    return daily_limit_reached(business_id, booking_date)


def get_customer(business_id, telegram_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name, phone
        FROM customers
        WHERE business_id = ? AND telegram_id = ?
        """,
        (business_id, telegram_id)
    )

    customer = cursor.fetchone()
    conn.close()

    return customer


def get_or_create_customer(business_id, telegram_id, name=None):
    # INSERT ... ON CONFLICT DO UPDATE instead of select-then-insert:
    # two concurrent /start calls for the same Telegram user used to be
    # able to both see "no row yet" and both insert, creating duplicate
    # customers; the UNIQUE(business_id, telegram_id) constraint plus
    # this single statement make the read-then-write atomic.
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO customers (business_id, telegram_id, name)
        VALUES (?, ?, ?)
        ON CONFLICT(business_id, telegram_id) DO UPDATE SET
            name = COALESCE(excluded.name, customers.name)
        """,
        (business_id, telegram_id, name)
    )
    conn.commit()

    cursor.execute(
        """
        SELECT id FROM customers
        WHERE business_id = ? AND telegram_id = ?
        """,
        (business_id, telegram_id)
    )
    customer_id = cursor.fetchone()["id"]

    conn.close()

    return customer_id


def set_customer_phone(business_id, telegram_id, phone):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE customers
        SET phone = ?
        WHERE business_id = ? AND telegram_id = ?
        """,
        (phone, business_id, telegram_id)
    )

    conn.commit()
    conn.close()


SLOT_STEP_MINUTES = 60
DEFAULT_DURATION_MINUTES = 60


def _minutes(hhmm):
    parsed = datetime.strptime(hhmm, "%H:%M")
    return parsed.hour * 60 + parsed.minute


def _service_duration(cursor, service_id):
    row = cursor.execute(
        "SELECT duration FROM services WHERE id = ?", (service_id,)
    ).fetchone()
    return row["duration"] if row else DEFAULT_DURATION_MINUTES


def _busy_intervals(cursor, business_id, booking_date, ignore_booking_id=None):
    rows = cursor.execute(
        """
        SELECT bookings.booking_time AS time, services.duration AS duration
        FROM bookings
        JOIN services ON services.id = bookings.service_id
        WHERE bookings.business_id = ?
          AND bookings.booking_date = ?
          AND bookings.status = 'confirmed'
          AND bookings.id IS NOT ?
        """,
        (business_id, booking_date, ignore_booking_id)
    ).fetchall()

    return [
        (_minutes(row["time"]), _minutes(row["time"]) + row["duration"])
        for row in rows
    ]


def _overlaps(start, end, intervals):
    return any(
        start < busy_end and busy_start < end
        for busy_start, busy_end in intervals
    )


def _resolve_service_id(cursor, service_id, ignore_booking_id):
    # При перенесенні послуга та сама, що й у записі, який переносимо.
    if service_id is None and ignore_booking_id is not None:
        row = cursor.execute(
            "SELECT service_id FROM bookings WHERE id = ?",
            (ignore_booking_id,)
        ).fetchone()
        return row["service_id"] if row else None

    return service_id


def _day_schedule(business_id, booking_date, service_id):
    """Рядок графіка на цей день або None, якщо бізнес не працює."""
    weekday = datetime.strptime(booking_date, "%Y-%m-%d").weekday()

    day_schedule = next(
        (
            row for row in get_working_hours(business_id, service_id)
            if row["weekday"] == weekday
        ),
        None
    )

    if not day_schedule or not day_schedule["is_open"]:
        return None

    return day_schedule


def _slot_conflicts(
    cursor, business_id, booking_date, booking_time,
    service_id=None, ignore_booking_id=None
):
    service_id = _resolve_service_id(cursor, service_id, ignore_booking_id)

    start = _minutes(booking_time)
    end = start + _service_duration(cursor, service_id)

    return _overlaps(
        start, end,
        _busy_intervals(cursor, business_id, booking_date, ignore_booking_id)
    )


def is_slot_taken(
    business_id, booking_date, booking_time,
    service_id=None, ignore_booking_id=None
):
    """Чи перетинається час [початок, початок + тривалість послуги)
    з іншим підтвердженим записом цього бізнесу. ignore_booking_id —
    запис, який переносимо: сам із собою він не конфліктує."""
    conn = get_connection()

    try:
        return _slot_conflicts(
            conn.cursor(), business_id, booking_date, booking_time,
            service_id, ignore_booking_id
        )
    finally:
        conn.close()


def get_available_times(business_id, date, service_id=None):
    day_schedule = _day_schedule(business_id, date, service_id)

    if day_schedule is None:
        return None

    day_start = _minutes(day_schedule["start_time"])
    day_end = _minutes(day_schedule["end_time"])

    conn = get_connection()
    cursor = conn.cursor()
    duration = _service_duration(cursor, service_id)
    busy = _busy_intervals(cursor, business_id, date)
    conn.close()

    # На сьогодні не пропонуємо час, який уже минув.
    now = now_local()
    earliest = (
        now.hour * 60 + now.minute + 1
        if date == now.strftime("%Y-%m-%d") else day_start
    )

    # Послуга має завершитись до кінця робочого дня.
    return [
        f"{start // 60:02d}:{start % 60:02d}"
        for start in range(day_start, day_end - duration + 1, SLOT_STEP_MINUTES)
        if start >= earliest and not _overlaps(start, start + duration, busy)
    ]


def booking_time_error(
    business_id, booking_date, booking_time,
    service_id=None, ignore_booking_id=None
):
    """None, якщо на цей час можна записатись, інакше — пояснення для
    клієнта. На відміну від get_available_times(), не вимагає, щоб час
    був на сітці слотів: AI може запропонувати, наприклад, 10:30."""
    start_dt = datetime.strptime(
        f"{booking_date} {booking_time}", "%Y-%m-%d %H:%M"
    )

    if start_dt <= now_local():
        return "⏰ Цей час уже минув. Оберіть, будь ласка, інший."

    conn = get_connection()

    try:
        cursor = conn.cursor()
        service_id = _resolve_service_id(cursor, service_id, ignore_booking_id)

        day_schedule = _day_schedule(business_id, booking_date, service_id)

        if day_schedule is None:
            return "😔 У цей день ми не працюємо. Оберіть, будь ласка, інший день."

        start = _minutes(booking_time)
        duration = _service_duration(cursor, service_id)

        if (
            start < _minutes(day_schedule["start_time"])
            or start + duration > _minutes(day_schedule["end_time"])
        ):
            return (
                f"🕒 Цього дня ми працюємо з {day_schedule['start_time']} "
                f"до {day_schedule['end_time']}, а послуга триває "
                f"{duration} хв. Оберіть, будь ласка, інший час."
            )

        if _overlaps(
            start, start + duration,
            _busy_intervals(cursor, business_id, booking_date, ignore_booking_id)
        ):
            return "😔 Цей час уже зайнятий. Оберіть, будь ласка, інший."

        return None
    finally:
        conn.close()


def create_booking(business_id, customer_id, service_id, booking_date, booking_time):
    """Повертає id запису або None, якщо час уже перетинається з іншим.
    BEGIN IMMEDIATE одразу бере блокування на запис, тож перевірка
    перетину і вставка атомарні: два одночасні клієнти не можуть обидва
    пройти перевірку і записатись на час, що перетинається."""
    conn = get_connection()
    conn.isolation_level = None
    cursor = conn.cursor()

    try:
        cursor.execute("BEGIN IMMEDIATE")

        if _slot_conflicts(
            cursor, business_id, booking_date, booking_time, service_id
        ):
            return None

        cursor.execute(
            """
            INSERT INTO bookings
                (business_id, customer_id, service_id, booking_date, booking_time)
            VALUES (?, ?, ?, ?, ?)
            """,
            (business_id, customer_id, service_id, booking_date, booking_time)
        )
        booking_id = cursor.lastrowid
        cursor.execute("COMMIT")

        return booking_id
    finally:
        # Закриття з'єднання з незавершеною транзакцією її відкочує.
        conn.close()


def get_customer_bookings(business_id, customer_id):
    today = now_local().strftime("%Y-%m-%d")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT bookings.id AS id,
               services.name AS service,
               bookings.booking_date AS date,
               bookings.booking_time AS time
        FROM bookings
        JOIN services ON services.id = bookings.service_id
        WHERE bookings.business_id = ?
          AND bookings.customer_id = ?
          AND bookings.status = 'confirmed'
          AND bookings.booking_date >= ?
        ORDER BY bookings.booking_date, bookings.booking_time
        """,
        (business_id, customer_id, today)
    )

    rows = cursor.fetchall()
    conn.close()

    return rows


def get_business_bookings(business_id):
    today = now_local().strftime("%Y-%m-%d")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT bookings.id AS id,
               customers.name AS customer_name,
               services.name AS service,
               bookings.booking_date AS date,
               bookings.booking_time AS time
        FROM bookings
        JOIN customers ON customers.id = bookings.customer_id
        JOIN services ON services.id = bookings.service_id
        WHERE bookings.business_id = ?
          AND bookings.status = 'confirmed'
          AND bookings.booking_date >= ?
        ORDER BY bookings.booking_date, bookings.booking_time
        """,
        (business_id, today)
    )

    rows = cursor.fetchall()
    conn.close()

    return rows


def get_booking_with_customer(booking_id, business_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT bookings.id AS id,
               customers.telegram_id AS customer_telegram_id,
               services.name AS service,
               bookings.booking_date AS date,
               bookings.booking_time AS time
        FROM bookings
        JOIN customers ON customers.id = bookings.customer_id
        JOIN services ON services.id = bookings.service_id
        WHERE bookings.id = ? AND bookings.business_id = ?
        """,
        (booking_id, business_id)
    )

    row = cursor.fetchone()
    conn.close()

    return row


def cancel_booking(booking_id, business_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE bookings
        SET status = 'cancelled'
        WHERE id = ? AND business_id = ?
        """,
        (booking_id, business_id)
    )

    conn.commit()
    matched = cursor.rowcount > 0
    conn.close()

    return matched


def mark_booking_completed(booking_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE bookings
        SET status = 'completed'
        WHERE id = ?
        """,
        (booking_id,)
    )

    conn.commit()
    matched = cursor.rowcount > 0
    conn.close()

    return matched


def get_upcoming_bookings_needing_reminder(hours_ahead=2):
    now = now_local()
    window_end = now + timedelta(hours=hours_ahead)

    now_str = now.strftime("%Y-%m-%d %H:%M")
    window_end_str = window_end.strftime("%Y-%m-%d %H:%M")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT bookings.id AS id,
               customers.telegram_id AS customer_telegram_id,
               services.name AS service,
               bookings.booking_date AS date,
               bookings.booking_time AS time,
               businesses.id AS business_id,
               businesses.name AS business_name,
               businesses.category AS business_category
        FROM bookings
        JOIN customers ON customers.id = bookings.customer_id
        JOIN services ON services.id = bookings.service_id
        JOIN businesses ON businesses.id = bookings.business_id
        WHERE bookings.status = 'confirmed'
          AND bookings.reminder_sent = 0
          AND (bookings.booking_date || ' ' || bookings.booking_time)
              BETWEEN ? AND ?
        """,
        (now_str, window_end_str)
    )

    rows = cursor.fetchall()
    conn.close()

    return rows


def mark_reminder_sent(booking_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE bookings
        SET reminder_sent = 1
        WHERE id = ?
        """,
        (booking_id,)
    )

    conn.commit()
    conn.close()


def reschedule_booking(booking_id, business_id, new_date, new_time):
    """Як create_booking(): перевірка перетину і оновлення атомарні.
    Повертає False, якщо новий час зайнятий або запис не знайдено."""
    conn = get_connection()
    conn.isolation_level = None
    cursor = conn.cursor()

    try:
        cursor.execute("BEGIN IMMEDIATE")

        if _slot_conflicts(
            cursor, business_id, new_date, new_time,
            ignore_booking_id=booking_id
        ):
            return False

        cursor.execute(
            """
            UPDATE bookings
            SET booking_date = ?, booking_time = ?
            WHERE id = ? AND business_id = ?
            """,
            (new_date, new_time, booking_id, business_id)
        )
        matched = cursor.rowcount > 0
        cursor.execute("COMMIT")

        return matched
    finally:
        conn.close()
