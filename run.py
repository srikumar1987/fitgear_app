"""Start TUR fitgear with the Waitress server and open it in the browser.

`run.py --check` (run by start.bat first) reuses a running copy of the same version, or stops an
outdated one after an update so the new code is used.

start.bat runs this with pythonw (no console window), so a stray Ctrl+C or a click in a
console cannot stop or freeze the app. Output goes to tur_fitgear.log in the data folder.
For troubleshooting, run it with python.exe in a console to see the output directly.
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

# Exit codes of `run.py --check` (used by start.bat)
START_NEW, ALREADY_LATEST, OLD_VERSION_RUNNING = 0, 10, 11


def already_running(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def _local(method: str, url: str, headers: dict | None = None) -> tuple[int, str]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never send localhost via a proxy
    try:
        with opener.open(urllib.request.Request(url, data=b"" if method == "POST" else None,
                                                method=method, headers=headers or {}), timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except OSError:
        return 0, ""


def check(port: int) -> int:
    """Before starting: reuse a running copy of the same version, or stop an outdated one."""
    if not already_running(port):
        return START_NEW
    from app import CONTROL_TOKEN_FILE, build_id, data_dir

    base = f"http://127.0.0.1:{port}"
    status, running_build = _local("GET", f"{base}/_control/build")
    if status == 200 and running_build.strip() == build_id():
        if os.environ.get("TUR_NO_BROWSER") != "1":
            webbrowser.open(f"http://localhost:{port}")
        return ALREADY_LATEST
    token_file = data_dir() / CONTROL_TOKEN_FILE
    if status != 200 or not token_file.exists():
        return OLD_VERSION_RUNNING
    print("An older version of TUR fitgear is running. Saving a backup and restarting it...")
    _local("POST", f"{base}/_control/stop", {"X-Control-Token": token_file.read_text().strip()})
    for _ in range(40):
        if not already_running(port):
            return START_NEW
        time.sleep(0.5)
    return OLD_VERSION_RUNNING


def main() -> None:
    host = os.environ.get("TUR_HOST", "127.0.0.1")
    port = int(os.environ.get("TUR_PORT", "5000"))
    url = f"http://localhost:{port}"
    open_browser = os.environ.get("TUR_NO_BROWSER") != "1"

    if already_running(port):
        if open_browser:
            webbrowser.open(url)
        return

    background = sys.stdout is None or os.path.basename(sys.executable).lower() == "pythonw.exe"
    if background:
        from app import data_dir

        folder = data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=str(folder / "tur_fitgear.log"), level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Pages fire a few small refresh requests at once; that is normal, so hide the queue notices.
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)

    from waitress import serve

    from app import create_app

    app = create_app()
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    if not background:
        print(f"TUR fitgear is running at {url}")
        print("Keep this window open while you use the app. Close it to stop.")
    logging.getLogger("tur").info("Starting on %s", url)
    serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(check(int(os.environ.get("TUR_PORT", "5000"))))
    try:
        main()
    except Exception:
        logging.getLogger("tur").exception("TUR fitgear stopped because of an error")
        raise
