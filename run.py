"""FreeBatch Studio launcher.

Usage:
    python run.py            # start the app
    python run.py --port 9000
"""

from __future__ import annotations

import argparse
import os
import socket
import threading
import time
import webbrowser


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) != 0


def main() -> None:
    parser = argparse.ArgumentParser(description="FreeBatch Studio launcher")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8737")))
    parser.add_argument("--no-browser", action="store_true", help="do not open browser")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        def _open() -> None:
            for _ in range(30):
                if not _port_free(args.host, args.port):
                    webbrowser.open(url)
                    return
                time.sleep(0.4)

        threading.Thread(target=_open, daemon=True).start()

    from app.main import app_state, run

    if app_state.runtime.get_bool("free_only_mode", True):
        print("FreeBatch Studio starting (FREE-ONLY mode)")
    print(f"  local UI: {url}")
    print("  press Ctrl+C to stop")
    run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
