from datetime import datetime

from database import get_connection


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
        ORDER BY bookings.booking_date, bookings.booking_time
        """,
        (business_id,)
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
