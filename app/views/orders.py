from __future__ import annotations

import json
import re
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func, or_, select

from .. import db
from ..formatting import format_inr
from ..models import (
    ALLOCATION_STATUSES, FILE_KINDS, ORDER_STATUSES, PAYMENT_MODES, PRICE_TYPES, ZERO,
    Adjustment, AllocationLine, Customer, Order, OrderItem, Quotation, SizeRow, Transaction, Vendor, VendorAllocation, audit,
)
from ..money import alloc_due, alloc_paid, customer_payment_type, order_money
from ..sizes import get_scale, normalise_size, parse_paste, size_summary, validate_order
from .designs import remove_stored
from .common import (
    apply_form, choice, date_or_none, error, get_by_id, int_min0, money, money_or_none, saved, text,
)

bp = Blueprint("orders", __name__)

TABS = [
    ("items", "Items & Sizes"),
    ("quotation", "Quotation"),
    ("designs", "Designs"),
    ("vendors", "Vendors"),
    ("payments", "Payments"),
    ("summary", "Summary"),
]


def _vendor_id(value) -> int:
    vendor = get_by_id(Vendor, value)
    if vendor is None:
        raise ValueError("Choose a vendor")
    return vendor.id


ORDER_FIELDS = {
    "title": text,
    "status": choice(ORDER_STATUSES),
    "order_date": lambda v: date_or_none(v) or date.today(),
    "delivery_date": date_or_none,
    "payment_due_date": date_or_none,
    "notes": text,
}
ITEM_FIELDS = {
    "name": text,
    "attributes": text,
    "price_type": choice(list(PRICE_TYPES)),
    "unit_price": money,
    "lump_amount": money,
    "manual_qty": int_min0,
}
SIZE_FIELDS = {"player_name": text, "number": text, "size": normalise_size, "qty": int_min0}
ALLOCATION_FIELDS = {
    "vendor_id": _vendor_id,
    "agreed_cost": money,
    "actual_cost": money_or_none,
    "status": choice(ALLOCATION_STATUSES),
    "received_date": date_or_none,
    "due_date": date_or_none,
}
LINE_FIELDS = {"label": text, "qty": int_min0, "rate": money}
MONEY_FIELDS = {"unit_price", "lump_amount", "agreed_cost", "actual_cost", "rate"}
DEFAULT_OPTIONS = ["Half sleeve", "Full sleeve", "Sleeveless", "Track pant", "Shorts", "Collar", "Round neck"]


def _back(order: Order, tab: str, anchor: str | None = None):
    return redirect(url_for("orders.detail", order_id=order.id, tab=tab, _anchor=anchor))


def _audit_money(entity: str, obj, changed: dict) -> None:
    for field, (old, new) in changed.items():
        if field in MONEY_FIELDS:
            audit(entity, obj.id, "update", f"{field}: {old} -> {new}")


def quotation_lines(order: Order) -> list[dict]:
    return [
        {
            "name": item.name, "attributes": item.attributes or "", "price_type": item.price_type,
            "qty": item.qty, "unit_price": str(item.unit_price or ZERO),
            "lump_amount": str(item.lump_amount or ZERO), "total": str(item.line_total),
        }
        for item in order.items
    ]


def ledger_rows(order: Order) -> list[tuple[Transaction, object]]:
    balance, rows = ZERO, []
    for txn in order.transactions:
        balance += txn.amount if txn.direction == "in" else -txn.amount
        rows.append((txn, balance))
    return rows


# ---------------------------------------------------------------- orders

@bp.get("/orders")
def list_orders():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    stmt = select(Order).join(Order.customer).order_by(Order.id.desc())
    if status in ORDER_STATUSES:
        stmt = stmt.where(Order.status == status)
    if q:
        conditions = [Order.title.ilike(f"%{q}%"), Customer.name.ilike(f"%{q}%")]
        number = re.fullmatch(r"(?:TUR-?)?0*(\d+)", q, re.IGNORECASE)
        if number:
            conditions.append(Order.id == int(number.group(1)))
        stmt = stmt.where(or_(*conditions))
    page = db.paginate(stmt, per_page=50, error_out=False)
    counts = dict(db.session.execute(select(Order.status, func.count()).group_by(Order.status)).all())
    rows = [(o, order_money(o)) for o in page.items]
    return render_template(
        "orders/list.html", rows=rows, page=page, q=q, status=status, counts=counts, statuses=ORDER_STATUSES,
    )


