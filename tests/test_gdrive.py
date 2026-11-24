"""Google Drive link, tested against a fake Google (no network)."""
import base64
import json
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app import gdrive

CLIENT_JSON = json.dumps({"installed": {"client_id": "cid.apps.googleusercontent.com", "client_secret": "sec"}})


def _jwt(payload: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'none'})}.{enc(payload)}.sig"


class FakeGoogle:
    def __init__(self):
        self.files, self.sessions, self.revoked, self.n = {}, {}, [], 0
        self.refresh_fails = False
        self.verifiers = []

    def _id(self):
        self.n += 1
        return f"id{self.n}"

    def __call__(self, method, url, data=None, headers=None, timeout=0):
        def ok(body, status=200, hdrs=None):
            return status, hdrs or {}, json.dumps(body).encode()

        assert not any(c <= " " or c == "\x7f" for c in url), f"URL must be encoded: {url}"   # like http.client

        parsed = urlparse(url)
        if url.startswith(gdrive.TOKEN_URL):
            form = {k: v[0] for k, v in parse_qs(data.decode()).items()}
            if form["grant_type"] == "authorization_code":
                self.verifiers.append(form["code_verifier"])
                return ok({"access_token": "at1", "expires_in": 3600, "refresh_token": "rt1",
                           "id_token": _jwt({"email": "owner@gmail.com"})})
            if self.refresh_fails:
                return ok({"error": "invalid_grant"}, 400)
            return ok({"access_token": "at2", "expires_in": 3600})
        if url.startswith(gdrive.REVOKE_URL):
            self.revoked.append(parse_qs(data.decode())["token"][0])
            return ok({})
        if url.startswith(gdrive.UPLOAD_URL):
            sid = self._id()
            self.sessions[sid] = json.loads(data)
            return 200, {"location": f"https://upload.test/{sid}"}, b""
        if url.startswith("https://upload.test/"):
            meta = self.sessions.pop(url.rsplit("/", 1)[1])
            fid = self._id()
            self.files[fid] = {**meta, "content": data, "mime": headers["Content-Type"], "trashed": False, "order": self.n}
            return ok({"id": fid})
        assert headers and headers.get("Authorization", "").startswith("Bearer ")
        rest = parsed.path[len("/drive/v3/files"):]
        if method == "POST":
            meta = json.loads(data)
            fid = self._id()
            self.files[fid] = {**meta, "trashed": False, "order": self.n}
            return ok({"id": fid})
        if method == "GET" and rest.startswith("/"):
            fid = rest[1:]
            return ok({"id": fid, "trashed": self.files[fid]["trashed"]}) if fid in self.files else ok({}, 404)
        if method == "GET":
            q = parse_qs(parsed.query)["q"][0]
            parent = q.split("'")[1]
            found = [dict(id=k, name=v["name"]) for k, v in sorted(self.files.items(), key=lambda kv: -kv[1]["order"])
                     if parent in v.get("parents", []) and gdrive.BACKUP_PREFIX in v["name"] and not v["trashed"]]
            return ok({"files": found})
        if method == "DELETE":
            self.files.pop(rest[1:], None)
            return 204, {}, b""
        raise AssertionError(f"unexpected call {method} {url}")

    def named(self, prefix):
        return [f for f in self.files.values() if f["name"].startswith(prefix)]


@pytest.fixture()
def google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(gdrive, "http", fake)
    gdrive._access.clear()
    yield fake
    gdrive._access.clear()


def _connect(file_client):
    file_client.post("/settings/gdrive/client", content_type="multipart/form-data",
                     data={"client": (BytesIO(CLIENT_JSON.encode()), "client_secret.json")})
    r = file_client.get("/settings/gdrive/connect")
    params = {k: v[0] for k, v in parse_qs(urlparse(r.headers["Location"]).query).items()}
    r = file_client.get(f"/?code=abc&state={params['state']}")              # Google redirects to the app root
    assert "/settings/gdrive/callback" in r.headers["Location"]
    file_client.get(r.headers["Location"])
    return params


