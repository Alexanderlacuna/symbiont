"""annot REST — door (c). ONE route per tool, returning the SAME bytes the CLI and the MCP return.

    python3 -m annot.rest --port 8765

    GET /                            the GN page (annot/web/gn_dossier.html) — door (d)
    GET /annot                       the surface: the six routes, their parameters, their purpose
    GET /annot/<tool>?<param>=…      the tool, as canonical JSON (identical bytes to every other door)

Read-only by construction: only do_GET is implemented; POST/PUT/PATCH/DELETE answer 405 with
`Allow: GET`. The handler never touches a file for writing and never makes an outbound request —
it parses the query string, calls core.dispatch(), and writes those bytes to the socket unchanged.

CORS is open because a notebook or a lab website is a named consumer; nothing here is a write, so an
open read door costs no integrity.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import core

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "gn_dossier.html")
READ_ONLY_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def route_table() -> dict:
    return {
        "routes": [{"method": "GET", "path": "/annot/" + t,
                    "tool": t, "purpose": core.SPECS[t]["purpose"],
                    "params": core.input_schema(t)} for t in core.TOOLS],
        "page": "/",
        "read_only": True,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "annot/%d" % core.CORE_VERSION
    protocol_version = "HTTP/1.1"
    store_path: str | None = None

    # -- helpers ---------------------------------------------------------------------------------
    def _send(self, body: bytes, code: int = 200, ctype: str = "application/json; charset=utf-8",
              extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt, *args):        # stderr, not a file
        sys.stderr.write("annot-rest: %s - %s\n" % (self.address_string(), fmt % args))

    # -- read ------------------------------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_OPTIONS(self):
        self._send(b"", 204)

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        if path == "/":
            try:
                with open(PAGE, "rb") as fh:                 # READ ONLY
                    body = fh.read()
            except OSError as exc:
                self._send(core.dumps({"status": "error", "reason": "page_missing",
                                       "message": str(exc)}), 500)
                return
            self._send(body, 200, "text/html; charset=utf-8")
            return
        if path == "/annot":
            self._send(core.dumps(route_table()))
            return
        if path.startswith("/annot/"):
            tool = path[len("/annot/"):]
            qs = parse_qs(url.query, keep_blank_values=True)
            args = {k: v[-1] for k, v in qs.items()}
            if tool not in core.SPECS:
                self._send(core.dumps({
                    "status": "error", "reason": "unknown_route",
                    "message": "no route /annot/%s; the surface is %s"
                               % (tool, ", ".join("/annot/" + t for t in core.TOOLS)),
                    "routes": ["/annot/" + t for t in core.TOOLS],
                }, ), 404)
                return
            self._send(core.dispatch(tool, args, self.store_path))
            return
        self._send(core.dumps({"status": "error", "reason": "not_found",
                               "message": "no route %r" % path,
                               "routes": ["/", "/annot"] + ["/annot/" + t for t in core.TOOLS]}), 404)

    # -- every write verb is refused, by construction --------------------------------------------
    def _refuse(self):
        self._send(core.dumps({
            "status": "error", "reason": "read_only",
            "message": ("this server is read-only by construction: %s is not implemented, and no "
                        "tool in this surface can create, update or upload anything"
                        % self.command),
        }), 405, extra={"Allow": "GET, HEAD, OPTIONS"})

    do_POST = do_PUT = do_PATCH = do_DELETE = _refuse


def serve(port: int = 8765, host: str = "127.0.0.1", store_path: str | None = None) -> int:
    Handler.store_path = store_path
    httpd = ThreadingHTTPServer((host, port), Handler)
    sys.stderr.write("annot-rest: read-only, %d routes + page at http://%s:%d/\n"
                     % (len(core.TOOLS), host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    port, host, store_path = 8765, "127.0.0.1", None
    for i, tok in enumerate(argv):
        if tok == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
        elif tok == "--host" and i + 1 < len(argv):
            host = argv[i + 1]
        elif tok == "--store" and i + 1 < len(argv):
            store_path = argv[i + 1]
        elif tok == "--routes":
            sys.stdout.buffer.write(core.dumps(route_table()))
            return 0
    return serve(port, host, store_path)


if __name__ == "__main__":
    sys.exit(main())
