from __future__ import annotations

import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, redirect, request, session, url_for
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

BASE_DIR = Path(__file__).resolve().parent.parent
PUBLIC_ENDPOINTS = {"auth.login", "auth.setup", "static"}


def data_dir() -> Path:
    default = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "TURfitgear" / "data"
    return Path(os.environ.get("TUR_DATA_DIR") or default)


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
    from .backup import backup_if_due
    from .formatting import register_filters
    from .views import auth, balances, designs, directory, orders, payments, reports, settings

    for module in (auth, balances, designs, directory, orders, payments, reports, settings):
        app.register_blueprint(module.bp)
    register_filters(app)

    with app.app_context():
        db.create_all()
        if not app.testing:
            backup_if_due(app, force=True)

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