@bp.route("/orders/new", methods=["GET", "POST"])
def new_order():
    customers = db.session.scalars(select(Customer).order_by(Customer.name)).all()
    if request.method == "POST":
        new_name = text(request.form.get("new_customer"))
        customer = Customer(name=new_name) if new_name else get_by_id(Customer, request.form.get("customer_id"))
        title = text(request.form.get("title"))
        try:
            order_date = date_or_none(request.form.get("order_date")) or date.today()
            delivery_date = date_or_none(request.form.get("delivery_date"))
        except ValueError as exc:
            flash(str(exc), "danger")
        else:
            if customer is None:
                flash("Choose a customer or type a new customer name", "danger")
            elif not title:
                flash("Give the order a title, for example 'Vintage Warriors jerseys'", "danger")
            else:
                order = Order(customer=customer, title=title, order_date=order_date, delivery_date=delivery_date)
                db.session.add(order)
                db.session.commit()
                return _back(order, "items")
    return render_template(
        "orders/new.html", customers=customers, form=request.form,
        selected=request.values.get("customer_id", type=int), today=date.today(),
    )


@bp.get("/orders/<int:order_id>")
def detail(order_id: int):
    order = db.get_or_404(Order, order_id)
    tab = request.args.get("tab", "items")
    if tab not in dict(TABS):
        tab = "items"
    ctx = {
        "order": order, "m": order_money(order), "tab": tab, "tabs": TABS,
        "statuses": ORDER_STATUSES, "price_types": PRICE_TYPES,
    }
    if tab == "items":
        scale = get_scale()
        ctx.update(scale=scale, summary=size_summary(order, scale), warnings=validate_order(order, scale))
    elif tab == "quotation":
        ctx.update(draft_lines=quotation_lines(order))
    elif tab == "designs":
        ctx.update(file_kinds=FILE_KINDS)
    elif tab == "vendors":
        used = db.session.scalars(select(AllocationLine.label).distinct()).all()
        ctx.update(
            option_names=DEFAULT_OPTIONS + sorted(set(used) - set(DEFAULT_OPTIONS)),
            vendors=db.session.scalars(select(Vendor).order_by(Vendor.name)).all(),
            alloc_statuses=ALLOCATION_STATUSES, alloc_paid=alloc_paid, alloc_due=alloc_due,
        )
    elif tab == "payments":
        ctx.update(
            modes=PAYMENT_MODES, ledger=ledger_rows(order), alloc_due=alloc_due,
            allocations=[a for item in order.items for a in item.allocations],
        )
    return render_template("orders/detail.html", **ctx)


@bp.post("/orders/<int:order_id>/update")
def order_update(order_id: int):
    order = db.get_or_404(Order, order_id)
    try:
        changed = apply_form(order, ORDER_FIELDS)
    except ValueError as exc:
        return error(str(exc))
    if not order.title:
        return error("Order title is required")
    if "status" in changed:
        audit("order", order.id, "status", f"{changed['status'][0]} -> {changed['status'][1]}")
    db.session.commit()
    return saved()


@bp.post("/orders/<int:order_id>/delete")
def order_delete(order_id: int):
    order = db.get_or_404(Order, order_id)
    if order.transactions:
        flash("This order has payments recorded. Delete those first, or set the status to Closed.", "danger")
        return _back(order, "summary")
    audit("order", order.id, "delete", f"{order.number} {order.title}")
    stored_files = [f.stored_name for f in order.files]
    db.session.delete(order)
    db.session.commit()
    for stored in stored_files:
        remove_stored(stored)
    flash(f"Deleted {order.number}", "success")
    return redirect(url_for("orders.list_orders"))


@bp.get("/orders/<int:order_id>/strip")
def strip(order_id: int):
    order = db.get_or_404(Order, order_id)
    return render_template("orders/_strip.html", order=order, m=order_money(order))


# ---------------------------------------------------------------- items & sizes

@bp.post("/orders/<int:order_id>/items/new")
def item_new(order_id: int):
    order = db.get_or_404(Order, order_id)
    item = OrderItem(order=order, name=text(request.form.get("name")), position=len(order.items))
    try:
        apply_form(item, ITEM_FIELDS)
    except ValueError as exc:
        flash(str(exc), "danger")
        return _back(order, "items")
    if not item.name:
        flash("Item name is required, for example 'Jersey, half sleeve'", "danger")
        return _back(order, "items")
    db.session.add(item)
    db.session.commit()
    return _back(order, "items", f"item-{item.id}")


