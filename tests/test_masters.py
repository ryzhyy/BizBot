import pytest

from conftest import MONDAY

import bookings
import database
import masters


@pytest.fixture
def salon(business):
    """On top of the shared business (services 1 and 2 have no master):
    Оля does a 60-min cut (3) and a 90-min colouring (4), Марина does a
    60-min manicure (5)."""
    olia = masters.get_or_create_master(1, "Оля")["id"]
    maryna = masters.get_or_create_master(1, "Марина")["id"]

    conn = database.get_connection()
    conn.execute(
        "INSERT INTO services (id, business_id, name, price, duration, master_id) "
        "VALUES (3, 1, 'Стрижка', 300, 60, ?), (4, 1, 'Фарбування', 900, 90, ?), "
        "(5, 1, 'Манікюр', 400, 60, ?)",
        (olia, olia, maryna)
    )
    conn.commit()
    conn.close()
    return {"olia": olia, "maryna": maryna}


def book(service_id, time, telegram_id):
    customer_id = bookings.get_or_create_customer(1, telegram_id, "Клієнт")
    return bookings.create_booking(1, customer_id, service_id, MONDAY, time)


def test_different_masters_work_in_parallel(salon, frozen_now):
    assert book(3, "10:00", 1)
    assert book(5, "10:00", 2)


def test_one_master_cannot_take_overlapping_services(salon, frozen_now):
    assert book(4, "10:00", 1)          # Оля: 10:00–11:30
    assert book(3, "11:00", 2) is None  # Оля again, overlaps
    assert book(3, "11:30", 3)


def test_masters_do_not_block_the_shared_queue(salon, frozen_now):
    assert book(3, "10:00", 1)
    assert book(2, "10:00", 2)          # no master
    assert book(1, "10:30", 3) is None  # no master, overlaps service 2


def test_available_times_only_see_the_same_masters_bookings(salon, frozen_now):
    book(4, "10:00", 1)                 # Оля busy 10:00–11:30

    assert "10:00" in bookings.get_available_times(1, MONDAY, 5)
    olia_times = bookings.get_available_times(1, MONDAY, 3)
    assert "10:00" not in olia_times and "11:00" not in olia_times
    assert "12:00" in olia_times


def test_booking_time_error_uses_master_queue(salon, frozen_now):
    book(3, "10:00", 1)

    assert bookings.booking_time_error(1, MONDAY, "10:00", 5) is None
    assert "зайнятий" in bookings.booking_time_error(1, MONDAY, "10:30", 4)


def test_reschedule_checks_the_bookings_own_master(salon, frozen_now):
    manicure = book(5, "10:00", 1)
    book(3, "12:00", 2)                 # Оля at 12:00

    assert bookings.reschedule_booking(manicure, 1, MONDAY, "12:00")


def test_free_limit_is_per_business_not_per_master(salon, free_business, frozen_now):
    assert book(3, "10:00", 1)
    assert book(5, "10:00", 2) is None


def test_master_names_are_unique_per_business_ignoring_case(salon):
    assert masters.get_or_create_master(1, "оля")["id"] == salon["olia"]
    assert [m["name"] for m in masters.get_masters(1)] == ["Марина", "Оля"]


def test_cannot_assign_a_master_from_another_business(salon, db):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO businesses (id, owner_telegram_id, name, slug) "
        "VALUES (2, 200, 'Інший', 'other')"
    )
    conn.commit()
    conn.close()
    stranger = masters.get_or_create_master(2, "Чужий")["id"]

    assert not masters.set_service_master(1, 3, stranger)
    assert masters.set_service_master(1, 3, salon["maryna"])
    assert masters.get_service_master_name(3) == "Марина"
    assert masters.set_service_master(1, 3, None)
    assert masters.get_service_master_name(3) is None


def test_deleting_a_master_moves_services_to_shared_queue(salon):
    conn = database.get_connection()
    conn.execute("DELETE FROM masters WHERE id = ?", (salon["olia"],))
    conn.commit()
    master_id = conn.execute(
        "SELECT master_id FROM services WHERE id = 3"
    ).fetchone()[0]
    conn.close()

    assert master_id is None


def test_owner_booking_list_shows_master(salon, frozen_now, monkeypatch):
    monkeypatch.setattr(bookings, "now_local", lambda: frozen_now)
    book(3, "10:00", 1)
    book(2, "12:00", 2)

    rows = bookings.get_business_bookings(1)
    assert [(row["service"], row["master"]) for row in rows] == [
        ("Стрижка", "Оля"), ("Стрижка", None),
    ]


@pytest.mark.parametrize("raw, expected", [
    ("  Оля  ", "Оля"),
    ("Анна   Марія", "Анна Марія"),
    ("", None),
    ("x" * 41, None),
])
def test_clean_master_name(raw, expected):
    assert masters.clean_master_name(raw) == expected
