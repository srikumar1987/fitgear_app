"""Restore: from Google Drive on a new PC, and from a backup file."""
import sqlite3
from io import BytesIO
from pathlib import Path

from app import create_app, db, gdrive
from app.backup import list_backups
from app.models import Customer
from tests.test_gdrive import _connect, google  # noqa: F401  (fixture)


def test_empty_install_is_never_uploaded(file_app, file_client, google):
    _connect(file_client, with_data=False)
    assert not google.named("tur_fitgear_")
    file_client.post("/settings/gdrive/sync")
    assert not google.named("tur_fitgear_")


def test_restore_on_a_new_pc(file_app, file_client, google, tmp_path, monkeypatch):
    # PC 1: real data, connected, backed up to Drive
    file_client.post("/customers/new", data={"name": "Vintage Warriors"})
    file_client.post("/orders/new", data={"customer_id": "1", "title": "League jerseys"})
    uploads = Path(file_app.config["UPLOAD_DIR"])
    uploads.mkdir(parents=True)
    (uploads / "1_front.png").write_bytes(b"design")
    _connect(file_client, with_data=False)
    assert len(google.named("tur_fitgear_")) == 1

    # PC 2: fresh install, same Google account and client file
    for name in ("TUR_DATA_DIR", "TUR_BACKUP_DIR", "TUR_UPLOAD_DIR"):
        monkeypatch.setenv(name, str(tmp_path / "pc2" / name.lower()))
    app2 = create_app({"TESTING": True, "SECRET_KEY": "test2"})
    try:
        with app2.app_context():
            pc2 = app2.test_client()
            pc2.post("/setup", data={"password": "newpass1", "confirm": "newpass1"})
            params = _connect(pc2, with_data=False)
            assert params["after_connect"].endswith("/settings/restore")          # offered to restore
            assert len([f for f in google.files.values() if f["name"] == gdrive.FOLDER_NAME]) == 1   # folder reused
            assert len(google.named("tur_fitgear_")) == 1                          # empty PC 2 not uploaded

            page = pc2.get("/settings/restore").get_data(as_text=True)
            [backup_id] = [k for k, v in google.files.items() if v["name"].startswith("tur_fitgear_")]
            assert "Latest" in page and backup_id in page
            r = pc2.post(f"/settings/restore/drive/{backup_id}", data={"designs": "1"}, follow_redirects=True)
            text = r.get_data(as_text=True)
            assert "Restored from Google Drive: 1 customers, 1 orders" in text
            assert "Design files downloaded: 1" in text
            assert [c.name for c in db.session.query(Customer)] == ["Vintage Warriors"]
            assert (Path(app2.config["UPLOAD_DIR"]) / "1_front.png").read_bytes() == b"design"

            pc2.post("/logout")
            assert pc2.post("/login", data={"password": "newpass1"}).status_code == 200    # wrong now
            assert pc2.post("/login", data={"password": "secret1"}).status_code == 302     # PC 1 password
            db.session.remove()
    finally:
        with app2.app_context():
            db.engine.dispose()


def test_restore_from_file_keeps_a_safety_copy(file_app, file_client):
    file_client.post("/customers/new", data={"name": "Before"})
    r = file_client.get("/settings/download-backup")
    backup = r.data
    r.close()
    file_client.post("/customers/new", data={"name": "After"})
    copies = len(list_backups(file_app))

    r = file_client.post("/settings/restore/file", content_type="multipart/form-data", follow_redirects=True,
                         data={"backup": (BytesIO(backup), "tur_fitgear_old.db")})
    assert "Restored from tur_fitgear_old.db: 1 customers" in r.get_data(as_text=True)
    assert [c.name for c in db.session.query(Customer)] == ["Before"]
    assert len(list_backups(file_app)) == copies + 1                      # current data saved before restoring


def test_restore_rejects_bad_files(file_app, file_client, tmp_path):
    file_client.post("/customers/new", data={"name": "Keep me"})
    r = file_client.post("/settings/restore/file", content_type="multipart/form-data", follow_redirects=True,
                         data={"backup": (BytesIO(b"not a database"), "x.db")})
    assert "not a TUR fitgear backup" in r.get_data(as_text=True)

    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE notes (id INTEGER)")
    conn.commit()
    conn.close()
    r = file_client.post("/settings/restore/file", content_type="multipart/form-data", follow_redirects=True,
                         data={"backup": (BytesIO(other.read_bytes()), "other.db")})
    assert "not from the TUR fitgear app" in r.get_data(as_text=True)
    assert [c.name for c in db.session.query(Customer)] == ["Keep me"]


def test_drive_restore_only_accepts_app_backups(file_app, file_client, google):
    uploads = Path(file_app.config["UPLOAD_DIR"])
    uploads.mkdir(parents=True)
    (uploads / "1_front.png").write_bytes(b"design")
    _connect(file_client)
    [design_id] = [k for k, v in google.files.items() if v["name"] == "1_front.png"]
    r = file_client.post(f"/settings/restore/drive/{design_id}", follow_redirects=True)
    assert "not one of the app&#39;s backups" in r.get_data(as_text=True)
    assert file_client.post("/settings/restore/drive/bad..id").status_code == 404
