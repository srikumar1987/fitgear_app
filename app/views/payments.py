"""Receive payment / pay vendor (with multi-order split) and statements."""
from __future__ import annotations

from datetime import date, datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from .. import db
from ..formatting import format_date, format_inr
from ..models import PAYMENT_MODES, ZERO, Customer, Transaction, Vendor, audit
from ..money import alloc_due, alloc_paid, customer_payment_type, order_money
from .common import date_or_none, get_by_id, money, text

bp = Blueprint("payments", __name__)


def _receipt_ref(prefix: str) -> str:
    return f"{prefix}-{datetime.now():%y%m%d-%H%M%S}"


def customer_rows(customer: Customer) -> list[dict]:
    rows = []
    for order in customer.orders:
        m = order_money(order)
        if m.outstanding > 0 or order.status != "Closed":
            rows.append({
                "key": f"split_{order.id}", "obj": order, "money": m,
                "title": f"{order.number} · {order.title}", "sub": order.status,
                "due_date": order.due_date, "balance": m.outstanding,
                "url": url_for("orders.detail", order_id=order.id, tab="payments"),
            })
    rows.sort(key=lambda r: (r["balance"] <= 0, r["due_date"] or date.max, r["obj"].id))
    return rows


def vendor_rows(vendor: Vendor) -> list[dict]:
    rows = []
    for alloc in vendor.allocations:
        due, order = alloc_due(alloc), alloc.item.order
        if due > 0 or order.status != "Closed":
            rows.append({
                "key": f"split_{alloc.id}", "obj": alloc,
                "title": f"{order.number} · {alloc.item.name}", "sub": f"{order.customer.name} · {alloc.status}",
                "due_date": alloc.effective_due_date, "balance": due,
                "url": url_for("orders.detail", order_id=order.id, tab="vendors"),
            })
    rows.sort(key=lambda r: (r["balance"] <= 0, r["due_date"] or date.max, r["obj"].id))
    return rows


def _read_split(rows: list[dict]):
    amount = money(request.form.get("amount"))
    when = date_or_none(request.form.get("date")) or date.today()
    parts = [(row, money(request.form.get(row["key"]))) for row in rows]
    allocated = sum((p for _, p in parts), ZERO)
    if amount <= 0:
        raise ValueError("Enter the amount")
    if allocated != amount:
        raise ValueError(f"The split adds up to {format_inr(allocated)} but the amount is {format_inr(amount)}")
    return amount, when, [(row, p) for row, p in parts if p > 0]


def _split_page(kind: str, parties, party, rows):
    return render_template(
        "payments/split.html", kind=kind, parties=parties, party=party, rows=rows,
        modes=PAYMENT_MODES, form=request.form, today=date.today(),
    )


@bp.route("/receive", methods=["GET", "POST"])
def receive():
    customer = get_by_id(Customer, request.values.get("customer_id"))
    rows = customer_rows(customer) if customer else []
    if request.method == "POST" and customer:
        try:
            amount, when, parts = _read_split(rows)
        except ValueError as exc:
            flash(str(exc), "danger")
        else:
            ref = _receipt_ref("R")
            for row, part in parts:
                txn = Transaction(
                    order=row["obj"], direction="in", party_type="customer", customer=customer,
                    type=customer_payment_type(row["money"], part), amount=part, date=when,
                    mode=text(request.form.get("mode")), note=text(request.form.get("note")), receipt_ref=ref,
                )
                db.session.add(txn)
                db.session.flush()
                audit("transaction", txn.id, "create", f"{txn.type} {part} from {customer.name} ({ref})")
            db.session.commit()
            flash(f"Recorded {format_inr(amount)} from {customer.name}", "success")
            return redirect(url_for("balances.index", tab="collect"))
    customers = db.session.scalars(select(Customer).order_by(Customer.name)).all()
    return _split_page("receive", customers, customer, rows)


@bp.route("/pay", methods=["GET", "POST"])
def pay():
    vendor = get_by_id(Vendor, request.values.get("vendor_id"))
    rows = vendor_rows(vendor) if vendor else []
    if request.method == "POST" and vendor:
        try:
            amount, when, parts = _read_split(rows)
        except ValueError as exc:
            flash(str(exc), "danger")
        else:
            ref = _receipt_ref("P")
            for row, part in parts:
                alloc = row["obj"]
                txn = Transaction(
                    order=alloc.item.order, direction="out", party_type="vendor", vendor=vendor, allocation=alloc,
                    type="vendor_payment", amount=part, date=when,
                    mode=text(request.form.get("mode")), note=text(request.form.get("note")), receipt_ref=ref,
                )
                db.session.add(txn)
                db.session.flush()
                audit("transaction", txn.id, "create", f"vendor_payment {part} to {vendor.name} ({ref})")
            db.session.commit()
            flash(f"Recorded {format_inr(amount)} paid to {vendor.name}", "success")
            return redirect(url_for("balances.index", tab="pay"))
    vendors = db.session.scalars(select(Vendor).order_by(Vendor.name)).all()
    return _split_page("pay", vendors, vendor, rows)


@bp.get("/statement/customer/<int:customer_id>")
def customer_statement(customer_id: int):
    customer = db.get_or_404(Customer, customer_id)
    lines = [
        {"title": f"{o.number} · {o.title}", "total": m.billed, "paid": m.received_net, "balance": m.outstanding,
         "due_date": o.due_date}
        for o in customer.orders for m in [order_money(o)] if m.billed or m.received or m.refunds
    ]
    payments = sorted(customer.transactions, key=lambda t: (t.date, t.id))
    return _statement("customer", customer, lines, payments)


@bp.get("/statement/vendor/<int:vendor_id>")
def vendor_statement(vendor_id: int):
    vendor = db.get_or_404(Vendor, vendor_id)
    lines = [
        {"title": f"{a.item.order.number} · {a.item.name}", "total": a.cost, "paid": p, "balance": a.cost - p,
         "due_date": a.effective_due_date}
        for a in vendor.allocations for p in [alloc_paid(a)]
    ]
    payments = sorted(vendor.transactions, key=lambda t: (t.date, t.id))
    return _statement("vendor", vendor, lines, payments)


def _statement(kind: str, party, lines: list[dict], payments):
    balance = sum((line["balance"] for line in lines), ZERO)
    open_lines = [line for line in lines if line["balance"] != 0]
    label = "Customer" if kind == "customer" else "Vendor"
    message = [f"TUR fitgear - Statement", f"{label}: {party.name}", f"Date: {format_date(date.today())}", ""]
    for line in open_lines:
        message.append(line["title"])
        message.append(
            f"  Total {format_inr(line['total'])} | Paid {format_inr(line['paid'])} | Balance {format_inr(line['balance'])}"
        )
    message += ["", f"Total balance: {format_inr(balance)}"]
    return render_template(
        "payments/statement.html", kind=kind, party=party, lines=lines, payments=payments, balance=balance,
        whatsapp_text="\n".join(message),
    )
