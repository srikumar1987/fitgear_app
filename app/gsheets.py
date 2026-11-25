"""Read an old Google Sheet for Old reports: through the user's Google login (read-only Sheets
permission) or, if the sheet is shared "Anyone with the link", without logging in.

The sheet is saved as an .xlsx file in the imports folder so it goes through exactly the same
column-guessing, preview and confirm step as an uploaded Excel file. Nothing is written to Google.
"""
from __future__ import annotations

import re
import urllib.parse
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

from . import gdrive
from .gdrive import DriveError, MissingPermission

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
EXPORT_URL = "https://docs.google.com/spreadsheets/d/{id}/export?format=xlsx"
MAX_ROWS = 10000

NOT_REACHABLE = (
    "Could not open that Google Sheet. Either connect Google Drive in Settings (with the Google Sheets "
    "permission ticked) using an account that can open the sheet, or set the sheet's sharing to "
    "'Anyone with the link can view' for a moment. You can also download it as Excel (File → Download "
    "→ .xlsx) and upload the file instead."
)
RECONNECT_FOR_SHEETS = (
    "Google did not allow reading Google Sheets. In Settings → Google Drive click Disconnect, then "
    "Connect Google Drive again and tick 'See all your Google Sheets spreadsheets'."
)
SHEETS_API_OFF = (
    "The Google Sheets API is turned off in your Google Cloud project. Open console.cloud.google.com → "
    "APIs & Services → Library → Google Sheets API → Enable, wait a minute, then try again."
)


def sheet_id(link: str) -> str:
    """Accepts a full Google Sheets link or just the id."""
    link = (link or "").strip()
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", link)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", link):
        return link
    raise DriveError("That is not a Google Sheets link. It should look like https://docs.google.com/spreadsheets/d/…")


def _safe_title(title: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", title or "Sheet").strip()[:31] or "Sheet"
    name, n = base, 1
    while name.lower() in used:
        n += 1
        name = f"{base[:28]} {n}"
    used.add(name.lower())
    return name


def _via_api(data_dir: Path, sid: str) -> tuple[str, bytes]:
    token = gdrive.access_token(data_dir)
    headers = {"Authorization": f"Bearer {token}"}

    def get(url: str, what: str) -> dict:
        status, hdrs, body = gdrive.http("GET", url, headers=headers)
        text = body.decode("utf-8", "replace") if body else ""
        if status == 403 and ("SERVICE_DISABLED" in text or "has not been used" in text or "is disabled" in text):
            raise DriveError(SHEETS_API_OFF)
        if status == 404:
            raise DriveError("Google Sheet not found, or the connected Google account can't open it.")
        try:
            return gdrive._json((status, hdrs, body), what)
        except MissingPermission:
            raise DriveError(RECONNECT_FOR_SHEETS) from None

    meta = get(f"{SHEETS_API}/{sid}?fields=properties.title,sheets.properties.title", "Opening the Google Sheet")
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if not titles:
        raise DriveError("That Google Sheet has no tabs.")
    params = [("ranges", "'" + t.replace("'", "''") + "'") for t in titles]
    params += [("valueRenderOption", "UNFORMATTED_VALUE"), ("dateTimeRenderOption", "FORMATTED_STRING")]
    data = get(f"{SHEETS_API}/{sid}/values:batchGet?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}",
               "Reading the Google Sheet")

    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    for title, block in zip(titles, data.get("valueRanges", [])):
        ws = wb.create_sheet(_safe_title(title, used))
        for row in block.get("values", [])[:MAX_ROWS]:
            ws.append(row)
    buffer = BytesIO()
    wb.save(buffer)
    return meta.get("properties", {}).get("title", "Google Sheet"), buffer.getvalue()


def _via_public_link(sid: str) -> bytes | None:
    status, _, body = gdrive.http("GET", EXPORT_URL.format(id=sid), timeout=120)
    return body if status == 200 and body[:2] == b"PK" else None  # an .xlsx is a zip; a login page is HTML


def fetch(data_dir: Path, link: str, dest: Path) -> str:
    """Save the sheet as .xlsx at `dest`; returns a display name. Raises DriveError with a clear message."""
    sid = sheet_id(link)
    api_error = None
    if gdrive.has_sheets_access(data_dir):
        try:
            title, content = _via_api(data_dir, sid)
            dest.write_bytes(content)
            return f"{title} (Google Sheet)"
        except DriveError as exc:
            api_error = exc
    content = _via_public_link(sid)
    if content:
        dest.write_bytes(content)
        return "Google Sheet (shared link)"
    if api_error:
        raise api_error
    if gdrive.is_connected(data_dir):
        raise DriveError(RECONNECT_FOR_SHEETS + " Or share the sheet as 'Anyone with the link can view'.")
    raise DriveError(NOT_REACHABLE)
