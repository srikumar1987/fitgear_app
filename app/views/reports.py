"""Order report (grouped by year) and Old reports imported from earlier spreadsheets."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import or_, select

from .. import db, gdrive, gsheets
from ..models import ZERO, Customer, OldImport, OldOrder, Order, audit
from ..money import OrderMoney, order_money
from ..old_import import (
    FIELDS, MONEY_FIELDS, column_letter, find_header, guess_mapping, parse_rows, read_sheet, to_text,
)

bp = Blueprint("reports", __name__)


def margin(profit: Decimal, billed: Decimal) -> Decimal | None:
    return (profit / billed * 100).quantize(Decimal("0.1")) if billed else None


@dataclass
class ReportRow:
    serial: int
    order: Order
    m: OrderMoney
    items: str
    spent: Decimal
    profit: Decimal

    @property
    def margin(self) -> Decimal | None:
        return margin(self.profit, self.m.billed)


@dataclass
class YearGroup:
    year: int
    rows: list[ReportRow]

    def _total(self, attr: str) -> Decimal:
        return sum((attr_of(r, attr) for r in self.rows), ZERO)

    @property
    def billed(self) -> Decimal:
        return self._total("billed")

    @property
    def spent(self) -> Decimal:
        return self._total("spent")

    @property
    def received(self) -> Decimal:
        return self._total("received")

    @property
    def profit(self) -> Decimal:
        return self._total("profit")

    @property
    def margin(self) -> Decimal | None:
        return margin(self.profit, self.billed)


def attr_of(row: ReportRow, attr: str) -> Decimal:
    return {
        "billed": row.m.billed, "received": row.m.received_net, "spent": row.spent, "profit": row.profit,
    }[attr]


def build_groups(orders) -> list[YearGroup]:
    """Years newest first; inside a year, orders oldest first so the serial number follows the date."""
    by_year: dict[int, list[Order]] = {}
    for order in orders:
        by_year.setdefault(order.order_date.year, []).append(order)
    groups = []
    for year in sorted(by_year, reverse=True):
        rows = []
        for serial, order in enumerate(sorted(by_year[year], key=lambda o: (o.order_date, o.id)), start=1):
            m = order_money(order)
            items = ", ".join(f"{i.name} × {i.qty}" for i in order.items) or "-"
            rows.append(ReportRow(serial, order, m, items, m.vendor_cost + m.expenses, m.actual_profit))
        groups.append(YearGroup(year, rows))
    return groups


def _orders(q: str):
    stmt = select(Order).join(Order.customer)
    if q:
        stmt = stmt.where(or_(Order.title.ilike(f"%{q}%"), Customer.name.ilike(f"%{q}%")))
    return db.session.scalars(stmt).all()


def _filters():
    q = request.args.get("q", "").strip()
    year = request.args.get("year", type=int)
    groups = build_groups(_orders(q))
    years = [g.year for g in groups]
    if year in years:
        groups = [g for g in groups if g.year == year]
    else:
        year = None
    return q, year, years, groups


@bp.get("/reports")
def index():
    q, year, years, groups = _filters()
    overall = YearGroup(0, [r for g in groups for r in g.rows])
    return render_template("reports/index.html", q=q, year=year, years=years, groups=groups, overall=overall)


@bp.get("/reports/export")
def export():
    _, year, _, groups = _filters()
    wb = Workbook()
    ws = wb.active
    ws.title = "Orders report"
    headers = ["Year", "S.No", "ID", "Order", "Customer", "Status", "What was ordered", "Order date",
               "Billed", "Spent", "Received", "Profit", "Profit %"]
    ws.append(headers)
    for g in groups:
        for r in g.rows:
            ws.append([g.year, r.serial, r.order.number, r.order.title, r.order.customer.name, r.order.status,
                       r.items, r.order.order_date, r.m.billed, r.spent, r.m.received_net, r.profit, r.margin])
        ws.append([g.year, "", "", f"Total {g.year}", "", "", f"{len(g.rows)} orders", "",
                   g.billed, g.spent, g.received, g.profit, g.margin])
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    suffix = f"_{year}" if year else ""
    return send_file(
        buffer, as_attachment=True, download_name=f"tur_fitgear_orders_report{suffix}_{datetime.now():%Y%m%d}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------------------------------------------------------- old reports (imported spreadsheets)

IMPORT_EXTENSIONS = {".xlsx", ".xlsm", ".csv"}
TOKEN_RE = re.compile(r"[0-9a-f]{32}")


@dataclass
class OldYear:
    year: int
    rows: list[OldOrder]

    def _total(self, attr: str) -> Decimal:
        return sum((getattr(r, attr) for r in self.rows if getattr(r, attr) is not None), ZERO)

    @property
    def billed(self) -> Decimal:
        return self._total("billed")

    @property
    def spent(self) -> Decimal:
        return self._total("spent")

    @property
    def received(self) -> Decimal:
        return self._total("received")

    @property
    def profit(self) -> Decimal:
        return self._total("profit")

    @property
    def margin(self) -> Decimal | None:
        return margin(self.profit, self.billed)


def old_groups(rows) -> list[OldYear]:
    by_year: dict[int, list[OldOrder]] = {}
    for row in rows:
        by_year.setdefault(row.year, []).append(row)
    return [
        OldYear(year, sorted(by_year[year], key=lambda r: (r.order_date or date.max, r.import_id, r.row_no)))
        for year in sorted(by_year, reverse=True)
    ]


def _import_dir() -> Path:
    folder = Path(current_app.config["DATA_DIR"]) / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _import_path(token: str) -> Path:
    if not TOKEN_RE.fullmatch(token or ""):
        abort(404)
    for ext in IMPORT_EXTENSIONS:
        path = _import_dir() / f"{token}{ext}"
        if path.exists():
            return path
    abort(404)


def _mapping_from_args(args, header: list) -> dict[str, int | None]:
    if not any(f"map_{field}" in args for field, _ in FIELDS):
        return guess_mapping(header)
    mapping = {}
    for field, _ in FIELDS:
        raw = args.get(f"map_{field}", "")
        mapping[field] = int(raw) if raw.isdigit() and int(raw) < len(header) else None
    return mapping


@bp.get("/reports/old")
def old():
    q = request.args.get("q", "").strip()
    year = request.args.get("year", type=int)
    stmt = select(OldOrder)
    if q:
        stmt = stmt.where(or_(OldOrder.name.ilike(f"%{q}%"), OldOrder.customer.ilike(f"%{q}%"),
                              OldOrder.ref.ilike(f"%{q}%"), OldOrder.items.ilike(f"%{q}%")))
    groups = old_groups(db.session.scalars(stmt).all())
    years = [g.year for g in groups]
    if year in years:
        groups = [g for g in groups if g.year == year]
    else:
        year = None
    imports = db.session.scalars(select(OldImport).order_by(OldImport.id.desc())).all()
    overall = OldYear(0, [r for g in groups for r in g.rows])
    return render_template("reports/old.html", groups=groups, years=years, year=year, q=q, overall=overall,
                           imports=imports)


@bp.route("/reports/old/import", methods=["GET", "POST"])
def old_upload():
    if request.method == "POST":
        upload = request.files.get("file")
        ext = Path(upload.filename).suffix.lower() if upload and upload.filename else ""
        if ext not in IMPORT_EXTENSIONS:
            flash("Choose an Excel (.xlsx) or CSV file. Old .xls files: open in Excel and 'Save As' .xlsx first.",
                  "danger")
            return redirect(url_for("reports.old_upload"))
        _clean_imports()
        token = uuid4().hex
        upload.save(_import_dir() / f"{token}{ext}")
        return redirect(url_for("reports.old_map", token=token, name=Path(upload.filename).name))
    data_dir = Path(current_app.config["DATA_DIR"])
    return render_template("reports/old_upload.html", drive_connected=gdrive.is_connected(data_dir),
                           sheets_access=gdrive.has_sheets_access(data_dir))


def _clean_imports() -> None:
    cutoff = time.time() - 86400
    for stale in _import_dir().glob("*"):
        if stale.stat().st_mtime < cutoff:
            stale.unlink(missing_ok=True)


@bp.post("/reports/old/import/sheet")
def old_sheet():
    """Read a Google Sheet (via Google login or a shared link) into the same preview as an upload."""
    _clean_imports()
    token = uuid4().hex
    dest = _import_dir() / f"{token}.xlsx"
    try:
        name = gsheets.fetch(Path(current_app.config["DATA_DIR"]), request.form.get("link", ""), dest)
    except gdrive.DriveError as exc:
        dest.unlink(missing_ok=True)
        flash(str(exc), "danger")
        return redirect(url_for("reports.old_upload"))
    return redirect(url_for("reports.old_map", token=token, name=name))


@bp.get("/reports/old/import/<token>")
def old_map(token: str):
    path = _import_path(token)
    try:
        sheets, sheet, rows = read_sheet(path, request.args.get("sheet"))
    except Exception as exc:  # unreadable or corrupt file: tell the user instead of a server error
        path.unlink(missing_ok=True)
        flash(f"Could not read that file ({exc.__class__.__name__}). Save it again as .xlsx and retry.", "danger")
        return redirect(url_for("reports.old_upload"))
    if not rows:
        flash(f"The sheet '{sheet}' is empty.", "danger")
        return redirect(url_for("reports.old_upload"))

    header_row = request.args.get("header", type=int)
    header_idx = header_row - 1 if header_row and 0 < header_row <= len(rows) else find_header(rows)
    header = rows[header_idx]
    width = max(len(r) for r in rows[header_idx:header_idx + 50])
    columns = [(i, f"{column_letter(i)}: {to_text(header[i]) if i < len(header) else ''}".rstrip(": ")) for i in range(width)]
    mapping = _mapping_from_args(request.args, header + [None] * (width - len(header)))
    default_year = request.args.get("default_year", type=int)
    parsed, skipped = parse_rows(rows, header_idx, mapping, default_year)

    return render_template(
        "reports/old_map.html", token=token, name=request.args.get("name", path.name), sheets=sheets, sheet=sheet,
        header_row=header_idx + 1, columns=columns, mapping=mapping, fields=FIELDS, money_fields=MONEY_FIELDS,
        raw_preview=rows[header_idx:header_idx + 9], width=width, parsed=parsed, skipped=skipped,
        default_year=default_year, column_letter=column_letter, to_text=to_text,
    )


@bp.post("/reports/old/import/<token>")
def old_confirm(token: str):
    path = _import_path(token)
    sheets, sheet, rows = read_sheet(path, request.form.get("sheet"))
    header_row = request.form.get("header", type=int) or 1
    header_idx = min(max(header_row - 1, 0), len(rows) - 1)
    width = max(len(r) for r in rows[header_idx:header_idx + 50])
    mapping = _mapping_from_args(request.form, list(rows[header_idx]) + [None] * width)
    parsed, _ = parse_rows(rows, header_idx, mapping, request.form.get("default_year", type=int))
    if not parsed:
        flash("No rows to import. Check the header row and which column is the order name.", "danger")
        return redirect(url_for("reports.old_map", token=token, **request.form.to_dict()))

    source = OldImport(filename=request.form.get("name", path.name)[:255], sheet=sheet[:100])
    source.orders = [OldOrder(**row) for row in parsed]
    db.session.add(source)
    db.session.flush()
    audit("old_import", source.id, "create", f"{len(parsed)} rows from {source.filename} [{sheet}]")
    db.session.commit()
    path.unlink(missing_ok=True)
    flash(f"Imported {len(parsed)} old orders from {source.filename}", "success")
    return redirect(url_for("reports.old"))


@bp.post("/reports/old/imports/<int:import_id>/delete")
def old_delete(import_id: int):
    source = db.get_or_404(OldImport, import_id)
    audit("old_import", source.id, "delete", f"{len(source.orders)} rows from {source.filename}")
    db.session.delete(source)
    db.session.commit()
    flash(f"Removed the import of {source.filename}", "success")
    return redirect(url_for("reports.old"))
