from __future__ import annotations

import hashlib
import shutil
import sqlite3
import threading
import time
from datetime import date, datetime
from pathlib import Path

PREFIX = "tur_fitgear_"


def db_path(app) -> Path:
    return Path(app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "", 1))


def custom_backup_dir() -> str:
    """Folder chosen in Settings (e.g. a Google Drive folder); empty means the default."""
    from .models import get_setting

    return get_setting("backup_dir")


def backup_dir(app) -> Path:
    return Path(custom_backup_dir() or app.config["BACKUP_DIR"])


def check_backup_dir(app, folder: str) -> Path:
    """Validate a folder chosen in Settings: absolute, writable, and not the live data folder."""
    path = Path(folder.strip().strip('"'))
    if not path.is_absolute():
        raise ValueError("Use a full path, for example G:\\My Drive\\TUR fitgear backups")
    data = Path(app.config["DATA_DIR"]).resolve()
    resolved = path.resolve()
    if resolved == data or data in resolved.parents:
        raise ValueError("Choose a folder outside the live data folder")
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".tur_write_test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        raise ValueError(f"Cannot write to that folder ({exc.strerror or exc})") from None
    return path


def make_backup(app, cloud: bool = True) -> Path:
    """Copy the live database using SQLite's online backup API (safe while the app is running).

    If nothing changed since the latest backup, no new file is kept and the latest backup is returned
    (check with `is_new_backup`). When Google Drive is connected the backup is also uploaded (in the
    background) unless cloud=False; an already-uploaded backup is not uploaded again.
    """
    dest_dir = backup_dir(app)
    dest_dir.mkdir(parents=True, exist_ok=True)
    previous = list_backups(app)
    stamp = f"{PREFIX}{datetime.now():%Y%m%d_%H%M%S}"
    dest, n = dest_dir / f"{stamp}.db", 1
    while dest.exists():
        n += 1
        dest = dest_dir / f"{stamp}_{n}.db"
    src_conn = sqlite3.connect(db_path(app))
    dest_conn = sqlite3.connect(dest)
    try:
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()
    if previous and _sha256(dest) == _sha256(previous[0]):
        dest.unlink()  # no changes since the latest backup
        dest = previous[0]
        app.config["LAST_BACKUP_NEW"] = False
    else:
        app.config["LAST_BACKUP_NEW"] = True
        for old in list_backups(app)[app.config["BACKUP_KEEP"]:]:
            old.unlink(missing_ok=True)
    if custom_backup_dir():
        mirror_uploads(app, dest_dir)
    app.config["LAST_BACKUP_DAY"] = date.today()
    if cloud:
        from . import gdrive

        data_dir = Path(app.config["DATA_DIR"])
        if gdrive.is_connected(data_dir):
            args = (data_dir, dest, Path(app.config["UPLOAD_DIR"]))
            gdrive.safe_sync(*args) if app.testing else gdrive.sync_in_background(*args)
    return dest


def is_new_backup(app) -> bool:
    """Whether the last make_backup call saved a new file (False = nothing had changed)."""
    return bool(app.config.get("LAST_BACKUP_NEW"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def start_daily_timer(app, every_seconds: int = 1800) -> None:
    """Check every 30 minutes, so the daily backup happens even on days nobody opens the app."""
    def loop():
        while True:
            time.sleep(every_seconds)
            with app.app_context():
                backup_if_due(app)

    threading.Thread(target=loop, daemon=True, name="daily-backup").start()


def mirror_uploads(app, dest_dir: Path) -> int:
    """Copy design / after-print files that are not in the backup folder yet. Nothing is deleted there."""
    src = Path(app.config["UPLOAD_DIR"])
    if not src.is_dir():
        return 0
    target = dest_dir / "uploads"
    copied = 0
    for file in src.iterdir():
        if file.is_file() and not (target / file.name).exists():
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, target / file.name)
            copied += 1
    return copied


def backup_if_due(app, force: bool = False) -> None:
    if force or app.config.get("LAST_BACKUP_DAY") != date.today():
        try:
            make_backup(app)
        except Exception:
            app.config["LAST_BACKUP_DAY"] = date.today()
            app.logger.exception("Automatic backup failed")


def list_backups(app) -> list[Path]:
    folder = backup_dir(app)
    if not folder.exists():
        return []
    return sorted(folder.glob(f"{PREFIX}*.db"), reverse=True)
