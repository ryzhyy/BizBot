import json

import pytest

from erudit_bot.main import collect_new_urls, run_once
from erudit_bot.config import Config
from erudit_bot.scraper import (
    Article,
    extract_article_links,
    is_article_url,
    parse_article,
)
from erudit_bot.publisher import TelegramPublisher
from erudit_bot.storage import SeenStore
from erudit_bot.writer import CAPTION_LIMIT, Post, post_from_ai_output

SOURCE = "https://tribuna.com/uk/news/"

LISTING_HTML = """
<html><body>
  <a href="/uk/news/">Усі новини</a>
  <a href="/uk/news/page-2/">Далі</a>
  <a href="/uk/news/football-2026-10-15-mudryk-povernuvsya/">Мудрик</a>
  <a href="https://tribuna.com/uk/news/football-2026-10-15-mudryk-povernuvsya/#comments">дубль</a>
  <a href="/uk/news/tag/apl-ukraine/">тег</a>
  <a href="https://other.com/uk/news/foo-bar/">чужий сайт</a>
  <a href="/uk/football/news/2026-10-15-shakhtar-dynamo/">Класико</a>
  <a href="/uk/blogs/some-blog-post/">блог</a>
</body></html>
"""

ARTICLE_LD_HTML = """
<html><head>
<meta property="og:title" content="OG заголовок">
<meta property="og:image" content="/img/mudryk.jpg">
<script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [
  {"@type": "BreadcrumbList"},
  {"@type": "NewsArticle", "headline": "Мудрик повернувся до тренувань",
   "description": "Вінгер Челсі знову з командою.",
   "articleBody": "Михайло Мудрик повернувся до загальної групи. Він може зіграти в суботу.",
   "image": [{"url": "https://cdn.tribuna.com/mudryk.jpg"}],
   "datePublished": "2026-10-15T10:00:00+03:00",
   "keywords": "Мудрик, Челсі"}
]}
</script>
</head><body><h1>H1 заголовок</h1></body></html>
"""

ARTICLE_FALLBACK_HTML = """
<html><head>
<meta property="og:title" content="Шахтар переміг Динамо">
<meta property="og:description" content="Донеччани забрали класико.">
<meta property="og:image" content="/img/classico.jpg">
</head><body>
<nav><p>Меню сайту, яке не має потрапити в текст новини взагалі ніяк</p></nav>
<article>
  <h1>Шахтар переміг Динамо</h1>
  <p>Шахтар обіграв Динамо з рахунком 2:1 у центральному матчі туру УПЛ.</p>
  <script>var ads = "реклама, яку треба пропустити";</script>
  <p>Коротко</p>
  <p>Переможний гол забив Судаков на 89-й хвилині після швидкої атаки.</p>
</article>
</body></html>
"""


def test_is_article_url():
    assert is_article_url(
        "https://tribuna.com/uk/news/football-2026-10-15-slug/", SOURCE)
    assert is_article_url(
        "https://www.tribuna.com/uk/football/news/2026-10-15-slug/", SOURCE)
    assert not is_article_url("https://tribuna.com/uk/news/", SOURCE)
    assert not is_article_url("https://tribuna.com/uk/news/page-2/", SOURCE)
    assert not is_article_url("https://tribuna.com/uk/news/tag/apl-ua/", SOURCE)
    assert not is_article_url("https://other.com/uk/news/foo-bar/", SOURCE)
    assert not is_article_url("mailto:news-foo@tribuna.com", SOURCE)


def test_extract_article_links_dedupes_and_filters():
    assert extract_article_links(LISTING_HTML, SOURCE) == [
        "https://tribuna.com/uk/news/football-2026-10-15-mudryk-povernuvsya/",
        "https://tribuna.com/uk/football/news/2026-10-15-shakhtar-dynamo/",
    ]


def test_parse_article_prefers_json_ld():
    article = parse_article(ARTICLE_LD_HTML, SOURCE + "football-x-y/")
    assert article.title == "Мудрик повернувся до тренувань"
    assert article.text.startswith("Михайло Мудрик повернувся")
    assert article.image_url == "https://cdn.tribuna.com/mudryk.jpg"
    assert article.tags == ["Мудрик", "Челсі"]
    assert article.published_at.startswith("2026-10-15")


