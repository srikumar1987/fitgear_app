"""Restore the live database from a backup file (downloaded from Google Drive or chosen by the user)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import db
from .backup import db_path, make_backup
from .gdrive import readonly_uri

REQUIRED_TABLES = {"admin", "customer", "orders", "ledger"}


class RestoreError(Exception):
    """Shown to the user as-is."""


def inspect_backup(path: Path) -> dict:
    """Check the file is a healthy TUR fitgear database; returns counts for the confirmation message."""
    path = Path(path)
    with path.open("rb") as handle:
        if handle.read(16) != b"SQLite format 3\x00":
            raise RestoreError("That file is not a TUR fitgear backup (.db).")
    try:
        conn = sqlite3.connect(readonly_uri(path), uri=True)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RestoreError("That backup file is damaged. Try an older backup.")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not REQUIRED_TABLES <= tables:
                raise RestoreError("That database is not from the TUR fitgear app.")
            count = lambda table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            return {"customers": count("customer"), "orders": count("orders"), "payments": count("ledger")}
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise RestoreError(f"That backup file could not be read ({exc}).") from None


def restore_database(app, source: Path) -> dict:
    """Replace the live database with `source`. A safety copy of the current data is made first."""
    counts = inspect_backup(source)
    safety = make_backup(app, cloud=False)
    db.session.remove()
    db.engine.dispose()
    src = sqlite3.connect(readonly_uri(source), uri=True)
    dest = sqlite3.connect(db_path(app))
    try:
        src.backup(dest)  # page-by-page copy into the live file; safe on Windows (no file replace)
    finally:
        dest.close()
        src.close()
    db.engine.dispose()
    db.create_all()  # tables added to the app after this backup was made
    return {**counts, "safety_copy": safety.name}