@bp.post("/items/<int:item_id>/update")
def item_update(item_id: int):
    item = db.get_or_404(OrderItem, item_id)
    try:
        changed = apply_form(item, ITEM_FIELDS)
    except ValueError as exc:
        return error(str(exc))
    if not item.name:
        return error("Item name is required")
    _audit_money("item", item, changed)
    db.session.commit()
    return saved()


@bp.post("/items/<int:item_id>/delete")
def item_delete(item_id: int):
    item = db.get_or_404(OrderItem, item_id)
    order = item.order
    if any(a.transactions for a in item.allocations):
        flash("Vendor payments are recorded against this item. Delete those payments first.", "danger")
        return _back(order, "items", f"item-{item.id}")
    audit("item", item.id, "delete", item.name)
    for record in order.files:
        if record.item_id == item.id:
            record.item = None
    db.session.delete(item)
    db.session.commit()
    return _back(order, "items")


@bp.get("/items/<int:item_id>/calc")
def item_calc(item_id: int):
    return render_template("orders/_item_calc.html", item=db.get_or_404(OrderItem, item_id))


@bp.post("/items/<int:item_id>/sizes/add")
def size_add(item_id: int):
    item = db.get_or_404(OrderItem, item_id)
    db.session.add(SizeRow(item=item, qty=1, position=len(item.sizes)))
    db.session.commit()
    return _back(item.order, "items", f"item-{item.id}")


@bp.post("/items/<int:item_id>/sizes/paste")
def size_paste(item_id: int):
    item = db.get_or_404(OrderItem, item_id)
    rows = parse_paste(request.form.get("rows", ""))
    if not rows:
        flash("Nothing to add. Copy the rows from Excel and paste them into the box.", "danger")
        return _back(item.order, "items", f"item-{item.id}")
    if request.form.get("mode") == "replace":
        item.sizes.clear()
        db.session.flush()
    start = len(item.sizes)
    for offset, row in enumerate(rows):
        db.session.add(SizeRow(item=item, position=start + offset, **row))
    db.session.commit()
    flash(f"Added {len(rows)} size rows to {item.name}", "success")
    return _back(item.order, "items", f"item-{item.id}")


@bp.post("/items/<int:item_id>/sizes/clear")
def size_clear(item_id: int):
    item = db.get_or_404(OrderItem, item_id)
    item.sizes.clear()
    db.session.commit()
    return _back(item.order, "items", f"item-{item.id}")


@bp.post("/sizes/<int:size_id>/update")
def size_update(size_id: int):
    row = db.get_or_404(SizeRow, size_id)
    try:
        apply_form(row, SIZE_FIELDS)
    except ValueError as exc:
        return error(str(exc))
    db.session.commit()
    return saved((f"size-{row.id}", row.size)) if "size" in request.form else saved()


@bp.post("/sizes/<int:size_id>/delete")
def size_delete(size_id: int):
    row = db.get_or_404(SizeRow, size_id)
    item = row.item
    db.session.delete(row)
    db.session.commit()
    return _back(item.order, "items", f"item-{item.id}")


@bp.get("/orders/<int:order_id>/sizes-summary")
def sizes_summary(order_id: int):
    order = db.get_or_404(Order, order_id)
    scale = get_scale()
    return render_template(
        "orders/_sizes_summary.html", order=order,
        summary=size_summary(order, scale), warnings=validate_order(order, scale),
    )


# ---------------------------------------------------------------- adjustments

@bp.post("/orders/<int:order_id>/adjustments/new")
def adjustment_new(order_id: int):
    order = db.get_or_404(Order, order_id)
    kind = request.form.get("kind")
    try:
        amount = money(request.form.get("amount"))
    except ValueError as exc:
        flash(str(exc), "danger")
        return _back(order, "quotation", "adjustments")
    if kind not in ("charge", "discount") or amount <= 0:
        flash("Choose charge or discount and enter an amount", "danger")
        return _back(order, "quotation", "adjustments")
    adj = Adjustment(order=order, kind=kind, label=text(request.form.get("label")), amount=amount)
    db.session.add(adj)
    db.session.flush()
    audit("adjustment", adj.id, "create", f"{kind} {amount} {adj.label}")
    db.session.commit()
    return _back(order, "quotation", "adjustments")


