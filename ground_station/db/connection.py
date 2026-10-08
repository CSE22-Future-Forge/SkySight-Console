"""SQLite connections: PRAGMAs, one connection per thread, schema + migrations.

Connections run in autocommit mode (``isolation_level=None``); writes go through
``Database.tx()`` or ``EventBus.unit()``, which issue ``BEGIN IMMEDIATE`` so the
write lock is taken up front and the claim race is decided by SQLite, not Python.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
MIGRATIONS_DIR = Path(__file__).with_name("migrations")

PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
)


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            # check_same_thread=False only so close_all() can run from the main thread.
            c = sqlite3.connect(self.path, isolation_level=None, timeout=5, check_same_thread=False)
            c.row_factory = sqlite3.Row
            for pragma in PRAGMAS:
                c.execute(pragma)
            self._local.conn = c
            with self._lock:
                self._all.append(c)
        return c

    @contextmanager
    def tx(self):
        """Write transaction without events (settings, migrations, maintenance)."""
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        c.execute("COMMIT")

    @contextmanager
    def read(self):
        """Read transaction: every query inside sees one consistent snapshot."""
        c = self.conn()
        c.execute("BEGIN")
        try:
            yield c
        finally:
            c.execute("COMMIT")

    def init(self) -> int:
        """Apply schema.sql, then any newer migrations. Returns the schema version."""
        c = self.conn()
        c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        current = c.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
        for version, path in _migrations():
            if version <= current:
                continue
            c.executescript(
                "BEGIN IMMEDIATE;\n"
                + path.read_text(encoding="utf-8")
                + f"\nINSERT INTO schema_version (version, applied_at) "
                f"VALUES ({version}, CAST(strftime('%s','now') AS INTEGER) * 1000);\nCOMMIT;"
            )
            current = version
        return current

    def close_all(self) -> None:
        with self._lock:
            for c in self._all:
                try:
                    c.close()
                except sqlite3.Error:
                    pass
            self._all.clear()
        self._local = threading.local()


def _migrations() -> list[tuple[int, Path]]:
    found = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        m = re.match(r"(\d+)_", path.name)
        if m:
            found.append((int(m.group(1)), path))
    return sorted(found)
