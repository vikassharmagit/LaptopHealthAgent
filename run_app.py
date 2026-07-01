from __future__ import annotations

import threading
import time
import webbrowser

import uvicorn

from laptop_health_agent.admin_utils import is_admin, relaunch_self_as_admin


HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"


def open_browser() -> None:
    time.sleep(1.5)
    webbrowser.open(URL)


if __name__ == "__main__":
    import socket
    if not is_admin():
        if relaunch_self_as_admin():
            raise SystemExit(0)
        raise SystemExit("Administrator permission is required to start Laptop Health Agent.")
    from laptop_health_agent.api import app

    # Wait for the port to become free if a previous process is exiting
    for attempt in range(15):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, PORT))
                break
            except OSError:
                time.sleep(0.4)

    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        reload=False,
        access_log=True,
    )

