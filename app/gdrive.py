"""Back up straight to Google Drive through the Drive API. No Drive app or extra packages needed.

Sign-in uses Google's flow for desktop apps (OAuth with PKCE) and the narrow `drive.file` scope:
the app can only see and change files it created itself, never the rest of the Drive.

Files in the data folder (never synced, never committed, not inside database backups):
  gdrive_client.json  client id and secret of the user's own "Desktop app" OAuth client
  gdrive_token.json   refresh token and the connected account's email
  gdrive_state.json   Drive folder ids, what has been uploaded, last sync time and error
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
SCOPES = "openid email https://www.googleapis.com/auth/drive.file"
FOLDER_NAME = "TUR fitgear backups"
FOLDER_MIME = "application/vnd.google-apps.folder"
BACKUP_PREFIX = "tur_fitgear_"
KEEP_BACKUPS = 30

log = logging.getLogger("tur.gdrive")
_lock = threading.Lock()
_access: dict = {}


class DriveError(Exception):
    """A problem worth showing to the user as-is."""


class MissingPermission(DriveError):
    """Signed in, but the Google Drive checkbox was not ticked on Google's consent page."""


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
MISSING_PERMISSION = (
    "Google did not give the app permission to use Drive. Click 'Connect Google Drive' again and, on "
    "Google's page, tick the box 'See, edit, create, and delete only the specific Google Drive files "
    "you use with this app' (or 'Select all') before clicking Continue."
)


# ---------------------------------------------------------------- small helpers

def _files(data_dir: Path) -> tuple[Path, Path, Path]:
    data_dir = Path(data_dir)
    return data_dir / "gdrive_client.json", data_dir / "gdrive_token.json", data_dir / "gdrive_state.json"


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:  # older Windows may lack newer root certificates; certifi fills the gap when installed
        import certifi

        context.load_verify_locations(certifi.where())
    except ImportError:
        pass
    return context


def http(method: str, url: str, data: bytes | None = None, headers: dict | None = None,
         timeout: int = 120) -> tuple[int, dict, bytes]:
    """One HTTPS request. Returns (status, lower-cased headers, body). Replaced by a fake in tests."""
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()
    except (urllib.error.URLError, OSError) as exc:
        raise DriveError(f"Could not reach Google ({getattr(exc, 'reason', exc)}). Check the internet connection.") from None


def _json(response: tuple[int, dict, bytes], what: str) -> dict:
    status, _, body = response
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except ValueError:
        data = {}
    if status >= 400:
        err = data.get("error")
        detail = (err.get("message") if isinstance(err, dict) else data.get("error_description") or err) or body[:200]
        if status == 403 and "insufficient" in str(detail).lower() and "scope" in str(detail).lower():
            raise MissingPermission(MISSING_PERMISSION)
        raise DriveError(f"{what} failed ({status}): {detail}")
    return data


def _form(fields: dict) -> bytes:
    return urllib.parse.urlencode(fields).encode("ascii")


# ---------------------------------------------------------------- client file and status

def save_client(data_dir: Path, raw: bytes) -> None:
    """Store the OAuth client downloaded from Google Cloud (type: Desktop app)."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise DriveError("That is not the JSON file downloaded from Google Cloud.") from None
    if "web" in data and "installed" not in data:
        raise DriveError("This client is a 'Web application'. Create one of type 'Desktop app' and download that.")
    info = data.get("installed") or {}
    if not info.get("client_id") or not info.get("client_secret"):
        raise DriveError("The file has no client id / secret. Download it again from Google Cloud → Clients.")
    _write(_files(data_dir)[0], {"client_id": info["client_id"], "client_secret": info["client_secret"]})


def load_client(data_dir: Path) -> dict:
    return _read(_files(data_dir)[0])


def is_connected(data_dir: Path) -> bool:
    return bool(_read(_files(data_dir)[1]).get("refresh_token"))


def status(data_dir: Path) -> dict:
    client, token, state = (_read(p) for p in _files(data_dir))
    return {
        "has_client": bool(client.get("client_id")),
        "connected": bool(token.get("refresh_token")),
        "email": token.get("email", ""),
        "connected_at": token.get("connected_at", ""),
        "folder": FOLDER_NAME,
        "last_sync": state.get("last_sync", ""),
        "last_error": state.get("last_error", ""),
        "uploaded_files": len(state.get("uploads", [])),
        "last_backup": (state.get("backups") or [""])[-1],
    }


# ---------------------------------------------------------------- sign-in

def new_pkce() -> tuple[str, str]:
    """Returns (state, code_verifier)."""
    return secrets.token_urlsafe(24), secrets.token_urlsafe(48)


def auth_url(data_dir: Path, redirect_uri: str, state: str, verifier: str) -> str:
    client = load_client(data_dir)
    if not client:
        raise DriveError("Add the Google client file first.")
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })


def _email_from_id_token(id_token: str) -> str:
    """The id_token comes straight from Google's token endpoint over HTTPS, so its payload is trusted."""
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email", "")
    except (IndexError, ValueError, AttributeError):
        return ""


