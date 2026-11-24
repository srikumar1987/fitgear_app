# TUR fitgear: Revised Plan

A web app to manage sportswear and jersey orders: customer orders, size sheets, quotations, vendors, payments and profit, for a single administrator. **Internal use only**, running on the administrator's desktop.

---

## 1. Goals

The plan is built around two real problems:

1. **Knowing where the money stands on every order** (received, outstanding, owed to vendors, profit).
2. **Turning messy customer size sheets into clean production sheets for vendors.**

**Success tests:**
- Open any order and see in under 10 seconds what was ordered, who is making it, what has been paid, what is pending, and the profit.
- Open the app and see in under 5 seconds **how much each customer owes you** and **how much you owe each vendor**.

---

## 2. App structure

Four top-level screens instead of twelve modules:

| Screen | Purpose |
|---|---|
| Balances (home) | Money to collect from customers and money to pay vendors (see section 5) |
| Orders | Main workspace |
| Directory | Customers and vendors |
| Dashboard | Upcoming deliveries, reminders, monthly profit (Phase 2) |

Each order has tabs: **Items & Sizes, Quotation, Vendors, Payments, Summary** (plus **Designs** from Phase 3).

---

## 3. Data model

```
Customer
  └─ Order (status, delivery date, payment due date)
       ├─ Quotation versions (link to the approved version)
       ├─ Adjustments (extra pieces, discount, delivery charge)
       ├─ OrderItem (product variant, price type, qty)
       │    ├─ SizeRow (player name, number, size, qty)
       │    └─ VendorAllocation (agreed cost, actual cost, status, due date)
       ├─ Transactions (ledger)
       └─ Designs / approvals (Phase 3)
Vendor (credit days) ── VendorAllocation
```

**Order status:** Enquiry, Quoted, Advance received, In design, In production, Delivered, Closed. A simple status field exists from Phase 1. The visual pipeline comes in Phase 2.

**Order item = product variant.** For example "Jersey, half sleeve" and "Jersey, full sleeve" are separate items, each with its own price and vendor allocation. Track pants are another item. Attributes (sleeve, collar, fabric, GSM, colour) belong to the item.

**Pricing:** per-piece, lump-sum and mixed pricing per item.

**Due dates:**
- **Customer payment due date** defaults to the order's delivery date, and can be edited.
- **Vendor due date** defaults to the date goods are received plus the vendor's credit days (0 if paid on delivery), and can be edited.

---

## 4. Money as a ledger

A single **Transactions** table:

| Field | Notes |
|---|---|
| date | |
| direction | in / out |
| party | customer or vendor |
| order | required (one row per order; see "Payments covering several orders" below) |
| vendor allocation | optional |
| type | advance, partial, final, vendor payment, expense, refund |
| amount, mode, note | mode: cash, UPI, bank transfer, cheque |
| receipt ref | groups rows that came from one real payment |

Balances and profit are always **calculated**, never typed in:

- **Billed amount** = approved quotation total + adjustments (extra pieces, delivery charge) - discounts
- **Expected profit** = approved quotation total - agreed vendor costs
- **Actual profit** = billed amount - actual vendor costs - actual expenses
- **Customer outstanding** = billed amount - customer payments received + refunds paid
- **Vendor due** = actual vendor cost (or agreed cost until actual is entered) - vendor payments made

Actual profit is not cash received minus vendor payments.

**Payments covering several orders:** when one payment covers more than one order, the payment form lists that party's open orders (oldest first) and auto-splits the amount across them. The split can be edited. It saves one transaction per order with a shared receipt ref, so every order balance stays correct.

---

## 5. Balances: collect and pay

This is the main daily task, so it is the **home screen** and is built in **Phase 1**.

### Layout

Two tabs at the top: **To collect** and **To pay**. Each tab shows one total and one table.

**To collect (customers)**

| Customer | Open orders | Billed | Received | Outstanding | Oldest due | Last payment |
|---|---|---|---|---|---|---|
| Vintage Warriors | 2 | ₹48,000 | ₹30,000 | **₹18,000** | 12 days overdue | 05 Nov |

**To pay (vendors)**

| Vendor | Open allocations | Cost | Paid | Due | Oldest due | Last payment |
|---|---|---|---|---|---|---|
| ABC Prints | 3 | ₹62,000 | ₹40,000 | **₹22,000** | Due in 4 days | 10 Nov |

### Behaviour

- Sorted by **most overdue first**, then by amount.
- **Ageing chips** above each table: *Not due*, *0-15 days overdue*, *16-30*, *31-60*, *60+*. Clicking a chip filters the table.
- Overdue amounts in red, due within 7 days in orange, the rest neutral.
- Parties with a zero balance are hidden (toggle: "Show settled").
- **Click a row:** opens a side panel with that party's open orders, each order's balance, and recent payments.
- **"Receive payment" / "Pay vendor" button** on each row: one form with amount, mode, date (today) and note. The amount is auto-split across open orders, oldest first, as described in section 4.
- **Statement:** one click gives a simple statement (orders, payments, balance) per customer or vendor, as a PDF or as plain text that can be pasted into WhatsApp.
- **Net position strip** at the top: *To collect ₹X · To pay ₹Y · Net ₹(X - Y)*.

