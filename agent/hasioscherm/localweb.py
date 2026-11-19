"""Kleine lokale webserver (alleen 127.0.0.1) voor de kiosk zelf.

Serveert de offline-pagina, de setup-wizard en een kleine JSON-API.
POST-verzoeken moeten de header 'X-Hasioscherm: 1' hebben: zo kan een
willekeurige webpagina in de browser geen instellingen wijzigen (die header
dwingt een CORS-preflight af, en die staan we niet toe).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

PORT = int(os.environ.get("HASIOSCHERM_LOCAL_PORT", "8099"))
WEB_ROOT = os.environ.get("HASIOSCHERM_WEB", "/opt/hasioscherm/kiosk")

# route: (methode, pad) -> functie(body: dict, query: dict) -> (status, object)
Route = Callable[[dict, dict], tuple]


class _Handler(SimpleHTTPRequestHandler):
    routes: dict = {}

    def _json(self, status: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method: str) -> bool:
        u = urlparse(self.path)
        fn = self.routes.get((method, u.path))
        if fn is None:
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            self._json(403, {"fout": "niet toegestaan"})
            return True
        body = {}
        if method == "POST":
            if self.headers.get("X-Hasioscherm") != "1":
                self._json(403, {"fout": "header ontbreekt"})
                return True
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                try:
                    body = json.loads(self.rfile.read(n))
                except ValueError:
                    self._json(400, {"fout": "ongeldige JSON"})
                    return True
        query = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            status, obj = fn(body, query)
        except Exception as e:  # noqa: BLE001
            log.exception("Fout in %s %s", method, u.path)
            status, obj = 500, {"fout": str(e)}
        self._json(status, obj)
        return True

    def do_GET(self):  # noqa: N802
        if not self._route("GET"):
            super().do_GET()

    def do_POST(self):  # noqa: N802
        if not self._route("POST"):
            self._json(404, {"fout": "onbekend"})

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):  # stil
        log.debug("web: " + fmt, *args)


def start(routes: dict) -> None:
    handler = type("Handler", (_Handler,), {"routes": routes})
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), partial(handler, directory=WEB_ROOT))
    threading.Thread(target=srv.serve_forever, daemon=True, name="localweb").start()
    log.info("Lokale webserver op http://127.0.0.1:%s (%s)", PORT, WEB_ROOT)
