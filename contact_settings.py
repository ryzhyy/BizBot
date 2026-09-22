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
    get_faq_items,
    add_faq_item,
    delete_faq_item,
)


CONTACT_CHOOSE_MODE, CONTACT_MANUAL_VALUE = range(2)
FAQ_MENU, FAQ_ADD_QUESTION, FAQ_ADD_ANSWER = range(3)


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
    # Lazy import: bot.py imports get_setcontact_handler at module load
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
            CommandHandler("setcontact", setcontact_start),
            MessageHandler(
                filters.Text(["⚙️ Контакт для клієнтів"]),
                setcontact_start
            ),
        ],
        states={
            CONTACT_CHOOSE_MODE: [
                CallbackQueryHandler(setcontact_choose_mode),
                MessageHandler(menu_button_filter, interrupt_on_menu_button),
            ],
            CONTACT_MANUAL_VALUE: [
                MessageHandler(menu_button_filter, interrupt_on_menu_button),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    setcontact_manual_value
                ),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", setcontact_cancel),
            MessageHandler(menu_button_filter, interrupt_on_menu_button),
        ],
    )


# =========================
# /setfaq
# =========================

def faq_menu_message(business_id):
    items = get_faq_items(business_id)

    if items:
        entries = []

        for i, item in enumerate(items, start=1):
            answer = item["answer"]
            if len(answer) > 150:
                answer = answer[:150] + "…"

            entries.append(
                f"{i}. {item['question']}\n"
                f"   Відповідь: {answer}"
            )

        lines = "\n\n".join(entries)

        text = (
            "❓ Налаштування FAQ\n\n"
            "Поточні питання:\n\n"
            f"{lines}\n\n"
            "Натисніть ✖️ біля питання, щоб видалити його."
        )
    else:
        text = (
            "❓ Налаштування FAQ\n\n"
            "Питань ще немає. Додайте перше — це два короткі "
            "кроки: питання, потім відповідь."
        )

    keyboard = []

    for item in items:
        label = item["question"]
        if len(label) > 40:
            label = label[:40] + "…"

        keyboard.append([
            InlineKeyboardButton(
                f"✖️ {label}",
                callback_data=f"faqdel_{item['id']}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "➕ Додати питання",
            callback_data="faq_add"
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            "✅ Готово",
            callback_data="faq_done"
        )
    ])

    return text, InlineKeyboardMarkup(keyboard)


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

    context.user_data["faq_business_id"] = business["id"]

    text, keyboard = faq_menu_message(business["id"])

    await update.message.reply_text(text, reply_markup=keyboard)

    return FAQ_MENU


async def setfaq_menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    business_id = context.user_data.get("faq_business_id")

    if query.data == "faq_add":
        await query.message.reply_text("Яке питання додаємо?")
        return FAQ_ADD_QUESTION

    if query.data == "faq_done":
        await query.message.reply_text("✅ Готово.")
        return ConversationHandler.END

    if query.data.startswith("faqdel_"):
        item_id = int(query.data.replace("faqdel_", ""))
        delete_faq_item(item_id, business_id)

        text, keyboard = faq_menu_message(business_id)
        await query.message.reply_text(text, reply_markup=keyboard)

        return FAQ_MENU

    return FAQ_MENU


async def setfaq_add_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["faq_pending_question"] = (
        update.message.text.strip()
    )

    await update.message.reply_text("А тепер відповідь на нього:")

    return FAQ_ADD_ANSWER


async def setfaq_add_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business_id = context.user_data.get("faq_business_id")
    question = context.user_data.pop("faq_pending_question", None)
    answer = update.message.text.strip()

    if business_id and question:
        add_faq_item(business_id, question, answer)

    text, keyboard = faq_menu_message(business_id)

    await update.message.reply_text(
        "✅ Додано!\n\n" + text,
        reply_markup=keyboard
    )

    return FAQ_MENU


async def setfaq_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text("❌ Скасовано.")
    return ConversationHandler.END


def get_setfaq_handler():
    # Lazy import: see comment in get_setcontact_handler().
    from bot import REPLY_MENU_BUTTON_TEXTS, reply_keyboard_router

    menu_button_filter = filters.Text(REPLY_MENU_BUTTON_TEXTS)

    async def interrupt_on_menu_button(update, context):
        await reply_keyboard_router(update, context)
        return ConversationHandler.END

    return ConversationHandler(
        entry_points=[
            CommandHandler("setfaq", setfaq_start),
            MessageHandler(
                filters.Text(["❓ Налаштувати FAQ"]),
                setfaq_start
            ),
        ],
        states={
            FAQ_MENU: [
                CallbackQueryHandler(
                    setfaq_menu_callback,
                    pattern="^(faq_add|faq_done|faqdel_)"
                ),
                MessageHandler(menu_button_filter, interrupt_on_menu_button),
            ],
            FAQ_ADD_QUESTION: [
                MessageHandler(menu_button_filter, interrupt_on_menu_button),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    setfaq_add_question
                ),
            ],
            FAQ_ADD_ANSWER: [
                MessageHandler(menu_button_filter, interrupt_on_menu_button),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    setfaq_add_answer
                ),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", setfaq_cancel),
            MessageHandler(menu_button_filter, interrupt_on_menu_button),
        ],
    )
