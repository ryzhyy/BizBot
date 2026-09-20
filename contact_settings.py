from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
    set_business_contact,
    set_business_faq,
)


CONTACT_CHOOSE_MODE, CONTACT_MANUAL_VALUE = range(2)
FAQ_TEXT = 0


# =========================
# /setcontact
# =========================

async def setcontact_start(
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
            "Автоматично через Telegram",
            callback_data="contact_auto"
        )],
        [InlineKeyboardButton(
            "Вказати вручну",
            callback_data="contact_manual"
        )],
    ]

    await update.message.reply_text(
        "Як клієнти зв'язуватимуться з вами?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return CONTACT_CHOOSE_MODE


async def setcontact_choose_mode(
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

    if query.data == "contact_auto":
        set_business_contact(business["id"], "auto", None)

        await query.message.reply_text(
            "✅ Клієнти зв'язуватимуться з вами "
            "автоматично через Telegram."
        )
        return ConversationHandler.END

    await query.message.reply_text(
        "Напишіть контакт (телефон, юзернейм, email):"
    )

    return CONTACT_MANUAL_VALUE


async def setcontact_manual_value(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business = get_business_by_owner(update.effective_user.id)

    if not business:
        await update.message.reply_text(
            "⚠️ Спочатку створіть бізнес через /setup."
        )
        return ConversationHandler.END

    value = update.message.text.strip()

    set_business_contact(business["id"], "manual", value)

    await update.message.reply_text(
        f"✅ Контакт збережено: {value}"
    )

    return ConversationHandler.END


async def setcontact_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text("❌ Скасовано.")
    return ConversationHandler.END


def get_setcontact_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler("setcontact", setcontact_start),
            MessageHandler(
                filters.Text(["⚙️ Контакт для клієнтів"]),
                setcontact_start
            ),
        ],
        states={
            CONTACT_CHOOSE_MODE: [
                CallbackQueryHandler(setcontact_choose_mode)
            ],
            CONTACT_MANUAL_VALUE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    setcontact_manual_value
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", setcontact_cancel)
        ],
    )


# =========================
# /setfaq
# =========================

async def setfaq_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business = get_business_by_owner(update.effective_user.id)

    if not business:
        await update.message.reply_text(
            "⚠️ Спочатку створіть бізнес через /setup."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "Напишіть текст, який бачитимуть клієнти "
        "в розділі Допомога:"
    )

    return FAQ_TEXT


async def setfaq_save(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business = get_business_by_owner(update.effective_user.id)

    if not business:
        await update.message.reply_text(
            "⚠️ Спочатку створіть бізнес через /setup."
        )
        return ConversationHandler.END

    faq_text = update.message.text.strip()

    set_business_faq(business["id"], faq_text)

    await update.message.reply_text("✅ FAQ збережено.")

    return ConversationHandler.END


async def setfaq_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text("❌ Скасовано.")
    return ConversationHandler.END


def get_setfaq_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler("setfaq", setfaq_start),
            MessageHandler(
                filters.Text(["❓ Налаштувати FAQ"]),
                setfaq_start
            ),
        ],
        states={
            FAQ_TEXT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    setfaq_save
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", setfaq_cancel)
        ],
    )
