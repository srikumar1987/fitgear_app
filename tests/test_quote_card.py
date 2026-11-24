from app.views.orders import quote_columns


def line(price_type, rate="0.00", lump="0.00", attributes=""):
    return {"name": "Jersey", "attributes": attributes, "price_type": price_type, "qty": 10,
            "unit_price": rate, "lump_amount": lump, "total": "0.00"}


def test_empty_columns_are_hidden():
    assert quote_columns([line("per_piece", "450.00")]) == {"rate": True, "lump": False, "details": False}
    assert quote_columns([line("lump_sum", lump="5000.00")]) == {"rate": False, "lump": True, "details": False}
    assert quote_columns([line("mixed", "450.00", "0.00", "Half sleeve")])["lump"] is False   # mixed but no lump
    assert quote_columns([line("per_piece", "450.00"), line("mixed", "100.00", "500.00")])["lump"] is True


def test_quote_card_pages(client):
    client.post("/customers/new", data={"name": "Houston Spartans"})
    client.post("/orders/new", data={"customer_id": "1", "title": "Cotton Tees"})
    client.post("/orders/1/items/new", data={"name": "Cotton Tees", "price_type": "per_piece", "unit_price": "410"})

    tab = client.get("/orders/1?tab=quotation").get_data(as_text=True)
    assert "qc-head" in tab and "hide-lump" in tab and "Share view" in tab

    share = client.get("/orders/1/quote-card").get_data(as_text=True)
    assert "QUOTATION" in share and "Houston Spartans" in share and "hide-lump" in share
    assert "v1" not in share

    client.post("/orders/1/quotations/new", data={"notes": "50% advance"})
    saved = client.get("/quotations/1").get_data(as_text=True)
    assert "TUR-0001 · v1" in saved and "50% advance" in saved and "hide-notes" not in saved
