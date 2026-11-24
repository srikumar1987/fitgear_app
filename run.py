"""Start TUR fitgear with the Waitress server and open it in the browser.

start.bat runs this with pythonw (no console window), so a stray Ctrl+C or a click in a
console cannot stop or freeze the app. Output goes to tur_fitgear.log in the data folder.
For troubleshooting, run it with python.exe in a console to see the output directly.
"""
import logging
import os
import socket
import sys
import threading
import webbrowser


def already_running(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


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
    try:
        main()
    except Exception:
        logging.getLogger("tur").exception("TUR fitgear stopped because of an error")
        raise
