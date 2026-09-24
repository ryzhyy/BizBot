"""
Сповіщення власника платформи (OWNER_TELEGRAM_ID) про помилки бота.

- error_handler — глобальний обробник PTB: ловить усі НЕперехоплені
  помилки з обробників повідомлень/кнопок і з фонових задач
  (нагадування).
- report_error — те саме для помилок, які код перехоплює сам
  (наприклад, падіння AI-чату), щоб вони не зникали тихо в логах.

Однакові помилки не спамлять: та сама помилка в тому самому місці
надсилається не частіше ніж раз на THROTTLE_SECONDS, а наступне
сповіщення каже, скільки разів вона повторилась за цей час.
"""
import html
import time
import traceback
from datetime import datetime

from telegram import Update
from telegram.error import Conflict, Forbidden, NetworkError

from business_context import get_business_by_id, get_business_by_owner
from platform_admin import OWNER_TELEGRAM_ID


THROTTLE_SECONDS = 10 * 60
MAX_MESSAGE_LENGTH = 3800

# signature -> {"last_sent": float, "suppressed": int}
_recent_errors = {}


def _error_location(error):
    """Останній рядок саме НАШОГО коду в traceback (не бібліотек)."""
    frames = traceback.extract_tb(error.__traceback__) if error else []

    own_frames = [
        frame for frame in frames
        if "site-packages" not in frame.filename
        and "dist-packages" not in frame.filename
    ]

    frame = (own_frames or frames or [None])[-1]

    if frame is None:
        return "невідомо"

    filename = frame.filename.replace("\\", "/").rsplit("/", 1)[-1]
    return f"{filename}:{frame.lineno} ({frame.name})"


def _hint_for(error):
    text = f"{type(error).__name__} {error}".lower()

    if "insufficient_quota" in text or "ratelimit" in text:
        return (
            "💡 Схоже на ліміт/баланс OpenAI — перевір рахунок "
            "і ліміти на platform.openai.com."
        )

    if "authentication" in text or "invalid_api_key" in text:
        return "💡 Схоже, недійсний OPENAI_API_KEY у змінних Railway."

    if isinstance(error, Conflict):
        return (
            "💡 Бот запущено двічі (наприклад, локально на Mac і на "
            "Railway одночасно) — зупини зайвий екземпляр."
        )

    return ""


def _should_send(signature):
    now = time.monotonic()
    entry = _recent_errors.get(signature)

    if entry and now - entry["last_sent"] < THROTTLE_SECONDS:
        entry["suppressed"] += 1
        return False, 0

    suppressed = entry["suppressed"] if entry else 0
    _recent_errors[signature] = {"last_sent": now, "suppressed": 0}

    return True, suppressed


def _describe_user_and_business(update, user_data):
    lines = []

    user = update.effective_user if isinstance(update, Update) else None

    if user:
        username = f" @{user.username}" if user.username else ""
        lines.append(f"👤 {user.full_name}{username} (id {user.id})")

        client_business_id = (user_data or {}).get("client_business_id")
        business = None
        role = ""

        try:
            if client_business_id:
                business = get_business_by_id(client_business_id)
                role = "як клієнт"
            else:
                business = get_business_by_owner(user.id)
                role = "як власник"
        except Exception:
            business = None

        if business:
            lines.append(
                f"🏢 {business['name']} (id {business['id']}) — {role}"
            )

    if isinstance(update, Update):
        if update.callback_query:
            lines.append(f"🔘 Кнопка: {update.callback_query.data}")
        elif update.effective_message and update.effective_message.text:
            text = update.effective_message.text
            if len(text) > 200:
                text = text[:200] + "…"
            lines.append(f"💬 Повідомлення: «{text}»")
    else:
        lines.append("⚙️ Фонова задача (без користувача)")

    return lines


async def report_error(bot, error, update=None, user_data=None, where=None):
    """Надіслати власнику платформи звіт про помилку. Ніколи не падає."""
    try:
        if not OWNER_TELEGRAM_ID:
            return

        location = _error_location(error)
        signature = f"{type(error).__name__}|{location}|{str(error)[:80]}"

        send, suppressed = _should_send(signature)

        if not send:
            return

        lines = [
            "🚨 Помилка в боті",
            f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]

        if where:
            lines.append(f"🧭 {where}")

        lines.append(
            f"❗ {type(error).__name__}: {str(error)[:500] or '—'}"
        )
        lines.append(f"📍 {location}")
        lines.extend(_describe_user_and_business(update, user_data))

        hint = _hint_for(error)
        if hint:
            lines.append(hint)

        if suppressed:
            lines.append(
                f"🔁 Ця помилка повторилась ще {suppressed} раз(ів) "
                "з моменту попереднього сповіщення."
            )

        text = "\n".join(lines)

        tb = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        tb_tail = tb[-(MAX_MESSAGE_LENGTH - len(text) - 50):]

        await bot.send_message(
            chat_id=int(OWNER_TELEGRAM_ID),
            text=(
                f"{html.escape(text, quote=False)}\n\n"
                f"<pre>{html.escape(tb_tail, quote=False)}</pre>"
            ),
            parse_mode="HTML",
        )

    except Exception as send_error:
        print("ERROR REPORT FAILED:", send_error)


async def error_handler(update, context):
    error = context.error

    # Короткі збої мережі бібліотека сама повторює — це не проблема
    # бізнесу, тільки шум у сповіщеннях.
    if isinstance(error, NetworkError):
        print("NETWORK ERROR (ignored):", error)
        return

    print("UNHANDLED ERROR:")
    traceback.print_exception(type(error), error, error.__traceback__)

    user_data = None
    try:
        user_data = context.user_data
    except Exception:
        pass

    await report_error(
        context.bot, error, update=update, user_data=user_data
    )

    # Клієнт/власник не повинен отримати тишу у відповідь.
    if (
        isinstance(update, Update)
        and update.effective_chat
        and not isinstance(error, Forbidden)
    ):
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    "⚠️ Щось пішло не так. Спробуйте ще раз трохи "
                    "згодом — ми вже знаємо про проблему."
                ),
            )
        except Exception:
            pass
