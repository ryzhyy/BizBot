"""
Сповіщення власника платформи (OWNER_TELEGRAM_ID) про ключові кроки
нових користувачів — «воронка онбордингу»:

  🆕 новий користувач уперше запустив бота
  👋 перший клієнт у бізнесі
  ✍️ почав створювати бізнес (/setup)
  ⏸ почав /setup, але не завершив за 30 хв
  🏢 створив бізнес
  🧾 додав першу послугу
  🗓 уперше налаштував графік
  📅 перший запис клієнта в бізнесі

Лише факти про кроки — вміст переписки людей з ботом сюди не
потрапляє. Усі функції «безпечні»: помилка сповіщення ніколи не
ламає основну дію користувача.
"""
from database import get_connection
from business_context import get_business_by_owner
from platform_admin import OWNER_TELEGRAM_ID


SETUP_ABANDON_SECONDS = 30 * 60


def describe_user(user):
    username = f" @{user.username}" if user.username else ""
    return f"{user.full_name}{username} (id {user.id})"


async def notify_platform_owner(bot, text):
    if not OWNER_TELEGRAM_ID:
        return

    try:
        await bot.send_message(chat_id=int(OWNER_TELEGRAM_ID), text=text)
    except Exception as error:
        print("OWNER EVENT NOTIFY ERROR:", error)


def _scalar(query, params):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(query, params)
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0


def _register_first_start(telegram_id, business_id=None):
    """True, якщо людина запускає бота вперше (і запам'ятовуємо її)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO bot_users (telegram_id, first_business_id)
        VALUES (?, ?)
        """,
        (telegram_id, business_id)
    )
    is_new = cursor.rowcount == 1
    conn.commit()
    conn.close()
    return is_new


# ---------- /start ----------

async def on_start(bot, user, business=None):
    try:
        is_new = _register_first_start(
            user.id, business["id"] if business else None
        )

        if not is_new:
            return

        if business is None:
            await notify_platform_owner(
                bot,
                "🆕 Новий користувач запустив бота\n\n"
                f"👤 {describe_user(user)}\n\n"
                "Схоже на потенційного власника бізнесу — "
                "наступний крок для нього: /setup"
            )
            return

        # Нові клієнти бізнесів — лише про першого клієнта кожного
        # бізнесу, інакше з ростом платформи це стане спамом.
        clients_count = _scalar(
            "SELECT COUNT(*) FROM bot_users WHERE first_business_id = ?",
            (business["id"],)
        )

        if clients_count == 1:
            await notify_platform_owner(
                bot,
                f"👋 Перший клієнт у бізнесі «{business['name']}»\n\n"
                f"👤 {describe_user(user)} відкрив(ла) посилання бізнесу"
            )
    except Exception as error:
        print("ON START EVENT ERROR:", error)


# ---------- /setup ----------

def _setup_job_name(user_id):
    return f"setup_abandon_check_{user_id}"


async def on_setup_started(context, user):
    try:
        await notify_platform_owner(
            context.bot,
            f"✍️ {describe_user(user)} почав(ла) створювати бізнес (/setup)"
        )

        job_queue = context.job_queue

        if job_queue is None:
            return

        # Якщо людина почала /setup кілька разів — лишаємо одну перевірку.
        for job in job_queue.get_jobs_by_name(_setup_job_name(user.id)):
            job.schedule_removal()

        job_queue.run_once(
            _check_setup_abandoned,
            when=SETUP_ABANDON_SECONDS,
            name=_setup_job_name(user.id),
            user_id=user.id,
            chat_id=user.id,
            data={"who": describe_user(user)},
        )
    except Exception as error:
        print("ON SETUP STARTED EVENT ERROR:", error)


async def _check_setup_abandoned(context):
    try:
        job = context.job

        if get_business_by_owner(job.user_id):
            return

        user_data = context.user_data or {}

        if user_data.get("setup_category"):
            step = "на кроці «місто» (назву й категорію вже ввів)"
        elif user_data.get("setup_name"):
            step = "на кроці «категорія» (назву вже ввів)"
        else:
            step = "на першому кроці «назва бізнесу» або скасував"

        await notify_platform_owner(
            context.bot,
            "⏸ Не завершив створення бізнесу\n\n"
            f"👤 {job.data['who']}\n"
            f"Почав /setup 30 хв тому й зупинився {step}.\n\n"
            "Можливо, варто написати й допомогти."
        )
    except Exception as error:
        print("SETUP ABANDON CHECK ERROR:", error)


async def on_business_created(context, user, name, category, city):
    try:
        if context.job_queue is not None:
            for job in context.job_queue.get_jobs_by_name(
                _setup_job_name(user.id)
            ):
                job.schedule_removal()

        await notify_platform_owner(
            context.bot,
            f"🏢 Створено бізнес «{name}»\n\n"
            f"🏷 {category}\n"
            f"📍 {city}\n"
            f"👤 {describe_user(user)}\n\n"
            "Далі: послуги (/addservice) і графік (/schedule)."
        )
    except Exception as error:
        print("ON BUSINESS CREATED EVENT ERROR:", error)


# ---------- послуги / графік / записи ----------

async def on_service_added(bot, user, business_id, service_name, price):
    try:
        count = _scalar(
            "SELECT COUNT(*) FROM services WHERE business_id = ?",
            (business_id,)
        )

        if count != 1:
            return

        business_name = _business_name(business_id)

        await notify_platform_owner(
            bot,
            f"🧾 «{business_name}» додав(ла) першу послугу\n\n"
            f"{service_name} — {price} грн\n"
            f"👤 {describe_user(user)}"
        )
    except Exception as error:
        print("ON SERVICE ADDED EVENT ERROR:", error)


def business_has_schedule(business_id):
    try:
        return _scalar(
            "SELECT COUNT(*) FROM working_hours WHERE business_id = ?",
            (business_id,)
        ) > 0
    except Exception:
        return True  # у разі сумніву — не шлемо «перший графік»


async def on_first_schedule(bot, user, business_id):
    try:
        await notify_platform_owner(
            bot,
            f"🗓 «{_business_name(business_id)}» уперше налаштував(ла) "
            "графік роботи\n\n"
            f"👤 {describe_user(user)}"
        )
    except Exception as error:
        print("ON FIRST SCHEDULE EVENT ERROR:", error)


async def on_booking_created(bot, business, customer_name,
                             service_name, date, time):
    try:
        count = _scalar(
            "SELECT COUNT(*) FROM bookings WHERE business_id = ?",
            (business["id"],)
        )

        if count != 1:
            return

        await notify_platform_owner(
            bot,
            f"📅 Перший запис у бізнесі «{business['name']}»! 🎉\n\n"
            f"👤 {customer_name}\n"
            f"✂️ {service_name}\n"
            f"🗓 {date} о {time}"
        )
    except Exception as error:
        print("ON BOOKING CREATED EVENT ERROR:", error)


def _business_name(business_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM businesses WHERE id = ?", (business_id,))
    row = cursor.fetchone()
    conn.close()
    return row["name"] if row else f"id {business_id}"
