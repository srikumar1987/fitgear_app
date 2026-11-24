from datetime import date
from decimal import Decimal

from app import db
from app.models import Customer, Order, OrderItem, Quotation, Transaction, Vendor, VendorAllocation
from app.views.reports import build_groups


def test_groups_by_year_with_serials_and_profit(app):
    c, v = Customer(name="A"), Vendor(name="V")
    old = Order(customer=c, title="Old", order_date=date(2025, 12, 30))
    o2 = Order(customer=c, title="Second", order_date=date(2026, 3, 1))
    o1 = Order(customer=c, title="First", order_date=date(2026, 1, 5))
    item = OrderItem(order=o1, name="Jersey", price_type="per_piece", unit_price=Decimal(500), manual_qty=10)
    o1.quotations.append(Quotation(version=1, total=Decimal(5000), approved=True))
    db.session.add_all([c, v, old, o1, o2])
    db.session.flush()
    alloc = VendorAllocation(item=item, vendor=v, agreed_cost=Decimal(3000))
    db.session.add(alloc)
    o1.transactions.append(Transaction(direction="in", party_type="customer", customer=c, type="advance", amount=Decimal(2000)))
    o1.transactions.append(Transaction(direction="out", party_type="other", type="expense", amount=Decimal(500)))
    db.session.commit()

    groups = build_groups([old, o2, o1])
    assert [g.year for g in groups] == [2026, 2025]
    first = groups[0].rows[0]
    assert (first.serial, first.order.title) == (1, "First")
    assert groups[0].rows[1].order.title == "Second"
    assert first.items == "Jersey × 10"
    assert first.spent == Decimal(3500)
    assert first.m.received_net == Decimal(2000)
    assert first.profit == Decimal(1500)
    assert first.margin == Decimal("30.0")
    assert groups[0].rows[1].margin is None          # nothing billed yet
    assert groups[0].profit == Decimal(1500) and groups[0].margin == Decimal("30.0")


def test_report_pages(client):
    client.post("/customers/new", data={"name": "A"})
    client.post("/orders/new", data={"customer_id": "1", "title": "T", "order_date": "2026-02-01"})
    assert client.get("/reports").status_code == 200
    assert "Total 2026" in client.get("/reports?year=2026").get_data(as_text=True)
    assert client.get("/reports?q=nomatch").status_code == 200
    r = client.get("/reports/export?year=2026")
    assert r.status_code == 200 and r.mimetype.endswith("spreadsheetml.sheet")
