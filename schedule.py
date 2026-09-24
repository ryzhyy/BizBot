from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from database import (
    set_working_hours,
    get_working_hours,
    DAY_NAMES,
)
from business_context import get_business_by_owner, get_business_services
from owner_events import business_has_schedule, on_first_schedule

CHOOSE_SCOPE, CHOOSE_SERVICE, WAITING_SCHEDULE = range(1, 4)


def schedule_instructions_text(service_name=None):
    if service_name:
        intro = f"🗓 Графік для послуги «{service_name}»\n\n"
    else:
        intro = "🗓 Загальний графік бізнесу\n\n"

    return (
        intro +
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
    context.user_data["schedule_service_id"] = None
    context.user_data.pop("schedule_service_name", None)

    keyboard = [
        [InlineKeyboardButton(
            "🏢 Загальний графік бізнесу",
            callback_data="schedule_scope_business"
        )],
        [InlineKeyboardButton(
            "✂️ Графік для конкретної послуги",
            callback_data="schedule_scope_service"
        )],
    ]

    await update.message.reply_text(
        "🗓 Налаштування графіка\n\n"
        "Що налаштовуємо?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return CHOOSE_SCOPE


async def schedule_choose_scope(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    business_id = context.user_data.get("schedule_business_id")

    if query.data == "schedule_scope_service":
        services = get_business_services(business_id)

        if not services:
            await query.message.reply_text(
                "😔 У вас ще немає доданих послуг.\n"
                "Спочатку додайте послугу через "
                "«➕ Додати послугу» (або /addservice)."
            )
            return ConversationHandler.END

        keyboard = [
            [InlineKeyboardButton(
                service["name"],
                callback_data=f"schedule_svc_{service['id']}"
            )]
            for service in services
        ]

        await query.message.reply_text(
            "Для якої послуги налаштовуємо графік?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return CHOOSE_SERVICE

    context.user_data["schedule_service_id"] = None
    context.user_data.pop("schedule_service_name", None)

    await query.message.reply_text(
        schedule_instructions_text()
    )

    return WAITING_SCHEDULE


async def schedule_choose_service(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    business_id = context.user_data.get("schedule_business_id")

    service_id = int(
        query.data.replace("schedule_svc_", "")
    )

    service = next(
        (
            s for s in get_business_services(business_id)
            if s["id"] == service_id
        ),
        None
    )

    if not service:
        await query.message.reply_text(
            "⚠️ Не вдалося знайти обрану послугу. "
            "Спробуйте /schedule ще раз."
        )
        return ConversationHandler.END

    context.user_data["schedule_service_id"] = service["id"]
    context.user_data["schedule_service_name"] = service["name"]

    await query.message.reply_text(
        schedule_instructions_text(service["name"])
    )

    return WAITING_SCHEDULE


async def save_schedule(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business_id = context.user_data.get(
        "schedule_business_id"
    )

    service_id = context.user_data.get("schedule_service_id")
    service_name = context.user_data.get("schedule_service_name")

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

    # Щоб повідомити власника платформи лише про ПЕРШИЙ графік бізнесу.
    had_schedule = business_has_schedule(business_id)

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
                    0,
                    service_id
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
                1,
                service_id
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

    if not had_schedule:
        await on_first_schedule(
            context.bot, update.effective_user, business_id
        )

    rows = get_working_hours(business_id, service_id)

    if service_id:
        header = f"✅ Графік для «{service_name}» збережено!\n"
    else:
        header = "✅ Загальний графік бізнесу збережено!\n"

    result = [header]

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
            CHOOSE_SCOPE: [
                CallbackQueryHandler(
                    schedule_choose_scope,
                    pattern="^schedule_scope_"
                )
            ],
            CHOOSE_SERVICE: [
                CallbackQueryHandler(
                    schedule_choose_service,
                    pattern="^schedule_svc_"
                )
            ],
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