@bp.post("/adjustments/<int:adjustment_id>/delete")
def adjustment_delete(adjustment_id: int):
    adj = db.get_or_404(Adjustment, adjustment_id)
    order = adj.order
    audit("adjustment", adj.id, "delete", f"{adj.kind} {adj.amount} {adj.label}")
    db.session.delete(adj)
    db.session.commit()
    return _back(order, "quotation", "adjustments")


# ---------------------------------------------------------------- vendor allocations

def _posted_lines() -> list[AllocationLine]:
    """Option rows from the allocate form; blank rows are ignored."""
    form = request.form
    lines = []
    for label, qty, rate in zip(form.getlist("line_label"), form.getlist("line_qty"), form.getlist("line_rate")):
        if not (label.strip() or qty.strip() or rate.strip()):
            continue
        lines.append(AllocationLine(label=text(label) or "Option", qty=int_min0(qty), rate=money(rate)))
    return lines


@bp.post("/items/<int:item_id>/allocations/new")
def allocation_new(item_id: int):
    item = db.get_or_404(OrderItem, item_id)
    try:
        vendor_id = _vendor_id(request.form.get("vendor_id"))
        lines = _posted_lines()
        agreed_cost = ZERO if lines else money(request.form.get("agreed_cost"))
    except ValueError as exc:
        flash(str(exc), "danger")
        return _back(item.order, "vendors", f"item-{item.id}")
    alloc = VendorAllocation(item=item, vendor_id=vendor_id, agreed_cost=agreed_cost, lines=lines)
    db.session.add(alloc)
    db.session.flush()
    audit("allocation", alloc.id, "create", f"agreed_total {alloc.agreed_total} ({len(lines)} options)")
    db.session.commit()
    return _back(item.order, "vendors", f"item-{item.id}")


@bp.post("/allocations/<int:allocation_id>/lines/new")
def allocation_line_new(allocation_id: int):
    alloc = db.get_or_404(VendorAllocation, allocation_id)
    order = alloc.item.order
    try:
        line = AllocationLine(
            label=text(request.form.get("label")) or "Option",
            qty=int_min0(request.form.get("qty")), rate=money(request.form.get("rate")),
        )
    except ValueError as exc:
        flash(str(exc), "danger")
        return _back(order, "vendors", f"item-{alloc.item_id}")
    if not alloc.lines and alloc.agreed_cost:
        audit("allocation", alloc.id, "update", f"agreed_cost {alloc.agreed_cost} -> priced by option")
        alloc.agreed_cost = ZERO
    alloc.lines.append(line)
    db.session.flush()
    audit("allocation_line", line.id, "create", f"{line.label} {line.qty} x {line.rate}")
    db.session.commit()
    return _back(order, "vendors", f"item-{alloc.item_id}")


@bp.post("/alloc-lines/<int:line_id>/update")
def allocation_line_update(line_id: int):
    line = db.get_or_404(AllocationLine, line_id)
    try:
        changed = apply_form(line, LINE_FIELDS)
    except ValueError as exc:
        return error(str(exc))
    if not line.label:
        return error("Option name is required")
    _audit_money("allocation_line", line, changed)
    db.session.commit()
    alloc = line.allocation
    return saved(
        (f"line-amt-{line.id}", format_inr(line.amount)),
        (f"agreed-{alloc.id}", format_inr(alloc.agreed_total)),
        (f"lines-qty-{alloc.id}", str(alloc.lines_qty)),
    )


@bp.post("/alloc-lines/<int:line_id>/delete")
def allocation_line_delete(line_id: int):
    line = db.get_or_404(AllocationLine, line_id)
    alloc = line.allocation
    audit("allocation_line", line.id, "delete", f"{line.label} {line.qty} x {line.rate}")
    db.session.delete(line)
    db.session.commit()
    return _back(alloc.item.order, "vendors", f"item-{alloc.item_id}")


@bp.post("/allocations/<int:allocation_id>/update")
def allocation_update(allocation_id: int):
    alloc = db.get_or_404(VendorAllocation, allocation_id)
    try:
        changed = apply_form(alloc, ALLOCATION_FIELDS)
    except ValueError as exc:
        return error(str(exc))
    set_values = []
    if changed.get("status", (None, None))[1] == "Received" and not alloc.received_date:
        alloc.received_date = date.today()
        set_values.append((f"recv-{alloc.id}", alloc.received_date.isoformat()))
    _audit_money("allocation", alloc, changed)
    db.session.commit()
    return saved(*set_values)


