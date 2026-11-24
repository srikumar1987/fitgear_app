from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from .. import db
from ..models import Customer, Vendor, audit
from ..money import customer_balances, vendor_balances
from .common import apply_form, error, int_min0, saved, text

bp = Blueprint("directory", __name__)

CUSTOMER_FIELDS = {"name": text, "phone": text, "email": text, "notes": text}
VENDOR_FIELDS = {"name": text, "phone": text, "credit_days": int_min0, "notes": text}


@bp.get("/directory")
def index():
    tab = "vendors" if request.args.get("tab") == "vendors" else "customers"
    q = request.args.get("q", "").strip()
    model = Vendor if tab == "vendors" else Customer
    stmt = select(model).order_by(model.name)
    if q:
        stmt = stmt.where(model.name.ilike(f"%{q}%"))
    parties = db.session.scalars(stmt).all()
    balances = (vendor_balances if tab == "vendors" else customer_balances)(parties)
    return render_template(
        "directory/index.html", tab=tab, q=q, parties=parties,
        balances={b.party.id: b for b in balances},
    )


def _create(model, fields, tab):
    name = text(request.form.get("name"))
    if not name:
        flash("Name is required", "danger")
        return redirect(url_for("directory.index", tab=tab))
    party = model(name=name)
    try:
        apply_form(party, fields)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("directory.index", tab=tab))
    db.session.add(party)
    db.session.commit()
    flash(f"Added {party.name}", "success")
    return redirect(url_for("directory.index", tab=tab))


def _update(model, party_id, fields):
    party = db.get_or_404(model, party_id)
    try:
        apply_form(party, fields)
    except ValueError as exc:
        return error(str(exc))
    if not party.name:
        return error("Name is required")
    db.session.commit()
    return saved()


@bp.post("/customers/new")
def customer_new():
    return _create(Customer, CUSTOMER_FIELDS, "customers")


@bp.post("/customers/<int:customer_id>/update")
def customer_update(customer_id: int):
    return _update(Customer, customer_id, CUSTOMER_FIELDS)


@bp.post("/customers/<int:customer_id>/delete")
def customer_delete(customer_id: int):
    customer = db.get_or_404(Customer, customer_id)
    if customer.orders or customer.transactions:
        flash(f"{customer.name} has orders or payments and cannot be deleted", "danger")
    else:
        audit("customer", customer.id, "delete", customer.name)
        db.session.delete(customer)
        db.session.commit()
        flash(f"Deleted {customer.name}", "success")
    return redirect(url_for("directory.index", tab="customers"))


@bp.post("/vendors/new")
def vendor_new():
    return _create(Vendor, VENDOR_FIELDS, "vendors")


@bp.post("/vendors/<int:vendor_id>/update")
def vendor_update(vendor_id: int):
    return _update(Vendor, vendor_id, VENDOR_FIELDS)


@bp.post("/vendors/<int:vendor_id>/delete")
def vendor_delete(vendor_id: int):
    vendor = db.get_or_404(Vendor, vendor_id)
    if vendor.allocations or vendor.transactions:
        flash(f"{vendor.name} has allocations or payments and cannot be deleted", "danger")
    else:
        audit("vendor", vendor.id, "delete", vendor.name)
        db.session.delete(vendor)
        db.session.commit()
        flash(f"Deleted {vendor.name}", "success")
    return redirect(url_for("directory.index", tab="vendors"))
