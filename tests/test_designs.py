from io import BytesIO
from pathlib import Path

from app import db
from app.models import OrderFile

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _setup(client):
    client.post("/customers/new", data={"name": "A"})
    client.post("/orders/new", data={"customer_id": "1", "title": "T"})
    client.post("/orders/1/items/new", data={"name": "Jersey", "price_type": "per_piece"})


def test_upload_view_approve_note_delete(client, app):
    _setup(client)
    r = client.post("/orders/1/files/upload", content_type="multipart/form-data", data={
        "kind": "design", "item_id": "1", "note": "Front v1",
        "files": [(BytesIO(PNG), "front v1.png"), (BytesIO(b"%PDF-1.4"), "back.pdf"), (BytesIO(b"MZ"), "virus.exe")],
    })
    assert r.status_code == 302
    files = db.session.query(OrderFile).order_by(OrderFile.id).all()
    assert [f.original_name for f in files] == ["front v1.png", "back.pdf"]
    assert files[0].is_image and not files[1].is_image and files[0].item_id == 1
    stored = Path(app.config["UPLOAD_DIR"]) / files[0].stored_name
    assert stored.read_bytes() == PNG

    page = client.get("/orders/1?tab=designs").get_data(as_text=True)
    assert "front v1.png" in page and "Mark approved" in page
    assert page.count("data-paste-upload") == 2 and page.count("data-paste-default") == 1
    assert client.get(f"/files/{files[0].id}").data == PNG
    assert "attachment" in client.get(f"/files/{files[0].id}?download=1").headers["Content-Disposition"]

    client.post(f"/files/{files[0].id}/approve")
    assert client.post(f"/files/{files[0].id}/update", data={"note": "Approved by captain"}).status_code == 200
    db.session.expire_all()
    assert files[0].approved and files[0].note == "Approved by captain"
    assert "approved ✓" in client.get("/orders/1?tab=summary").get_data(as_text=True)

    client.post("/orders/1/files/upload", content_type="multipart/form-data",
                data={"kind": "printed", "files": [(BytesIO(PNG), "printed.jpg")]})
    assert db.session.query(OrderFile).filter_by(kind="printed").count() == 1

    client.post(f"/files/{files[0].id}/delete")
    assert not stored.exists()
    assert db.session.get(OrderFile, files[0].id) is None


def test_deleting_order_removes_files(client, app):
    _setup(client)
    client.post("/orders/1/files/upload", content_type="multipart/form-data",
                data={"kind": "design", "files": [(BytesIO(PNG), "a.png")]})
    stored = Path(app.config["UPLOAD_DIR"]) / db.session.query(OrderFile).one().stored_name
    assert stored.exists()
    client.post("/orders/1/delete")
    assert not stored.exists()


def test_bad_kind_rejected(client):
    _setup(client)
    r = client.post("/orders/1/files/upload", content_type="multipart/form-data",
                    data={"kind": "other", "files": [(BytesIO(PNG), "a.png")]})
    assert r.status_code == 400
