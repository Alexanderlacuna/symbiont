"""annot MCP server — door (b). A thin reader of core.dispatch() over stdio JSON-RPC 2.0.

    python3 -m annot.mcp_server            # MCP over stdin/stdout (newline-delimited JSON-RPC)

Why hand-rolled rather than an SDK: this box has no `mcp` package (measured: ModuleNotFoundError),
and the protocol surface a read-only tool server needs is three methods. Writing them here keeps the
server dependency-free and keeps the property that matters: THE SERVER CANNOT WRITE. There is no
open() in this file at all, no http client, no temp file — it reads stdin, calls the core, writes
stdout. The tools list and the argument schemas are generated FROM core.SPECS, so this file
declares nothing about genetics and cannot drift from the core.

Methods: initialize · notifications/initialized · ping · tools/list · tools/call
Every tools/call result carries the SAME bytes core.dispatch() returns — the one-definition property
is checked byte-for-byte against the CLI by annot/selftest.sh.
"""
from __future__ import annotations

import json
import sys

from . import core

SERVER_NAME = "annot"
SERVER_VERSION = "1.0.0"
PROTOCOL_DEFAULT = "2024-11-05"
PROTOCOL_OK = ("2024-11-05", "2025-03-26", "2025-06-18")


def _tools() -> list[dict]:
    return [{"name": t,
             "description": core.SPECS[t]["purpose"],
             "inputSchema": core.input_schema(t),
             "annotations": {"readOnlyHint": True, "destructiveHint": False,
                             "idempotentHint": True, "openWorldHint": False}}
            for t in core.TOOLS]


def handle(msg: dict, store_path: str | None = None) -> dict | None:
    """One request -> one response (None for notifications)."""
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}

    if mid is None and method is not None:
        return None                                    # a notification: no reply on the wire

    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": asked if asked in PROTOCOL_OK else PROTOCOL_DEFAULT,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": ("Read-only genetics dossier over the harvested store. Every response "
                             "carries provenance {source, release, fetched_at, sha256} and names any "
                             "source that could not be reached. No tool writes anything."),
        }}

    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": _tools()}}

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in core.SPECS:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602,
                              "message": "unknown tool %r; the surface is %s"
                                         % (name, ", ".join(core.TOOLS))}}
        text = core.dispatch(name, args, store_path).decode("utf-8")
        try:
            status = json.loads(text).get("status")
        except ValueError:
            status = "error"
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}],
            "isError": status == "error",
        }}

    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": "method not found: %r" % (method,)}}


def serve(store_path: str | None = None) -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError as exc:
            sys.stderr.write("annot-mcp: unparsable message (%s)\n" % exc)
            continue
        try:
            reply = handle(msg, store_path)
        except Exception as exc:                        # never die on one bad request
            reply = {"jsonrpc": "2.0", "id": msg.get("id"),
                     "error": {"code": -32603, "message": "%s: %s" % (type(exc).__name__, exc)}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    store_path = None
    if "--store" in argv:
        store_path = argv[argv.index("--store") + 1]
    return serve(store_path)


if __name__ == "__main__":
    sys.exit(main())
