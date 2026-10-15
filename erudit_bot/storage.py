"""Пам'ять про вже опубліковані новини, щоб не дублювати пости."""

import sqlite3
from datetime import datetime, timezone


class SeenStore:

    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS published_news (
                url TEXT PRIMARY KEY,
                title TEXT,
                published_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def is_seen(self, url):
        row = self.conn.execute(
            "SELECT 1 FROM published_news WHERE url = ?", (url,)
        ).fetchone()
        return row is not None

    def mark_seen(self, url, title=""):
        self.conn.execute(
            "INSERT OR IGNORE INTO published_news (url, title, published_at) "
            "VALUES (?, ?, ?)",
            (url, title, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
