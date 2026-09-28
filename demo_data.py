"""Демо-бізнеси: готові набори даних для ручного тестування бота.

Код тут нічого не знає про конкретний салон — він бере опис бізнесу
(DEMO_BUSINESSES) і створює його тими ж функціями, що й бот, коли
власник налаштовує все вручну: бізнес, майстри, послуги майстрів,
графік кожного майстра з перервою. Новий демо-бізнес = новий запис у
DEMO_BUSINESSES, без змін у коді.

Створити демо-бізнес:
  • у боті (лише власник платформи): /demo beauty-studio <telegram_id>
  • з консолі: python demo_data.py beauty-studio --owner <telegram_id>

telegram_id — акаунт, який стане власником демо-бізнесу. Один акаунт
може мати лише один бізнес, тож для демо потрібен окремий акаунт.
"""
import argparse
import logging

from telegram.ext import CommandHandler

import database
import masters
import plans
import setup

logger = logging.getLogger(__name__)

# Демо показує всі послуги й усіх майстрів, а на Free видно лише
# plans.FREE_MAX_SERVICES послуг, тож демо-бізнес отримує Pro.
DEMO_PRO_DAYS = 30

WORKDAY_WITH_LUNCH = ("09:00", "18:00", "13:00", "14:00")
DAY_OFF = None

# Пн–Пт 09:00–18:00, обід 13:00–14:00; Сб, Нд — вихідні.
# Індекси — дні тижня (0 = Пн), як у working_hours.weekday.
WEEKDAYS_WITH_LUNCH = [WORKDAY_WITH_LUNCH] * 5 + [DAY_OFF] * 2

DEMO_BUSINESSES = {
    "beauty-studio": {
        "name": "Beauty Studio",
        "category": "Салон краси",
        "city": "Київ",
        # Графік бізнесу за замовчуванням; кожен майстер отримує власну
        # копію, яку власник потім може змінити окремо (/schedule).
        "schedule": WEEKDAYS_WITH_LUNCH,
        "masters": [
            {
                "name": "Анна",
                "services": [
                    ("💇‍♀️ Жіноча стрижка", 700, 60),
                    ("🎨 Фарбування волосся", 1500, 120),
                ],
            },
            {
                "name": "Марія",
                "services": [("💅 Манікюр", 600, 60)],
            },
            {
                "name": "Софія",
                "services": [("👁️ Оформлення брів", 450, 40)],
            },
            {
                "name": "Олена",
                "services": [("💆‍♀️ Масаж обличчя", 800, 60)],
            },
        ],
    },
}


def _set_schedule(business_id, schedule, master_id=None):
    for weekday, day in enumerate(schedule):
        if day is DAY_OFF:
            database.set_working_hours(
                business_id, weekday, None, None, 0, master_id=master_id
            )
            continue

        start, end, break_start, break_end = day
        database.set_working_hours(
            business_id, weekday, start, end, 1, master_id=master_id,
            break_start=break_start, break_end=break_end
        )


def _add_service(business_id, name, price, duration, master_id):
    conn = database.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO services
            (business_id, name, price, duration, master_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (business_id, name, price, duration, master_id)
        )
        conn.commit()
    finally:
        conn.close()


def _delete_business(business_id):
    conn = database.get_connection()
    try:
        # Послуги, майстри й графіки видаляються каскадом.
        conn.execute("DELETE FROM businesses WHERE id = ?", (business_id,))
        conn.commit()
    finally:
        conn.close()


def seed_business(owner_telegram_id, spec, pro_days=DEMO_PRO_DAYS):
    """Створити бізнес за описом spec. Повертає id бізнесу або None,
    якщо в цього власника вже є бізнес."""
    business_id = setup.create_business(
        owner_telegram_id, spec["name"], spec["category"], spec["city"]
    )
    if business_id is None:
        return None

    try:
        _set_schedule(business_id, spec["schedule"])

        for master_spec in spec["masters"]:
            master = masters.get_or_create_master(
                business_id, master_spec["name"]
            )
            _set_schedule(
                business_id,
                master_spec.get("schedule", spec["schedule"]),
                master_id=master["id"]
            )
            for name, price, duration in master_spec["services"]:
                _add_service(business_id, name, price, duration, master["id"])

        if pro_days:
            plans.extend_pro(business_id, pro_days)
    except Exception:
        # Напівстворений демо-бізнес гірший за відсутній.
        _delete_business(business_id)
        raise

    return business_id


def get_business_slug(business_id):
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT slug FROM businesses WHERE id = ?", (business_id,)
        ).fetchone()
        return row["slug"] if row else None
    finally:
        conn.close()


# ---------- команда для власника платформи ----------

DEMO_USAGE = (
    "Використання: /demo <назва> <telegram_id власника>\n\n"
    "Доступні демо: " + ", ".join(sorted(DEMO_BUSINESSES)) + "\n\n"
    "Один акаунт може мати лише один бізнес, тож власником демо "
    "має бути окремий Telegram-акаунт."
)


async def demo_command(update, context):
    # Імпорт тут, щоб модуль можна було запускати з консолі без
    # OWNER_TELEGRAM_ID та інших налаштувань бота.
    from platform_admin import is_platform_owner

    if not is_platform_owner(update.effective_user.id):
        await update.message.reply_text(
            "⛔ Ця команда доступна лише власнику платформи."
        )
        return

    args = context.args or []
    spec = DEMO_BUSINESSES.get(args[0]) if args else None

    try:
        owner_id = int(args[1]) if len(args) == 2 else None
    except ValueError:
        owner_id = None

    if spec is None or owner_id is None:
        await update.message.reply_text(DEMO_USAGE)
        return

    business_id = seed_business(owner_id, spec)

    if business_id is None:
        await update.message.reply_text(
            "⚠️ У цього акаунта вже є бізнес. Вкажіть інший telegram_id."
        )
        return

    logger.info("Demo business %s created for owner %s", business_id, owner_id)

    link = (
        f"https://t.me/{context.bot.username}"
        f"?start={get_business_slug(business_id)}"
    )
    await update.message.reply_text(
        f"✅ Демо-бізнес «{spec['name']}» створено (ID {business_id}).\n\n"
        f"🔗 Посилання для клієнтів:\n{link}"
    )


def get_demo_handler():
    return CommandHandler("demo", demo_command)


def main():
    parser = argparse.ArgumentParser(description="Створити демо-бізнес")
    parser.add_argument("demo", choices=sorted(DEMO_BUSINESSES))
    parser.add_argument("--owner", type=int, required=True,
                        help="Telegram ID власника демо-бізнесу")
    parser.add_argument("--pro-days", type=int, default=DEMO_PRO_DAYS)
    args = parser.parse_args()

    database.init_database()
    business_id = seed_business(
        args.owner, DEMO_BUSINESSES[args.demo], pro_days=args.pro_days
    )

    if business_id is None:
        raise SystemExit("У цього власника вже є бізнес.")

    print(
        f"✅ Створено бізнес {business_id}, "
        f"посилання: ?start={get_business_slug(business_id)}"
    )


if __name__ == "__main__":
    main()
