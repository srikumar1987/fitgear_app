import pytest

from app import create_app, db


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("TUR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TUR_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("TUR_UPLOAD_DIR", str(tmp_path / "uploads"))


@pytest.fixture()
def app(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        yield app
        db.session.remove()


@pytest.fixture()
def client(app):
    client = app.test_client()
    client.post("/setup", data={"password": "secret1", "confirm": "secret1"})
    return client


@pytest.fixture()
def file_app(tmp_path, monkeypatch):
    """App with a real SQLite file, for tests that make real backups."""
    _env(monkeypatch, tmp_path)
    app = create_app({"TESTING": True, "SECRET_KEY": "test"})
    with app.app_context():
        yield app
        db.session.remove()
        db.engine.dispose()


@pytest.fixture()
def file_client(file_app):
    client = file_app.test_client()
    client.post("/setup", data={"password": "secret1", "confirm": "secret1"})
    return client
