#!/usr/bin/env python3
"""
run_ui.py — Launch the APEX Dark Electric Dashboard.

Zero external dependencies (pure Python standard library).
Serves the modern UI at http://localhost:8080 and opens the browser automatically.

Usage:
  python3 run_ui.py
  python3 run_ui.py --port 8080
  python3 run_ui.py --no-browser
"""
from __future__ import annotations

import argparse
import functools
import http.server
import os
import socketserver
import sys
import threading
import time
import webbrowser
from pathlib import Path

UI_DIR = Path(__file__).parent / "ui"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def log_message(self, format, *args):
        # Quiet standard HTTP request logs, only show startup
        pass


def main():
    parser = argparse.ArgumentParser(description="Launch APEX Dashboard")
    parser.add_argument("--port", "-p", type=int, default=8080, help="Port to serve on (default: 8080)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    args = parser.parse_args()

    port = args.port
    # Allow port fallback if occupied
    server = None
    for p in range(port, port + 10):
        try:
            socketserver.TCPServer.allow_reuse_address = True
            server = socketserver.TCPServer(("", p), QuietHandler)
            port = p
            break
        except OSError:
            continue

    if server is None:
        print(f"❌ Error: Ports {args.port}-{args.port+9} are all occupied.")
        sys.exit(1)

    url = f"http://localhost:{port}/index.html"
    print(f"\n⚡ APEX — AI-Powered Product Intelligence Dashboard")
    print(f"🚀 Live Server: {url}")
    print(f"📂 Serving:     {UI_DIR}\n")
    print(f"Press Ctrl+C to stop.\n")

    if not args.no_browser:
        def _open():
            time.sleep(0.5)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 APEX Dashboard server stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
