"""
Тарифи BizBot.

Free:
  • до 2 послуг (клієнтам видно й доступно для запису лише перші 2)
  • 1 запис на день
  • розклад роботи, AI-консультації клієнтів

Pro (599 ₴/міс, активує власник платформи вручну через /platform):
  • безліміт послуг і записів
  • локація бізнесу для клієнтів, FAQ, режим клієнта для власника,
    нагадування клієнтам

Pro зберігається як дата закінчення (businesses.pro_until). Коли Pro
закінчується, нічого не видаляється — зайве лише ховається від
клієнтів і знову з'являється, щойно Pro повернеться.

Модуль навмисно не імпортує business_context, щоб той міг імпортувати
plans без циклічного імпорту.
"""
from datetime import datetime, timedelta

from database import get_connection


FREE_MAX_SERVICES = 2
FREE_DAILY_BOOKINGS = 1
PRO_PRICE_UAH = 599
PLATFORM_CONTACT = "@kUrp1ak"
PRO_WARNING_DAYS = 3

TS_FORMAT = "%Y-%m-%d %H:%M:%S"

PRO_FEATURES_TEXT = (
    "💎 Pro — {price} ₴/міс:\n"
    "• необмежена кількість записів і послуг\n"
    "• посилання на локацію бізнесу для клієнтів\n"
    "• FAQ для клієнтів\n"
    "• перегляд бота в режимі клієнта\n"
    "• нагадування клієнтам про запис"
).format(price=PRO_PRICE_UAH)


def _business_id(business_or_id):
    if business_or_id is None:
        return None
    if isinstance(business_or_id, int):
        return business_or_id
    return business_or_id["id"]


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.strptime(ts, TS_FORMAT)
    except ValueError:
        return None


# ---------- статус тарифу ----------

def get_pro_until(business_or_id):
    business_id = _business_id(business_or_id)
    if business_id is None:
        return None

    conn = get_connection()
    row = conn.execute(
        "SELECT pro_until FROM businesses WHERE id = ?", (business_id,)
    ).fetchone()
    conn.close()

    return _parse(row["pro_until"]) if row else None


def is_pro(business_or_id):
    until = get_pro_until(business_or_id)
    return until is not None and until > datetime.now()


