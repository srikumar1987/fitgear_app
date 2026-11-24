"""Form parsing helpers and the autosave response used by HTMX inline edits."""
from __future__ import annotations

import json
from datetime import datetime

from flask import make_response, request

from .. import db
from ..models import to_decimal


def text(value) -> str:
    return (value or "").strip()


def money(value):
    amount = to_decimal(value)
    if amount < 0:
        raise ValueError("Amount cannot be negative")
    return amount


def money_or_none(value):
    return money(value) if (value or "").strip() else None


def date_or_none(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"'{value}' is not a valid date") from None


def int_min0(value) -> int:
    value = (value or "").strip()
    if not value:
        return 0
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"'{value}' is not a whole number") from None
    if number < 0:
        raise ValueError("Number cannot be negative")
    return number


def choice(options):
    def parse(value):
        if value not in options:
            raise ValueError("Invalid choice")
        return value
    return parse


def get_by_id(model, raw):
    try:
        return db.session.get(model, int(raw))
    except (TypeError, ValueError):
        return None


def apply_form(obj, spec: dict) -> dict:
    """Apply only the posted fields; returns {field: (old, new)} for fields that changed."""
    changed = {}
    for field, parse in spec.items():
        if field in request.form:
            new = parse(request.form[field])
            old = getattr(obj, field)
            if new != old:
                setattr(obj, field, new)
                changed[field] = (old, new)
    return changed


def saved(*set_values: tuple[str, str]):
    """Empty 200 response that tells the page to refresh calculated values."""
    trigger: dict = {"moneyChanged": True}
    if set_values:
        trigger["setValues"] = {"items": [list(pair) for pair in set_values]}
    response = make_response("", 200)
    response.headers["HX-Trigger"] = json.dumps(trigger)
    return response


def error(message: str):
    db.session.rollback()
    return make_response(message, 400)
