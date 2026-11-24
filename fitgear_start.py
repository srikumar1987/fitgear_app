"""Start TUR fitgear with the Waitress server and open it in the browser.

`fitgear_start.py --stop` (run by start.bat before updating, and by stop.bat) stops the running copy:
politely through a local token-protected endpoint (it saves a backup), or, for very old copies, by
ending the Python process listening on the port.

start.bat runs this with pythonw (no console window), so a stray Ctrl+C or a click in a
console cannot stop or freeze the app. Output goes to tur_fitgear.log in the data folder.
For troubleshooting, run it with python.exe in a console to see the output directly.
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser


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


def _wait_until_stopped(port: int, seconds: int = 20) -> bool:
    for _ in range(seconds * 2):
        if not already_running(port):
            return True
        time.sleep(0.5)
    return False


def _python_pid_on_port(port: int) -> int | None:
    """PID listening on the port, only if it is a Python process (never touches other programs)."""
    try:
        netstat = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in netstat.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}") and parts[4].isdigit():
            pid = int(parts[4])
            image = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                   capture_output=True, text=True, timeout=20).stdout.lower()
            return pid if ('"python.exe"' in image or '"pythonw.exe"' in image) else None
    return None


def stop(port: int) -> int:
    """Stop the running copy. Politely first (it saves a backup); very old copies are ended directly."""
    if not already_running(port):
        return 0
    from app import CONTROL_TOKEN_FILE, data_dir

    print("Stopping the running TUR fitgear (a backup is saved first)...")
    token_file = data_dir() / CONTROL_TOKEN_FILE
    if token_file.exists():
        status, _ = _local("POST", f"http://127.0.0.1:{port}/_control/stop",
                           {"X-Control-Token": token_file.read_text().strip()})
        if status == 200 and _wait_until_stopped(port):
            return 0
    pid = _python_pid_on_port(port)
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20)
        if _wait_until_stopped(port):
            return 0
    print(f"Port {port} is used by another program, or TUR fitgear could not be stopped.")
    return 1


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
    if "--stop" in sys.argv:
        sys.exit(stop(int(os.environ.get("TUR_PORT", "5000"))))
    try:
        main()
    except Exception:
        logging.getLogger("tur").exception("TUR fitgear stopped because of an error")
        raise
