"""Перетворення сирої новини на короткий клікбейтний пост для Telegram."""

import html
import json
import logging
from dataclasses import dataclass, field

from openai import OpenAI

logger = logging.getLogger(__name__)

OPENAI_TIMEOUT_SECONDS = 60
OPENAI_MAX_RETRIES = 1
# Підпис до фото в Telegram обмежений 1024 символами, текст — 4096.
CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096

SYSTEM_PROMPT = """Ти — редактор спортивного Telegram-каналу «Ерудит».
Твоє завдання: переписати новину українською так, щоб її хотілося дочитати.

Правила:
1. Заголовок — клікбейтний і інтригуючий (емоція, питання, недомовленість,
   цифра або несподіваний поворот), до 90 символів. Але БЕЗ брехні: усе,
   що обіцяє заголовок, має підтверджуватися текстом новини.
2. Гачок — 1 речення, яке чіпляє й змушує читати далі.
3. Основний текст — 2-4 короткі речення: що сталося, хто, головна цифра
   чи факт, чому це важливо. Простими словами, без води й канцеляриту.
4. Не вигадуй фактів, цитат, рахунків чи імен, яких немає в новині.
5. 1-2 доречні емодзі в заголовку, не більше 3 на весь пост.
6. 2-4 хештеги без пробілів (наприклад: #футбол #АПЛ #Мудрик).
7. Без markdown і HTML у полях.

Поверни ТІЛЬКИ валідний JSON:
{"title": "...", "hook": "...", "body": "...", "hashtags": ["#...", "#..."]}
"""


@dataclass
class Post:
    title: str
    hook: str
    body: str
    hashtags: list = field(default_factory=list)
    source_url: str = ""

    def to_html(self, limit=MESSAGE_LIMIT):
        """Текст поста в Telegram HTML, обрізаний до ліміту."""
        tags = " ".join(
            h if h.startswith("#") else f"#{h}"
            for h in (t.replace(" ", "") for t in self.hashtags) if h.strip("#")
        )
        footer_parts = []
        if self.source_url:
            footer_parts.append(
                f'<a href="{html.escape(self.source_url, quote=True)}">'
                "Джерело: Tribuna</a>"
            )
        if tags:
            footer_parts.append(html.escape(tags))
        footer = "\n\n" + "\n".join(footer_parts) if footer_parts else ""

        head = f"<b>{html.escape(self.title)}</b>\n\n"
        if self.hook:
            head += f"<i>{html.escape(self.hook)}</i>\n\n"
        body = html.escape(self.body)

        room = limit - len(head) - len(footer)
        if len(body) > room:
            body = body[:max(0, room - 1)].rstrip() + "…"
        return head + body + footer


def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("AI returned non-object JSON")
    return data


def post_from_ai_output(text, source_url):
    data = _parse_json(text)
    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "").strip()
    if not title or not body:
        raise ValueError("AI output is missing title or body")
    hashtags = data.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = hashtags.split()
    return Post(
        title=title,
        hook=str(data.get("hook") or "").strip(),
        body=body,
        hashtags=[str(h).strip() for h in hashtags if str(h).strip()][:4],
        source_url=source_url,
    )


class PostWriter:

    def __init__(self, api_key, model):
        self.client = OpenAI(
            api_key=api_key,
            timeout=OPENAI_TIMEOUT_SECONDS,
            max_retries=OPENAI_MAX_RETRIES,
        )
        self.model = model

    def write(self, article):
        news = (
            f"Заголовок: {article.title}\n"
            f"Лід: {article.description}\n"
            f"Теги: {', '.join(article.tags)}\n\n"
            f"Текст новини:\n{article.text}"
        )
        response = self.client.responses.create(
            model=self.model,
            instructions=SYSTEM_PROMPT,
            input=news,
        )
        return post_from_ai_output(response.output_text, article.url)