def pro_days_left(business_or_id):
    until = get_pro_until(business_or_id)
    if until is None or until <= datetime.now():
        return 0
    # Округлюємо вгору: 13 днів 2 год — це «ще 14 днів».
    seconds = (until - datetime.now()).total_seconds()
    return int(-(-seconds // 86400))


def plan_label(business_or_id):
    if is_pro(business_or_id):
        until = get_pro_until(business_or_id)
        return (
            f"💎 Pro до {until.strftime('%d.%m.%Y')} "
            f"(ще {pro_days_left(business_or_id)} дн.)"
        )
    return "🆓 Free"


def extend_pro(business_id, days):
    """Продовжити Pro на N днів від пізнішого з: зараз / поточний кінець."""
    now = datetime.now()
    current = get_pro_until(business_id)
    start = current if current and current > now else now
    new_until = start + timedelta(days=days)

    conn = get_connection()
    conn.execute(
        """
        UPDATE businesses
        SET pro_until = ?, pro_warning_sent_for = NULL,
            pro_expired_notified_for = NULL
        WHERE id = ?
        """,
        (new_until.strftime(TS_FORMAT), business_id)
    )
    conn.commit()
    conn.close()

    return new_until


def disable_pro(business_id):
    conn = get_connection()
    conn.execute(
        """
        UPDATE businesses
        SET pro_until = NULL, pro_warning_sent_for = NULL,
            pro_expired_notified_for = NULL
        WHERE id = ?
        """,
        (business_id,)
    )
    conn.commit()
    conn.close()


# ---------- послуги ----------

def _all_active_services(business_id):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, name, price, duration
        FROM services
        WHERE business_id = ? AND active = 1
        ORDER BY id
        """,
        (business_id,)
    ).fetchall()
    conn.close()
    return rows


def get_visible_services(business_or_id):
    """Послуги, які клієнт бачить і на які може записатися."""
    business_id = _business_id(business_or_id)
    if business_id is None:
        return []

    services = _all_active_services(business_id)

    if is_pro(business_id):
        return services

    return services[:FREE_MAX_SERVICES]


def get_visible_service_by_id(service_id, business_or_id):
    try:
        service_id = int(service_id)
    except (TypeError, ValueError):
        return None

    return next(
        (s for s in get_visible_services(business_or_id)
         if s["id"] == service_id),
        None
    )


def hidden_service_ids(business_or_id):
    business_id = _business_id(business_or_id)
    all_ids = [s["id"] for s in _all_active_services(business_id)]
    visible = {s["id"] for s in get_visible_services(business_id)}
    return [i for i in all_ids if i not in visible]


def can_add_service(business_or_id):
    business_id = _business_id(business_or_id)
    if is_pro(business_id):
        return True
    return len(_all_active_services(business_id)) < FREE_MAX_SERVICES


# ---------- записи ----------

def daily_limit_reached(business_or_id, booking_date):
    """Free: не більше FREE_DAILY_BOOKINGS підтверджених записів на дату
    (рахується по даті, на яку запис, а не коли клієнт написав)."""
    business_id = _business_id(business_or_id)

    if is_pro(business_id):
        return False

    conn = get_connection()
    count = conn.execute(
        """
        SELECT COUNT(*) FROM bookings
        WHERE business_id = ? AND booking_date = ?
          AND status = 'confirmed'
        """,
        (business_id, booking_date)
    ).fetchone()[0]
    conn.close()

    return count >= FREE_DAILY_BOOKINGS


# ---------- тексти ----------

def pro_required_text(feature):
    return (
        f"🔒 {feature} — це функція тарифу Pro.\n\n"
        f"{PRO_FEATURES_TEXT}\n\n"
        f"Щоб підключити Pro, напишіть {PLATFORM_CONTACT}"
    )


def plan_overview_text(business):
    business_id = business["id"]
    services_total = len(_all_active_services(business_id))
    hidden = len(hidden_service_ids(business_id))
    today = datetime.now().strftime("%Y-%m-%d")

    lines = [
        f"🏢 {business['name']}",
        f"Тариф: {plan_label(business_id)}",
        "",
    ]

    if is_pro(business_id):
        lines += [
            "Усі можливості відкриті:",
            PRO_FEATURES_TEXT.split("\n", 1)[1],
            "",
            f"Щоб продовжити Pro, напишіть {PLATFORM_CONTACT}",
        ]
        return "\n".join(lines)

    conn = get_connection()
    today_bookings = conn.execute(
        """
        SELECT COUNT(*) FROM bookings
        WHERE business_id = ? AND booking_date = ?
          AND status = 'confirmed'
        """,
        (business_id, today)
    ).fetchone()[0]
    conn.close()

    lines += [
        "🆓 Free:",
        f"• послуги: {min(services_total, FREE_MAX_SERVICES)}"
        f"/{FREE_MAX_SERVICES}",
        f"• записи сьогодні: {today_bookings}/{FREE_DAILY_BOOKINGS}",
        "• розклад роботи і AI-консультації клієнтів — доступні",
    ]

    if hidden:
        lines.append(
            f"\n⚠️ Приховано від клієнтів послуг: {hidden} "
            "(на Free видно лише перші 2). З Pro вони знову з'являться."
        )

    lines += [
        "",
        PRO_FEATURES_TEXT,
        "",
        f"Щоб підключити Pro, напишіть {PLATFORM_CONTACT}",
    ]

    return "\n".join(lines)


# ---------- закінчення Pro (фонова перевірка, раз на годину) ----------

async def _send(bot, chat_id, text):
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as error:
        print("PLAN NOTIFY ERROR:", error)


async def check_pro_expirations(bot):
    # Лінивий імпорт: owner_events -> platform_admin -> business_context
    # -> plans, тож імпорт угорі модуля дав би цикл.
    from owner_events import notify_platform_owner

    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id, name, owner_telegram_id, pro_until,
                   pro_warning_sent_for, pro_expired_notified_for
            FROM businesses
            WHERE pro_until IS NOT NULL
            """
        ).fetchall()
        conn.close()
    except Exception as error:
        print("PRO EXPIRATION CHECK ERROR:", error)
        return

    now = datetime.now()

    for row in rows:
        until = _parse(row["pro_until"])

        if until is None:
            continue

        if until <= now:
            if row["pro_expired_notified_for"] == row["pro_until"]:
                continue

            hidden = len(hidden_service_ids(row["id"]))
            hidden_line = (
                f"• клієнтам видно лише перші {FREE_MAX_SERVICES} послуги "
                f"(приховано: {hidden})\n" if hidden else ""
            )

            await _send(
                bot,
                row["owner_telegram_id"],
                f"⏳ Термін Pro для «{row['name']}» закінчився — бізнес "
                "переведено на тариф Free.\n\n"
                "Що змінилось для клієнтів:\n"
                f"{hidden_line}"
                f"• не більше {FREE_DAILY_BOOKINGS} запису на день\n"
                "• FAQ і посилання на локацію приховані\n"
                "• нагадування клієнтам вимкнені\n\n"
                "Усі ваші дані збережені — щойно Pro повернеться, "
                "усе запрацює як раніше.\n\n"
                f"Щоб продовжити Pro ({PRO_PRICE_UAH} ₴/міс), напишіть "
                f"{PLATFORM_CONTACT}"
            )
            await notify_platform_owner(
                bot,
                f"⏳ Pro закінчився: «{row['name']}» (id {row['id']}) "
                "тепер на Free. Варто нагадати про продовження."
            )
            _set_flag(row["id"], "pro_expired_notified_for", row["pro_until"])
            continue

        if until - now <= timedelta(days=PRO_WARNING_DAYS):
            if row["pro_warning_sent_for"] == row["pro_until"]:
                continue

            await _send(
                bot,
                row["owner_telegram_id"],
                f"⏰ Pro для «{row['name']}» діє до "
                f"{until.strftime('%d.%m.%Y %H:%M')} "
                f"(залишилось {pro_days_left(row['id'])} дн.).\n\n"
                "Після цього бізнес перейде на Free: клієнтам буде видно "
                f"лише {FREE_MAX_SERVICES} послуги, {FREE_DAILY_BOOKINGS} "
                "запис на день, без FAQ, локації й нагадувань.\n\n"
                f"Щоб продовжити, напишіть {PLATFORM_CONTACT}"
            )
            _set_flag(row["id"], "pro_warning_sent_for", row["pro_until"])


def _set_flag(business_id, column, value):
    assert column in ("pro_warning_sent_for", "pro_expired_notified_for")
    conn = get_connection()
    conn.execute(
        f"UPDATE businesses SET {column} = ? WHERE id = ?",
        (value, business_id)
    )
    conn.commit()
    conn.close()
