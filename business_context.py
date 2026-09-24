import urllib.parse

from plans import get_visible_services, is_pro

from database import (
    get_connection,
    get_working_hours,
    get_own_working_hours,
    DAY_NAMES,
)


def get_all_businesses():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name, category, city, phone, owner_telegram_id,
               support_contact_mode, support_contact_value, faq_text,
               latitude, longitude, address_text, slug
        FROM businesses
        ORDER BY id
        """
    )

    businesses = cursor.fetchall()
    conn.close()

    return businesses


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
               support_contact_mode, support_contact_value, faq_text
        FROM businesses
        WHERE id = ?
        """,
        (business_id,)
    )

    business = cursor.fetchone()

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

    # Для AI — лише послуги, доступні клієнтам за тарифом (на Free — 2).
    services = get_visible_services(business_id)

    conn.close()

    working_hours = get_working_hours(business_id)

    if not business:
        return None

    def format_hours_lines(rows, prefix="- "):
        lines = ""

        for row in rows:
            day = DAY_NAMES[row["weekday"]]
            if row["is_open"]:
                lines += (
                    f"{prefix}{day}: "
                    f"{row['start_time']}–{row['end_time']}\n"
                )
            else:
                lines += f"{prefix}{day}: вихідний\n"

        return lines

    services_text = ""

    if services:
        for service in services:
            services_text += (
                f"- {service['name']}: "
                f"{service['price']} грн, "
                f"{service['duration']} хв\n"
            )

            own_hours = get_own_working_hours(
                business_id, service["id"]
            )

            if own_hours:
                services_text += (
                    "  (окремий графік саме для цієї послуги:)\n"
                )
                services_text += format_hours_lines(
                    own_hours, prefix="  "
                )
    else:
        services_text = "- Послуги ще не додані\n"

    working_hours_text = ""

    if working_hours:
        working_hours_text = format_hours_lines(working_hours)
    else:
        working_hours_text = "- Графік роботи ще не вказано\n"

    if (
        business["support_contact_mode"] == "manual"
        and business["support_contact_value"]
    ):
        contact = business["support_contact_value"]
    else:
        contact = business["phone"] or "не вказано"

    # FAQ — функція Pro: на Free AI не використовує відповіді з FAQ.
    faq_items = get_faq_items(business_id) if is_pro(business_id) else []

    if faq_items:
        faq_text = ""
        for item in faq_items:
            faq_text += (
                f"- Питання: {item['question']}\n"
                f"  Відповідь: {item['answer']}\n"
            )
    else:
        faq_text = "- FAQ ще не наповнено власником\n"

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

FAQ (відповіді, які власник підготував заздалегідь):

{faq_text}

ПРАВИЛА:

- Відповідай українською.
- Спілкуйся коротко, природно та ввічливо.
- Використовуй тільки інформацію, яку отримав
  про цей бізнес.
- Не вигадуй послуги, ціни, адресу, контакт
  або години роботи.
- Якщо в FAQ є відповідь, що стосується
  запитання клієнта, спирайся саме на неї.
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


def get_faq_items(business_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, question, answer
        FROM faq_items
        WHERE business_id = ?
        ORDER BY id
        """,
        (business_id,)
    )

    items = cursor.fetchall()
    conn.close()

    return items


def add_faq_item(business_id, question, answer):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO faq_items (business_id, question, answer)
        VALUES (?, ?, ?)
        """,
        (business_id, question, answer)
    )

    conn.commit()
    item_id = cursor.lastrowid
    conn.close()

    return item_id


def delete_faq_item(item_id, business_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM faq_items
        WHERE id = ? AND business_id = ?
        """,
        (item_id, business_id)
    )

    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()

    return deleted


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