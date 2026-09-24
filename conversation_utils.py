"""
Спільна логіка «переривання» покрокових діалогів (ConversationHandler).

Проблема: поки власник, наприклад, додає послугу, бот чекає звичайний
текст (назву, ціну...). Якщо в цей момент натиснути кнопку меню
(«📝 Мій бізнес») чи ввести команду (/start), бот сприймав це як
відповідь на своє питання: «📝 Мій бізнес» ставало назвою послуги,
а наступні кнопки — «неправильною ціною».

Рішення: у кожному кроці кожного діалогу першими стоять обробники,
які розпізнають кнопку меню або команду, завершують поточний діалог
(прибираючи його тимчасові дані), коротко повідомляють про це і
повертають те саме повідомлення в чергу бота. Далі воно обробляється
так, ніби діалогу й не було: кнопка відкриває свій розділ, команда
виконується, а кнопка іншого діалогу — запускає той діалог.
"""
from telegram.ext import ConversationHandler, MessageHandler, filters


# Кнопки меню, які самі є точкою входу в окремий діалог, тож не входять
# до REPLY_MENU_BUTTON_TEXTS (там — кнопки, які обробляє роутер меню).
CONVERSATION_ENTRY_BUTTON_TEXTS = [
    "➕ Додати послугу",
    "⚙️ Контакт для клієнтів",
    "❓ Налаштувати FAQ",
    "📍 Локація бізнесу",
]

# /cancel кожен діалог обробляє сам (своїм повідомленням «скасовано»).
_NOT_CANCEL_COMMAND = ~filters.Regex(r"^/cancel(@\w+)?(\s|$)")

# Захист від зациклення: одне й те саме повідомлення повертаємо в
# чергу щонайбільше один раз.
_requeued_update_ids = set()


def interrupt_handlers(cleanup_keys=(), action_name=None):
    # Лінивий імпорт: bot.py імпортує модулі з діалогами під час свого
    # завантаження, а ця функція викликається вже з main().
    from bot import REPLY_MENU_BUTTON_TEXTS

    menu_texts = list(REPLY_MENU_BUTTON_TEXTS) + [
        text for text in CONVERSATION_ENTRY_BUTTON_TEXTS
        if text not in REPLY_MENU_BUTTON_TEXTS
    ]

    async def interrupt(update, context):
        for key in cleanup_keys:
            context.user_data.pop(key, None)

        if update.update_id in _requeued_update_ids:
            return ConversationHandler.END

        if len(_requeued_update_ids) > 5000:
            _requeued_update_ids.clear()
        _requeued_update_ids.add(update.update_id)

        if action_name and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    f"↩️ {action_name} скасовано."
                )
            except Exception as error:
                print("INTERRUPT NOTICE ERROR:", error)

        # Повертаємо повідомлення в чергу: бот обробить його вже ПІСЛЯ
        # того, як цей діалог завершиться — як звичайну кнопку/команду.
        await context.application.update_queue.put(update)

        return ConversationHandler.END

    return [
        MessageHandler(filters.Text(menu_texts), interrupt),
        MessageHandler(filters.COMMAND & _NOT_CANCEL_COMMAND, interrupt),
    ]
