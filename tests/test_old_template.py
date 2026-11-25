"""The sample Excel for Old reports: every column is recognised, and a filled copy imports cleanly."""
import re
from datetime import date
from io import BytesIO

from openpyxl import load_workbook

from app import db
from app.models import OldOrder
from app.old_import import FIELDS, TEMPLATE_COLUMNS, build_template, guess_mapping


def test_template_headers_map_to_every_field():
    mapping = guess_mapping([c[0] for c in TEMPLATE_COLUMNS])
    assert all(mapping[field] is not None for field, _ in FIELDS if field != "year")
    assert mapping["customer"] == 2 and mapping["items"] == 3 and mapping["name"] == 1


def test_download_fill_and_import(client):
    r = client.get("/reports/old/sample")
    assert r.status_code == 200 and "TUR_fitgear_old_orders_template.xlsx" in r.headers["Content-Disposition"]
    wb = load_workbook(BytesIO(r.data))
    assert wb.sheetnames == ["Orders", "How to fill"]

    ws = wb["Orders"]                                   # fill it like the user would
    ws.append(["201", "School sports day", "St. Mary's", "T-shirts × 120", date(2023, 11, 20), 36000, 24000, 36000, None])
    ws.append(["202", "Corporate run", "Acme Ltd", "Dry-fit tees × 80", date(2024, 2, 2), 28000, 19500, 20000, 9000])
    filled = BytesIO()
    wb.save(filled)

    r = client.post("/reports/old/import", content_type="multipart/form-data",
                    data={"file": (BytesIO(filled.getvalue()), "my old orders.xlsx")})
    picker = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "which sheet should be imported" in picker and "Orders" in picker
    token = re.search(r"/reports/old/import/([0-9a-f]{32})", r.headers["Location"]).group(1)

    preview = client.get(f"/reports/old/import/{token}?sheet=Orders&name=my+old+orders.xlsx").get_data(as_text=True)
    assert "2</strong> rows ready" in preview
    assert db.session.query(OldOrder).count() == 0      # nothing saved before confirming

    client.post(f"/reports/old/import/{token}", data={"name": "my old orders.xlsx", "sheet": "Orders", "header": "1"})
    rows = {o.ref: o for o in db.session.query(OldOrder)}
    assert rows["201"].year == 2023 and rows["201"].profit == 12000       # profit = billed - spent
    assert rows["202"].profit == 9000 and rows["202"].customer == "Acme Ltd"
    assert "Total 2024" in client.get("/reports/old").get_data(as_text=True)
