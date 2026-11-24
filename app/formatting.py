from __future__ import annotations

from datetime import date
from decimal import Decimal

STATUS_CLASSES = {
    "Enquiry": "st-neutral",
    "Quoted": "st-neutral",
    "Advance received": "st-partial",
    "In design": "st-progress",
    "In production": "st-progress",
    "Delivered": "st-paid",
    "Closed": "st-closed",
    "Pending": "st-neutral",
    "Received": "st-paid",
}


def format_inr(value) -> str:
    """Indian digit grouping: 125000 -> ₹1,25,000 (paise shown only when present)."""
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    negative = amount < 0
    whole, frac = f"{abs(amount):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    text = "₹" + whole + ("" if frac == "00" else "." + frac)
    return "-" + text if negative else text


def plain_number(value) -> str:
    if value is None:
        return ""
    amount = Decimal(str(value))
    return str(int(amount)) if amount == amount.to_integral_value() else f"{amount:.2f}"


def format_date(value, fmt: str = "%d %b %Y") -> str:
    return value.strftime(fmt) if value else "-"


def iso(value) -> str:
    return value.isoformat() if value else ""


def due_label(due: date | None, today: date | None = None) -> str:
    if due is None:
        return "No due date"
    days = (due - (today or date.today())).days
    if days < 0:
        return f"{-days} day{'s' if days != -1 else ''} overdue"
    if days == 0:
        return "Due today"
    return f"Due in {days} day{'s' if days != 1 else ''}"


def due_class(due: date | None, today: date | None = None) -> str:
    if due is None:
        return "text-muted"
    days = (due - (today or date.today())).days
    if days < 0:
        return "text-overdue"
    return "text-soon" if days <= 7 else ""


def register_filters(app) -> None:
    app.jinja_env.filters.update(
        inr=format_inr,
        plain=plain_number,
        d=format_date,
        iso=iso,
        due_label=due_label,
        due_class=due_class,
        status_class=lambda s: STATUS_CLASSES.get(s, "st-neutral"),
    )
    app.jinja_env.globals.update(today=date.today)
