"""Backups with a real database file: custom (Google Drive) folder, uploads mirror, downloads."""
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from app import create_app, db
from app.backup import check_backup_dir, list_backups


@pytest.fixture()
def file_app(tmp_path, monkeypatch):
    monkeypatch.setenv("TUR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TUR_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("TUR_UPLOAD_DIR", str(tmp_path / "uploads"))
    app = create_app({"TESTING": True, "SECRET_KEY": "test"})   # real SQLite file in the data folder
    with app.app_context():
        yield app
        db.session.remove()
        db.engine.dispose()


@pytest.fixture()
def file_client(file_app):
    client = file_app.test_client()
    client.post("/setup", data={"password": "secret1", "confirm": "secret1"})
    return client


def test_custom_backup_folder_gets_backups_and_design_files(file_app, file_client, tmp_path):
    uploads = Path(file_app.config["UPLOAD_DIR"])
    uploads.mkdir(parents=True)
    (uploads / "1_abc.png").write_bytes(b"png")
    drive = tmp_path / "My Drive" / "TUR fitgear backups"

    r = file_client.post("/settings/backup-folder", data={"folder": str(drive)})
    assert r.status_code == 302
    assert len(list(drive.glob("tur_fitgear_*.db"))) == 1
    assert (drive / "uploads" / "1_abc.png").read_bytes() == b"png"
    assert "TUR fitgear backups" in file_client.get("/settings").get_data(as_text=True)

    r = file_client.get("/settings/download-backup")
    assert r.status_code == 200 and r.data[:15] == b"SQLite format 3"
    r.close()
    assert len(list_backups(file_app)) == 2

    r = file_client.get("/settings/download-designs")
    assert zipfile.ZipFile(BytesIO(r.data)).namelist() == ["1_abc.png"]
    r.close()

    file_client.post("/settings/backup-folder", data={"folder": ""})
    assert "(default)" in file_client.get("/settings").get_data(as_text=True)


def test_backup_folder_validation(file_app):
    with pytest.raises(ValueError, match="full path"):
        check_backup_dir(file_app, "relative\\folder")
    with pytest.raises(ValueError, match="outside the live data folder"):
        check_backup_dir(file_app, str(Path(file_app.config["DATA_DIR"]) / "sub"))


def test_download_designs_when_empty(file_client):
    r = file_client.get("/settings/download-designs")
    assert r.status_code == 302
