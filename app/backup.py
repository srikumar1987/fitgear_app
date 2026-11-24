from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

PREFIX = "tur_fitgear_"


def db_path(app) -> Path:
    return Path(app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "", 1))


def make_backup(app) -> Path:
    """Copy the live database using SQLite's online backup API (safe while the app is running)."""
    dest_dir = Path(app.config["BACKUP_DIR"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{PREFIX}{datetime.now():%Y%m%d_%H%M%S}.db"
    src_conn = sqlite3.connect(db_path(app))
    dest_conn = sqlite3.connect(dest)
    try:
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()
    for old in list_backups(app)[app.config["BACKUP_KEEP"]:]:
        old.unlink(missing_ok=True)
    app.config["LAST_BACKUP_DAY"] = date.today()
    return dest


def backup_if_due(app, force: bool = False) -> None:
    if force or app.config.get("LAST_BACKUP_DAY") != date.today():
        try:
            make_backup(app)
        except Exception:
            app.config["LAST_BACKUP_DAY"] = date.today()
            app.logger.exception("Automatic backup failed")


def list_backups(app) -> list[Path]:
    folder = Path(app.config["BACKUP_DIR"])
    if not folder.exists():
        return []
    return sorted(folder.glob(f"{PREFIX}*.db"), reverse=True)
