from __future__ import annotations

import hashlib
import os
import secrets
import threading
from datetime import timedelta
from pathlib import Path

from flask import Flask, abort, redirect, request, session, url_for
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

BASE_DIR = Path(__file__).resolve().parent.parent
PUBLIC_ENDPOINTS = {"auth.login", "auth.setup", "static", "control_build", "control_stop"}
CONTROL_TOKEN_FILE = "control.token"


def data_dir() -> Path:
    default = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "TURfitgear" / "data"
    return Path(os.environ.get("TUR_DATA_DIR") or default)


def build_id() -> str:
    """Fingerprint of the app's code and pages; changes after every update (git pull / new ZIP)."""
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).resolve().parent.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".html", ".css", ".js"}:
            stat = path.stat()
            digest.update(f"{path.relative_to(BASE_DIR)}|{stat.st_size}|{int(stat.st_mtime)}".encode())
    return digest.hexdigest()[:16]


def _secret_key(data_dir: Path) -> str:
    key_file = data_dir / "secret.key"
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32))
    return key_file.read_text().strip()


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///" + (folder / "tur_fitgear.db").as_posix(),
        DATA_DIR=folder,
        BACKUP_DIR=Path(os.environ.get("TUR_BACKUP_DIR") or BASE_DIR / "backups"),
        BACKUP_KEEP=30,
        UPLOAD_DIR=Path(os.environ.get("TUR_UPLOAD_DIR") or BASE_DIR / "uploads"),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        SECRET_KEY=None,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = _secret_key(folder)

    db.init_app(app)

    from . import models  # noqa: F401
    from .backup import backup_if_due, start_daily_timer
    from .formatting import register_filters
    from .views import auth, balances, designs, directory, orders, payments, reports, settings

    for module in (auth, balances, designs, directory, orders, payments, reports, settings):
        app.register_blueprint(module.bp)
    register_filters(app)

    with app.app_context():
        db.create_all()
        if not app.testing:
            backup_if_due(app, force=True)
    if not app.testing:
        start_daily_timer(app)

    # Lets start.bat find out whether the running copy is outdated and stop it (local machine only).
    app.config["BUILD_ID"] = build_id()
    if not app.testing:
        (folder / CONTROL_TOKEN_FILE).write_text(secrets.token_hex(24))

    def _local_only():
        if request.remote_addr not in ("127.0.0.1", "::1"):
            abort(403)

    @app.get("/_control/build", endpoint="control_build")
    def control_build():
        _local_only()
        return app.config["BUILD_ID"]

    @app.post("/_control/stop", endpoint="control_stop")
    def control_stop():
        _local_only()
        token_file = Path(app.config["DATA_DIR"]) / CONTROL_TOKEN_FILE
        expected = token_file.read_text().strip() if token_file.exists() else ""
        if not expected or not secrets.compare_digest(request.headers.get("X-Control-Token", ""), expected):
            abort(403)
        from .backup import make_backup

        try:
            make_backup(app, cloud=False)
        except Exception:
            app.logger.exception("Backup before restart failed")
        app.logger.info("Stopped by start.bat to run the updated version")
        if not app.testing:
            threading.Timer(0.5, lambda: os._exit(0)).start()
        return "stopping"

    @app.before_request
    def require_login():
        if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if not session.get("admin"):
            return redirect(url_for("auth.login", next=request.full_path))
        session.permanent = True
        if not app.testing:
            backup_if_due(app)
        return None

    return app
