"""Точка входу: python -m erudit_bot [--once] [--dry-run] [--url URL]"""

import argparse
import logging
import sys
import time

from erudit_bot.config import Config
from erudit_bot.publisher import TelegramPublisher
from erudit_bot.scraper import TribunaScraper
from erudit_bot.storage import SeenStore
from erudit_bot.writer import PostWriter

logger = logging.getLogger("erudit_bot")


def collect_new_urls(scraper, store, source_urls, limit):
    """Найсвіжіші ще не опубліковані новини, від старішої до новішої,
    щоб у каналі зберігся хронологічний порядок."""
    fresh = []
    for source_url in source_urls:
        try:
            urls = scraper.list_articles(source_url)
        except Exception:
            logger.exception("Не вдалося отримати стрічку %s", source_url)
            continue
        logger.info("%s: знайдено %d посилань", source_url, len(urls))
        for url in urls:
            if url not in fresh and not store.is_seen(url):
                fresh.append(url)
    return list(reversed(fresh[:limit]))


def process_article(url, scraper, writer, publisher, store, dry_run=False):
    """Повертає True, якщо пост створено (і опубліковано, якщо не dry-run)."""
    article = scraper.fetch_article(url)
    if not article.title or not article.text:
        logger.warning("Порожня стаття, пропускаю: %s", url)
        if not dry_run:
            store.mark_seen(url)
        return False

    post = writer.write(article)

    if dry_run:
        print("=" * 60)
        print(post.to_html())
        print(f"[картинка: {article.image_url or '—'}]")
        return True

    publisher.publish(post, image_url=article.image_url)
    store.mark_seen(url, post.title)
    logger.info("Опубліковано: %s", post.title)
    return True


def run_once(config, scraper, writer, publisher, store, dry_run=False,
             urls=None):
    if urls is None:
        urls = collect_new_urls(
            scraper, store, config.source_urls, config.max_posts_per_run
        )
    if not urls:
        logger.info("Нових новин немає")
        return 0

    done = 0
    for index, url in enumerate(urls):
        if index and not dry_run:
            time.sleep(config.post_delay_seconds)
        try:
            if process_article(url, scraper, writer, publisher, store, dry_run):
                done += 1
        except Exception:
            # Не позначаємо як опубліковану — спробуємо наступного разу.
            logger.exception("Не вдалося обробити %s", url)
    return done


def main(argv=None):
    parser = argparse.ArgumentParser(description="Erudit-bot: новини Tribuna → Telegram")
    parser.add_argument("--once", action="store_true",
                        help="один прохід і вихід (для cron)")
    parser.add_argument("--dry-run", action="store_true",
                        help="лише показати пости в консолі, нічого не надсилати")
    parser.add_argument("--url", action="append",
                        help="обробити конкретну статтю (можна кілька разів)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = Config()
    if not config.openai_api_key:
        sys.exit("Не задано OPENAI_API_KEY")
    missing = config.missing_for_publish()
    if missing and not args.dry_run:
        sys.exit(f"Не задано: {', '.join(missing)} (або запустіть з --dry-run)")

    scraper = TribunaScraper()
    writer = PostWriter(config.openai_api_key, config.openai_model)
    publisher = None if args.dry_run else TelegramPublisher(
        config.telegram_bot_token, config.telegram_channel_id
    )
    store = SeenStore(config.db_path)

    try:
        if args.url or args.once or args.dry_run:
            run_once(config, scraper, writer, publisher, store,
                     dry_run=args.dry_run, urls=args.url)
            return
        while True:
            run_once(config, scraper, writer, publisher, store)
            logger.info("Наступна перевірка через %d хв", config.interval_minutes)
            time.sleep(config.interval_minutes * 60)
    finally:
        store.close()


if __name__ == "__main__":
    main()
