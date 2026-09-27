import asyncio
import gzip
import sqlite3
import time
import types
from datetime import datetime

import backups
import timeutils
from update_processing import PerUserUpdateProcessor


def test_bot_module_imports():
    # Catches a broken import or a typo in bot.py before Railway does.
    import bot  # noqa: F401


def test_now_local_is_kyiv_wall_clock():
    expected = datetime.now(timeutils.BOT_TIMEZONE).replace(tzinfo=None)
    assert abs((timeutils.now_local() - expected).total_seconds()) < 5
    assert timeutils.now_local().tzinfo is None


def test_backup_file_restores_to_same_data(business, tmp_path):
    path = backups.make_backup_file(str(tmp_path))

    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(open(path, "rb").read()))

    conn = sqlite3.connect(restored)
    assert conn.execute("SELECT name FROM businesses").fetchall() == [("Салон",)]
    conn.close()


def _update(user_id):
    return types.SimpleNamespace(
        effective_user=types.SimpleNamespace(id=user_id), effective_chat=None
    )


def test_different_users_run_concurrently_same_user_in_order():
    async def scenario():
        processor = PerUserUpdateProcessor()
        log = []

        async def handler(user_id, n):
            log.append(("start", user_id, n))
            await asyncio.sleep(0.2)
            log.append(("end", user_id, n))

        started = time.monotonic()
        await asyncio.gather(*(
            processor.process_update(_update(user), handler(user, 0))
            for user in range(10)
        ))
        parallel_seconds = time.monotonic() - started

        log.clear()
        await asyncio.gather(*(
            processor.process_update(_update(7), handler(7, n))
            for n in range(3)
        ))

        return parallel_seconds, log, processor

    parallel_seconds, log, processor = asyncio.run(scenario())

    assert parallel_seconds < 1.0
    assert [(event, n) for event, _, n in log] == [
        ("start", 0), ("end", 0),
        ("start", 1), ("end", 1),
        ("start", 2), ("end", 2),
    ]
    assert processor._locks == {}


def _per_message_warnings(install_bot_filter):
    import importlib
    import warnings

    import bot
    from contact_settings import get_setcontact_handler, get_setfaq_handler
    from location_settings import get_setlocation_handler
    from schedule import get_schedule_handler

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if install_bot_filter:
            importlib.reload(bot)
        get_schedule_handler()
        get_setlocation_handler()
        get_setcontact_handler()
        get_setfaq_handler()

    return [w for w in caught if "per_message" in str(w.message)]


def test_per_message_warning_is_silenced_by_bot():
    # Without bot.py's filter PTB does warn, so the check below is real.
    assert _per_message_warnings(install_bot_filter=False)
    assert not _per_message_warnings(install_bot_filter=True)
