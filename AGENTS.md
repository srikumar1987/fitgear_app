# TUR fitgear: notes for working on this project

Internal order, size-sheet and payments app for one administrator. The plan is in `TUR_fitgear_Plan.md`.

## Stack
- Python 3.8+ (the host desktop runs 3.8.10 on an older Windows; keep `from __future__ import annotations` in every module and avoid 3.9+ syntax and stdlib APIs, such as `str.removeprefix`, `zoneinfo`, `match` or runtime `X | Y` types) · Flask 3 · Flask-SQLAlchemy · SQLite · Jinja templates · Bootstrap 5 + HTMX (stored locally in `app/static/vendor`, no CDN) · Waitress server · openpyxl.
- Amounts are stored as integer paise through the `Money` column type (`app/models.py`) and handled as `Decimal`.
- All balance and profit maths lives in `app/money.py`. Never store calculated balances.
- Size parsing, normalisation and warnings are in `app/sizes.py`.
- Old reports: `app/old_import.py` reads legacy .xlsx/.csv sheets locally (header detection + keyword column guessing, user confirms). Rows go into `OldImport`/`OldOrder`, which are report-only and never used in balances. No external AI services: the data is customer financials and must stay on the machine. Temporary uploads go in `<data dir>/imports` and are deleted after import or after 24 h.

## Paths
- Live database and secret key: `%LOCALAPPDATA%\TURfitgear\data` (override with `TUR_DATA_DIR`). Keep it out of OneDrive.
- Virtual environment: `%LOCALAPPDATA%\TURfitgear\venv` (created by `start.bat`).
- Backups: `./backups` (override with `TUR_BACKUP_DIR`), or the `backup_dir` setting chosen in Settings (e.g. a Google Drive for desktop folder), which takes precedence. Made on startup, daily and on stop; the last 30 are kept. When a custom folder is set, uploads are mirrored to `<backup folder>/uploads` (copy only, never deleted). `check_backup_dir` refuses relative paths and anything inside the live data folder.
- Paste: `parse_player_list` handles the team sheet (P.NO, Name, Name on Jersey, Number on Jersey, T Shirt Size, Tshirt Sleeve, Track Size). "Number on Jersey" outranks "P.No"; top vs bottom sizes are told apart by TRACK_WORDS. Cells are split with `csv`, so quoted multi-line Excel cells stay in one row.
- Design / after-print uploads: `./uploads` (override with `TUR_UPLOAD_DIR`). It sits inside the OneDrive folder, so files are synced off the machine. Images and PDF only, 25 MB per request; stored as `<order>_<uuid>.<ext>`, served only to a logged-in user via `/files/<id>`.

## Commands
- Run (end user): double-click `start.bat`. Opens http://localhost:5000.
- Run (dev): `"%LOCALAPPDATA%\TURfitgear\venv\Scripts\python.exe" run.py`. Env: `TUR_PORT`, `TUR_HOST=0.0.0.0` for phone access on Wi-Fi, `TUR_NO_BROWSER=1`.
- Install dev deps: `"%LOCALAPPDATA%\TURfitgear\venv\Scripts\python.exe" -m pip install -r requirements-dev.txt`
- Tests: `"%LOCALAPPDATA%\TURfitgear\venv\Scripts\python.exe" -m pytest -q -p no:cacheprovider -W error::sqlalchemy.exc.SAWarning` (no cache: OneDrive locks `.pytest_cache`)

- Test on Python 3.8 (uv is installed): `uv python install 3.8.10`, `uv venv --python 3.8.10 %TEMP%\tur_py38`, `uv pip install --python %TEMP%\tur_py38\Scripts\python.exe -r requirements-dev.txt`, then run pytest with that interpreter.

## Conventions
- Inline edits: inputs use `hx-post` + `hx-trigger="change"` + `hx-swap="none"`. Endpoints return `saved()` from `app/views/common.py`, which fires `moneyChanged`. Calculated partials (`_strip`, `_item_calc`, `_sizes_summary`, `_alloc_calc`) refresh on `moneyChanged from:body`.
- Structural changes (add/delete rows) are normal form posts that redirect back. `app.js` restores the scroll position.
- Compute `order_money(order)` before constructing a new `Transaction(order=...)`, because the constructor appends to `order.transactions`.
- Schema changes: the app uses `db.create_all()` (no migrations yet). Adding columns to existing tables needs a manual migration or Flask-Migrate.
