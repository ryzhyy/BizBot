import concurrent.futures

from conftest import MONDAY, SUNDAY

import bookings


def book(time, service_id=1, telegram_id=1, date=MONDAY):
    customer_id = bookings.get_or_create_customer(1, telegram_id, "Клієнт")
    return bookings.create_booking(1, customer_id, service_id, date, time)


def test_available_times_respect_duration_and_closing(business, frozen_now):
    assert bookings.get_available_times(1, MONDAY, 1) == [
        "09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00",
    ]


def test_long_booking_blocks_following_hour(business, frozen_now):
    assert book("10:00", service_id=1)

    assert bookings.get_available_times(1, MONDAY, 2) == [
        "09:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00",
    ]
    assert bookings.is_slot_taken(1, MONDAY, "11:00", 2)
    assert bookings.is_slot_taken(1, MONDAY, "11:29", 2)
    assert not bookings.is_slot_taken(1, MONDAY, "11:30", 2)


def test_closed_day_has_no_times(business, frozen_now):
    assert bookings.get_available_times(1, SUNDAY, 2) is None


def test_today_skips_past_hours(business, monkeypatch):
    from datetime import datetime

    monkeypatch.setattr(
        bookings, "now_local", lambda: datetime(2026, 10, 5, 13, 20)
    )
    assert bookings.get_available_times(1, MONDAY, 2) == [
        "14:00", "15:00", "16:00", "17:00",
    ]


def test_create_booking_rejects_overlap(business, frozen_now):
    assert book("10:00", service_id=1)
    assert book("11:00", service_id=2, telegram_id=2) is None
    assert book("11:30", service_id=2, telegram_id=2)


def test_reschedule_ignores_itself_but_not_others(business, frozen_now):
    first = book("10:00", service_id=1)
    book("12:00", service_id=2, telegram_id=2)

    assert bookings.reschedule_booking(first, 1, MONDAY, "10:30")
    assert not bookings.reschedule_booking(first, 1, MONDAY, "11:00")


def test_booking_time_error_cases(business, frozen_now):
    book("12:00", service_id=1)
    error = bookings.booking_time_error

    assert "минув" in error(1, "2026-10-03", "10:00", 2)
    assert "не працюємо" in error(1, SUNDAY, "10:00", 2)
    assert "09:00 до 18:00" in error(1, MONDAY, "08:00", 2)
    assert "09:00 до 18:00" in error(1, MONDAY, "23:00", 2)
    assert "90 хв" in error(1, MONDAY, "17:00", 1)
    assert error(1, MONDAY, "17:00", 2) is None
    assert error(1, MONDAY, "10:30", 2) is None
    assert "зайнятий" in error(1, MONDAY, "11:30", 2)
    assert error(1, MONDAY, "13:30", 2) is None


def test_concurrent_overlapping_bookings_never_overlap(business, frozen_now):
    times = ["14:00", "14:30", "15:00", "13:30", "14:00", "15:15"] * 2

    def attempt(i):
        return book(times[i], service_id=1, telegram_id=100 + i)

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(attempt, range(12)))

    conn = bookings.get_connection()
    starts = sorted(
        int(row[0][:2]) * 60 + int(row[0][3:])
        for row in conn.execute(
            "SELECT booking_time FROM bookings WHERE status = 'confirmed'"
        )
    )
    conn.close()

    assert starts
    assert all(b - a >= 90 for a, b in zip(starts, starts[1:]))


def test_get_or_create_customer_is_race_free(business):
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        ids = set(pool.map(
            lambda _: bookings.get_or_create_customer(1, 555, "Той самий"),
            range(20),
        ))

    assert len(ids) == 1


def test_get_or_create_customer_keeps_name_when_none_given(business):
    customer_id = bookings.get_or_create_customer(1, 7, "Іван")
    assert bookings.get_or_create_customer(1, 7, None) == customer_id
    assert bookings.get_customer(1, 7)["name"] == "Іван"
