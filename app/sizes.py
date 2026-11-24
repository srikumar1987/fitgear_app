"""Size scale, paste-from-Excel parsing, size totals and validation warnings."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

DEFAULT_SCALE = ["XS", "S", "M", "L", "XL", "XXL", "XXXL", "4XL", "5XL"]
ALIASES = {
    "2XL": "XXL", "3XL": "XXXL", "XXXXL": "4XL", "XXXXXL": "5XL",
    "EXTRASMALL": "XS", "SMALL": "S", "MEDIUM": "M", "LARGE": "L",
    "EXTRALARGE": "XL", "XLARGE": "XL", "XXLARGE": "XXL",
}
BLANK = "(blank)"
QTY_KEYS = {"qty", "quantity", "count", "pcs", "pieces", "nos"}
NUMBER_KEYS = {"no", "number", "num", "jerseyno", "jerseynumber", "jno", "shirtno", "playerno", "tshirtno"}


def normalise_size(raw: str | None) -> str:
    key = re.sub(r"[\s.\-_]", "", (raw or "").upper())
    return ALIASES.get(key, key)


def get_scale() -> list[str]:
    from .models import get_setting

    raw = get_setting("size_scale")
    if not raw:
        return list(DEFAULT_SCALE)
    scale: list[str] = []
    for part in raw.split(","):
        size = normalise_size(part)
        if size and size not in scale:
            scale.append(size)
    return scale


def _clean(header: str) -> str:
    return re.sub(r"[^a-z]", "", (header or "").lower())


def _header_columns(cells: list[str]) -> dict[str, int] | None:
    cols: dict[str, int] = {}
    for idx, raw in enumerate(cells):
        c = _clean(raw)
        if not c:
            continue
        if "size" in c:
            cols.setdefault("size", idx)
        elif c in QTY_KEYS:
            cols.setdefault("qty", idx)
        elif "nameonjersey" in c or "jerseyname" in c or "printname" in c:
            cols["jersey_name"] = idx
        elif "name" in c:
            cols.setdefault("name", idx)
        elif c.startswith("pno") or c in NUMBER_KEYS:
            cols.setdefault("number", idx)
    return cols if "size" in cols else None


def _positional_columns(table: list[list[str]]) -> dict[str, int]:
    width = max(len(r) for r in table)
    if width == 1:
        return {"size": 0}
    if width == 2:
        first_is_number = all(re.fullmatch(r"\d+(\.0+)?", r[0] or "0") for r in table)
        return {"number": 0, "size": 1} if first_is_number else {"name": 0, "size": 1}
    if width == 3:
        return {"name": 0, "number": 1, "size": 2}
    return {"name": 0, "number": 1, "size": 2, "qty": 3}


def parse_paste(text: str) -> list[dict]:
    """Turn rows copied from Excel (tab separated) or CSV into size rows.

    Lines above the header row (for example a team name) are skipped. Without a
    header, columns are assumed to be Name, Number, Size, Qty. A missing quantity
    means one row = one piece.
    """
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return []
    sep = "\t" if any("\t" in line for line in lines) else ","
    table = [[c.strip() for c in line.split(sep)] for line in lines]

    cols, start = None, 0
    for idx, cells in enumerate(table[:10]):
        cols = _header_columns(cells)
        if cols:
            start = idx + 1
            break
    if cols is None:
        cols = _positional_columns(table)

    rows = []
    for cells in table[start:]:
        def get(key: str) -> str:
            pos = cols.get(key)
            return cells[pos].strip() if pos is not None and pos < len(cells) else ""

        name = get("jersey_name") or get("name")
        number = re.sub(r"\.0+$", "", get("number"))
        size = get("size")
        if not (name or number or size):
            continue
        qty_raw = get("qty")
        qty = int(float(qty_raw)) if re.fullmatch(r"\d+(\.0+)?", qty_raw) else 1
        rows.append({"player_name": name, "number": number, "size": normalise_size(size), "qty": qty})
    return rows


@dataclass
class SizeSummary:
    sizes: list[str]
    items: list
    cells: dict
    size_totals: dict
    item_totals: dict
    grand_total: int


def size_summary(order, scale: list[str]) -> SizeSummary:
    items = [i for i in order.items if i.sizes]
    cells: dict[tuple[str, int], int] = defaultdict(int)
    for item in items:
        for row in item.sizes:
            cells[(row.size or BLANK, item.id)] += row.qty or 0
    present = {size for size, _ in cells}
    sizes = [s for s in scale if s in present] + sorted(present - set(scale))
    cells = dict(cells)
    size_totals = {s: sum(cells.get((s, i.id), 0) for i in items) for s in sizes}
    item_totals = {i.id: sum(cells.get((s, i.id), 0) for s in sizes) for i in items}
    return SizeSummary(sizes, items, cells, size_totals, item_totals, sum(item_totals.values()))


def _is_pant(item) -> bool:
    return "pant" in (item.name or "").lower()


def _number_key(number: str):
    return (0, int(number)) if number.isdigit() else (1, number)


def validate_order(order, scale: list[str]) -> list[str]:
    """Warnings only; they never block saving."""
    warnings: list[str] = []
    numbers: dict[str, list[str]] = defaultdict(list)
    unusual, missing, unknown = [], [], []
    jersey_qty = pant_qty = 0

    for item in order.items:
        if not item.sizes:
            continue
        pant = _is_pant(item)
        for row in item.sizes:
            who = row.player_name or "unnamed row"
            if pant:
                pant_qty += row.qty or 0
            else:
                jersey_qty += row.qty or 0
                if row.number:
                    numbers[row.number].append(who)
                    if not row.number.isdigit() or len(row.number) > 2:
                        unusual.append(f"{who} ({row.number})")
            if not row.size:
                missing.append(f"{who} in {item.name}")
            elif row.size not in scale:
                unknown.append(f"{row.size} ({who})")

    duplicates = [
        f"#{n} ({', '.join(names)})"
        for n, names in sorted(numbers.items(), key=lambda kv: _number_key(kv[0]))
        if len(names) > 1
    ]
    if duplicates:
        warnings.append("Duplicate jersey numbers: " + "; ".join(duplicates))
    if unusual:
        warnings.append("Unusual numbers: " + ", ".join(unusual))
    if missing:
        warnings.append("Missing size: " + ", ".join(missing))
    if unknown:
        warnings.append("Size not in the size scale: " + ", ".join(unknown))
    if jersey_qty and pant_qty and jersey_qty != pant_qty:
        warnings.append(f"Pant count ({pant_qty}) does not match jersey count ({jersey_qty})")
    return warnings
