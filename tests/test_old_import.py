import re
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook

from app import db
from app.models import OldImport, OldOrder
from app.old_import import find_header, guess_mapping, parse_rows, to_money

HEADER = ["S.No", "Order ID", "Team", "Order Name", "Items", "Date", "Order Value", "Total Cost",
          "Received Amount", "Profit", "Profit %"]


def sample_rows():
    return [
        ["TUR fitgear orders 2024"],
        HEADER,
        [1, 101, "Vintage Warriors", "League jerseys", "Jersey x 23", datetime(2024, 3, 5), 11500, 7000, 11500, 4500, 0.39],
        [2, 102, "Blue Tigers", None, "Jersey x 15, Pants x 15", "12/08/2024", "₹18,000", "Rs. 12,500/-", "15,000", None, None],
        [None, None, None, None, None, None, None, None, None, None, None],
        [3, 103, "Old Boys", "Cricket whites", "Whites x 11", None, 9000, 6000, 9000, 3000, 0.33],
        [None, None, None, "Total", None, None, 38500, 25500, 35500, 13000, None],
    ]


def test_header_detection_and_guessing():
    rows = [r for r in sample_rows() if any(v is not None for v in r)]
    header_idx = find_header(rows)
    assert header_idx == 1
    m = guess_mapping(rows[header_idx])
    assert m == {"order_date": 5, "year": None, "ref": 1, "name": 3, "customer": 2, "items": 4,
                 "billed": 6, "spent": 7, "received": 8, "profit": 9}


def test_parse_rows_skips_totals_and_derives_profit():
    rows = [r for r in sample_rows() if any(v is not None for v in r)]
    parsed, skipped = parse_rows(rows, 1, guess_mapping(rows[1]), default_year=2024)
    assert [p["ref"] for p in parsed] == ["101", "102", "103"]
    blue = parsed[1]
    assert blue["name"] == "Blue Tigers"                       # no order name -> customer
    assert blue["order_date"].isoformat() == "2024-08-12"
    assert (blue["billed"], blue["spent"], blue["received"]) == (Decimal("18000.00"), Decimal("12500.00"), Decimal("15000.00"))
    assert blue["profit"] == Decimal("5500.00")                # billed - spent
    assert parsed[2]["year"] == 2024 and parsed[2]["order_date"] is None
    assert skipped["total"] == 1


def test_money_parsing():
    assert to_money("(1,200)") == Decimal("-1200.00")
    assert to_money("INR 2,50,000.50") == Decimal("250000.50")
    assert to_money("-") is None and to_money("abc") is None


def _xlsx_bytes():
    wb = Workbook()
    wb.active.title = "2024"
    for row in sample_rows():
        wb.active.append(row)
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def test_import_flow(client):
    r = client.post("/reports/old/import", content_type="multipart/form-data",
                    data={"file": (_xlsx_bytes(), "orders 2024.xlsx")})
    assert r.status_code == 302
    map_url = r.headers["Location"]
    page = client.get(map_url).get_data(as_text=True)
    assert "2</strong> rows ready" in page                     # Old Boys has no date and no default year yet
    assert "1 rows have no date or year" in page

    token = re.search(r"/reports/old/import/([0-9a-f]{32})", map_url).group(1)
    form = {"name": "orders 2024.xlsx", "sheet": "2024", "header": "2", "default_year": "2024",
            **{f"map_{k}": "" if v is None else str(v) for k, v in guess_mapping(HEADER).items()}}
    assert client.post(f"/reports/old/import/{token}", data=form).status_code == 302
    assert db.session.query(OldOrder).count() == 3

    page = client.get("/reports/old").get_data(as_text=True)
    assert "Vintage Warriors" in page and "Total 2024" in page and "₹38,500" in page
    assert client.get("/reports/old?year=2024&q=tigers").status_code == 200
    assert client.get(map_url).status_code == 404               # temp file removed after import

    client.post(f"/reports/old/imports/{db.session.query(OldImport).one().id}/delete")
    assert db.session.query(OldOrder).count() == 0


def test_rejects_other_files(client):
    r = client.post("/reports/old/import", content_type="multipart/form-data",
                    data={"file": (BytesIO(b"x"), "old.xls")})
    assert r.status_code == 302 and r.headers["Location"].endswith("/reports/old/import")
    assert client.get("/reports/old/import/../../secret").status_code == 404
