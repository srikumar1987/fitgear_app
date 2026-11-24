from __future__ import annotations

import os
import tempfile
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, session, url_for
from sqlalchemy import select
from werkzeug.security import check_password_hash

from .. import db, gdrive
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
        drive=gdrive.status(_data_dir()), app_url=request.host_url,
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


def _data_dir() -> Path:
    return Path(current_app.config["DATA_DIR"])


@bp.post("/settings/gdrive/client")
def gdrive_client():
    upload = request.files.get("client")
    try:
        if not upload or not upload.filename:
            raise gdrive.DriveError("Choose the JSON file you downloaded from Google Cloud.")
        gdrive.save_client(_data_dir(), upload.read(64 * 1024))
    except gdrive.DriveError as exc:
        flash(str(exc), "danger")
    else:
        audit("setting", None, "gdrive_client", "added")
        db.session.commit()
        flash("Google client saved. Now click 'Connect Google Drive'.", "success")
    return redirect(url_for("settings.index", _anchor="gdrive"))


@bp.post("/settings/gdrive/remove-client")
def gdrive_remove_client():
    gdrive.remove_client(_data_dir())
    flash("Google client removed.", "success")
    return redirect(url_for("settings.index", _anchor="gdrive"))


@bp.get("/settings/gdrive/connect")
def gdrive_connect():
    state, verifier = gdrive.new_pkce()
    redirect_uri = request.host_url  # Google sends the browser back to the app's own address
    try:
        url = gdrive.auth_url(_data_dir(), redirect_uri, state, verifier)
    except gdrive.DriveError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("settings.index", _anchor="gdrive"))
    session.update(gdrive_state=state, gdrive_verifier=verifier, gdrive_redirect=redirect_uri)
    return redirect(url)


@bp.get("/settings/gdrive/callback")
def gdrive_callback():
    expected = session.pop("gdrive_state", None)
    verifier, redirect_uri = session.pop("gdrive_verifier", None), session.pop("gdrive_redirect", None)
    if request.args.get("error"):
        flash("Google sign-in was cancelled or refused.", "danger")
    elif not expected or request.args.get("state") != expected or not verifier:
        flash("That sign-in link has expired. Click 'Connect Google Drive' again.", "danger")
    else:
        try:
            email = gdrive.connect(_data_dir(), request.args.get("code", ""), redirect_uri, verifier)
        except gdrive.DriveError as exc:
            flash(str(exc), "danger")
        else:
            audit("setting", None, "gdrive_connect", email)
            db.session.commit()
            try:
                result = gdrive.sync(_data_dir(), make_backup(current_app, cloud=False),
                                     Path(current_app.config["UPLOAD_DIR"]))
                flash(f"Connected to Google Drive as {email}. Uploaded: {result}.", "success")
            except gdrive.DriveError as exc:
                flash(f"Connected as {email}, but the first upload failed: {exc}", "danger")
    return redirect(url_for("settings.index", _anchor="gdrive"))


@bp.post("/settings/gdrive/sync")
def gdrive_sync():
    try:
        result = gdrive.sync(_data_dir(), make_backup(current_app, cloud=False), Path(current_app.config["UPLOAD_DIR"]))
    except gdrive.DriveError as exc:
        flash(f"Google Drive upload failed: {exc}", "danger")
    else:
        flash(f"Uploaded to Google Drive: {result}.", "success")
    return redirect(url_for("settings.index", _anchor="gdrive"))


@bp.post("/settings/gdrive/disconnect")
def gdrive_disconnect():
    gdrive.disconnect(_data_dir())
    audit("setting", None, "gdrive_disconnect", "")
    db.session.commit()
    flash("Google Drive disconnected. Files already in your Drive stay there.", "success")
    return redirect(url_for("settings.index", _anchor="gdrive"))


@bp.post("/settings/stop")
def stop():
    """Stop the background server. A backup is taken (and uploaded) first; start.bat starts it again."""
    if not current_app.testing:
        try:
            path = make_backup(current_app, cloud=False)
            if gdrive.is_connected(_data_dir()):
                gdrive.safe_sync(_data_dir(), path, Path(current_app.config["UPLOAD_DIR"]))
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