### Weekly routine (suggested)

1. Open **To collect**, filter *Overdue*, and send statements to the top customers.
2. Open **To pay**, filter *Due in 7 days*, and plan vendor payments.
3. Record any payments received or made the same day, so the balances stay trustworthy.

---

## 6. Size data and spreadsheet import

### Principles

- Sizes are structured rows, not stored files.
- **One shared size scale** (M, L, XL, XXL, XXXL, etc.) is used for jerseys and track pants.
- Import is one way to fill the size grid. An editable grid with paste-from-Excel is also supported.
- Assisted mapping, not full automation: upload, auto-guess columns, you confirm, and the mapping is saved per customer.

### Import must handle

- Team name above the header row (detect the real header row).
- No quantity column (option: "one row = one piece").
- **Split into items by a column**, such as Sleeve = Full / Half becoming two items.
- Size normalisation (`XXXL` = `3XL`).
- Stray text in headers (for example `P.No` with a link artifact).
- Track pants: either separate size columns in the same sheet (one row creates a jersey line and a pant line) or a separate sheet or tab linked by P.No or name. Name and number are optional for pants.

### Validation panel (warnings, not errors)

- Duplicate jersey numbers
- Unusual numbers (for example three digits)
- Missing sizes
- Name and Name on Jersey differing
- Pant count not matching jersey count when both are ordered

### Worked example: Vintage Warriors (23 players)

| Size | Half | Full | Total |
|---|---|---|---|
| M | 4 | 2 | 6 |
| L | 1 | 2 | 3 |
| XL | 2 | 3 | 5 |
| XXL | 2 | 3 | 5 |
| XXXL | 0 | 4 | 4 |
| **Total** | **9** | **14** | **23** |

Flags found: duplicate numbers #2 (Dhana, Mustaq), #9 (Varun, Harsha) and #26 (Prakash, Loki); Karthik's number 999 looks unusual.

### Main output: vendor production sheet

One click gives each vendor an Excel or PDF with only their items and a size-wise count. Jerseys can go to one vendor and track pants to another.

---

## 7. Technology

The app runs **locally on the administrator's desktop** (an older machine with Python installed). It is opened in the browser at `http://localhost:5000`. No internet hosting is needed.

| Component | Choice |
|---|---|
| Application | Python Flask |
| Frontend | Server-rendered HTML (Jinja), Bootstrap 5, HTMX for partial updates, a little JavaScript for the size grid |
| ORM and migrations | Flask-SQLAlchemy (tables created automatically; add Flask-Migrate when the schema first changes) |
| Database | SQLite (single file, no installation) |
| Spreadsheets | OpenPyXL |
| PDF | Print-ready pages saved with the browser's "Save as PDF" (no extra library to install) |
| File storage | Local `uploads/` folder |
| Server | Waitress (pure-Python, works on Windows), started by a `start.bat` double-click |
| Access | Private, single administrator |

### Running on an older desktop

- **Check first:** run `python --version`. The app supports **Python 3.8 or newer** and has been tested on 3.8.10, which the host desktop runs.
- **Keep it light:** no Docker, no PostgreSQL, no Node.js build step. All CSS and JS files are stored locally, so the app works offline.
- **Browser:** use an up-to-date Chrome or Edge.
- **One-click start:** a `start.bat` that activates the virtual environment, starts the server and opens the browser.
- **Optional phone access:** on the same Wi-Fi, open `http://<desktop-ip>:5000`. Keep this off by default.
- **Later, if needed:** the same code can move to PostgreSQL and a host such as Render, because SQLAlchemy hides the difference.

### Security and safety

- Login with a hashed password and a session timeout (HTTPS only if it is ever hosted online).
- **Backups:** on every app start and once a day, the SQLite file is copied to a dated file in a **OneDrive backup folder** (keep the last 30). Keep the live database in a normal local folder, not inside OneDrive, because syncing an open database can corrupt it.
- A full-data export button (Excel).
- Audit trail for changes to prices and payments.

---

## 8. UI/UX

Internal tool for one administrator: **minimal, simple and fast**. Function over decoration.

### Principles

- **Speed first:** every page loads in under 1 second, even on the older desktop. No single-page-app framework, no heavy libraries.
- **Fewest clicks:** common actions (receive payment, pay vendor, update status, edit sizes) take 3 clicks or fewer.
- **Edit in place:** inline editing with autosave and a small "Saved" indicator. No separate edit pages.
- **Warnings, not blockers:** show problems clearly but never stop work.
- **Desktop-first:** designed for a desktop monitor (1366 px wide and up). Phone layout is a basic fallback only.

### Brand and colours

Based on the two logos in `assets/`:
- `tur-fitgear-logo.png`: the gold TUR FITGEAR wordmark
- `turtle-mark.png`: the teal turtle mark

