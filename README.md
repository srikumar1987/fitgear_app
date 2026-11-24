<p align="center">
  <img src="assets/tur-fitgear-logo.png" alt="TUR fitgear" width="160">
</p>

# TUR fitgear app

An internal web app for managing sportswear and jersey orders: customers, size sheets, quotations, vendors, payments and profit. It is built for **one administrator** and runs **locally on a Windows desktop**. No internet hosting is needed, and the data stays on the PC.

**Main question it answers:** *who owes me money, and whom do I owe?* Open any order and within 10 seconds you can see what was ordered, who is making it, what has been paid, what is pending, and the profit.

---

## Features

| Area | What you can do |
|---|---|
| **Balances** (home) | *To collect* from customers and *To pay* vendors, with an ageing filter (overdue, due in 7 days, 1–15 / 16–30 / 31–60 / 60+ days), a side panel per party, and a net position |
| **Receive / Pay** | One payment form that splits the amount across open orders (oldest first, editable) |
| **Orders** | Items with per-piece, lump-sum or mixed pricing; status; order, delivery and payment-due dates |
| **Items & Sizes** | **Paste the team's player list** (P.NO, Name, Name on Jersey, Number on Jersey, T Shirt Size, Tshirt Sleeve, Track Size): jerseys are split into items by sleeve and a Track pants item is filled from Track Size. Also an editable size grid, per-item paste from Excel, size normalising (3XL → XXXL), a live size summary, and warnings for duplicate or odd jersey numbers and missing sizes |
| **Quotation** | Saved versions, *Mark approved*, a print / save-as-PDF page with the logo, adjustments (extra charges, discounts) |
| **Designs** | Upload or **paste (Ctrl+V)** design mockups and after-print photos; mark the approved design |
| **Vendors** | Allocate each item to a vendor; agreed cost as one total or **priced by option** (half sleeve, full sleeve, track pant, …); actual cost; received and pay-by dates |
| **Payments** | Ledger per order: customer payments, vendor payments, expenses, refunds |
| **Statements** | Printable statement per customer or vendor, plus text you can copy into WhatsApp |
| **Reports** | Every order grouped by year (billed, spent, received, profit, profit %) with Excel download |
| **Old reports** | Import older Excel/CSV order sheets: columns are guessed, you confirm with a preview, and the rows are imported. Read locally, no internet |
| **Settings** | Size scale; **Connect Google Drive** (direct upload of every backup and design file through the Drive API, no Drive app needed); **backup folder** (e.g. a Google Drive for desktop folder); backup now; download the latest backup or the design files as a zip; full Excel export; change password; stop the app |

Money rules (always calculated, never typed in):

- **Billed** = approved quotation + charges − discounts
- **Pending** (from the customer) = billed − received + refunds
- **Vendor due** = actual cost (or agreed cost if actual is blank) − paid to the vendor
- **Expected profit** = approved quotation − agreed vendor cost
- **Actual profit** = billed − vendor cost − expenses

---

## Requirements

- Windows with **Python 3.8 or newer** (tested on 3.8.10 and 3.14)
- An up-to-date Chrome or Edge (Chrome 109 on Windows 7 works)
- Internet **once**, on the first start, to download the Python packages

## Getting started

1. Download this repository (green **Code** button → *Download ZIP*, or `git clone https://github.com/srikumar1987/fitgear_app.git`).
2. Double-click **`start.bat`**.
   - The first run creates a private Python environment and installs the packages, which takes a minute.
   - The app starts **in the background** and opens `http://localhost:5000` in your browser.
3. On first open, set the admin password.

To stop the app, go to **Settings → Stop the app** (a backup is saved first). Running `start.bat` again starts it, or just opens the browser if it is already running.

### Updating

```bat
git pull
```

Then stop the app and run `start.bat` again. Your data is not stored in this folder, so updating never touches it.

---

## Where things are stored

