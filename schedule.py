from datetime import datetime

from telegram import Update
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from database import (
    set_working_hours,
    get_working_hours,
    DAY_NAMES,
)
from business_context import get_business_by_owner

WAITING_SCHEDULE = 1


async def schedule_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business = get_business_by_owner(
        update.effective_user.id
    )

    if not business:
        await update.message.reply_text(
            "⚠️ У вас ще немає бізнесу.\n"
            "Спочатку використайте /setup."
        )
        return ConversationHandler.END

    context.user_data["schedule_business_id"] = business["id"]

    await update.message.reply_text(
        "🗓 Налаштування графіка\n\n"
        "Надішліть графік одним повідомленням:\n\n"
        "Пн 09:00-18:00\n"
        "Вт 09:00-18:00\n"
        "Ср 09:00-18:00\n"
        "Чт 09:00-18:00\n"
        "Пт 09:00-18:00\n"
        "Сб 10:00-16:00\n"
        "Нд вихідний\n\n"
        "Можна вказати «вихідний» для закритого дня."
    )

    return WAITING_SCHEDULE


async def save_schedule(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business_id = context.user_data.get(
        "schedule_business_id"
    )

    lines = update.message.text.strip().splitlines()

    day_map = {
        "пн": 0,
        "вт": 1,
        "ср": 2,
        "чт": 3,
        "пт": 4,
        "сб": 5,
        "нд": 6,
    }

    saved = 0
    errors = []

    try:
        for line in lines:
            parts = line.strip().split(maxsplit=1)

            if len(parts) != 2:
                continue

            day_text = parts[0].lower().rstrip(".:")
            value = parts[1].strip()

            if day_text not in day_map:
                continue

            weekday = day_map[day_text]

            if value.lower() == "вихідний":
                set_working_hours(
                    business_id,
                    weekday,
                    None,
                    None,
                    0
                )
                saved += 1
                continue

            normalized = (
                value
                .replace("–", "-")
                .replace("—", "-")
            )

            time_parts = [
                x.strip()
                for x in normalized.split("-", 1)
            ]

            if len(time_parts) != 2:
                errors.append(DAY_NAMES[weekday])
                continue

            start_time, end_time = time_parts

            try:
                datetime.strptime(start_time, "%H:%M")
                datetime.strptime(end_time, "%H:%M")
            except ValueError:
                errors.append(DAY_NAMES[weekday])
                continue

            set_working_hours(
                business_id,
                weekday,
                start_time,
                end_time,
                1
            )

            saved += 1

    except Exception as error:
        print("SCHEDULE ERROR:", error)

        await update.message.reply_text(
            "⚠️ Не вдалося розпізнати графік.\n\n"
            "Приклад:\n"
            "Пн 09:00-18:00\n"
            "Вт 09:00-18:00\n"
            "Нд вихідний"
        )

        return WAITING_SCHEDULE

    if saved == 0 and not errors:
        await update.message.reply_text(
            "⚠️ Я не знайшов жодного дня.\n\n"
            "Напишіть, наприклад:\n"
            "Пн 09:00-18:00"
        )
        return WAITING_SCHEDULE

    if errors:
        await update.message.reply_text(
            "⚠️ Не вдалося розпізнати графік для: "
            f"{', '.join(errors)}\n"
            "Перевірте формат — приклад: Пн 09:00-18:00"
        )

    if saved == 0:
        return WAITING_SCHEDULE

    rows = get_working_hours(business_id)

    result = ["✅ Графік збережено!\n"]

    for row in rows:
        day = DAY_NAMES[row["weekday"]]

        if row["is_open"]:
            result.append(
                f"{day}: {row['start_time']}–{row['end_time']}"
            )
        else:
            result.append(
                f"{day}: вихідний"
            )

    await update.message.reply_text(
        "\n".join(result)
    )

    return ConversationHandler.END


async def schedule_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text("❌ Скасовано.")
    return ConversationHandler.END


def get_schedule_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler(
                "schedule",
                schedule_start
            )
        ],
        states={
            WAITING_SCHEDULE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    save_schedule
                )
            ]
        },
        fallbacks=[
            CommandHandler("cancel", schedule_cancel),
        ],
    )