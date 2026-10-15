"""Публікація постів у Telegram-канал через Bot API."""

import logging

import httpx

from erudit_bot.writer import CAPTION_LIMIT, MESSAGE_LIMIT

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
REQUEST_TIMEOUT_SECONDS = 30
READ_BUTTON_TEXT = "ЧИТАТИ ↗"


class TelegramError(RuntimeError):
    pass


def read_button(url):
    return {"inline_keyboard": [[{"text": READ_BUTTON_TEXT, "url": url}]]}


class TelegramPublisher:

    def __init__(self, token, channel_id, client=None):
        self.base_url = f"{TELEGRAM_API}/bot{token}"
        self.channel_id = channel_id
        self.client = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)

    def _call(self, method, payload):
        response = self.client.post(f"{self.base_url}/{method}", json=payload)
        data = response.json()
        if not data.get("ok"):
            raise TelegramError(
                f"{method} failed: {data.get('error_code')} "
                f"{data.get('description')}"
            )
        return data["result"]

    def check(self):
        """Перевіряє токен і що бот — адмін каналу з правом публікації.
        Повертає (bot_username, channel_title)."""
        me = self._call("getMe", {})
        chat = self._call("getChat", {"chat_id": self.channel_id})
        member = self._call("getChatMember", {
            "chat_id": self.channel_id,
            "user_id": me["id"],
        })
        if member.get("status") not in ("administrator", "creator"):
            raise TelegramError(
                f"@{me.get('username')} не адміністратор каналу"
            )
        if member.get("status") == "administrator" and not member.get(
                "can_post_messages", False):
            raise TelegramError(
                f"@{me.get('username')} не має права публікувати в каналі"
            )
        return me.get("username"), chat.get("title")

    def publish(self, post, image_url=""):
        """Фото з підписом і кнопкою «ЧИТАТИ»; якщо фото не вантажиться —
        текстовий пост з тією ж кнопкою."""
        markup = read_button(post.source_url) if post.source_url else None
        if image_url:
            payload = {
                "chat_id": self.channel_id,
                "photo": image_url,
                "caption": post.to_html(limit=CAPTION_LIMIT),
                "parse_mode": "HTML",
            }
            if markup:
                payload["reply_markup"] = markup
            try:
                return self._call("sendPhoto", payload)
            except TelegramError as exc:
                # Telegram іноді не може завантажити картинку за URL —
                # пост важливіший за фото.
                logger.warning("sendPhoto failed, falling back to text: %s", exc)

        payload = {
            "chat_id": self.channel_id,
            "text": post.to_html(limit=MESSAGE_LIMIT),
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if markup:
            payload["reply_markup"] = markup
        return self._call("sendMessage", payload)
