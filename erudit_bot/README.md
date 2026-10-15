# Erudit-bot (прототип)

Агент, який:
1. забирає свіжі новини зі стрічки **tribuna.com**;
2. переписує кожну через OpenAI: клікбейтний заголовок, гачок, 2–4 речення суті, хештеги — коротко, ясно, без вигаданих фактів;
3. публікує пост у **Telegram-канал** (фото з підписом або текст із прев'ю) з посиланням на джерело;
4. запам'ятовує опубліковані новини в SQLite, щоб не дублювати.

## Налаштування

1. Створіть бота в [@BotFather](https://t.me/BotFather) і додайте його **адміністратором** каналу з правом публікації.
2. Додайте в `.env`:

```env
OPENAI_API_KEY=sk-...
ERUDIT_TELEGRAM_BOT_TOKEN=123456:ABC...
ERUDIT_CHANNEL_ID=@my_channel          # або -100xxxxxxxxxx

# необов'язково
ERUDIT_SOURCE_URLS=https://tribuna.com/uk/news/,https://tribuna.com/uk/football/news/
ERUDIT_OPENAI_MODEL=gpt-5.6-luna
ERUDIT_MAX_POSTS_PER_RUN=3
ERUDIT_INTERVAL_MINUTES=30
ERUDIT_POST_DELAY_SECONDS=5
ERUDIT_DB_PATH=erudit_bot.db
```

Залежності ті самі, що й у BizBot: `pip install -r requirements.txt`.

## Запуск

```bash
# подивитися, які пости вийдуть, нічого не надсилаючи
python -m erudit_bot --dry-run

# переписати й опублікувати конкретну статтю
python -m erudit_bot --url https://tribuna.com/uk/news/...

# один прохід (для cron / Railway cron)
python -m erudit_bot --once

# постійна робота: перевірка кожні ERUDIT_INTERVAL_MINUTES хвилин
python -m erudit_bot
```

## Як влаштовано

| Файл | Що робить |
|---|---|
| `scraper.py` | Посилання на новини зі стрічки + розбір статті: JSON-LD `NewsArticle` → og-теги → абзаци в `<article>` |
| `writer.py` | Промпт редактора й перетворення відповіді AI на пост у Telegram-HTML з урахуванням лімітів (1024 для підпису, 4096 для тексту) |
| `publisher.py` | `sendPhoto` / `sendMessage` через Bot API; якщо фото не вантажиться — шле текстом |
| `storage.py` | SQLite-таблиця вже опублікованих URL |
| `main.py` | Пайплайн і CLI |

Поведінка при помилках: якщо впав AI чи Telegram — новина не позначається опублікованою і буде спробувана наступного проходу; порожня стаття пропускається назавжди. Нові пости йдуть від старішого до новішого, щоб у каналі зберігся хронологічний порядок.

## Обмеження прототипу

- Розмітку tribuna.com не перевірено наживо (з середовища розробки сайт недоступний). Якщо посилання не знаходяться — перевірте `is_article_url` у `scraper.py` і запустіть `--dry-run`.
- Стиль постів налаштовується в `SYSTEM_PROMPT` у `writer.py`.
- Тексти — переказ із посиланням на джерело; перед комерційним використанням варто перевірити умови використання контенту Tribuna.