@bp.post("/allocations/<int:allocation_id>/delete")
def allocation_delete(allocation_id: int):
    alloc = db.get_or_404(VendorAllocation, allocation_id)
    order = alloc.item.order
    if alloc.transactions:
        flash("Payments are recorded against this allocation. Delete those payments first.", "danger")
    else:
        audit("allocation", alloc.id, "delete", f"{alloc.vendor.name} {alloc.agreed_total}")
        db.session.delete(alloc)
        db.session.commit()
    return _back(order, "vendors")


@bp.get("/allocations/<int:allocation_id>/calc")
def allocation_calc(allocation_id: int):
    alloc = db.get_or_404(VendorAllocation, allocation_id)
    return render_template("orders/_alloc_calc.html", a=alloc, paid=alloc_paid(alloc), due=alloc_due(alloc))


# ---------------------------------------------------------------- quotations

@bp.post("/orders/<int:order_id>/quotations/new")
def quotation_new(order_id: int):
    order = db.get_or_404(Order, order_id)
    lines = quotation_lines(order)
    if not lines:
        flash("Add items before creating a quotation", "danger")
        return _back(order, "quotation")
    version = max((q.version for q in order.quotations), default=0) + 1
    quote = Quotation(
        order=order, version=version, lines_json=json.dumps(lines),
        total=sum((i.line_total for i in order.items), ZERO), notes=text(request.form.get("notes")),
    )
    if order.status == "Enquiry":
        order.status = "Quoted"
    db.session.add(quote)
    db.session.flush()
    audit("quotation", quote.id, "create", f"v{version} total {quote.total}")
    db.session.commit()
    flash(f"Saved quotation v{version}", "success")
    return _back(order, "quotation")


@bp.post("/quotations/<int:quotation_id>/approve")
def quotation_approve(quotation_id: int):
    quote = db.get_or_404(Quotation, quotation_id)
    for other in quote.order.quotations:
        other.approved = other.id == quote.id
    audit("quotation", quote.id, "approve", f"v{quote.version} total {quote.total}")
    db.session.commit()
    flash(f"Quotation v{quote.version} approved. Billed amount now uses it.", "success")
    return _back(quote.order, "quotation")


@bp.get("/quotations/<int:quotation_id>")
def quotation_print(quotation_id: int):
    quote = db.get_or_404(Quotation, quotation_id)
    return render_template("orders/quotation_print.html", q=quote, order=quote.order, price_types=PRICE_TYPES)


# ---------------------------------------------------------------- order payments

@bp.post("/orders/<int:order_id>/transactions/new")
def transaction_new(order_id: int):
    order = db.get_or_404(Order, order_id)
    kind = request.form.get("kind")
    try:
        amount = money(request.form.get("amount"))
        when = date_or_none(request.form.get("date")) or date.today()
    except ValueError as exc:
        flash(str(exc), "danger")
        return _back(order, "payments")
    if amount <= 0:
        flash("Enter an amount", "danger")
        return _back(order, "payments")
    fields = {"amount": amount, "date": when, "mode": text(request.form.get("mode")), "note": text(request.form.get("note"))}
    if kind == "customer":
        fields.update(direction="in", party_type="customer", customer=order.customer,
                      type=customer_payment_type(order_money(order), amount))
    elif kind == "refund":
        fields.update(direction="out", party_type="customer", customer=order.customer, type="refund")
    elif kind == "vendor":
        alloc = get_by_id(VendorAllocation, request.form.get("allocation_id"))
        if alloc is None or alloc.item.order_id != order.id:
            flash("Choose which vendor and item this payment is for", "danger")
            return _back(order, "payments")
        fields.update(direction="out", party_type="vendor", vendor=alloc.vendor, allocation=alloc, type="vendor_payment")
    elif kind == "expense":
        fields.update(direction="out", party_type="other", type="expense")
    else:
        abort(400)
    txn = Transaction(order=order, **fields)
    db.session.add(txn)
    db.session.flush()
    audit("transaction", txn.id, "create", f"{txn.type} {amount} on {order.number}")
    db.session.commit()
    flash(f"Recorded {txn.type_label.lower()} of {format_inr(amount)}", "success")
    return _back(order, "payments")


@bp.post("/transactions/<int:transaction_id>/delete")
def transaction_delete(transaction_id: int):
    txn = db.get_or_404(Transaction, transaction_id)
    order = txn.order
    audit("transaction", txn.id, "delete", f"{txn.type} {txn.amount} {txn.date} on {order.number}")
    db.session.delete(txn)
    db.session.commit()
    flash("Payment deleted", "success")
    return _back(order, "payments")