def test_connect_uploads_backup_and_design_files(file_app, file_client, google):
    uploads = Path(file_app.config["UPLOAD_DIR"])
    uploads.mkdir(parents=True)
    (uploads / "1_front.png").write_bytes(b"png-bytes")

    params = _connect(file_client)
    assert params["redirect_uri"] == "http://localhost/"
    assert params["code_challenge_method"] == "S256" and "drive.file" in params["scope"]
    assert params["access_type"] == "offline" and google.verifiers                  # PKCE verifier sent

    status = gdrive.status(Path(file_app.config["DATA_DIR"]))
    assert status["connected"] and status["email"] == "owner@gmail.com" and not status["last_error"]
    [folder] = [k for k, v in google.files.items() if v["name"] == gdrive.FOLDER_NAME]
    [backup] = google.named("tur_fitgear_")
    assert backup["parents"] == [folder] and backup["content"][:15] == b"SQLite format 3"
    [design] = google.named("1_front")
    assert design["content"] == b"png-bytes" and design["mime"] == "image/png"
    page = file_client.get("/settings").get_data(as_text=True)
    assert "Connected" in page and "owner@gmail.com" in page

    file_client.post("/settings/gdrive/sync")
    assert len(google.named("tur_fitgear_")) == 2 and len(google.named("1_front")) == 1   # no re-upload

    file_client.post("/settings/backup")                                         # every backup goes to Drive
    assert len(google.named("tur_fitgear_")) == 3


def test_keeps_last_backups_and_recreates_deleted_folder(file_app, file_client, google, monkeypatch):
    monkeypatch.setattr(gdrive, "KEEP_BACKUPS", 2)
    _connect(file_client)
    for _ in range(3):
        file_client.post("/settings/gdrive/sync")
    assert len(google.named("tur_fitgear_")) == 2

    for f in google.files.values():
        if f["name"] == gdrive.FOLDER_NAME:
            f["trashed"] = True
    file_client.post("/settings/gdrive/sync")
    live = [f for f in google.files.values() if f["name"] == gdrive.FOLDER_NAME and not f["trashed"]]
    assert len(live) == 1


def test_expired_access_asks_to_reconnect(file_app, file_client, google):
    _connect(file_client)
    gdrive._access.clear()
    google.refresh_fails = True
    r = file_client.post("/settings/gdrive/sync", follow_redirects=True)
    assert "Connect Google Drive again" in r.get_data(as_text=True)
    assert not gdrive.is_connected(Path(file_app.config["DATA_DIR"]))


def test_disconnect_revokes(file_app, file_client, google):
    _connect(file_client)
    file_client.post("/settings/gdrive/disconnect")
    assert google.revoked == ["rt1"]
    data_dir = Path(file_app.config["DATA_DIR"])
    assert not gdrive.is_connected(data_dir) and gdrive.status(data_dir)["has_client"]


def test_wrong_state_and_bad_client_files(file_app, file_client, google):
    r = file_client.post("/settings/gdrive/client", content_type="multipart/form-data", follow_redirects=True,
                         data={"client": (BytesIO(b'{"web": {"client_id": "x"}}'), "c.json")})
    assert "Desktop app" in r.get_data(as_text=True)
    file_client.post("/settings/gdrive/client", content_type="multipart/form-data",
                     data={"client": (BytesIO(CLIENT_JSON.encode()), "c.json")})
    file_client.get("/settings/gdrive/connect")
    r = file_client.get("/settings/gdrive/callback?code=abc&state=forged", follow_redirects=True)
    assert "expired" in r.get_data(as_text=True)
    assert not gdrive.is_connected(Path(file_app.config["DATA_DIR"]))


def test_offline_error_is_kept_for_settings(file_app, file_client, google, monkeypatch):
    _connect(file_client)
    gdrive._access.clear()

    def offline(*_a, **_k):
        raise gdrive.DriveError("Could not reach Google (offline). Check the internet connection.")

    monkeypatch.setattr(gdrive, "http", offline)
    file_client.post("/settings/backup")                                          # backup still saved locally
    assert "Could not reach Google" in file_client.get("/settings").get_data(as_text=True)
