"""start.bat's version check: build fingerprint and token-protected stop (local only)."""
from app import CONTROL_TOKEN_FILE, build_id


def test_build_endpoint_matches_code(app):
    client = app.test_client()                    # no login needed, but only from this PC
    r = client.get("/_control/build")
    assert r.status_code == 200 and r.get_data(as_text=True) == build_id() == app.config["BUILD_ID"]
    assert client.get("/_control/build", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403


def test_stop_needs_the_local_token(app, tmp_path):
    client = app.test_client()
    assert client.post("/_control/stop").status_code == 403
    token_file = app.config["DATA_DIR"] / CONTROL_TOKEN_FILE
    token_file.write_text("abc123")
    assert client.post("/_control/stop", headers={"X-Control-Token": "wrong"}).status_code == 403
    assert client.post("/_control/stop", headers={"X-Control-Token": "abc123"},
                       environ_base={"REMOTE_ADDR": "10.0.0.5"}).status_code == 403
    r = client.post("/_control/stop", headers={"X-Control-Token": "abc123"})
    assert r.status_code == 200 and r.get_data(as_text=True) == "stopping"
