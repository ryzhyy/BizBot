from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from business_context import (
    get_business_by_owner,
    set_business_location,
)


LOCATION_CHOOSE_MODE, LOCATION_WAITING_GEO, LOCATION_WAITING_ADDRESS = range(3)

REQUEST_LOCATION_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📍 Надіслати геолокацію", request_location=True)],
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)


async def setlocation_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business = get_business_by_owner(update.effective_user.id)

    if not business:
        await update.message.reply_text(
            "⚠️ Спочатку створіть бізнес через /setup."
        )
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(
            "Надіслати геолокацію",
            callback_data="location_geo"
        )],
        [InlineKeyboardButton(
            "Ввести адресу текстом",
            callback_data="location_address"
        )],
    ]

    await update.message.reply_text(
        "Як вказати локацію бізнесу?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return LOCATION_CHOOSE_MODE


async def setlocation_choose_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    business = get_business_by_owner(update.effective_user.id)

    if not business:
        await query.message.reply_text(
            "⚠️ Спочатку створіть бізнес через /setup."
        )
        return ConversationHandler.END

    if query.data == "location_geo":
        await query.message.reply_text(
            "Натисніть кнопку нижче, щоб надіслати "
            "геолокацію бізнесу 👇",
            reply_markup=REQUEST_LOCATION_KEYBOARD
        )
        return LOCATION_WAITING_GEO

    await query.message.reply_text(
        "Введіть адресу бізнесу текстом:"
    )

    return LOCATION_WAITING_ADDRESS


async def setlocation_save_geo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    # Lazy import: see comment in get_setlocation_handler().
    from bot import OWNER_MENU_KEYBOARD

    business = get_business_by_owner(update.effective_user.id)

    if not business:
        await update.message.reply_text(
            "⚠️ Спочатку створіть бізнес через /setup.",
            reply_markup=OWNER_MENU_KEYBOARD
        )
        return ConversationHandler.END

    location = update.message.location

    set_business_location(
        business["id"],
        latitude=location.latitude,
        longitude=location.longitude,
        address_text=None
    )

    await update.message.reply_text(
        "✅ Локацію збережено.",
        reply_markup=OWNER_MENU_KEYBOARD
    )

    return ConversationHandler.END


async def setlocation_reject_non_location(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "Натисніть кнопку «📍 Надіслати геолокацію» нижче."
    )
    return LOCATION_WAITING_GEO


async def setlocation_save_address(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    # Lazy import: see comment in get_setlocation_handler().
    from bot import OWNER_MENU_KEYBOARD

    business = get_business_by_owner(update.effective_user.id)

    if not business:
        await update.message.reply_text(
            "⚠️ Спочатку створіть бізнес через /setup.",
            reply_markup=OWNER_MENU_KEYBOARD
        )
        return ConversationHandler.END

    address_text = update.message.text.strip()

    set_business_location(
        business["id"],
        latitude=None,
        longitude=None,
        address_text=address_text
    )

    await update.message.reply_text(
        f"✅ Адресу збережено: {address_text}",
        reply_markup=OWNER_MENU_KEYBOARD
    )

    return ConversationHandler.END


async def setlocation_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    # Lazy import: see comment in get_setlocation_handler().
    from bot import OWNER_MENU_KEYBOARD

    await update.message.reply_text(
        "❌ Скасовано.",
        reply_markup=OWNER_MENU_KEYBOARD
    )
    return ConversationHandler.END


def get_setlocation_handler():
    # Lazy import: bot.py imports get_setlocation_handler at module load
    # time, so importing from bot at module level here would circular-
    # import. By the time this function actually runs (from main()),
    # bot.py has finished loading.
    from bot import REPLY_MENU_BUTTON_TEXTS, reply_keyboard_router

    menu_button_filter = filters.Text(REPLY_MENU_BUTTON_TEXTS)

    async def interrupt_on_menu_button(update, context):
        await reply_keyboard_router(update, context)
        return ConversationHandler.END

    return ConversationHandler(
        entry_points=[
            CommandHandler("setlocation", setlocation_start),
            MessageHandler(
                filters.Text(["📍 Локація бізнесу"]),
                setlocation_start
            ),
        ],
        states={
            LOCATION_CHOOSE_MODE: [
                CallbackQueryHandler(setlocation_choose_mode),
                MessageHandler(menu_button_filter, interrupt_on_menu_button),
            ],
            LOCATION_WAITING_GEO: [
                MessageHandler(menu_button_filter, interrupt_on_menu_button),
                MessageHandler(filters.LOCATION, setlocation_save_geo),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    setlocation_reject_non_location
                ),
            ],
            LOCATION_WAITING_ADDRESS: [
                MessageHandler(menu_button_filter, interrupt_on_menu_button),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    setlocation_save_address
                ),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", setlocation_cancel),
            MessageHandler(menu_button_filter, interrupt_on_menu_button),
        ],
    )
