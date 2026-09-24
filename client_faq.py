"""
Окреме інтерактивне FAQ для клієнтів.

Одне повідомлення зі списком питань-кнопок. Натискання на питання
НЕ надсилає нове повідомлення, а редагує те саме: над кнопками
з'являється відповідь, а обране питання в списку виділяється.
Повторне натискання на вже обране питання згортає відповідь.

callback_data містить business_id, тож кнопки працюють навіть після
перезапуску бота (коли клієнтський контекст у пам'яті вже втрачено).
"""
from html import escape as html_escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

from business_context import get_business_by_id, get_faq_items
from plans import is_pro


MAX_BUTTON_LABEL = 55


def _capitalize_first(text):
    text = (text or "").strip()
    return text[:1].upper() + text[1:]


def format_question(question):
    # Лише для показу: самі записи в базі не змінюємо.
    question = _capitalize_first(question)

    if question and question[-1] not in "?!.":
        question += "?"

    return question


def format_faq_item_html(question, answer):
    return (
        f"❔ <b>{html_escape(format_question(question))}</b>\n"
        f"💬 {html_escape(_capitalize_first(answer))}"
    )


def _button_label(question, selected):
    label = format_question(question)

    if len(label) > MAX_BUTTON_LABEL:
        label = label[:MAX_BUTTON_LABEL - 1].rstrip() + "…"

    return f"{'🔹' if selected else '❔'} {label}"


def build_faq_view(business, selected_item_id=None):
    header = (
        f"<b>📖 Часті запитання «{html_escape(business['name'])}»</b>"
    )

    # FAQ — функція Pro: на Free клієнт бачить «порожньо» (дані
    # власника не видаляються й повернуться разом із Pro).
    items = get_faq_items(business["id"]) if is_pro(business["id"]) else []

    if not items:
        return (
            f"{header}\n\n"
            "Поки що тут порожньо. Якщо маєте питання — натисніть "
            "«❓ Допомога», щоб зв'язатися з власником, або просто "
            "напишіть мені в чат 🙂",
            None
        )

    selected = next(
        (item for item in items if item["id"] == selected_item_id),
        None
    )

    if selected:
        body = (
            f"{format_faq_item_html(selected['question'], selected['answer'])}"
            "\n\n<i>Оберіть інше питання 👇</i>"
        )
    else:
        body = "Оберіть питання — відповідь з'явиться тут 👇"

    keyboard = []

    for item in items:
        is_selected = selected is not None and item["id"] == selected["id"]

        keyboard.append([InlineKeyboardButton(
            _button_label(item["question"], is_selected),
            # Повторне натискання на обране питання згортає відповідь.
            callback_data=(
                f"cfaq_{business['id']}_list"
                if is_selected
                else f"cfaq_{business['id']}_{item['id']}"
            )
        )])

    return f"{header}\n\n{body}", InlineKeyboardMarkup(keyboard)


async def client_faq_callback(update, context):
    query = update.callback_query

    _, business_id, target = query.data.split("_")

    business = get_business_by_id(int(business_id))

    # На callback можна відповісти лише один раз — тому спершу
    # перевіряємо бізнес, а тоді відповідаємо.
    if not business:
        await query.answer("⚠️ Бізнес не знайдено.", show_alert=True)
        return

    await query.answer()

    selected_item_id = None if target == "list" else int(target)

    text, keyboard = build_faq_view(business, selected_item_id)

    try:
        await query.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as error:
        # Той самий вміст (подвійне натискання) — нічого не робимо.
        if "not modified" in str(error).lower():
            return

        print("CLIENT FAQ EDIT ERROR:", error)
        await query.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


def get_client_faq_handler():
    return CallbackQueryHandler(
        client_faq_callback,
        pattern=r"^cfaq_\d+_(\d+|list)$"
    )