def test_parse_article_falls_back_to_paragraphs_and_og():
    url = "https://tribuna.com/uk/news/football-classico/"
    article = parse_article(ARTICLE_FALLBACK_HTML, url)
    assert article.title == "Шахтар переміг Динамо"
    assert article.description == "Донеччани забрали класико."
    assert article.image_url == "https://tribuna.com/img/classico.jpg"
    assert "2:1" in article.text and "Судаков" in article.text
    assert "Меню" not in article.text
    assert "реклама" not in article.text
    assert "Коротко" not in article.text


def test_post_from_ai_output_handles_markdown_fence():
    raw = "```json\n" + json.dumps({
        "title": "🧡 «Всі футболісти заслуговують на оплески»",
        "teaser": "Ахметов прокоментував перемогу «Шахтаря» 💬",
        "hook": "Кого саме виділив 👇",
        "score": "8",
    }, ensure_ascii=False) + "\n```"
    post = post_from_ai_output(raw, "https://tribuna.com/x")
    assert post.title.startswith("🧡 «Всі")
    assert post.hook == "Кого саме виділив 👇"
    assert post.score == 8


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '{"title": "", "teaser": "x"}'])
def test_post_from_ai_output_rejects_bad_output(raw):
    with pytest.raises(ValueError):
        post_from_ai_output(raw, "https://tribuna.com/x")


def test_post_score_is_clamped_and_tolerates_garbage():
    assert post_from_ai_output('{"title": "a", "teaser": "b", "score": 99}', "u").score == 10
    assert post_from_ai_output('{"title": "a", "teaser": "b", "score": "hm"}', "u").score == 0


def test_post_to_html_matches_channel_format_and_limit():
    post = Post(
        title="🧡 «Рахунок <2:1> & драма»",
        teaser="Дуже довгий текст. " * 200,
        hook="Кого саме виділив 👇",
        source_url="https://tribuna.com/uk/news/a-b/?x=1&y=2",
    )
    text = post.to_html(limit=CAPTION_LIMIT)
    assert len(text) <= CAPTION_LIMIT
    assert text.startswith("<b>🧡 «Рахунок &lt;2:1&gt; &amp; драма»</b>\n\n")
    assert text.endswith("…\n\nКого саме виділив 👇")
    assert "tribuna.com" not in text   # посилання — у кнопці, не в тексті


class FakeHttp:
    def __init__(self, fail_methods=()):
        self.fail_methods = set(fail_methods)
        self.calls = []

    def post(self, url, json):
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, json))

        class Resp:
            def __init__(self, data):
                self._data = data

            def json(self):
                return self._data

        if method in self.fail_methods:
            return Resp({"ok": False, "error_code": 400, "description": "bad photo"})
        return Resp({"ok": True, "result": {"message_id": 1}})


def test_publisher_sends_photo_with_read_button():
    http = FakeHttp()
    publisher = TelegramPublisher("t", "@chan", client=http)
    post = Post(title="T", teaser="t", hook="h 👇", source_url="https://tribuna.com/n")
    publisher.publish(post, image_url="https://cdn/x.jpg")
    method, payload = http.calls[0]
    assert method == "sendPhoto"
    assert payload["chat_id"] == "@chan"
    assert payload["reply_markup"] == {"inline_keyboard": [[
        {"text": "ЧИТАТИ ↗", "url": "https://tribuna.com/n"}]]}


def test_publisher_falls_back_to_text_when_photo_fails():
    http = FakeHttp(fail_methods={"sendPhoto"})
    publisher = TelegramPublisher("t", "@chan", client=http)
    post = Post(title="T", teaser="t", source_url="https://tribuna.com/n")
    publisher.publish(post, image_url="https://cdn/x.jpg")
    assert [m for m, _ in http.calls] == ["sendPhoto", "sendMessage"]
    assert http.calls[1][1]["reply_markup"]["inline_keyboard"][0][0]["url"] == \
        "https://tribuna.com/n"


class FakeScraper:
    def __init__(self, listing, articles):
        self.listing = listing
        self.articles = articles

    def list_articles(self, source_url):
        return self.listing

    def fetch_article(self, url):
        return self.articles[url]


