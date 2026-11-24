from __future__ import annotations

import os
import tempfile
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import select
from werkzeug.security import check_password_hash

from .. import db
from ..backup import backup_dir, check_backup_dir, custom_backup_dir, list_backups, make_backup
from ..export import build_workbook
from ..models import Admin, audit, set_setting
from ..sizes import get_scale, normalise_size
from .auth import MIN_PASSWORD, hash_password

bp = Blueprint("settings", __name__)


@bp.get("/settings")
def index():
    backups = [
        {"name": p.name, "size_kb": max(1, p.stat().st_size // 1024), "time": datetime.fromtimestamp(p.stat().st_mtime)}
        for p in list_backups(current_app)[:10]
    ]
    return render_template(
        "settings/index.html", scale=", ".join(get_scale()), backups=backups,
        backup_dir=backup_dir(current_app), custom_dir=custom_backup_dir(),
        default_dir=current_app.config["BACKUP_DIR"], data_dir=current_app.config["DATA_DIR"],
    )


@bp.post("/settings/backup-folder")
def save_backup_folder():
    folder = request.form.get("folder", "").strip()
    if not folder:
        set_setting("backup_dir", "")
        db.session.commit()
        flash(f"Backups go to the default folder again: {current_app.config['BACKUP_DIR']}", "success")
        return redirect(url_for("settings.index"))
    try:
        path = check_backup_dir(current_app, folder)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("settings.index"))
    set_setting("backup_dir", str(path))
    audit("setting", None, "backup_dir", str(path))
    db.session.commit()
    try:
        saved = make_backup(current_app)
    except Exception as exc:  # shown to the single admin so they can fix the folder
        flash(f"Folder saved, but the first backup failed: {exc}", "danger")
    else:
        flash(f"Backups now go to {path}. First backup saved ({saved.name}); design files copied too.", "success")
    return redirect(url_for("settings.index"))


@bp.get("/settings/download-backup")
def download_backup():
    path = make_backup(current_app)
    return send_file(path, as_attachment=True, download_name=path.name, mimetype="application/octet-stream")


@bp.get("/settings/download-designs")
def download_designs():
    folder = Path(current_app.config["UPLOAD_DIR"])
    files = [f for f in folder.iterdir() if f.is_file()] if folder.is_dir() else []
    if not files:
        flash("There are no design or after-print files yet.", "danger")
        return redirect(url_for("settings.index"))
    archive = tempfile.TemporaryFile()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:  # images are already compressed
        for file in files:
            zf.write(file, file.name)
    archive.seek(0)
    return send_file(archive, as_attachment=True, mimetype="application/zip",
                     download_name=f"tur_fitgear_designs_{datetime.now():%Y%m%d}.zip")


@bp.post("/settings/sizes")
def save_sizes():
    sizes: list[str] = []
    for part in request.form.get("scale", "").split(","):
        size = normalise_size(part)
        if size and size not in sizes:
            sizes.append(size)
    if not sizes:
        flash("Enter at least one size", "danger")
    else:
        set_setting("size_scale", ",".join(sizes))
        db.session.commit()
        flash("Size scale saved", "success")
    return redirect(url_for("settings.index"))


@bp.post("/settings/backup")
def backup_now():
    try:
        path = make_backup(current_app)
    except Exception as exc:  # shown to the single admin so they can fix the folder
        flash(f"Backup failed: {exc}", "danger")
    else:
        flash(f"Backup saved: {path.name}", "success")
    return redirect(url_for("settings.index"))


@bp.get("/settings/export")
def export():
    return send_file(
        build_workbook(), as_attachment=True,
        download_name=f"tur_fitgear_export_{datetime.now():%Y%m%d_%H%M}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.post("/settings/stop")
def stop():
    """Stop the background server. A backup is taken first; start.bat starts it again."""
    if not current_app.testing:
        try:
            make_backup(current_app)
        except Exception:
            current_app.logger.exception("Backup before stopping failed")
        current_app.logger.info("Stopped from Settings")
        threading.Timer(0.5, lambda: os._exit(0)).start()
    return render_template("settings/stopped.html")


@bp.post("/settings/password")
def change_password():
    admin = db.session.scalars(select(Admin)).first()
    new = request.form.get("new", "")
    if not check_password_hash(admin.password_hash, request.form.get("current", "")):
        flash("Current password is wrong", "danger")
    elif len(new) < MIN_PASSWORD:
        flash(f"Use at least {MIN_PASSWORD} characters", "danger")
    elif new != request.form.get("confirm", ""):
        flash("The new passwords do not match", "danger")
    else:
        admin.password_hash = hash_password(new)
        db.session.commit()
        flash("Password changed", "success")
    return redirect(url_for("settings.index"))