| Role | Colour | Use |
|---|---|---|
| Brand gold | `#FDB913` | Primary buttons (with dark text), active tab underline, logo |
| Gold dark | `#B7791F` | Gold text and links on white (readable contrast) |
| Brand teal | `#1EDCC0` | Secondary highlights, selected rows |
| Teal dark | `#0F9E8A` | "Received / paid" amounts and badges |
| Ink | `#1F2328` | Main text; sidebar background |
| Surface | `#FFFFFF` / `#F6F7F9` | Cards / page background |
| Border | `#E5E7EB` | Dividers and table lines |

- **Sidebar:** dark ink background with the gold TUR logo at the top. Gold looks best on dark.
- **Favicon and login page:** turtle mark.
- Gold is never used for text on white, because its contrast is too low. Use gold dark instead.

**Status colours**

| Colour | Meaning |
|---|---|
| Teal dark `#0F9E8A` | Paid / settled |
| Orange `#EA580C` | Partially paid / due within 7 days |
| Red `#DC2626` | Overdue |
| Blue `#2563EB` | In production |
| Grey `#6B7280` | Closed |

### Look and feel

- Light content area: white cards, soft grey borders, generous spacing.
- One font: system font stack (no web-font download). Tabular figures for all amounts so columns line up.
- Small, consistent icon set (Bootstrap Icons, stored locally). No decorative images, no animations beyond quick (about 150 ms) transitions.
- Amounts shown in INR with Indian digit grouping (for example ₹1,25,000).

### Layout

- **Left sidebar:** Balances, Orders, Directory, Dashboard (Phase 2), Settings.
- **Top bar:** search box, **+ New order**, **Receive payment**, **Pay vendor**.
- **Order page:** sticky header with customer, status, delivery date and a money strip (**Billed / Paid / Pending / Vendor due / Profit**), with the tabs below it.

### Key screens

| Screen | Content |
|---|---|
| Balances | To collect / To pay tabs, ageing chips, party table, side panel, statements (section 5) |
| Orders | Searchable table with status filter chips and delivery-date sort |
| Order: Items & Sizes | Editable grid, paste from Excel, live totals by size and variant, validation flags inline |
| Order: Quotation | Line editor with a PDF preview, version list, "Mark approved" |
| Order: Vendors | Allocation per item with agreed and actual cost, due date, "Export production sheet" |
| Order: Payments | Ledger list (in / out) with running balance and an **Add payment** form (amount, mode, note; date defaults to today) |
| Order: Summary | One card answering the success test: ordered, who is making it, paid, pending, vendor due, expected vs actual profit |
| Directory | Customers and Vendors tables, each with search and current balance |
| Dashboard (Phase 2) | Deliveries due this week, reminders, this month's profit |
| Settings | Size scale, item templates, vendor credit days, backup and export |

### Interaction details

- Keyboard shortcuts: `/` search, `N` new order, `R` receive payment, `V` pay vendor.
- Every empty state shows one line of help and one primary button.
- Lists show 50 rows per page with search; no infinite scroll.
- Confirmation only for destructive actions (delete). Everything else saves immediately, with an Undo toast where practical.
- Forms remember sensible defaults (today's date, last-used payment mode).

---

## 9. Phases

### Phase 0: validate on paper (a few days)
Enter 3 real past orders (one simple, one messy with multiple vendors, one with a partial payment still pending) into a spreadsheet using the ledger structure. If profit and balances come out right, the model is right. Also check `python --version` on the desktop.

### Phase 1: daily-use core
- Customers and vendors (with vendor credit days)
- Orders with items, editable size grid, mixed pricing, simple status field
- Quotation as PDF, with versions
- Payments ledger (customer and vendor), including multi-order split
- Vendor allocation with agreed and actual cost
- **Balances screen** (to collect / to pay, ageing, statements)
- Order summary with expected vs actual profit
- Login, automatic backups to OneDrive, `start.bat`

### Phase 2: make it faster
- Vendor production sheet export
- Spreadsheet import with saved column mapping and validation panel
- Dashboard and reminders (dues, approvals, deliveries)
- Visual order status pipeline

### Phase 3: nice to have
- Design uploads and approval versions (done: Designs tab with mockups, "Mark approved", and after-print photos)
- Reports and exports
- Google Drive integration
- Defects and reprints

---

## 10. Deferred or cut

- Fully automatic spreadsheet "intelligence" (replaced by assisted mapping)
- Separate Spreadsheet Viewer with charts (a size-wise summary table is enough at first)
- Google Sheets API
- Standalone Products & Materials module (use reusable item templates)
- Inventory management
- GST invoicing (not planned)
- Online hosting (the app runs locally on the desktop)
- Variant attributes for track pants and size grids for other products (caps, hats, bags, shorts), until needed

---

## 11. Decisions

1. **Invoicing / GST:** not needed for now, and no invoicing is planned. Quotations and simple payment records are enough.
2. **Track pant variants:** none for now. Pants only need a size.
3. **Other products (caps, hats, duffle bags, shorts):** not a concern for now. Handle them later if needed.
4. **Hosting:** runs locally on the administrator's desktop with SQLite and daily OneDrive backups.
5. **Home screen:** Balances (to collect / to pay), because this is the main daily check.

Revisit these when the Phase 1 core is in daily use.