| What | Location |
|---|---|
| Live database, secret key, log | `%LOCALAPPDATA%\TURfitgear\data\` (kept out of OneDrive and Google Drive, because syncing an open database can corrupt it) |
| Python environment | `%LOCALAPPDATA%\TURfitgear\venv\` |
| Backups | `backups\` in this folder by default, or the folder chosen in **Settings → Backups** (for example `G:\My Drive\TUR fitgear backups` with Google Drive for desktop). Made on start, once a day (a timer checks every 30 minutes, even if nobody opens the app) and on stop, **only if the data changed** since the last backup; the last 30 are kept |
| Design / after-print files | `uploads\` in this folder |

`backups\` and `uploads\` hold real business data and are **git-ignored**.

**Restore a backup:** go to **Settings → Restore…**. You can pick a backup from Google Drive (newest first, design files included) or upload a `.db` backup file. The app checks the file, saves a safety copy of the current data first, then replaces it. Afterwards, log in with the password that was used when the backup was made.

**Moving to a new PC:** install the app, set any password, connect Google Drive with the **same Google account and the same client JSON file**, then restore the latest backup when the app offers it. An empty new install is never uploaded, so it can't replace your real "latest" backup.

### Google Drive (direct link, no Drive app needed)

**Settings → Google Drive** uploads every backup (on start, daily, on stop) and every new design file to a **“TUR fitgear backups”** folder in your Drive, keeping the last 30 backups. It uses the narrow `drive.file` permission, so the app can only see files it created. One-time setup with your own Google account:

1. In [Google Cloud console](https://console.cloud.google.com), create a project, e.g. “TUR fitgear”.
2. **APIs & Services → Library** → enable **Google Drive API**.
3. **OAuth consent screen**: app name, your email, audience **External**; then **Audience → Publish app**. If it stays in *Testing*, access expires every 7 days.
4. **Clients → Create client** → **Desktop app** → **Download JSON**.
5. In the app: **Settings → Google Drive** → choose the JSON → **Save client** → **Connect Google Drive**. If Google says the app isn't verified, choose **Advanced → Go to TUR fitgear**; it's your own app.

The client file and sign-in token are kept in the data folder (`gdrive_client.json`, `gdrive_token.json`). They are never synced, never in database backups, and never in git. **Disconnect** revokes the access at Google.

Optional environment variables: `TUR_DATA_DIR`, `TUR_BACKUP_DIR`, `TUR_UPLOAD_DIR`, `TUR_PORT` (default 5000), `TUR_HOST` (set `0.0.0.0` to allow phone access on the same Wi-Fi), `TUR_NO_BROWSER=1`.

---

## Tech stack

Python · Flask 3 · Flask-SQLAlchemy · SQLite · Jinja templates · Bootstrap 5 · HTMX · Waitress · openpyxl. All CSS and JS are stored locally, so the app works offline.

```
app/
  __init__.py      app factory, login guard, automatic backups
  models.py        database tables (amounts stored as integer paise)
  money.py         all balance and profit calculations
  sizes.py         size scale, paste parsing, size checks
  old_import.py    reading old Excel sheets for Old reports
  backup.py, export.py, formatting.py
  views/           routes: balances, orders, payments, designs, reports, directory, settings, auth
  templates/       pages and partials
  static/          css, js, images, vendor (Bootstrap, HTMX)
tests/             pytest suite
run.py             starts the Waitress server
start.bat          one-click start for Windows
```

## Development

```bat
"%LOCALAPPDATA%\TURfitgear\venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
"%LOCALAPPDATA%\TURfitgear\venv\Scripts\python.exe" -m pytest -q -p no:cacheprovider
"%LOCALAPPDATA%\TURfitgear\venv\Scripts\python.exe" run.py
```

Running `run.py` with `python.exe` shows output in the console; `start.bat` uses `pythonw.exe` (no window) and logs to `tur_fitgear.log` in the data folder. Keep the code compatible with Python 3.8. See [`AGENTS.md`](AGENTS.md) for conventions, and [`TUR_fitgear_Plan.md`](TUR_fitgear_Plan.md) for the product plan and roadmap.

## Security notes

- The app is for internal use: one admin password (hashed), an 8-hour session timeout, and it listens on `127.0.0.1` only by default.
- Changes to prices, payments and files are recorded in an audit log (included in the Excel export).
- No data is sent to any outside service.
