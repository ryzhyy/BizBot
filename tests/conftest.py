import os
import sys
import tempfile
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Importing bot.py initializes DB_PATH, so point it away from a
# developer's real bizbot_v06.db before anything imports database.
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "import.db")
# bot.py refuses to import without these; tests never talk to Telegram
# or OpenAI.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:test")
os.environ.setdefault("OPENAI_API_KEY", "test")

import database  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh, fully migrated database file for one test."""
    monkeypatch.setattr(database, "DB_NAME", str(tmp_path / "test.db"))
    database.init_database()
    return database


@pytest.fixture
def business(db):
    """Business 1, open Mon-Sat 09:00-18:00, closed Sunday, with a
    90-minute service (id 1) and a 60-minute service (id 2)."""
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO businesses (id, owner_telegram_id, name, slug) "
        "VALUES (1, 100, 'Салон', 'salon')"
    )
    conn.execute(
        "INSERT INTO services (id, business_id, name, price, duration) "
        "VALUES (1, 1, 'Фарбування', 900, 90), (2, 1, 'Стрижка', 300, 60)"
    )
    conn.commit()
    conn.close()

    for weekday in range(6):
        db.set_working_hours(1, weekday, "09:00", "18:00", 1)
    db.set_working_hours(1, 6, None, None, 0)

    return 1


# Monday 2026-10-05, a normal working day, seen from Sunday morning.
MONDAY = "2026-10-05"
SUNDAY = "2026-10-04"
FROZEN_NOW = datetime(2026, 10, 4, 9, 0)


@pytest.fixture
def frozen_now(monkeypatch):
    import bookings

    monkeypatch.setattr(bookings, "now_local", lambda: FROZEN_NOW)
    return FROZEN_NOW
