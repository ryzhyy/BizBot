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

    for version, name, module in _discover_migrations():
        if version in applied:
            continue

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
