import pytest

from app import create_app, db


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("TUR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TUR_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("TUR_UPLOAD_DIR", str(tmp_path / "uploads"))
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        yield app
        db.session.remove()


@pytest.fixture()
def client(app):
    client = app.test_client()
    client.post("/setup", data={"password": "secret1", "confirm": "secret1"})
    return client
