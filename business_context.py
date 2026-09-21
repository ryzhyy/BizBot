import urllib.parse

from database import get_connection, get_working_hours


DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]


def get_business_by_owner(owner_telegram_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name, category, city, phone, owner_telegram_id,
               support_contact_mode, support_contact_value, faq_text,
               latitude, longitude, address_text, slug
        FROM businesses
        WHERE owner_telegram_id = ?
        """,
        (owner_telegram_id,)
    )

    business = cursor.fetchone()
    conn.close()

    return business


def get_business_by_id(business_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name, category, city, phone, owner_telegram_id,
               support_contact_mode, support_contact_value, faq_text,
               latitude, longitude, address_text, slug
        FROM businesses
        WHERE id = ?
        """,
        (business_id,)
    )

    business = cursor.fetchone()
    conn.close()

    return business


def get_business_by_slug(slug):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name, category, city, phone, owner_telegram_id,
               support_contact_mode, support_contact_value, faq_text,
               latitude, longitude, address_text, slug
        FROM businesses
        WHERE slug = ?
        """,
        (slug,)
    )

    business = cursor.fetchone()
    conn.close()

    return business

def get_business_services(business_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name, price, duration
        FROM services
        WHERE business_id = ?
          AND active = 1
        ORDER BY id
        """,
        (business_id,)
    )

    services = cursor.fetchall()
    conn.close()

    return services


def get_service_by_id(service_id, business_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name, price, duration
        FROM services
        WHERE id = ?
          AND business_id = ?
          AND active = 1
        """,
        (service_id, business_id)
    )

    service = cursor.fetchone()
    conn.close()

    return service


def get_or_create_service(business_id, name, price=0, duration=0):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name, price, duration
        FROM services
        WHERE business_id = ? AND name = ? AND active = 1
        """,
        (business_id, name)
    )

    service = cursor.fetchone()

    if not service:
        cursor.execute(
            """
            INSERT INTO services (business_id, name, price, duration)
            VALUES (?, ?, ?, ?)
            """,
            (business_id, name, price, duration)
        )
        conn.commit()

        cursor.execute(
            """
            SELECT id, name, price, duration
            FROM services
            WHERE id = ?
            """,
            (cursor.lastrowid,)
        )
        service = cursor.fetchone()

    conn.close()

    return service


def build_business_prompt(business_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name, category, city, phone,
               support_contact_mode, support_contact_value
        FROM businesses
        WHERE id = ?
        """,
        (business_id,)
    )

    business = cursor.fetchone()

    cursor.execute(
        """
        SELECT name, price, duration
        FROM services
        WHERE business_id = ?
          AND active = 1
        ORDER BY id
        """,
        (business_id,)
    )

    services = cursor.fetchall()

    conn.close()

    working_hours = get_working_hours(business_id)

    if not business:
        return None

    services_text = ""

    if services:
        for service in services:
            services_text += (
                f"- {service['name']}: "
                f"{service['price']} грн, "
                f"{service['duration']} хв\n"
            )
    else:
        services_text = "- Послуги ще не додані\n"

    working_hours_text = ""

    if working_hours:
        for row in working_hours:
            day = DAY_NAMES[row["weekday"]]
            if row["is_open"]:
                working_hours_text += (
                    f"- {day}: {row['start_time']}–{row['end_time']}\n"
                )
            else:
                working_hours_text += f"- {day}: вихідний\n"
    else:
        working_hours_text = "- Графік роботи ще не вказано\n"

    if (
        business["support_contact_mode"] == "manual"
        and business["support_contact_value"]
    ):
        contact = business["support_contact_value"]
    else:
        contact = business["phone"] or "не вказано"

    city = business["city"] or "не вказано"
    category = business["category"] or "не вказано"

    prompt = f"""
Ти AI-менеджер бізнесу.

ІНФОРМАЦІЯ ПРО БІЗНЕС:

Назва: {business['name']}
Категорія: {category}
Місто: {city}
Контакт: {contact}

ПОСЛУГИ:

{services_text}

ГРАФІК РОБОТИ:

{working_hours_text}

ПРАВИЛА:

- Відповідай українською.
- Спілкуйся коротко, природно та ввічливо.
- Використовуй тільки інформацію, яку отримав
  про цей бізнес.
- Не вигадуй послуги, ціни, адресу, контакт
  або години роботи.
- Якщо інформації немає, прямо скажи,
  що вона ще не вказана.
- Якщо клієнт запитує ціну, використовуй
  актуальну ціну з переліку послуг.
- Якщо клієнт хоче записатися, допоможи
  визначити потрібну послугу.
"""

    return prompt


def set_business_contact(business_id, mode, value=None):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE businesses
        SET support_contact_mode = ?, support_contact_value = ?
        WHERE id = ?
        """,
        (mode, value, business_id)
    )

    conn.commit()
    conn.close()


def set_business_faq(business_id, faq_text):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE businesses
        SET faq_text = ?
        WHERE id = ?
        """,
        (faq_text, business_id)
    )

    conn.commit()
    conn.close()


def set_business_location(
    business_id, latitude=None, longitude=None, address_text=None
):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE businesses
        SET latitude = ?, longitude = ?, address_text = ?
        WHERE id = ?
        """,
        (latitude, longitude, address_text, business_id)
    )

    conn.commit()
    conn.close()


def get_business_maps_link(business):
    if not business:
        return None

    if business["latitude"] is not None and business["longitude"] is not None:
        return (
            f"https://maps.google.com/?q="
            f"{business['latitude']},{business['longitude']}"
        )

    if business["address_text"]:
        return (
            "https://maps.google.com/?q="
            f"{urllib.parse.quote(business['address_text'])}"
        )

    return None