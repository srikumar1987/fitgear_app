"""Read an older Excel/CSV order sheet: find the header row, guess columns, turn rows into old orders.

Everything runs locally; nothing is sent anywhere.
"""
from __future__ import annotations

import csv
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import load_workbook

MAX_ROWS = 10000
FIELDS = [
    ("order_date", "Order date"),
    ("year", "Year"),
    ("ref", "ID / order no"),
    ("name", "Order name"),
    ("customer", "Customer / team"),
    ("items", "What was ordered"),
    ("billed", "Billed / order value"),
    ("spent", "Spent / cost"),
    ("received", "Received"),
    ("profit", "Profit"),
]
MONEY_FIELDS = {"billed", "spent", "received", "profit"}

# (exact header matches, header contains). Headers are lower-cased with non-letters removed.
KEYWORDS = {
    "order_date": ({"day", "dated"}, ["date"]),
    "year": ({"year", "yr", "fy"}, ["year"]),
    "ref": ({"id", "orderid", "orderno", "ordernumber", "orderref", "no", "ref", "refno", "invoice", "invoiceno",
             "billno", "code"}, []),
    "customer": ({"team", "teamname", "club"}, ["customer", "client", "party", "buyer"]),
    "items": (set(), ["item", "ordered", "product", "particular", "description", "details", "material"]),
    "profit": (set(), ["profit", "margin", "gain", "earning"]),
    "spent": (set(), ["spent", "cost", "expense", "vendor", "purchase", "outflow", "paidout", "payout"]),
    "received": (set(), ["received", "collected", "receipt", "inflow", "amountpaid", "payment"]),
    "billed": (set(), ["billed", "billing", "sale", "revenue", "value", "quote", "price", "total", "amount"]),
    "name": (set(), ["ordername", "order", "name", "event", "title", "tournament", "project"]),
}
GUESS_ORDER = ["order_date", "year", "ref", "customer", "items", "profit", "spent", "received", "billed", "name"]
DATE_FORMATS = ["%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%m-%y", "%d/%m/%y", "%d %b %Y", "%d-%b-%Y",
                "%d %B %Y", "%b %d %Y", "%d-%b-%y"]


def _clean(header) -> str:
    return re.sub(r"[^a-z]", "", str(header or "").lower())


def _is_percent(header) -> bool:
    raw = str(header or "").lower()
    return "%" in raw or "percent" in raw or "pct" in raw


def _matches(field: str, header) -> bool:
    exact, contains = KEYWORDS[field]
    cleaned = _clean(header)
    if not cleaned or (field in MONEY_FIELDS and _is_percent(header)):
        return False
    return cleaned in exact or any(word in cleaned for word in contains)


def column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def read_sheet(path: Path, sheet: str | None = None) -> tuple[list[str], str, list[list]]:
    """Returns (sheet names, chosen sheet, non-empty rows)."""
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
            rows = [row for row in csv.reader(handle)]
        names, chosen = ["CSV"], "CSV"
    else:
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            names = wb.sheetnames
            chosen = sheet if sheet in names else names[0]
            rows = [list(row) for _, row in zip(range(MAX_ROWS), wb[chosen].iter_rows(values_only=True))]
        finally:
            wb.close()
    cleaned = []
    for row in rows:
        values = [None if (isinstance(v, str) and not v.strip()) else v for v in row]
        while values and values[-1] is None:
            values.pop()
        if values:
            cleaned.append(values)
    return names, chosen, cleaned


def find_header(rows: list[list]) -> int:
    """The header is the row (within the first 15) that matches the most known column names."""
    best, best_score = 0, 1
    for idx, row in enumerate(rows[:15]):
        score = sum(1 for cell in row if isinstance(cell, str) and any(_matches(f, cell) for f, _ in FIELDS))
        if score > best_score:
            best, best_score = idx, score
    return best


def guess_mapping(header: list) -> dict[str, int | None]:
    mapping: dict[str, int | None] = {field: None for field, _ in FIELDS}
    used: set[int] = set()
    for field in GUESS_ORDER:
        for idx, cell in enumerate(header):
            if idx not in used and _matches(field, cell):
                mapping[field] = idx
                used.add(idx)
                break
    return mapping


def to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime, date)):
        return value.strftime("%d %b %Y")
    return str(value).strip()


def to_money(value) -> Decimal | None:
    if value is None or isinstance(value, (datetime, date, bool)):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")") or text.startswith("-")
    text = re.sub(r"(?i)rs\.?|inr|₹|/-|[,\s()]", "", text).lstrip("-")
    if not text or not re.fullmatch(r"\d+(\.\d+)?", text):
        return None
    try:
        amount = Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return -amount if negative else amount


def to_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = re.sub(r"\s+", " ", str(value or "").replace(",", " ")).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def to_year(value) -> int | None:
    if isinstance(value, (datetime, date)):
        return value.year
    match = re.search(r"(19|20)\d{2}", to_text(value))
    return int(match.group(0)) if match else None


def parse_rows(rows: list[list], header_idx: int, mapping: dict[str, int | None],
               default_year: int | None) -> tuple[list[dict], dict[str, int]]:
    """Returns (orders, skipped counts). Blank and 'Total' rows are skipped."""
    parsed, skipped = [], {"blank": 0, "total": 0, "no_year": 0}
    for row_no, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        def get(field):
            idx = mapping.get(field)
            return row[idx] if idx is not None and idx < len(row) else None

        name, customer, ref = to_text(get("name")), to_text(get("customer")), to_text(get("ref"))
        money = {f: to_money(get(f)) for f in MONEY_FIELDS}
        if not (name or customer or ref) and all(v is None for v in money.values()):
            skipped["blank"] += 1
            continue
        label = (name or customer).lower()
        if not ref and ("total" in label or "grand" in label):
            skipped["total"] += 1
            continue
        order_date = to_date(get("order_date"))
        year = order_date.year if order_date else (to_year(get("year")) or default_year)
        if year is None:
            skipped["no_year"] += 1
            continue
        if money["profit"] is None and money["billed"] is not None:
            money["profit"] = money["billed"] - (money["spent"] or Decimal("0.00"))
        parsed.append({
            "row_no": row_no, "year": year, "order_date": order_date, "ref": ref[:50],
            "name": (name or customer)[:255], "customer": customer[:255], "items": to_text(get("items")), **money,
        })
    return parsed, skipped
