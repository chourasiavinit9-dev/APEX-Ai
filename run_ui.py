#!/usr/bin/env python3
"""
run_ui.py — APEX Product Intelligence Dashboard Server.

Serves index.html on port 8080 AND provides a live pipeline API:
  POST /api/process   — run APEX pipeline on submitted text/MPN
  POST /api/enrich    — run unihack_pipeline enrich_row on a raw row
  GET  /api/status    — health check

Usage:
  python3 run_ui.py
  python3 run_ui.py --port 8080
  python3 run_ui.py --no-browser
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

UI_DIR = Path(__file__).parent / "ui"
PORT = 8080


# ─── Pipeline API ─────────────────────────────────────────────────────────────

def _run_pipeline(text: str, product_type: str | None = None) -> dict:
    """Run the full APEX pipeline on raw text and return structured result."""
    from core.ingest import ingest_text
    from core.llm_client import get_client
    from core.pipeline import run_single

    doc = ingest_text(text)
    client = get_client()
    result = run_single(doc, product_type=product_type or None, client=client, enrich_enabled=True)
    # Convert AmbiguousUOM objects to string for JSON serialization
    _json_safe(result)
    return result


def _run_enrich(row: dict) -> dict:
    """Run the unihack enrich_row pipeline on a raw row dict."""
    from core.llm_client import get_client
    from loaders.unihack_pipeline import enrich_row

    client = get_client()
    result = enrich_row(row, client=client)
    _json_safe(result)
    return result


def _json_safe(obj: object) -> None:
    """Recursively convert non-serializable objects to strings in-place."""
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, (dict, list)):
                _json_safe(v)
            elif not isinstance(v, (str, int, float, bool, type(None))):
                obj[k] = str(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, (dict, list)):
                _json_safe(v)
            elif not isinstance(v, (str, int, float, bool, type(None))):
                obj[i] = str(v)


# ─── HTTP Handler ──────────────────────────────────────────────────────────────

class ApexHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress noisy per-request logs
        pass

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_preflight(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_OPTIONS(self):
        self._send_cors_preflight()

    def do_GET(self):
        if self.path in ("/api/status", "/api/status/"):
            from core.llm_client import is_available
            self._send_json({
                "status": "ok",
                "api_available": is_available(),
                "version": "APEX-1.0",
            })
            return

        # Serve static files from ui/
        path = self.path.split("?")[0]
        if path == "/" or path == "":
            path = "/index.html"
        file_path = UI_DIR / path.lstrip("/")
        if file_path.exists() and file_path.is_file():
            suffix = file_path.suffix.lower()
            content_types = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css",
                ".js": "application/javascript",
                ".json": "application/json",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".svg": "image/svg+xml",
                ".ico": "image/x-icon",
            }
            ct = content_types.get(suffix, "application/octet-stream")
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        else:
            self._send_json({"error": "Not Found"}, 404)

    def do_POST(self):
        try:
            body = self._read_json_body()
        except Exception:
            self._send_json({"error": "Invalid JSON body"}, 400)
            return

        if self.path in ("/api/process", "/api/process/"):
            text = (body.get("text") or "").strip()
            product_type = body.get("product_type") or None
            if not text:
                self._send_json({"error": "Missing 'text' field"}, 400)
                return
            try:
                result = _run_pipeline(text, product_type)
                self._send_json({"ok": True, "result": result})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e), "trace": traceback.format_exc()}, 500)

        elif self.path in ("/api/enrich", "/api/enrich/"):
            row = body.get("row") or body
            try:
                result = _run_enrich(row)
                self._send_json({"ok": True, "result": result})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e), "trace": traceback.format_exc()}, 500)

        else:
            self._send_json({"error": "Unknown endpoint"}, 404)


# ─── Server boot ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="APEX Dashboard Server")
    parser.add_argument("--port", "-p", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    port = args.port
    server = None
    for p in range(port, port + 10):
        try:
            HTTPServer.allow_reuse_address = True
            server = HTTPServer(("", p), ApexHandler)
            port = p
            break
        except OSError:
            continue

    if server is None:
        print(f"❌  Ports {args.port}–{args.port+9} all occupied.")
        return

    url = f"http://localhost:{port}"
    print(f"\n⚡  APEX Product Intelligence Platform")
    print(f"🚀  Dashboard  → {url}")
    print(f"🔌  Live API   → {url}/api/process  (POST)")
    print(f"❤️   Health     → {url}/api/status   (GET)")
    print(f"\nPress Ctrl+C to stop.\n")

    if not args.no_browser:
        def _open():
            time.sleep(0.5)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑  APEX server stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
