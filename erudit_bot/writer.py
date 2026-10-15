"""Перетворення новини на короткий клікбейтний тизер для Telegram.

Пост не переказує новину повністю: він інтригує й веде читача на Tribuna
через кнопку «ЧИТАТИ» під постом.
"""

import html
import json
import logging
from dataclasses import dataclass

from openai import OpenAI

logger = logging.getLogger(__name__)

OPENAI_TIMEOUT_SECONDS = 60
OPENAI_MAX_RETRIES = 1
# Підпис до фото в Telegram обмежений 1024 символами, текст — 4096.
CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096

SYSTEM_PROMPT = """Ти — редактор популярного футбольного Telegram-каналу.
Ти отримуєш новину з Tribuna і пишеш до неї короткий пост-тизер, після
якого читач обов'язково натисне кнопку «ЧИТАТИ» й перейде на сайт.

Як виглядає пост:
1. title — найгучніша цитата з новини в «ялинках» або інтригуюча фраза.
   Починається з 1 доречного емодзі (🧡 ❗️ 😱 🔥 ⚡️ 💔 🤯 тощо). До 80 символів.
   Приклад: 🧡 «Всі футболісти заслуговують на величезні оплески»
2. teaser — 1 коротке речення: хто і що зробив/сказав, щоб був зрозумілий
   контекст. Можна 1 емодзі в кінці. Приклад:
   Рінат Ахметов прокоментував перемогу «Шахтаря» в Лізі чемпіонів 💬
3. hook — 2-5 слів, що залишають головне нерозкритим і кличуть читати далі,
   завершуються 👇. Приклади: «Кого саме виділив 👇», «Що сталося далі 👇»,
   «Названо суму трансферу 👇», «Реакція тренера 👇».
4. score — ціле число 1-10: наскільки новина гучна для українського
   вболівальника (10 — сенсація: топ-трансфер, скандал, гучна заява,
   історичний результат; 1 — дрібна рутинна новина).

Правила:
- Головну інтригу НЕ розкривай у пості — вона має бути за кнопкою.
- Не вигадуй фактів, цитат, цифр чи імен, яких немає в новині. Цитата в
  title — лише справжня цитата з тексту (можна скоротити, не змінюючи змісту).
- Пиши живою українською, просто й ясно, без канцеляриту.
- Без markdown, HTML і хештегів.

Поверни ТІЛЬКИ валідний JSON:
{"title": "...", "teaser": "...", "hook": "...", "score": 7}
"""


@dataclass
class Post:
    title: str
    teaser: str
    hook: str = ""
    score: int = 0
    source_url: str = ""

    def to_html(self, limit=MESSAGE_LIMIT):
        """Текст поста в Telegram HTML, обрізаний до ліміту."""
        head = f"<b>{html.escape(self.title)}</b>\n\n"
        tail = f"\n\n{html.escape(self.hook)}" if self.hook else ""
        teaser = html.escape(self.teaser)
        room = limit - len(head) - len(tail)
        if len(teaser) > room:
            teaser = teaser[:max(0, room - 1)].rstrip() + "…"
        return head + teaser + tail


def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("AI returned non-object JSON")
    return data


def _score(value):
    try:
        return max(0, min(10, int(value)))
    except (TypeError, ValueError):
        return 0


def post_from_ai_output(text, source_url):
    data = _parse_json(text)
    title = str(data.get("title") or "").strip()
    teaser = str(data.get("teaser") or "").strip()
    if not title or not teaser:
        raise ValueError("AI output is missing title or teaser")
    return Post(
        title=title,
        teaser=teaser,
        hook=str(data.get("hook") or "").strip(),
        score=_score(data.get("score")),
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
