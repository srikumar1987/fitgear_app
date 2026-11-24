"""End-to-end smoke test: walk through a full order and hit every page."""
from app import db
from app.models import Order, Transaction, VendorAllocation


def test_login_required(app):
    client = app.test_client()
    assert client.get("/balances").status_code == 302
    assert client.get("/login").status_code == 302          # no admin yet -> setup
    assert client.get("/setup").status_code == 200


def test_full_order_flow(client):
    assert client.post("/customers/new", data={"name": "Vintage Warriors"}).status_code == 302
    assert client.post("/vendors/new", data={"name": "ABC Prints", "credit_days": "7"}).status_code == 302
    r = client.post("/orders/new", data={"customer_id": "1", "title": "Jerseys", "delivery_date": "2026-01-10"})
    assert r.status_code == 302
    order_id = db.session.query(Order).one().id

    client.post(f"/orders/{order_id}/items/new", data={"name": "Jersey, half sleeve", "price_type": "per_piece", "unit_price": "400"})
    client.post("/items/1/sizes/paste", data={"rows": "Name\tNo\tSize\nVarun\t9\tM\nHarsha\t9\t3xl", "mode": "append"})
    r = client.post("/sizes/2/update", data={"size": "2xl"})
    assert r.status_code == 200 and "XXL" in r.headers["HX-Trigger"]
    assert client.post("/items/1/update", data={"unit_price": "abc"}).status_code == 400

    client.post(f"/orders/{order_id}/quotations/new", data={"notes": "50% advance"})
    client.post("/quotations/1/approve")
    client.post("/items/1/allocations/new", data={"vendor_id": "1", "agreed_cost": "500"})
    client.post("/allocations/1/update", data={"status": "Received"})
    assert db.session.get(VendorAllocation, 1).received_date is not None

    r = client.post("/receive", data={"customer_id": "1", "amount": "500", "mode": "UPI", f"split_{order_id}": "500"})
    assert r.status_code == 302
    client.post("/pay", data={"vendor_id": "1", "amount": "200", "mode": "Cash", "split_1": "200"})
    client.post(f"/orders/{order_id}/transactions/new", data={"kind": "expense", "amount": "50", "mode": "Cash"})
    assert db.session.query(Transaction).count() == 3

    for path in [
        "/balances", "/balances?tab=pay", "/balances?filter=overdue", "/balances/customer/1", "/balances/vendor/1",
        "/orders", "/orders?q=TUR-0001", "/orders/new", "/receive", "/receive?customer_id=1", "/pay?vendor_id=1",
        "/statement/customer/1", "/statement/vendor/1", "/directory", "/directory?tab=vendors", "/settings",
        "/quotations/1", f"/orders/{order_id}/strip", "/items/1/calc", f"/orders/{order_id}/sizes-summary",
        "/allocations/1/calc",
    ] + [f"/orders/{order_id}?tab={t}" for t in ("items", "quotation", "vendors", "payments", "summary")]:
        assert client.get(path).status_code == 200, path

    page = client.get("/balances").get_data(as_text=True)
    assert "₹300" in page            # 800 billed - 500 received
    assert client.get("/settings/export").status_code == 200


def test_vendor_cost_by_option(client):
    client.post("/customers/new", data={"name": "A"})
    client.post("/vendors/new", data={"name": "Raghu - Jersey"})
    client.post("/orders/new", data={"customer_id": "1", "title": "T"})
    client.post("/orders/1/items/new", data={"name": "Jersey", "price_type": "per_piece", "unit_price": "500"})
    client.post("/items/1/allocations/new", data={
        "vendor_id": "1", "agreed_cost": "",
        "line_label": ["Half sleeve", "Full sleeve", ""], "line_qty": ["20", "30", ""], "line_rate": ["250", "280", ""],
    })
    alloc = db.session.get(VendorAllocation, 1)
    assert [l.label for l in alloc.lines] == ["Half sleeve", "Full sleeve"]
    assert alloc.agreed_total == 13400

    client.post("/allocations/1/lines/new", data={"label": "Track pant", "qty": "8", "rate": "300"})
    r = client.post("/alloc-lines/1/update", data={"rate": "260"})
    assert r.status_code == 200 and "agreed-1" in r.headers["HX-Trigger"]
    assert client.post("/alloc-lines/1/update", data={"label": ""}).status_code == 400
    client.post("/alloc-lines/2/delete")
    db.session.expire_all()
    assert db.session.get(VendorAllocation, 1).agreed_total == 20 * 260 + 8 * 300
    assert client.get("/orders/1?tab=vendors").status_code == 200
    assert client.get("/orders/1?tab=summary").status_code == 200

    # A lump agreed cost is replaced when the first option is added.
    client.post("/items/1/allocations/new", data={"vendor_id": "1", "agreed_cost": "5000"})
    client.post("/allocations/2/lines/new", data={"label": "Sleeveless", "qty": "10", "rate": "200"})
    db.session.expire_all()
    assert db.session.get(VendorAllocation, 2).agreed_total == 2000


def test_customer_payment_types(client):
    client.post("/customers/new", data={"name": "A"})
    client.post("/orders/new", data={"customer_id": "1", "title": "T"})
    client.post("/orders/1/items/new", data={"name": "Jersey", "price_type": "lump_sum", "lump_amount": "1000"})
    client.post("/orders/1/quotations/new")
    client.post("/quotations/1/approve")
    for amount in ("300", "300", "400"):
        client.post("/orders/1/transactions/new", data={"kind": "customer", "amount": amount})
    types = [t.type for t in db.session.query(Transaction).order_by(Transaction.id)]
    assert types == ["advance", "partial", "final"]


def test_stop_page(client):
    r = client.post("/settings/stop")
    assert r.status_code == 200 and "has stopped" in r.get_data(as_text=True)


def test_split_must_add_up(client):
    client.post("/customers/new", data={"name": "A"})
    client.post("/orders/new", data={"customer_id": "1", "title": "T"})
    r = client.post("/receive", data={"customer_id": "1", "amount": "500", "split_1": "400"})
    assert r.status_code == 200 and "adds up to" in r.get_data(as_text=True)
    assert db.session.query(Transaction).count() == 0
