"""Майстри: хто виконує послугу.

Записи конфліктують лише в межах одного майстра, тож різні майстри
приймають клієнтів паралельно. Послуги без майстра — одна спільна черга
(як у бізнесу, де працює одна людина).
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from business_context import get_business_by_owner
from conversation_utils import interrupt_handlers
from database import get_connection

MAX_MASTER_NAME_LENGTH = 40
NO_MASTER_LABEL = "без майстра"

PICK_MASTER, NEW_MASTER_NAME = range(2)
ASSIGN_KEYS = ("assign_service_id",)


# ---------- дані ----------

def get_masters(business_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name FROM masters WHERE business_id = ? ORDER BY name_key",
        (business_id,)
    ).fetchall()
    conn.close()
    return rows


def get_master(business_id, master_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, name FROM masters WHERE id = ? AND business_id = ?",
        (master_id, business_id)
    ).fetchone()
    conn.close()
    return row


def get_or_create_master(business_id, name):
    """Майстер з таким ім'ям (без урахування регістру) або новий."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO masters (business_id, name, name_key)
            VALUES (?, ?, ?)
            ON CONFLICT(business_id, name_key) DO NOTHING
            """,
            (business_id, name, name.casefold())
        )
        conn.commit()
        return conn.execute(
            "SELECT id, name FROM masters "
            "WHERE business_id = ? AND name_key = ?",
            (business_id, name.casefold())
        ).fetchone()
    finally:
        conn.close()


def set_service_master(business_id, service_id, master_id):
    """master_id=None — послуга без майстра. Повертає False, якщо послуга
    чи майстер не належать цьому бізнесу."""
    if master_id is not None and get_master(business_id, master_id) is None:
        return False

    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE services SET master_id = ? WHERE id = ? AND business_id = ?",
            (master_id, service_id, business_id)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_service_master_name(service_id):
    conn = get_connection()
    row = conn.execute(
        """
        SELECT masters.name FROM services
        JOIN masters ON masters.id = services.master_id
        WHERE services.id = ?
        """,
        (service_id,)
    ).fetchone()
    conn.close()
    return row["name"] if row else None


def _get_owned_service(business_id, service_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, name FROM services "
        "WHERE id = ? AND business_id = ? AND active = 1",
        (service_id, business_id)
    ).fetchone()
    conn.close()
    return row


def clean_master_name(text):
    """Нормалізоване ім'я або None, якщо воно не підходить."""
    name = " ".join((text or "").split())
    if not name or len(name) > MAX_MASTER_NAME_LENGTH:
        return None
    return name


# ---------- клавіатура вибору ----------

def master_picker_keyboard(business_id, callback_prefix):
    """Кнопки: наявні майстри, «новий майстер», «без майстра».
    callback_data: <prefix><id> | <prefix>new | <prefix>none."""
    rows = [
        [InlineKeyboardButton(
            f"👤 {master['name']}",
            callback_data=f"{callback_prefix}{master['id']}"
        )]
        for master in get_masters(business_id)
    ]
    rows.append([InlineKeyboardButton(
        "➕ Новий майстер", callback_data=f"{callback_prefix}new"
    )])
    rows.append([InlineKeyboardButton(
        "🚫 Без майстра (спільна черга)", callback_data=f"{callback_prefix}none"
    )])
    return InlineKeyboardMarkup(rows)


MASTER_QUESTION_TEXT = (
    "👤 Хто виконує цю послугу?\n\n"
    "Записи до різних майстрів можуть бути одночасно, а до одного "
    "майстра — ні. Якщо працюєте самі, оберіть «Без майстра»."
)

NEW_MASTER_PROMPT_TEXT = (
    f"✍️ Напишіть ім'я майстра (до {MAX_MASTER_NAME_LENGTH} символів)."
)


# ---------- зміна майстра з «📋 Мої послуги» ----------

async def assign_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    business = get_business_by_owner(query.from_user.id)
    service_id = int(query.data.removeprefix("svcmaster_"))
    service = business and _get_owned_service(business["id"], service_id)

    if not service:
        await query.message.reply_text("⚠️ Послугу не знайдено.")
        return ConversationHandler.END

    context.user_data["assign_service_id"] = service_id

    await query.message.reply_text(
        f"✂️ {service['name']}\n\n{MASTER_QUESTION_TEXT}",
        reply_markup=master_picker_keyboard(business["id"], "setmaster_")
    )
    return PICK_MASTER


async def _finish_assign(message, context, business_id, master_id):
    service_id = context.user_data.pop("assign_service_id", None)

    if service_id is None or not set_service_master(
        business_id, service_id, master_id
    ):
        await message.reply_text("⚠️ Не вдалося змінити майстра.")
        return ConversationHandler.END

    master = get_master(business_id, master_id) if master_id else None
    await message.reply_text(
        "✅ Готово! Майстер послуги: "
        f"{master['name'] if master else NO_MASTER_LABEL}."
    )
    return ConversationHandler.END


async def assign_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    business = get_business_by_owner(query.from_user.id)
    if not business:
        context.user_data.pop("assign_service_id", None)
        return ConversationHandler.END

    choice = query.data.removeprefix("setmaster_")

    if choice == "new":
        await query.message.reply_text(NEW_MASTER_PROMPT_TEXT)
        return NEW_MASTER_NAME

    master_id = None if choice == "none" else int(choice)
    return await _finish_assign(query.message, context, business["id"], master_id)


async def assign_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = clean_master_name(update.message.text)

    if name is None:
        await update.message.reply_text(NEW_MASTER_PROMPT_TEXT)
        return NEW_MASTER_NAME

    business = get_business_by_owner(update.effective_user.id)
    if not business:
        context.user_data.pop("assign_service_id", None)
        return ConversationHandler.END

    master = get_or_create_master(business["id"], name)
    return await _finish_assign(
        update.message, context, business["id"], master["id"]
    )


async def assign_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("assign_service_id", None)
    await update.message.reply_text("❌ Зміну майстра скасовано.")
    return ConversationHandler.END


def get_assign_master_handler():
    interrupts = interrupt_handlers(
        cleanup_keys=ASSIGN_KEYS, action_name="Зміну майстра"
    )

    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(assign_start, pattern=r"^svcmaster_\d+$"),
        ],
        states={
            PICK_MASTER: [
                *interrupts,
                CallbackQueryHandler(
                    assign_pick, pattern=r"^setmaster_(\d+|new|none)$"
                ),
            ],
            NEW_MASTER_NAME: [
                *interrupts,
                MessageHandler(filters.TEXT & ~filters.COMMAND, assign_new_name),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", assign_cancel),
            *interrupts,
        ],
    )

