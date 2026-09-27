import logging
import os
import sys


class _BelowWarning(logging.Filter):
    def filter(self, record):
        return record.levelno < logging.WARNING


def configure_logging():
    """Timestamps and levels on every line, and LOG_LEVEL=DEBUG to see
    per-message details like parsed AI intents.

    Railway marks every stderr line as an error, so routine INFO/DEBUG
    lines go to stdout and only WARNING and above go to stderr."""
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    stdout = logging.StreamHandler(sys.stdout)
    stdout.addFilter(_BelowWarning())
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(logging.WARNING)

    for handler in (stdout, stderr):
        handler.setFormatter(formatter)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        handlers=[stdout, stderr],
        force=True,
    )
    # httpx logs every Telegram long-poll request at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