def connect(data_dir: Path, code: str, redirect_uri: str, verifier: str) -> str:
    """Finish sign-in: swap the code for tokens, remember the account, create the Drive folder."""
    client = load_client(data_dir)
    tokens = _json(http("POST", TOKEN_URL, _form({
        "client_id": client.get("client_id", ""), "client_secret": client.get("client_secret", ""),
        "code": code, "code_verifier": verifier, "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }), {"Content-Type": "application/x-www-form-urlencoded"}), "Google sign-in")
    granted = tokens.get("scope")
    if granted is not None and DRIVE_SCOPE not in granted.split():
        _revoke(tokens.get("refresh_token") or tokens.get("access_token"))
        raise MissingPermission(MISSING_PERMISSION)
    if not tokens.get("refresh_token"):
        raise DriveError("Google did not give long-term access. Remove 'TUR fitgear' at "
                         "myaccount.google.com/permissions and connect again.")
    email = _email_from_id_token(tokens.get("id_token", ""))
    _, token_file, state_file = _files(data_dir)
    _write(token_file, {"refresh_token": tokens["refresh_token"], "email": email,
                        "connected_at": datetime.now().isoformat(timespec="seconds")})
    _access[str(data_dir)] = (tokens.get("access_token", ""), time.time() + int(tokens.get("expires_in", 0)) - 60)
    _write(state_file, {})
    try:
        with _lock:
            _folders(data_dir, _access_token(data_dir))
    except MissingPermission:
        disconnect(data_dir)
        raise
    return email


def disconnect(data_dir: Path) -> None:
    """Revoke the app's access at Google (best effort) and forget the account."""
    _, token_file, state_file = _files(data_dir)
    _revoke(_read(token_file).get("refresh_token"))
    for path in (token_file, state_file):
        path.unlink(missing_ok=True)
    _access.pop(str(data_dir), None)


def _revoke(token: str | None) -> None:
    """Best effort: tell Google to drop this app's access."""
    if not token:
        return
    try:
        http("POST", REVOKE_URL, _form({"token": token}), {"Content-Type": "application/x-www-form-urlencoded"},
             timeout=20)
    except DriveError:
        pass


def remove_client(data_dir: Path) -> None:
    disconnect(data_dir)
    _files(data_dir)[0].unlink(missing_ok=True)


# ---------------------------------------------------------------- Drive calls

def _access_token(data_dir: Path) -> str:
    cached = _access.get(str(data_dir))
    if cached and cached[0] and cached[1] > time.time():
        return cached[0]
    client = load_client(data_dir)
    token_file = _files(data_dir)[1]
    refresh = _read(token_file).get("refresh_token")
    if not refresh:
        raise DriveError("Google Drive is not connected.")
    status_code, headers, body = http("POST", TOKEN_URL, _form({
        "client_id": client.get("client_id", ""), "client_secret": client.get("client_secret", ""),
        "refresh_token": refresh, "grant_type": "refresh_token",
    }), {"Content-Type": "application/x-www-form-urlencoded"})
    if status_code == 400 and b"invalid_grant" in body:
        token_file.unlink(missing_ok=True)
        raise DriveError("Google access has expired or was removed. Connect Google Drive again in Settings.")
    tokens = _json((status_code, headers, body), "Refreshing Google access")
    _access[str(data_dir)] = (tokens["access_token"], time.time() + int(tokens.get("expires_in", 0)) - 60)
    return tokens["access_token"]


def _auth(token: str, extra: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(extra or {})
    return headers


def _folder_ok(token: str, folder_id: str | None) -> bool:
    if not folder_id:
        return False
    status_code, headers, body = http("GET", f"{API_URL}/{folder_id}?fields=id,trashed", headers=_auth(token))
    if status_code == 404:
        return False
    return not _json((status_code, headers, body), "Checking the Drive folder").get("trashed", False)


def _create_folder(token: str, name: str, parent: str | None = None) -> str:
    meta = {"name": name, "mimeType": FOLDER_MIME}
    if parent:
        meta["parents"] = [parent]
    return _json(http("POST", f"{API_URL}?fields=id", json.dumps(meta).encode("utf-8"),
                      _auth(token, {"Content-Type": "application/json; charset=UTF-8"})), "Creating the Drive folder")["id"]


def _folders(data_dir: Path, token: str) -> tuple[str, str]:
    """The 'TUR fitgear backups' folder and its 'uploads' subfolder (re-created if deleted)."""
    state_file = _files(data_dir)[2]
    state = _read(state_file)
    if not _folder_ok(token, state.get("folder_id")):
        state.update(folder_id=_create_folder(token, FOLDER_NAME), uploads_folder_id=None, uploads=[], backups=[])
    if not _folder_ok(token, state.get("uploads_folder_id")):
        state.update(uploads_folder_id=_create_folder(token, "uploads", state["folder_id"]), uploads=[])
    _write(state_file, state)
    return state["folder_id"], state["uploads_folder_id"]


def _upload(token: str, path: Path, parent: str, mime: str) -> str:
    """Resumable upload: works for any file size with two requests."""
    content = path.read_bytes()
    status_code, headers, body = http("POST", f"{UPLOAD_URL}?uploadType=resumable",
                                      json.dumps({"name": path.name, "parents": [parent]}).encode("utf-8"),
                                      _auth(token, {"Content-Type": "application/json; charset=UTF-8",
                                                    "X-Upload-Content-Type": mime,
                                                    "X-Upload-Content-Length": str(len(content))}))
    _json((status_code, headers, body), f"Starting upload of {path.name}")
    session = headers.get("location")
    if not session:
        raise DriveError(f"Google did not accept the upload of {path.name}.")
    return _json(http("PUT", session, content, {"Content-Type": mime}, timeout=600), f"Uploading {path.name}")["id"]


def _prune(token: str, folder_id: str, keep: int) -> int:
    params = urllib.parse.urlencode({
        "q": f"'{folder_id}' in parents and name contains '{BACKUP_PREFIX}' and trashed = false",
        "orderBy": "createdTime desc", "pageSize": 200, "fields": "files(id,name)",
    }, quote_via=urllib.parse.quote)
    listing = _json(http("GET", f"{API_URL}?{params}", headers=_auth(token)), "Listing Drive backups")
    removed = 0
    for old in listing.get("files", [])[keep:]:
        status_code, headers, body = http("DELETE", f"{API_URL}/{old['id']}", headers=_auth(token))
        if status_code not in (204, 404):
            _json((status_code, headers, body), f"Removing old backup {old.get('name')}")
        removed += 1
    return removed


def _mime(path: Path) -> str:
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
        ".gif": "image/gif", ".pdf": "application/pdf",
    }.get(path.suffix.lower(), "application/octet-stream")


# ---------------------------------------------------------------- sync

def sync(data_dir: Path, backup_file: Path | None, upload_dir: Path) -> str:
    """Upload the given database backup and any design files not uploaded yet; keep the last 30 backups."""
    state_file = _files(data_dir)[2]
    with _lock:
        try:
            token = _access_token(data_dir)
            folder_id, uploads_id = _folders(data_dir, token)
            state = _read(state_file)
            done = []
            if backup_file and Path(backup_file).is_file() and backup_file.name not in state.get("backups", []):
                _upload(token, Path(backup_file), folder_id, "application/x-sqlite3")
                state["backups"] = (state.get("backups", []) + [backup_file.name])[-100:]
                done.append("backup " + backup_file.name)
            uploaded = set(state.get("uploads", []))
            new_files = [f for f in sorted(Path(upload_dir).glob("*")) if f.is_file() and f.name not in uploaded] \
                if Path(upload_dir).is_dir() else []
            for file in new_files:
                _upload(token, file, uploads_id, _mime(file))
                state.setdefault("uploads", []).append(file.name)
                _write(state_file, {**_read(state_file), "uploads": state["uploads"]})  # progress survives a failure
            if new_files:
                done.append(f"{len(new_files)} design file{'s' if len(new_files) != 1 else ''}")
            _prune(token, folder_id, KEEP_BACKUPS)
            state.update(last_sync=datetime.now().isoformat(timespec="seconds"), last_error="")
            _write(state_file, state)
            return ", ".join(done) or "everything was already uploaded"
        except DriveError as exc:
            if isinstance(exc, MissingPermission):  # half-working sign-in: forget it so Settings offers Connect again
                _revoke(_read(_files(data_dir)[1]).get("refresh_token"))
                _files(data_dir)[1].unlink(missing_ok=True)
                _access.pop(str(data_dir), None)
            _write(state_file, {**_read(state_file), "last_error": str(exc),
                                "last_error_at": datetime.now().isoformat(timespec="seconds")})
            raise


def safe_sync(data_dir: Path, backup_file: Path | None, upload_dir: Path) -> None:
    try:
        result = sync(data_dir, backup_file, upload_dir)
        log.info("Google Drive: %s", result)
    except DriveError as exc:
        log.warning("Google Drive upload failed: %s", exc)
    except Exception:  # never let a background upload crash the app
        log.exception("Google Drive upload failed")


def sync_in_background(data_dir: Path, backup_file: Path | None, upload_dir: Path) -> None:
    threading.Thread(target=safe_sync, args=(data_dir, backup_file, upload_dir), daemon=True,
                     name="gdrive-sync").start()
