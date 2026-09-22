from datetime import datetime, timedelta

from database import get_connection, get_working_hours


# Free-tier businesses can accept this many confirmed bookings per
# calendar day through the bot. Raise/bypass this once paid plans
# exist.
FREE_DAILY_BOOKING_LIMIT = 1


def is_daily_free_limit_reached(business_id, booking_date):
    # ТИМЧАСОВО ВИМКНЕНО: ліміт заважає повноцінно тестувати бота на
    # реальному бізнесі. Платних планів ще немає, тож поки що
    # дозволяємо необмежену кількість записів на день. Щоб повернути
    # ліміт — розкоментувати блок нижче й прибрати "return False".
    return False

    # conn = get_connection()
    # cursor = conn.cursor()
    #
    # cursor.execute(
    #     """
    #     SELECT COUNT(*) AS count FROM bookings
    #     WHERE business_id = ?
    #       AND booking_date = ?
    #       AND status = 'confirmed'
    #     """,
    #     (business_id, booking_date)
    # )
    #
    # count = cursor.fetchone()["count"]
    # conn.close()
    #
    # return count >= FREE_DAILY_BOOKING_LIMIT


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
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id FROM customers
        WHERE business_id = ? AND telegram_id = ?
        """,
        (business_id, telegram_id)
    )

    row = cursor.fetchone()

    if row:
        customer_id = row["id"]

        if name:
            cursor.execute(
                "UPDATE customers SET name = ? WHERE id = ?",
                (name, customer_id)
            )
            conn.commit()
    else:
        cursor.execute(
            """
            INSERT INTO customers (business_id, telegram_id, name)
            VALUES (?, ?, ?)
            """,
            (business_id, telegram_id, name)
        )
        conn.commit()
        customer_id = cursor.lastrowid

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


def is_slot_taken(business_id, booking_date, booking_time):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id FROM bookings
        WHERE business_id = ?
          AND booking_date = ?
          AND booking_time = ?
          AND status = 'confirmed'
        """,
        (business_id, booking_date, booking_time)
    )

    taken = cursor.fetchone() is not None
    conn.close()

    return taken


def get_taken_times(business_id, booking_date):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT booking_time FROM bookings
        WHERE business_id = ?
          AND booking_date = ?
          AND status = 'confirmed'
        """,
        (business_id, booking_date)
    )

    taken_times = {row["booking_time"] for row in cursor.fetchall()}
    conn.close()

    return taken_times


def get_available_times(business_id, date, service_id=None):
    booking_date = datetime.strptime(date, "%Y-%m-%d")
    weekday = booking_date.weekday()

    working_hours = get_working_hours(business_id, service_id)

    day_schedule = next(
        (row for row in working_hours if row["weekday"] == weekday),
        None
    )

    if not day_schedule or not day_schedule["is_open"]:
        return None

    start_time = datetime.strptime(day_schedule["start_time"], "%H:%M")
    end_time = datetime.strptime(day_schedule["end_time"], "%H:%M")

    available_times = []
    current_time = start_time

    while current_time < end_time:
        available_times.append(current_time.strftime("%H:%M"))
        current_time += timedelta(minutes=60)

    taken_times = get_taken_times(business_id, date)

    return [
        t for t in available_times
        if t not in taken_times
    ]


def create_booking(business_id, customer_id, service_id, booking_date, booking_time):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO bookings
            (business_id, customer_id, service_id, booking_date, booking_time)
        VALUES (?, ?, ?, ?, ?)
        """,
        (business_id, customer_id, service_id, booking_date, booking_time)
    )

    conn.commit()
    booking_id = cursor.lastrowid
    conn.close()

    return booking_id


def get_customer_bookings(business_id, customer_id):
    today = datetime.now().strftime("%Y-%m-%d")

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
    today = datetime.now().strftime("%Y-%m-%d")

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
    now = datetime.now()
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
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE bookings
        SET booking_date = ?, booking_time = ?
        WHERE id = ? AND business_id = ?
        """,
        (new_date, new_time, booking_id, business_id)
    )

    conn.commit()
    matched = cursor.rowcount > 0
    conn.close()

    return matched
