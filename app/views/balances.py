from __future__ import annotations

from datetime import date

from flask import Blueprint, redirect, render_template, request, url_for
from sqlalchemy import select

from .. import db
from ..models import ZERO, Customer, Vendor
from ..money import FILTERS, alloc_due, customer_balances, order_money, vendor_balances

bp = Blueprint("balances", __name__)


@bp.get("/")
def home():
    # Google sends the browser back to the app's root address after "Connect Google Drive".
    if request.args.get("state") and ("code" in request.args or "error" in request.args):
        return redirect(url_for("settings.gdrive_callback", **request.args.to_dict()))
    return redirect(url_for("balances.index"))


@bp.get("/balances")
def index():
    tab = "pay" if request.args.get("tab") == "pay" else "collect"
    active = request.args.get("filter", "")
    show_settled = request.args.get("settled") == "1"
    today = date.today()

    collect = customer_balances(db.session.scalars(select(Customer)).all())
    pay = vendor_balances(db.session.scalars(select(Vendor)).all())
    to_collect = sum((r.balance for r in collect if r.balance > 0), ZERO)
    to_pay = sum((r.balance for r in pay if r.balance > 0), ZERO)

    rows = [r for r in (collect if tab == "collect" else pay) if show_settled or r.balance != 0]
    owing = [r for r in rows if r.balance > 0]
    chips = [
        (key, label, sum(1 for r in owing if r.matches(key, today)),
         sum((r.balance for r in owing if r.matches(key, today)), ZERO))
        for key, label in FILTERS
    ]
    if active in dict(FILTERS):
        rows = [r for r in rows if r.balance > 0 and r.matches(active, today)]
    else:
        active = ""

    return render_template(
        "balances/index.html", tab=tab, rows=rows, chips=chips, active=active, show_settled=show_settled,
        to_collect=to_collect, to_pay=to_pay, today=today,
    )


@bp.get("/balances/customer/<int:customer_id>")
def customer_panel(customer_id: int):
    customer = db.get_or_404(Customer, customer_id)
    orders = [(o, m) for o in customer.orders for m in [order_money(o)] if m.outstanding != 0]
    payments = sorted(customer.transactions, key=lambda t: (t.date, t.id), reverse=True)[:10]
    return render_template("balances/_customer_panel.html", customer=customer, orders=orders, payments=payments)


@bp.get("/balances/vendor/<int:vendor_id>")
def vendor_panel(vendor_id: int):
    vendor = db.get_or_404(Vendor, vendor_id)
    allocations = [(a, due) for a in vendor.allocations for due in [alloc_due(a)] if due != 0]
    allocations.sort(key=lambda r: r[0].effective_due_date or date.max)
    payments = sorted(vendor.transactions, key=lambda t: (t.date, t.id), reverse=True)[:10]
    return render_template("balances/_vendor_panel.html", vendor=vendor, allocations=allocations, payments=payments)
