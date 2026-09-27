"""Versioned schema migrations for the BizBot SQLite database.

Each migration lives in its own module named ``NNNN_description.py`` and
exposes an ``up(cursor)`` function. Applied versions are tracked in the
``schema_migrations`` table so every migration runs at most once, in
order, and each one is wrapped in its own transaction (rolled back whole
on failure, never partially applied).

To add a migration: create ``migrations/000N_name.py`` with a module
docstring and an ``up(cursor)`` function, then nothing else — it is
picked up automatically next time ``run_migrations`` is called.
"""

import importlib
import pkgutil
import sqlite3
from datetime import datetime


def _discover_migrations():
    modules = []

    for module_info in pkgutil.iter_modules(__path__):
        name = module_info.name

        if module_info.ispkg or not name[:4].isdigit():
            continue

        version = int(name[:4])
        module = importlib.import_module(f"{__name__}.{name}")
        modules.append((version, name, module))

    modules.sort(key=lambda item: item[0])
    return modules


def _backup_before_migrating(conn, first_pending_version):
    """Snapshot the database file next to itself before any pending
    migration touches it, so a bad deploy can be rolled back by
    swapping the file. Skipped for a brand-new (empty) database."""
    cursor = conn.cursor()

    has_data = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'businesses'"
    ).fetchone()

    if not has_data:
        return None

    db_path = next(
        (row[2] for row in cursor.execute("PRAGMA database_list")
         if row[1] == "main"),
        ""
    )

    if not db_path:
        return None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{db_path}.pre-{first_pending_version:04d}-{stamp}.bak"

    # The backup API copies a consistent snapshot even in WAL mode,
    # unlike a plain file copy that could miss un-checkpointed pages.
    backup_conn = sqlite3.connect(backup_path)
    try:
        conn.backup(backup_conn)
    finally:
        backup_conn.close()

    print(f"Database backed up to {backup_path} before migrating")
    return backup_path


def run_migrations(conn):
    # DDL in Python's sqlite3 driver auto-commits under the default
    # isolation level, which would make multi-statement migrations
    # (e.g. table rebuilds) non-atomic. Autocommit mode plus explicit
    # BEGIN/COMMIT below gives each migration real transaction safety.
    conn.isolation_level = None
    cursor = conn.cursor()

    # Table rebuilds (used to add constraints SQLite can't ALTER in)
    # temporarily drop and recreate tables, which would trip FK
    # enforcement mid-flight. This pragma only affects this connection.
    cursor.execute("PRAGMA foreign_keys = OFF")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute("SELECT version FROM schema_migrations")
    applied = {row[0] for row in cursor.fetchall()}

    pending = [
        migration for migration in _discover_migrations()
        if migration[0] not in applied
    ]

    if pending:
        _backup_before_migrating(conn, pending[0][0])

    for version, name, module in pending:
        cursor.execute("BEGIN IMMEDIATE")

        try:
            module.up(cursor)
            cursor.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (version, name)
            )
        except Exception:
            cursor.execute("ROLLBACK")
            raise
        else:
            cursor.execute("COMMIT")

    violations = cursor.execute("PRAGMA foreign_key_check").fetchall()

    if violations:
        raise RuntimeError(
            "Schema migrations left dangling foreign keys, refusing to "
            f"enable enforcement: {violations}"
        )

    cursor.execute("PRAGMA foreign_keys = ON")
