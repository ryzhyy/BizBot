from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from conversation_utils import interrupt_handlers
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from database import get_connection
from plans import (
    FREE_MAX_SERVICES,
    can_add_service,
    hidden_service_ids,
    pro_required_text,
)
from owner_events import on_service_added
from masters import (
    MASTER_QUESTION_TEXT,
    NEW_MASTER_PROMPT_TEXT,
    NO_MASTER_LABEL,
    clean_master_name,
    get_master,
    get_or_create_master,
    master_picker_keyboard,
)


(
    SERVICE_NAME, SERVICE_PRICE, SERVICE_DURATION,
    SERVICE_MASTER, SERVICE_MASTER_NAME,
) = range(5)

SERVICE_FLOW_KEYS = (
    "service_business_id",
    "service_name",
    "service_price",
    "service_duration",
)


def get_owner_business(owner_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name
        FROM businesses
        WHERE owner_telegram_id = ?
        """,
        (owner_id,)
    )

    business = cursor.fetchone()
    conn.close()

    return business


async def addservice_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business = get_owner_business(
        update.effective_user.id
    )

    if not business:
        await update.message.reply_text(
            "⚠️ Спочатку створіть бізнес через /setup."
        )
        return ConversationHandler.END

    if not can_add_service(business["id"]):
        await update.message.reply_text(
            f"⚠️ На тарифі Free можна мати до {FREE_MAX_SERVICES} послуг, "
            "і цей ліміт уже досягнуто.\n\n"
            + pro_required_text("Необмежена кількість послуг")
        )
        return ConversationHandler.END

    context.user_data["service_business_id"] = business["id"]

    await update.message.reply_text(
        f"🏢 {business['name']}\n\n"
        "Яку послугу хочете додати?"
    )

    return SERVICE_NAME


async def service_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["service_name"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(
        "💰 Вкажіть ціну в гривнях.\n\n"
        "Наприклад: 600"
    )

    return SERVICE_PRICE


async def service_price(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = update.message.text.strip()

    try:
        price = int(text)
    except ValueError:
        await update.message.reply_text(
            "⚠️ Введіть тільки число.\n"
            "Наприклад: 600"
        )
        return SERVICE_PRICE

    if price < 0:
        await update.message.reply_text(
            "⚠️ Ціна не може бути від'ємною."
        )
        return SERVICE_PRICE

    context.user_data["service_price"] = price

    await update.message.reply_text(
        "⏱ Скільки хвилин триває послуга?\n\n"
        "Наприклад: 60"
    )

    return SERVICE_DURATION


async def service_duration(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = update.message.text.strip()

    try:
        duration = int(text)
    except ValueError:
        await update.message.reply_text(
            "⚠️ Введіть кількість хвилин числом.\n"
            "Наприклад: 60"
        )
        return SERVICE_DURATION

    if duration <= 0:
        await update.message.reply_text(
            "⚠️ Тривалість повинна бути більшою за 0."
        )
        return SERVICE_DURATION

    context.user_data["service_duration"] = duration

    await update.message.reply_text(
        MASTER_QUESTION_TEXT,
        reply_markup=master_picker_keyboard(
            context.user_data["service_business_id"], "addsvc_master_"
        )
    )

    return SERVICE_MASTER


async def service_master_pick(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    choice = query.data.removeprefix("addsvc_master_")

    if choice == "new":
        await query.message.reply_text(NEW_MASTER_PROMPT_TEXT)
        return SERVICE_MASTER_NAME

    master_id = None

    if choice != "none":
        master = get_master(
            context.user_data["service_business_id"], int(choice)
        )
        if master is None:
            await query.message.reply_text(
                "⚠️ Майстра не знайдено. Оберіть ще раз."
            )
            return SERVICE_MASTER
        master_id = master["id"]

    return await save_service(
        query.message, update.effective_user, context, master_id
    )


async def service_master_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    name = clean_master_name(update.message.text)

    if name is None:
        await update.message.reply_text(NEW_MASTER_PROMPT_TEXT)
        return SERVICE_MASTER_NAME

    master = get_or_create_master(
        context.user_data["service_business_id"], name
    )

    return await save_service(
        update.message, update.effective_user, context, master["id"]
    )


async def save_service(message, user, context, master_id):
    business_id = context.user_data["service_business_id"]
    name = context.user_data["service_name"]
    price = context.user_data["service_price"]
    duration = context.user_data["service_duration"]

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO services
        (business_id, name, price, duration, master_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            business_id,
            name,
            price,
            duration,
            master_id
        )
    )

    conn.commit()
    conn.close()

    await on_service_added(
        context.bot, user, business_id, name, price
    )

    for key in SERVICE_FLOW_KEYS:
        context.user_data.pop(key, None)

    master = get_master(business_id, master_id) if master_id else None

    keyboard = [
        [
            InlineKeyboardButton(
                "➕ Додати ще послугу",
                callback_data="setup_add_service"
            )
        ],
        [
            InlineKeyboardButton(
                "✅ Завершити налаштування",
                callback_data="setup_finish"
            )
        ]
    ]

    await message.reply_text(
        "✅ Послугу додано!\n\n"
        f"✂️ {name}\n"
        f"💰 {price} грн\n"
        f"⏱ {duration} хв\n"
        f"👤 {master['name'] if master else NO_MASTER_LABEL}\n\n"
        "Що робимо далі? 👇",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return ConversationHandler.END


async def services_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business = get_owner_business(
        update.effective_user.id
    )

    if not business:
        await update.message.reply_text(
            "⚠️ Спочатку створіть бізнес через /setup."
        )
        return

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT services.id AS id, services.name AS name,
               services.price AS price, services.duration AS duration,
               masters.name AS master
        FROM services
        LEFT JOIN masters ON masters.id = services.master_id
        WHERE services.business_id = ?
          AND services.active = 1
        ORDER BY services.id
        """,
        (business["id"],)
    )

    services = cursor.fetchall()
    conn.close()

    if not services:
        await update.message.reply_text(
            "📭 Послуг ще немає.\n"
            "Додайте першу через /addservice."
        )
        return

    text = f"🏢 {business['name']}\n\n📋 Послуги:\n\n"

    hidden_ids = set(hidden_service_ids(business["id"]))

    for service in services:
        hidden_mark = (
            "🔒 приховано від клієнтів (Free)\n"
            if service["id"] in hidden_ids else ""
        )
        text += (
            f"#{service['id']} — {service['name']}\n"
            f"{hidden_mark}"
            f"💰 {service['price']} грн\n"
            f"⏱ {service['duration']} хв\n"
            f"👤 {service['master'] or NO_MASTER_LABEL}\n\n"
        )

    keyboard = [
        [InlineKeyboardButton(
            f"👤 Майстер для «{service['name']}»",
            callback_data=f"svcmaster_{service['id']}"
        )]
        for service in services
    ]

    await update.message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def addservice_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    for key in SERVICE_FLOW_KEYS:
        context.user_data.pop(key, None)

    await update.message.reply_text(
        "❌ Додавання послуги скасовано."
    )

    return ConversationHandler.END


def get_addservice_handler():
    interrupts = interrupt_handlers(
        cleanup_keys=SERVICE_FLOW_KEYS,
        action_name="Додавання послуги"
    )

    return ConversationHandler(
        entry_points=[
            CommandHandler(
                "addservice",
                addservice_start
            ),
            MessageHandler(
                filters.Text(["➕ Додати послугу"]),
                addservice_start
            ),
        ],
        states={
            SERVICE_NAME: [
                *interrupts,
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    service_name
                )
            ],
            SERVICE_PRICE: [
                *interrupts,
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    service_price
                )
            ],
            SERVICE_DURATION: [
                *interrupts,
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    service_duration
                )
            ],
            SERVICE_MASTER: [
                *interrupts,
                CallbackQueryHandler(
                    service_master_pick,
                    pattern=r"^addsvc_master_(\d+|new|none)$"
                ),
            ],
            SERVICE_MASTER_NAME: [
                *interrupts,
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    service_master_name
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                addservice_cancel
            ),
            *interrupts,
        ],
    )


def get_services_handler():
    return CommandHandler(
        "services",
        services_list
    )