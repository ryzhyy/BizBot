"""Публікація постів у Telegram-канал через Bot API."""

import logging

import httpx

from erudit_bot.writer import CAPTION_LIMIT, MESSAGE_LIMIT

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
REQUEST_TIMEOUT_SECONDS = 30


class TelegramError(RuntimeError):
    pass


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

    def publish(self, post, image_url=""):
        """Фото з підписом, якщо є картинка і текст влазить у підпис;
        інакше — звичайне повідомлення з прев'ю посилання."""
        if image_url:
            caption = post.to_html(limit=CAPTION_LIMIT)
            try:
                return self._call("sendPhoto", {
                    "chat_id": self.channel_id,
                    "photo": image_url,
                    "caption": caption,
                    "parse_mode": "HTML",
                })
            except TelegramError as exc:
                # Telegram іноді не може завантажити картинку за URL —
                # пост важливіший за фото.
                logger.warning("sendPhoto failed, falling back to text: %s", exc)

        return self._call("sendMessage", {
            "chat_id": self.channel_id,
            "text": post.to_html(limit=MESSAGE_LIMIT),
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": False},
        })
