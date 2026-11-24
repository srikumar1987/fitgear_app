"""Size scale, paste-from-Excel parsing, size totals and validation warnings."""
from __future__ import annotations

import csv
import io
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


TRACK_WORDS = ("track", "pant", "short", "lower", "bottom", "trouser")
JERSEY_NAME_KEYS = ("nameonjersey", "jerseyname", "printname", "nameonshirt", "nameontshirt", "nameonback")


def is_bottom_wear(name: str) -> bool:
    """Track pants, shorts, lowers: items sized from the 'Track Size' column."""
    return any(word in (name or "").lower() for word in TRACK_WORDS)


def _clean(header: str) -> str:
    return re.sub(r"[^a-z]", "", (header or "").lower())


def _number_rank(c: str) -> int:
    """How likely a header is the printed jersey number (higher wins). P.No is a fallback."""
    numberish = "number" in c or c.endswith("no") or c in NUMBER_KEYS
    if numberish and any(word in c for word in ("jersey", "shirt", "numberon", "print", "back")):
        return 3
    if c in NUMBER_KEYS or c.startswith("number"):
        return 2
    return 1 if c.startswith("pno") or c == "playerno" else 0


def _header_columns(cells: list[str]) -> dict[str, int] | None:
    """Map header cells to fields. Knows 'Number on Jersey' beats 'P.No', and
    'T Shirt Size' vs 'Track Size' vs 'Sleeve' columns."""
    cols: dict[str, int] = {}
    numbers: list[tuple[int, int]] = []
    top_sizes: list[int] = []
    bottom_sizes: list[int] = []
    for idx, raw in enumerate(cells):
        c = _clean(raw)
        if not c:
            continue
        if "size" in c:
            (bottom_sizes if any(w in c for w in TRACK_WORDS) else top_sizes).append(idx)
        elif "sleeve" in c:
            cols.setdefault("sleeve", idx)
        elif c in QTY_KEYS:
            cols.setdefault("qty", idx)
        elif any(key in c for key in JERSEY_NAME_KEYS):
            cols["jersey_name"] = idx
        elif "name" in c:
            cols.setdefault("name", idx)
        elif _number_rank(c):
            numbers.append((_number_rank(c), -idx))
    if numbers:
        cols["number"] = -max(numbers)[1]
    if top_sizes:
        cols["size"] = top_sizes[0]
    if bottom_sizes:
        cols["track_size"] = bottom_sizes[0]
    return cols if ("size" in cols or "track_size" in cols) else None


def _split_table(text: str) -> list[list[str]]:
    """Excel copies cells tab-separated and wraps cells with line breaks in quotes; csv handles both."""
    raw = text or ""
    sep = "\t" if "\t" in raw else ","
    rows = csv.reader(io.StringIO(raw), delimiter=sep)
    table = [[re.sub(r"\s+", " ", c or "").strip() for c in row] for row in rows]
    return [row for row in table if any(row)]


def _find_header(table: list[list[str]]) -> tuple[dict[str, int] | None, int]:
    for idx, cells in enumerate(table[:10]):
        cols = _header_columns(cells)
        if cols:
            return cols, idx + 1
    return None, 0


def _cell(cells: list[str], cols: dict[str, int], key: str) -> str:
    pos = cols.get(key)
    return cells[pos] if pos is not None and pos < len(cells) else ""


def _qty(raw: str) -> int:
    return int(float(raw)) if re.fullmatch(r"\d+(\.0+)?", raw or "") else 1


def _number(raw: str) -> str:
    return re.sub(r"\.0+$", "", raw or "")


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


def parse_paste(text: str, bottom_wear: bool = False) -> list[dict]:
    """Turn rows copied from Excel (tab separated) or CSV into size rows for one item.

    Lines above the header row (for example a team name) are skipped. Without a
    header, columns are assumed to be Name, Number, Size, Qty. A missing quantity
    means one row = one piece. For track pants / shorts the 'Track Size' column is used.
    """
    table = _split_table(text)
    if not table:
        return []
    cols, start = _find_header(table)
    if cols is None:
        cols = _positional_columns(table)
    size_key = "track_size" if (bottom_wear and "track_size" in cols) or "size" not in cols else "size"

    rows = []
    for cells in table[start:]:
        name = _cell(cells, cols, "jersey_name") or _cell(cells, cols, "name")
        number, size = _number(_cell(cells, cols, "number")), _cell(cells, cols, size_key)
        if not (name or number or size):
            continue
        rows.append({"player_name": name, "number": number, "size": normalise_size(size),
                     "qty": _qty(_cell(cells, cols, "qty"))})
    return rows


def parse_player_list(text: str) -> list[dict] | None:
    """A team's player list with a header row, e.g.
    P.NO | Name | Name on Jersey | Number on Jersey | T Shirt Size | Tshirt Sleeve | Track Size.

    Returns one dict per player (size, track_size, sleeve may be blank), or None if no header was found.
    """
    table = _split_table(text)
    cols, start = _find_header(table)
    if cols is None:
        return None
    players = []
    for cells in table[start:]:
        player = {
            "player_name": _cell(cells, cols, "jersey_name") or _cell(cells, cols, "name"),
            "number": _number(_cell(cells, cols, "number")),
            "size": normalise_size(_cell(cells, cols, "size")),
            "track_size": normalise_size(_cell(cells, cols, "track_size")),
            "sleeve": _cell(cells, cols, "sleeve"),
            "qty": _qty(_cell(cells, cols, "qty")),
        }
        if player["player_name"] or player["number"] or player["size"] or player["track_size"]:
            players.append(player)
    return players


def sleeve_label(raw: str) -> str:
    """'HALF' -> 'Half sleeve', 'Full Sleeve' -> 'Full sleeve', 'sleeve less' -> 'Sleeveless'."""
    value = re.sub(r"\s+", " ", raw or "").strip()
    if not value:
        return ""
    if "less" in value.lower():
        return "Sleeveless"
    if "sleeve" not in value.lower():
        value += " sleeve"
    return value[0].upper() + value[1:].lower()


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
        pant = is_bottom_wear(item.name)
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
