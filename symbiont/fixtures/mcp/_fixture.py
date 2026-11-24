"""THE SMALLEST HONEST MCP SERVER — the shared core of the three fixtures next door.

A fixture that is not really an MCP server proves nothing about an MCP adapter, so these speak the
protocol a client actually needs: newline-delimited JSON-RPC 2.0 on stdio (or POST /mcp for the
streamable-HTTP variant), with

    initialize                 -> protocolVersion + capabilities + serverInfo{name,version}
    notifications/initialized   -> no answer (a notification)
    tools/list                 -> [{name, description, inputSchema}]   (the PIN is a hash of these)
    tools/call                 -> {content:[{type:"text", text:<json>}], structuredContent:{...}}

Nothing here knows what a source is: which shape a fixture answers in, and whether it can state a
count, is the WHOLE POINT of having three of them (see server_alpha / server_beta / server_gamma).

WHY A FIXTURE, NOT A MOCK: the adapter under test spawns this process and talks to it over a real
pipe — the transport, the handshake, tools/list and the pin check are exercised for real, and the
only thing faked is the DATA.
"""
from __future__ import annotations

import json
import sys

PROTOCOL = "2025-06-18"


def handle(spec: dict, msg: dict) -> dict | None:
    """One JSON-RPC message -> {"result": …} | {"error": …} | None (for a notification)."""
    method = msg.get("method")
    if msg.get("id") is None and method is not None:
        return None                                  # a notification never gets an answer
    if method == "initialize":
        return {"result": {"protocolVersion": PROTOCOL,
                           "capabilities": {"tools": {}},
                           "serverInfo": dict(spec["server"])}}
    if method == "tools/list":
        return {"result": {"tools": [dict(t) for t in spec["tools"]]}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name, args = params.get("name"), params.get("arguments") or {}
        for t in spec["tools"]:
            if t["name"] == name:
                out = spec["call"](name, args)
                if out.get("isError"):
                    return {"result": {"isError": True, "content": [
                        {"type": "text", "text": out.get("text", "error")}]}}
                res = {"content": [{"type": "text", "text": json.dumps(out["document"], sort_keys=True)}]}
                if out.get("structured"):
                    res["structuredContent"] = out["document"]
                return {"result": res}
        return {"error": {"code": -32602, "message": f"unknown tool {name!r}"}}
    return {"error": {"code": -32601, "message": f"unknown method {method!r}"}}


def serve_stdio(spec: dict) -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        out = handle(spec, msg)
        if out is None:
            continue
        reply = {"jsonrpc": "2.0", "id": msg.get("id")}
        reply.update(out)
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
    return 0


def serve_http(spec: dict, port: int) -> int:
    """POST /mcp -> one JSON-RPC answer as application/json (no SSE: the client accepts both, and a
    fixture that only answers the SIMPLER of the two channels cannot hide a client that only speaks
    the other)."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):                    # a quiet fixture: no clatter on stderr
            pass

        def do_POST(self):
            n = int(self.headers.get("content-length") or 0)
            try:
                msg = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                self.send_error(400, "bad JSON")
                return
            out = handle(spec, msg)
            body = b"" if out is None else json.dumps(
                dict({"jsonrpc": "2.0", "id": msg.get("id")}, **out)).encode()
            self.send_response(200 if out is not None else 202)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.send_header("mcp-session-id", "fixture-session-1")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.send_error(405, "POST /mcp only")

    srv = HTTPServer(("127.0.0.1", port), H)
    print(f"LISTENING 127.0.0.1:{srv.server_port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def tool(name: str, description: str, props: dict, required=()) -> dict:
    return dict(name=name, description=description,
                inputSchema={"type": "object", "properties": props, "required": list(required)})
