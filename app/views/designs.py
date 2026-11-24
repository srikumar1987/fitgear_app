"""Design mockups and after-print photos attached to an order."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from flask import Blueprint, abort, current_app, flash, redirect, request, send_from_directory, url_for

from .. import db
from ..models import FILE_KINDS, IMAGE_EXTENSIONS, Order, OrderFile, OrderItem, audit
from .common import error, get_by_id, saved, text

bp = Blueprint("designs", __name__)
ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf"}


def upload_dir() -> Path:
    folder = Path(current_app.config["UPLOAD_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def remove_stored(stored_name: str) -> None:
    (upload_dir() / stored_name).unlink(missing_ok=True)


def _back(order_id: int):
    return redirect(url_for("orders.detail", order_id=order_id, tab="designs"))


def _item_for(order_id: int, raw) -> OrderItem | None:
    item = get_by_id(OrderItem, raw)
    return item if item and item.order_id == order_id else None


@bp.post("/orders/<int:order_id>/files/upload")
def upload(order_id: int):
    order = db.get_or_404(Order, order_id)
    kind = request.form.get("kind")
    if kind not in FILE_KINDS:
        abort(400)
    uploads = [f for f in request.files.getlist("files") if f and f.filename]
    if not uploads:
        flash("Choose one or more files to upload", "danger")
        return _back(order.id)

    item, note, added, skipped = _item_for(order.id, request.form.get("item_id")), text(request.form.get("note")), 0, []
    for upload_file in uploads:
        original = Path(upload_file.filename).name
        ext = Path(original).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            skipped.append(original)
            continue
        stored = f"{order.id}_{uuid4().hex}{ext}"
        upload_file.save(upload_dir() / stored)
        record = OrderFile(order=order, item=item, kind=kind, stored_name=stored, original_name=original[:255], note=note)
        db.session.add(record)
        db.session.flush()
        audit("file", record.id, "upload", f"{kind} {original}")
        added += 1
    db.session.commit()
    if added:
        flash(f"Uploaded {added} file{'s' if added != 1 else ''} to {FILE_KINDS[kind]}", "success")
    if skipped:
        flash(f"Skipped (only images and PDF allowed): {', '.join(skipped)}", "danger")
    return _back(order.id)


@bp.get("/files/<int:file_id>")
def serve(file_id: int):
    record = db.get_or_404(OrderFile, file_id)
    return send_from_directory(
        upload_dir(), record.stored_name, download_name=record.original_name,
        as_attachment=request.args.get("download") == "1", max_age=3600,
    )


@bp.post("/files/<int:file_id>/update")
def update(file_id: int):
    record = db.get_or_404(OrderFile, file_id)
    if "note" in request.form:
        record.note = text(request.form["note"])[:300]
    if "item_id" in request.form:
        raw = request.form["item_id"]
        item = _item_for(record.order_id, raw)
        if raw and item is None:
            return error("Choose an item from this order")
        record.item = item
    db.session.commit()
    return saved()


@bp.post("/files/<int:file_id>/approve")
def approve(file_id: int):
    record = db.get_or_404(OrderFile, file_id)
    record.approved = not record.approved
    audit("file", record.id, "approve" if record.approved else "unapprove", record.original_name)
    db.session.commit()
    return _back(record.order_id)


@bp.post("/files/<int:file_id>/delete")
def delete(file_id: int):
    record = db.get_or_404(OrderFile, file_id)
    order_id, stored = record.order_id, record.stored_name
    audit("file", record.id, "delete", f"{record.kind} {record.original_name}")
    db.session.delete(record)
    db.session.commit()
    remove_stored(stored)
    flash("File deleted", "success")
    return _back(order_id)


@bp.app_errorhandler(413)
def too_large(_exc):
    flash("That upload is too big. The limit is 25 MB at a time; upload fewer or smaller files.", "danger")
    return redirect(request.referrer or url_for("orders.list_orders"))