class FakeWriter:
    def __init__(self, fail_on=(), scores=None):
        self.fail_on = set(fail_on)
        self.scores = scores or {}

    def write(self, article):
        if article.url in self.fail_on:
            raise RuntimeError("AI down")
        return Post(title="T " + article.title, teaser="t", hook="h",
                    score=self.scores.get(article.url, 9),
                    source_url=article.url)


class FakePublisher:
    def __init__(self):
        self.sent = []

    def publish(self, post, image_url=""):
        self.sent.append((post.source_url, image_url))


def _article(url, title="t", text="x"):
    return Article(url=url, title=title, text=text, image_url=url + ".jpg")


def test_run_once_publishes_oldest_first_and_skips_seen(tmp_path):
    store = SeenStore(str(tmp_path / "seen.db"))
    store.mark_seen("u1")
    listing = ["u0", "u1", "u2", "u3", "u4"]  # новіші першими
    scraper = FakeScraper(listing, {u: _article(u) for u in listing})
    config = Config(source_urls=[SOURCE], max_posts_per_run=3,
                    max_candidates=3, post_delay_seconds=0)
    publisher = FakePublisher()

    assert collect_new_urls(scraper, store, [SOURCE], 3) == ["u3", "u2", "u0"]
    assert run_once(config, scraper, FakeWriter(), publisher, store) == 3
    assert [u for u, _ in publisher.sent] == ["u3", "u2", "u0"]
    assert publisher.sent[0][1] == "u3.jpg"
    assert all(store.is_seen(u) for u in ["u0", "u2", "u3"])
    assert not store.is_seen("u4")


def test_run_once_retries_failed_and_skips_empty(tmp_path):
    store = SeenStore(str(tmp_path / "seen.db"))
    scraper = FakeScraper(["bad", "empty", "ok"], {
        "bad": _article("bad"),
        "empty": _article("empty", text=""),
        "ok": _article("ok"),
    })
    config = Config(source_urls=[SOURCE], max_posts_per_run=5,
                    post_delay_seconds=0)
    publisher = FakePublisher()

    done = run_once(config, scraper, FakeWriter(fail_on={"bad"}), publisher,
                    store)
    assert done == 1
    assert [u for u, _ in publisher.sent] == ["ok"]
    assert store.is_seen("empty")      # порожню не чіпатимемо знову
    assert not store.is_seen("bad")    # збій AI — спробуємо наступного разу


def test_dry_run_does_not_publish_or_remember(tmp_path, capsys):
    store = SeenStore(str(tmp_path / "seen.db"))
    scraper = FakeScraper(["u0"], {"u0": _article("u0", title="Новина")})
    config = Config(source_urls=[SOURCE], post_delay_seconds=0)

    assert run_once(config, scraper, FakeWriter(), None, store,
                    dry_run=True) == 1
    assert "T Новина" in capsys.readouterr().out
    assert not store.is_seen("u0")


def test_run_once_publishes_only_sensational_news(tmp_path):
    store = SeenStore(str(tmp_path / "seen.db"))
    listing = ["u0", "u1", "u2", "u3"]
    scraper = FakeScraper(listing, {u: _article(u) for u in listing})
    writer = FakeWriter(scores={"u3": 3, "u2": 9, "u1": 8, "u0": 10})
    config = Config(source_urls=[SOURCE], max_posts_per_run=1,
                    max_candidates=4, min_score=6, post_delay_seconds=0)
    publisher = FakePublisher()

    assert run_once(config, scraper, writer, publisher, store) == 1
    assert [u for u, _ in publisher.sent] == ["u2"]
    assert store.is_seen("u3")          # нудна — більше не оцінюємо
    assert not store.is_seen("u1")      # ліміт постів — дійде наступного разу

    publisher.sent.clear()
    assert run_once(config, scraper, writer, publisher, store) == 1
    assert [u for u, _ in publisher.sent] == ["u1"]


def test_manual_urls_ignore_score_threshold(tmp_path):
    store = SeenStore(str(tmp_path / "seen.db"))
    scraper = FakeScraper([], {"m": _article("m")})
    config = Config(source_urls=[SOURCE], min_score=6, post_delay_seconds=0)
    publisher = FakePublisher()
    writer = FakeWriter(scores={"m": 1})

    assert run_once(config, scraper, writer, publisher, store, urls=["m"]) == 1
    assert [u for u, _ in publisher.sent] == ["m"]
