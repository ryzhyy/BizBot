import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _list_env(name, default):
    raw = os.getenv(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Config:
    telegram_bot_token: str = field(
        default_factory=lambda: os.getenv("ERUDIT_TELEGRAM_BOT_TOKEN", "")
    )
    # @channel_username або числовий id (-100...). Бот має бути адміном каналу.
    telegram_channel_id: str = field(
        default_factory=lambda: os.getenv("ERUDIT_CHANNEL_ID", "")
    )
    openai_api_key: str = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY", "")
    )
    openai_model: str = field(
        default_factory=lambda: os.getenv("ERUDIT_OPENAI_MODEL", "gpt-5.6-luna")
    )
    # Сторінки зі стрічкою новин, з яких беремо посилання на статті.
    source_urls: list = field(
        default_factory=lambda: _list_env(
            "ERUDIT_SOURCE_URLS", ["https://tribuna.com/uk/news/"]
        )
    )
    db_path: str = field(
        default_factory=lambda: os.getenv("ERUDIT_DB_PATH", "erudit_bot.db")
    )
    # Скільки нових постів максимум за один прохід.
    max_posts_per_run: int = field(
        default_factory=lambda: int(os.getenv("ERUDIT_MAX_POSTS_PER_RUN", "3"))
    )
    # Скільки свіжих новин максимум оцінює AI за прохід (решта — наступного разу).
    max_candidates: int = field(
        default_factory=lambda: int(os.getenv("ERUDIT_MAX_CANDIDATES", "10"))
    )
    interval_minutes: int = field(
        default_factory=lambda: int(os.getenv("ERUDIT_INTERVAL_MINUTES", "30"))
    )
    # Мінімальна «гучність» новини (1-10, оцінює AI), щоб потрапити в канал.
    min_score: int = field(
        default_factory=lambda: int(os.getenv("ERUDIT_MIN_SCORE", "6"))
    )
    # Пауза між постами, щоб не спамити канал і не ловити flood-limit.
    post_delay_seconds: float = field(
        default_factory=lambda: float(os.getenv("ERUDIT_POST_DELAY_SECONDS", "5"))
    )

    def missing_for_publish(self):
        missing = []
        if not self.telegram_bot_token:
            missing.append("ERUDIT_TELEGRAM_BOT_TOKEN")
        if not self.telegram_channel_id:
            missing.append("ERUDIT_CHANNEL_ID")
        return missing
