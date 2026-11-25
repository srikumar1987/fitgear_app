"""Old reports from a Google Sheet: via Google login or a shared link, always through the preview."""
import re
from io import BytesIO

import pytest
from openpyxl import Workbook

from app import db, gdrive, gsheets
from app.models import OldOrder
from tests.test_gdrive import _connect, google  # noqa: F401  (fixture)

SID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
LINK = f"https://docs.google.com/spreadsheets/d/{SID}/edit#gid=0"
ROWS = [
    ["TUR fitgear orders"],
    ["S.No", "Order ID", "Team", "Order Name", "Date", "Order Value", "Total Cost", "Received Amount"],
    [1, 101, "Vintage Warriors", "League jerseys", "05/03/2024", 11500, 7000, 11500],
    [2, 102, "Blue Tigers", "Cricket whites", "12/08/2024", 18000, 12500, 15000],
]


def _xlsx(rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _token(location):
    return re.search(r"/reports/old/import/([0-9a-f]{32})", location).group(1)


def test_sheet_link_parsing():
    assert gsheets.sheet_id(LINK) == SID
    assert gsheets.sheet_id(SID) == SID
    with pytest.raises(gdrive.DriveError, match="not a Google Sheets link"):
        gsheets.sheet_id("https://example.com/file")


def test_read_with_google_login_then_preview_and_confirm(file_app, file_client, google):
    google.sheets[SID] = {"title": "Orders 2024", "tabs": {"2024": ROWS, "Bob's tab": [["x"]]}}
    _connect(file_client)
    assert gdrive.has_sheets_access(file_app.config["DATA_DIR"])

    r = file_client.post("/reports/old/import/sheet", data={"link": LINK})
    assert "/reports/old/import/" in r.headers["Location"]
    picker = file_client.get(r.headers["Location"]).get_data(as_text=True)     # 2 tabs: choose one first
    assert "which sheet should be imported" in picker and "Bob&#39;s tab" in picker
    assert "Order Name" in picker and "rows ready" not in picker
    page = file_client.get(r.headers["Location"] + "&sheet=2024").get_data(as_text=True)
    assert "Orders 2024 (Google Sheet)" in page and "2</strong> rows ready" in page and "Vintage Warriors" in page
    assert "return confirm(" in page                                    # Import asks before saving
    assert db.session.query(OldOrder).count() == 0                      # nothing saved by the preview

    form = {"name": "Orders 2024", "sheet": "2024", "header": "2"}
    file_client.post(f"/reports/old/import/{_token(r.headers['Location'])}", data=form)
    rows = db.session.query(OldOrder).order_by(OldOrder.row_no).all()
    assert [o.name for o in rows] == ["League jerseys", "Cricket whites"]
    assert rows[0].order_date.isoformat() == "2024-03-05" and rows[1].received == 15000


def test_shared_link_works_without_login(file_client, google):
    google.public_sheets[SID] = _xlsx(ROWS)
    r = file_client.post("/reports/old/import/sheet", data={"link": LINK})
    page = file_client.get(r.headers["Location"]).get_data(as_text=True)
    assert "shared link" in page and "2</strong> rows ready" in page
    assert not google.sheet_calls


def test_private_sheet_without_login_explains_options(file_client, google):
    r = file_client.post("/reports/old/import/sheet", data={"link": LINK}, follow_redirects=True)
    text = r.get_data(as_text=True)
    assert "Anyone with the link can view" in text and "Download" in text


def test_old_connection_without_sheets_permission(file_app, file_client, google):
    google.granted = "openid email https://www.googleapis.com/auth/drive.file"   # connected before Sheets was added
    google.sheets[SID] = {"title": "Orders", "tabs": {"2024": ROWS}}
    _connect(file_client)
    assert not gdrive.has_sheets_access(file_app.config["DATA_DIR"])
    r = file_client.post("/reports/old/import/sheet", data={"link": LINK}, follow_redirects=True)
    assert "See all your Google Sheets spreadsheets" in r.get_data(as_text=True)
    assert "Not allowed" in file_client.get("/settings").get_data(as_text=True)


def test_sheets_api_turned_off_is_explained(file_client, google):
    google.sheets[SID] = {"title": "Orders", "tabs": {"2024": ROWS}}
    google.sheets_api_on = False
    _connect(file_client)
    r = file_client.post("/reports/old/import/sheet", data={"link": LINK}, follow_redirects=True)
    assert "Google Sheets API" in r.get_data(as_text=True) and "Enable" in r.get_data(as_text=True)


def test_sheet_not_shared_with_account(file_client, google):
    _connect(file_client)
    r = file_client.post("/reports/old/import/sheet", data={"link": LINK}, follow_redirects=True)
    assert "can&#39;t open it" in r.get_data(as_text=True)
