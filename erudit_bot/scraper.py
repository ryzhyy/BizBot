"""Збір новин з tribuna.com.

Розмітка сайту може змінюватися, тому парсер не прив'язаний до CSS-класів:
статтю читаємо з JSON-LD (NewsArticle), og:-мета-тегів і, як запасний
варіант, з абзаців <p> всередині <article>.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 20
# Більше тексту AI не потрібно для короткого поста, а промпт дешевшає.
MAX_ARTICLE_CHARS = 6000
MIN_PARAGRAPH_CHARS = 40

# Слаг статті: щось довше за службові сегменти на кшталт "page" чи "2".
_SLUG_RE = re.compile(r"^[\w-]*[a-z\d][\w-]*-[\w-]+$", re.IGNORECASE)
_SKIP_SEGMENTS = {"page", "tag", "tags", "author", "authors", "rss", "amp"}
_PAGINATION_RE = re.compile(r"^page-?\d+$", re.IGNORECASE)


@dataclass
class Article:
    url: str
    title: str
    text: str
    description: str = ""
    image_url: str = ""
    published_at: str = ""
    tags: list = field(default_factory=list)


def is_article_url(url, source_url):
    """Чи схоже посилання на окрему новину того ж сайту."""
    parsed = urlparse(url)
    source = urlparse(source_url)
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.netloc.removeprefix("www.") != source.netloc.removeprefix("www."):
        return False
    segments = [s for s in parsed.path.split("/") if s]
    if "news" not in segments:
        return False
    after_news = segments[segments.index("news") + 1:]
    if not after_news:
        return False
    if any(s.lower() in _SKIP_SEGMENTS or _PAGINATION_RE.match(s)
           for s in after_news):
        return False
    return bool(_SLUG_RE.match(after_news[-1]))


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


def extract_article_links(html, source_url):
    """Унікальні посилання на новини в порядку появи на сторінці."""
    parser = _LinkParser()
    parser.feed(html)
    seen = set()
    result = []
    for href in parser.links:
        url = urldefrag(urljoin(source_url, href)).url
        if url.rstrip("/") == source_url.rstrip("/"):
            continue
        if url in seen or not is_article_url(url, source_url):
            continue
        seen.add(url)
        result.append(url)
    return result


class _ArticleParser(HTMLParser):
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "figure", "figcaption"}
    _BLOCK_TAGS = {"p", "h2", "h3", "li", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.json_ld = []
        self.h1 = ""
        self.article_blocks = []
        self.all_blocks = []
        self._skip_depth = 0
        self._in_json_ld = False
        self._json_buf = []
        self._article_depth = 0
        self._in_h1 = False
        self._block_tag = None
        self._block_buf = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key and attrs.get("content") and key not in self.meta:
                self.meta[key.lower()] = attrs["content"].strip()
            return
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self._in_json_ld = True
            self._json_buf = []
            return
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "article":
            self._article_depth += 1
        elif tag == "h1" and not self.h1:
            self._in_h1 = True
            self._block_buf = []
        elif tag in self._BLOCK_TAGS and self._block_tag is None:
            self._block_tag = tag
            self._block_buf = []

    def handle_endtag(self, tag):
        if tag == "script" and self._in_json_ld:
            self._in_json_ld = False
            self.json_ld.append("".join(self._json_buf))
            return
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "article":
            self._article_depth = max(0, self._article_depth - 1)
        elif tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.h1 = _clean("".join(self._block_buf))
        elif tag == self._block_tag:
            text = _clean("".join(self._block_buf))
            if text:
                self.all_blocks.append(text)
                if self._article_depth:
                    self.article_blocks.append(text)
            self._block_tag = None

    def handle_data(self, data):
        if self._in_json_ld:
            self._json_buf.append(data)
        elif self._skip_depth:
            return
        elif self._in_h1 or self._block_tag:
            self._block_buf.append(data)


def _clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _iter_json_ld(raw_blocks):
    for raw in raw_blocks:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if not isinstance(item, dict):
                continue
            if "@graph" in item and isinstance(item["@graph"], list):
                stack.extend(item["@graph"])
            yield item


def _news_json_ld(raw_blocks):
    for item in _iter_json_ld(raw_blocks):
        kind = item.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if any(k in ("NewsArticle", "Article", "ReportageNewsArticle") for k in kinds):
            return item
    return {}


def _image_from_json_ld(image):
    if isinstance(image, str):
        return image
    if isinstance(image, list) and image:
        return _image_from_json_ld(image[0])
    if isinstance(image, dict):
        return image.get("url", "")
    return ""


def parse_article(html, url):
    parser = _ArticleParser()
    parser.feed(html)
    ld = _news_json_ld(parser.json_ld)
    meta = parser.meta

    title = _clean(
        ld.get("headline") or meta.get("og:title") or parser.h1 or meta.get("title")
    )
    description = _clean(ld.get("description") or meta.get("og:description")
                         or meta.get("description"))

    text = _clean(ld.get("articleBody"))
    if not text:
        blocks = parser.article_blocks or parser.all_blocks
        blocks = [b for b in blocks if len(b) >= MIN_PARAGRAPH_CHARS]
        text = "\n\n".join(blocks)
    if not text:
        text = description

    keywords = ld.get("keywords") or meta.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",")]

    image = _image_from_json_ld(ld.get("image")) or meta.get("og:image", "")

    return Article(
        url=url,
        title=title,
        text=text[:MAX_ARTICLE_CHARS],
        description=description,
        image_url=urljoin(url, image) if image else "",
        published_at=ld.get("datePublished") or meta.get(
            "article:published_time", ""),
        tags=[k for k in keywords if k][:10],
    )


class TribunaScraper:

    def __init__(self, client=None):
        self.client = client or httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "uk,en;q=0.8",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
        )

    def _get(self, url):
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def list_articles(self, source_url):
        return extract_article_links(self._get(source_url), source_url)

    def fetch_article(self, url):
        return parse_article(self._get(url), url)
