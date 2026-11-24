"""THE WRAP — the literature arm's only client, and it is the MCP server, not E-utilities.

This module is a stdio JSON-RPC client for the PINNED server (@cyanheads/pubmed-mcp-server,
exact version from sources.yml — verified against the installed package.json at startup, and
refused if they disagree). The arm writes NO E-utilities calls, NO pagination, NO retry and NO
429 backoff of its own: the server searches, fetches, throttles and retries (the survey's whole
point — reports/MCP-SURVEY-BIOINFORMATICS-REPORT.md, '399 == 399'). If a tool call errors, the
error surfaces with its code; this client never retries it — a silent retry would be the exact
429 logic the row forbids.

What this client DOES own, because the MCP cannot do it for us:
  · the pin check (installed version == sources.yml version, by name, before a call leaves the box)
  · the server's own licence evidence (the Apache-2.0 LICENSE file on disk + the npm integrity
    recorded in package-lock.json — attribution recorded, not assumed)
  · the tool-call budget (config tool_calls/wall_s — a runaway loop refuses, it never wanders)
"""
from __future__ import annotations

import json
import os
import subprocess
import time

from ..contract import BudgetExceeded


class MCPError(RuntimeError):
    """The server answered with an error, or did not answer in time. Carried to the run record;
    never retried here — retrying is the server's job (it self-throttles)."""
    code = "mcp"


class MCPClient:
    """One server process per run; `call(tool, args)` returns the tool result's text."""

    def __init__(self, mcp_cfg: dict, budget: dict, base_dir: str):
        self.package = mcp_cfg["package"]
        self.version_pin = str(mcp_cfg["version"])
        install = os.path.normpath(os.path.join(base_dir, mcp_cfg["install"]))
        self.entry = os.path.join(install, mcp_cfg["server_entry"])
        self.timeout_s = float(mcp_cfg["call_timeout_s"])
        self._calls = 0
        self._budget = dict(budget or {})
        self._t0 = time.time()
        self.proc = None
        self.server_info = {}
        self.provenance = {}

        # -- the pin check: what is on disk must be what sources.yml pins, BY NAME ------------
        pkg_json = os.path.join(install, "node_modules", self.package, "package.json")
        _need_file(pkg_json, f"MCP install incomplete: {pkg_json} missing — "
                             f"run npm install in {install} (cache inside the farm)")
        installed = json.load(open(pkg_json, encoding="utf-8"))["version"]
        if installed != self.version_pin:
            raise MCPError(f"MCP pin broken: sources.yml pins {self.package}@{self.version_pin} but "
                           f"{install} has {installed} — refusing to run against an unpinned server "
                           f"(the tool output format this arm parses is version-specific)")
        # -- the server's licence evidence, from disk, quoted not assumed ------------------------
        lic_path = os.path.join(install, "node_modules", self.package, "LICENSE")
        _need_file(lic_path, f"MCP install incomplete: {lic_path} missing")
        first_line = open(lic_path, encoding="utf-8").readline().strip()
        integrity = ""
        lock_path = os.path.join(install, "package-lock.json")
        if os.path.isfile(lock_path):
            lock = json.load(open(lock_path, encoding="utf-8"))
            for name, dep in (lock.get("packages") or {}).items():
                if name.endswith(self.package):
                    integrity = dep.get("integrity", "")
                    break
        self.provenance = dict(package=self.package, version=installed, pin=self.version_pin,
                               install=os.path.relpath(install, base_dir),
                               entry=os.path.relpath(self.entry, base_dir),
                               licence_file=os.path.relpath(lic_path, base_dir),
                               licence_first_line=first_line,
                               npm_integrity=integrity)
        if "Apache License" not in first_line:
            raise MCPError(f"MCP server licence changed on disk: {lic_path} line 1 is {first_line!r} "
                           f"— expected Apache-2.0; refusing to wrap a server under terms we did not read")

        # -- spawn + initialize ---------------------------------------------------------------
        if not os.path.isfile(self.entry):
            raise MCPError(f"MCP server entry missing: {self.entry}")
        self.proc = subprocess.Popen(["node", self.entry], cwd=install,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
        hello = self._request(1, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "symbiont-literature-arm", "version": "1.0.0"}})
        self.server_info = dict(hello.get("serverInfo") or {})
        self._notify("notifications/initialized")
        self.provenance["server_name"] = self.server_info.get("name", "?")
        self.provenance["server_version"] = self.server_info.get("version", "?")

    # -- the wire ------------------------------------------------------------------------------
    def _charge(self) -> None:
        self._calls += 1
        cap = self._budget.get("tool_calls")
        if cap is not None and self._calls > cap:
            raise BudgetExceeded(f"literature tool-call budget spent: {self._calls - 1} calls, "
                                 f"cap {cap} (sources.yml budget.tool_calls)")
        wall = self._budget.get("wall_s")
        if wall is not None and time.time() - self._t0 > wall:
            raise BudgetExceeded(f"literature wall budget spent: {time.time() - self._t0:.0f}s > "
                                 f"{wall}s (sources.yml budget.wall_s)")

    def _request(self, msg_id: int, method: str, params: dict) -> dict:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method,
                                          "params": params}) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + self.timeout_s
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise MCPError(f"MCP {method}: no answer in {self.timeout_s:.0f}s (id={msg_id})")
            line = self.proc.stdout.readline()
            if not line:
                raise MCPError(f"MCP server died while answering id={msg_id}")
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("id") != msg_id:
                continue          # a notification or someone else's reply: not ours
            if "error" in msg and msg["error"] is not None:
                e = msg["error"]
                raise MCPError(f"MCP {method} failed: [{e.get('code')}] {e.get('message')}")
            return msg.get("result") or {}

    def _notify(self, method: str) -> None:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def call(self, tool: str, args: dict) -> str:
        """One tool call -> the result's text content, joined. NO retry: an error is the answer."""
        self._charge()
        result = self._request(2 + self._calls, "tools/call", {"name": tool, "arguments": args})
        if result.get("isError"):
            raise MCPError(f"MCP tool {tool} returned isError with args {json.dumps(args)[:200]}")
        return "\n".join(c.get("text", "") for c in result.get("content", [])
                         if isinstance(c, dict) and c.get("type") == "text")

    def close(self) -> None:
        if self.proc is not None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
            self.proc = None

    @property
    def calls(self) -> int:
        return self._calls


def _need_file(path: str, msg: str) -> None:
    if not os.path.isfile(path):
        raise MCPError(msg)
