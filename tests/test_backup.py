"""Backups with a real database file: custom (Google Drive) folder, uploads mirror, downloads."""
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from app.backup import check_backup_dir, list_backups


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
    assert len(list_backups(file_app)) == 1                       # nothing changed: latest backup reused

    file_client.post("/customers/new", data={"name": "New team"})
    file_client.get("/settings/download-backup").close()
    assert len(list_backups(file_app)) == 2

    r = file_client.get("/settings/download-designs")
    assert zipfile.ZipFile(BytesIO(r.data)).namelist() == ["1_abc.png"]
    r.close()

    file_client.post("/settings/backup-folder", data={"folder": ""})
    assert "(default)" in file_client.get("/settings").get_data(as_text=True)


def test_no_new_backup_without_changes(file_app, file_client):
    r = file_client.post("/settings/backup", follow_redirects=True)
    assert "Backup saved" in r.get_data(as_text=True)
    r = file_client.post("/settings/backup", follow_redirects=True)
    assert "No changes since the last backup" in r.get_data(as_text=True)
    assert len(list_backups(file_app)) == 1
    file_client.post("/customers/new", data={"name": "Changed"})
    r = file_client.post("/settings/backup", follow_redirects=True)
    assert "Backup saved" in r.get_data(as_text=True) and len(list_backups(file_app)) == 2


def test_backup_folder_validation(file_app):
    with pytest.raises(ValueError, match="full path"):
        check_backup_dir(file_app, "relative\\folder")
    with pytest.raises(ValueError, match="outside the live data folder"):
        check_backup_dir(file_app, str(Path(file_app.config["DATA_DIR"]) / "sub"))


def test_download_designs_when_empty(file_client):
    r = file_client.get("/settings/download-designs")
    assert r.status_code == 302
