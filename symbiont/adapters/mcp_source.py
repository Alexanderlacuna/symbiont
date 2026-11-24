"""ADAPTER `mcp_source` — ANY external MCP server as a SOURCE, configured from its sources.yml entry.

WHY THIS FILE (req-P1-A-GENERIC-MCP-ADAPTER-any-external-M, measured 2026-11-11): until today the
only MCP code in the farm was symbiont/literature/mcp.py, a client PINNED to
@cyanheads/pubmed-mcp-server 2.12.0 because the pin IS the contract — it parses that ONE server's
markdown tool output with regexes. So "an adapter may speak MCP" was true of the four-call
contract and untrue of the CODE for any second server. This file is the second server: nothing in
it names a source, an endpoint, a tool, a field or a shape. Every one of those comes from the
entry, and an entry that cannot say them is REFUSED BY NAME before a call leaves the box.

THE FOUR CALLS ARE THE GENE ARM'S, AND SO ARE THE DATACLASSES (symbiont/contract.py:
Discovery · FetchPlan · Normalised · Verify) — one vocabulary for "a source", whichever transport
carries it. What is NOT the gene arm's is the JOIN: this adapter is not an id_authority and not an
xref_provider, so it declares `OTHER_ARM` and contract.load_adapters skips it BY NAME instead of
filing it under a role it does not have (a false role is the kind of policy text this farm keeps
out of code). It is driven by its own runner here, and by nothing in loop.py — the loop is
untouched, which the row's grep proves.

    discover(_, since)  -> Discovery   connect + handshake + PIN CHECK + the count rule's answer
    fetch(_, since)     -> FetchPlan   ONE tool call, the raw JSON kept content-addressed
    normalise(resources)-> Normalised  the RESPONSE MAPPING: pointers -> canonical records
    verify(d, n)        -> Verify      ours vs the source's OWN count, quoted

────────────────────────────────────────────────────────────────────────────────────────────
THE SOURCES.YML ENTRY (schema is TOTAL — an unknown key is refused by name, so config and code can
never disagree about what exists). Written out once, here, because this docstring is the one
definition site an operator reads:

  sources:
    - id: fixture_alpha              # the key of state/, raw/, records/ and the run record
      adapter: mcp_source            # required, and must be exactly this adapter's name
      enabled: true
      note: "why this source exists"
      mode: source                   # source | tool  (see THE COUNT RULE below)
      transport:
        kind: stdio                  # stdio | http
        command: python3             # stdio: the child process and its args ({FARM} = farm root)...
        args: ["{FARM}/symbiont/fixtures/mcp/server_alpha.py"]
        call_timeout_s: 30
        # kind: http                 # http: the streamable-HTTP endpoint instead
        # url: "http://127.0.0.1:8901/mcp"
      request:
        tool: alpha_search           # the MCP tool name (checked against tools/list)
        kind: query                  # question | query | id  (how the term is validated+recorded)
        arguments:                   # the tool's OWN argument names; {TERM} is the run's term
          q: "{TERM}"
          max: 25
      response:                      # the RESPONSE MAPPING: JSON pointers over the tool's output
        records: /result/hits        # where the records live
        fields:                      # canonical field -> pointer (a pointer that stops resolving
          id: /acc                   # is SCHEMA DRIFT, never a silently empty string)
          key: /symbol
          title: /title
        xrefs:                       # optional: canonical xref namespace -> pointer
          alpha_cross: /cross
        coords:                      # optional: chrom/start/end pointers
          chrom: /coords/chr
          start: /coords/start
          end: /coords/end
        optional: [title]            # fields allowed to be ABSENT (recorded per row as `missing`)
        total: /result/total         # optional: the source's own total for the query (quoted, not
                                     # verified: a page's count is what we can check)
      count:                         # THE COUNT RULE — a source must state its own count
        kind: response_field         # response_field | count_tool | none
        pointer: /result/total
        # kind: count_tool
        # tool: alpha_count
        # arguments: {q: "{TERM}"}
        # pointer: /counts/documents
      pin:                           # "THE PIN IS THE CONTRACT", made mechanical
        package: fixture-alpha-mcp   # the server's own package/id, recorded in the run record
        version: "1.0.0"             # the serverInfo.version the handshake MUST report
        tool_description_sha256: ""  # optional; when set the description hash MUST match, when
                                     # empty it is RECORDED on the first run (pin_state recorded)
        tools_sha256: ""             # optional; the WHOLE tools/list hash (--print-pin prints
                                     # it). When set it is the admission-time DRIFT SIGNAL — a
                                     # changed tool list is REFUSED[schema-drift] and the trial
                                     # EXPIRED; when empty it is recorded by the first run's trial
                                     # and compared on every run after it.
      licence_text: "fixture data: no rights claimed"   # what the terms ARE (required)
      licence_url: ""                                   # the page they are stated on (citation)
      budget: {tool_calls: 8, wall_s: 60}               # over it the run REFUSES with the count

────────────────────────────────────────────────────────────────────────────────────────────
THE COUNT RULE ENFORCED (the row's deliverable 2): a server that cannot state how many records it
has may be used as a TOOL but is REFUSED as a SOURCE, by name, before a request leaves the box —
REFUSED[no-count-endpoint]. It is the same rule every other source obeys, so "it is MCP" buys no
exemption: `mode: source` + `count.kind: none` refuses at configure(); `mode: tool` runs, and every
record it produces is stamped `count_source: none` and `published: false`, and verify() says in its
own words that this was a TOOL call and not a claim.

PINNING (deliverable 3): the handshake's serverInfo.version is checked against pin.version, and the
tool's description hash against pin.tool_description_sha256 when the entry states one. Both are
RECORDED in the run record on every run, so a silent shape change is REFUSED[schema-drift] with the
transports, the pointers and both hashes quoted — instead of quietly reshaping records.

THE TOOL-DESCRIPTION HASH, one definition (an operator can recompute it with --print-pin):
    sha256( name + "\\x00" + description + "\\x00" + json.dumps(inputSchema, sort_keys=True) )
over the tools/list entry for the configured tool. Absent description/inputSchema count as "" and
{} — never as a wildcard.

SANDBOX (deliverable 4): the allowlist is generated FROM THE ENTRY and, for the trial run, enforced
in-process by the station's guard (symbiont/sandbox.py :: network_guard, the informative first line
the sandbox docstring already describes). For `kind: http` the allowlist is that URL's host and
nothing else; for `kind: stdio` it is EMPTY — the server is a child process and this adapter opens
no socket of its own — so any connect attempt inside a stdio run is refused and named as a
`sandbox-breach`. The licence page is NOT fetched here: this adapter records what the entry STATES
(licence_text) and cites licence_url; reading a licence page under terms we could not read is the
gene arm's T6, and this arm refuses an entry whose licence_text is empty rather than pretend it read
one.

THE ROUTINE RUN IS A CONFINED RUN (req-P1-ROUTINE-MCP-RUNS-STAY-CONFINED-AND-T, deliverable 1):
admission confined the TRIAL, and after admission every harvest ran IN THE STATION'S OWN PROCESS —
no write confinement, and the python network guard as the only standing control (which is a NAME,
not a boundary: selftest_sandbox F5a is a raw ctypes connect() that SUCCEEDS with the guard armed).
So `run_source()` no longer does the work itself: it re-executes THIS module through
`symbiont/sandbox.py :: run()` — the same confinement the admission trial uses (read-only root, only
the run's own store + /tmp writable) — and the child, which knows which tier confined it
(`SYMBIONT_SANDBOX_TIER`), does the work and writes the run record. For a `stdio` entry the child
also runs inside the KERNEL network rule (TIER-DEFAULT `deny-private`: loopback, RFC1918 and
link-local denied), so a raw connect() by the server process is refused by nftables and named in the
run record — the F5b fixture, on the routine path. For an `http` entry the kernel rule is NOT
installed (the run must reach its own host and a namespace in this deployment has no egress) and the
record SAYS SO instead of implying a control it does not have. There is no unconfined path: if no
confinement tier can confine the box the run is REFUSED[unconfined] BY NAME with every tier's
reason, never run anyway.

THE DRIFT SIGNAL (req-P1-THE-DRIFT-SIGNAL-EXPIRE-THE-TRIAL): an admitted hook must not silently
become a different program. Three parts, one definition site each:

  (1) AT ADMISSION the sha256 of the server's WHOLE `tools/list` (`tools_list_sha256`, one
      definition below) and the version the handshake declared are STORED beside the admission
      record — the `admitted` block of `state/<id>.trial.json` (the state file beside the roster,
      exactly where `state/<id>.json` keeps `since`). Written once, when the trial is armed, and
      carried forward untouched by every later run.
  (2) ON EVERY ROUTINE RUN both are re-read and compared. If EITHER moved the run is
      REFUSED[schema-drift] naming BOTH hashes, and the trial is marked state=expired — which the
      RUNNER obeys as a fact, not a word: the next run SKIPS that source BY NAME with the expiry
      reason, before any child process exists, so an expired source cannot harvest again until it is
      re-trialled. The way back is an operator's deliberate act: `--retrial` (the confined re-read
      of tools/list that arms the trial on what the server now offers, zero tool calls), or a new
      `pin.tools_sha256` in the entry that names exactly what the expired trial saw.
  (3) THE RUN RECORD carries `tools_hash` and `version` (plus the declared pin), so a run months
      later can be told apart from the run the admission was made on, without re-reading the
      server.

The code covers the SHAPE, never the INTENT: a server that keeps this shape and lies is caught by
the count check and the licence gate, not by a hash.

REFUSALS AND EXIT CODES (a refusal is a legal answer with a reason file; silent acceptance is the
only forbidden outcome — DESIGN-STATION §6 keeps 7 for schema-drift):
    0  records produced and verified            1  ours != the source's own count (count-mismatch)
    2  REFUSED before/without a run: config | no-count-endpoint | budget | licence-unknown |
       transport | tool-error | bad-input       7  REFUSED[schema-drift]

CLI (one definition per operation; the self-test and an operator run the same words):
    python3 -m symbiont.adapters.mcp_source --config F --store S --term T [--source ID[,...]]
    python3 -m symbiont.adapters.mcp_source --config F --store S --term T --source ID --retrial
    python3 -m symbiont.adapters.mcp_source --config F [--source ID] --print-pin
    python3 -m symbiont.adapters.mcp_source --config F --source ID --print-policy
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time

from .. import contract, sandbox
from ..contract import ConfigError, Discovery, FetchPlan, Normalised, Refusal, Verify

# The declaration contract.load_adapters honours: this module IS a plug-in (it answers the four
# calls), but of ANOTHER arm, so it may not be filed under a gene-arm role. Stated as a NAME so an
# undeclared non-plug-in still refuses there, exactly as before.
OTHER_ARM = "mcp_source"

NAME = "mcp_source"
FARM = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_CONFIG = os.path.join(FARM, "symbiont", "fixtures", "mcp", "sources.yml")

ENTRY_KEYS = ("id", "adapter", "enabled", "note", "mode", "transport", "request", "response",
              "count", "pin", "licence_text", "licence_url", "budget")
ENTRY_REQUIRED = ("id", "adapter", "enabled", "mode", "transport", "request", "response", "count",
                  "pin", "licence_text", "budget")
TRANSPORT_KEYS = ("kind", "command", "args", "url", "call_timeout_s")
REQUEST_KEYS = ("tool", "kind", "arguments")
RESPONSE_KEYS = ("records", "fields", "xrefs", "coords", "optional", "total")
COUNT_KEYS = ("kind", "pointer", "tool", "arguments")
PIN_KEYS = ("package", "version", "tool_description_sha256", "tools_sha256")
BUDGET_KEYS = ("tool_calls", "wall_s")
MODES = ("source", "tool")
TRANSPORTS = ("stdio", "http")
REQUEST_KINDS = ("question", "query", "id")
COUNT_KINDS = ("response_field", "count_tool", "none")
COORD_KEYS = ("chrom", "start", "end")
TERM = "{TERM}"
ID_TERM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]*$")
CANONICAL_MIN = ("key", "id")     # a canonical record must carry both, or the row is a reject


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE REFUSALS — one definition site, each with the code that lands verbatim in the run record and
# on stdout ("REFUSED[schema-drift] ..."), so "the source was refused" and "the run broke" can
# never be mistaken for each other.
# ─────────────────────────────────────────────────────────────────────────────────────────────
class NoCountEndpoint(Refusal):
    """The server cannot state how many records it has: usable as a TOOL, never as a SOURCE."""
    code = "no-count-endpoint"


class SchemaDrift(Refusal):
    """The pin or the output shape moved under a config that declared it. Never silently reshaped."""
    code = "schema-drift"


class ToolError(Refusal):
    """The server answered a tool call with an error. That answer IS the answer: never retried."""
    code = "tool-error"


class TransportError(Refusal):
    """The transport could not carry the call (no child, no answer in time, an HTTP refusal)."""
    code = "transport"


class _RetrialDone(Exception):
    """Internal control flow, not a refusal: the re-trial gesture (handshake + tools/list, zero
    tool calls) finished, so there is nothing left to harvest in this run."""


# ─────────────────────────────────────────────────────────────────────────────────────────────
# JSON POINTERS (RFC 6901) — the RESPONSE MAPPING's whole vocabulary.
# ─────────────────────────────────────────────────────────────────────────────────────────────
_MISSING = object()


def pointer_get(doc, pointer: str):
    """Resolve one JSON pointer. Returns _MISSING when it does not resolve — which is a SHAPE
    FACT, never an empty value: that distinction is the whole anti-silent-reshape rule."""
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ConfigError(f"response pointer {pointer!r} must be a JSON pointer starting with '/'")
    cur = doc
    for tok in pointer.split("/")[1:]:
        tok = tok.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict):
            if tok not in cur:
                return _MISSING
            cur = cur[tok]
        elif isinstance(cur, list):
            if not tok.isdigit() or int(tok) >= len(cur):
                return _MISSING
            cur = cur[int(tok)]
        else:
            return _MISSING
    return cur


def _scalar(v):
    return isinstance(v, (str, int, float, bool)) and not isinstance(v, bytes)


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE CONFIG — parsed and validated HERE, before a socket or a child process exists.
# ─────────────────────────────────────────────────────────────────────────────────────────────
def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def load_sources(path: str) -> dict:
    """Parse + validate an MCP arm config -> {id: cfg}. Total over its schema: an unknown key, a
    missing key, an unknown transport, an unknown count kind, an empty licence_text is REFUSED BY
    NAME. A config with NO entries is refused too — an empty roster is not a claim that no MCP
    server exists, and a run over it would publish nothing while looking like it ran."""
    import yaml
    _need(isinstance(path, str) and path, "an MCP arm config path is required (--config)")
    _need(os.path.isfile(path), f"MCP arm config missing: {path}")
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    _need(isinstance(raw, dict), f"{path}: top level must be a mapping with `version` and `sources`")
    _need(raw.get("version") == 1, f"{path}: unsupported version {raw.get('version')!r} (this code reads 1)")
    srcs = raw.get("sources")
    _need(isinstance(srcs, list) and srcs, f"{path}: `sources` must be a NON-EMPTY list — an empty "
                                           f"roster is not a claim that no MCP server exists")
    out: dict[str, dict] = {}
    for i, cfg in enumerate(srcs):
        where = f"{path}: sources[{i}]"
        _need(isinstance(cfg, dict), f"{where} is not a mapping")
        sid = cfg.get("id")
        _need(isinstance(sid, str) and sid, f"{where} has no `id`")
        where = f"{path}: source {sid!r}"
        unknown = [k for k in cfg if k not in ENTRY_KEYS]
        _need(not unknown, f"{where} carries unknown key(s) {sorted(unknown)} — the schema is {list(ENTRY_KEYS)}")
        missing = [k for k in ENTRY_REQUIRED if k not in cfg]
        _need(not missing, f"{where} is missing required key(s) {missing}")
        _need(sid not in out, f"{where} is defined twice")
        _need(cfg["adapter"] == NAME, f"{where}: `adapter` must be {NAME!r}, got {cfg['adapter']!r} — "
                                      f"one adapter, one name")
        _need(isinstance(cfg["enabled"], bool), f"{where}: `enabled` must be true or false")
        _need(cfg["mode"] in MODES, f"{where}: `mode` must be one of {list(MODES)}, got {cfg['mode']!r}")
        _need(isinstance(cfg["licence_text"], str) and cfg["licence_text"].strip(),
              f"{where}: `licence_text` must say what the terms ARE — this adapter will not run "
              f"over a source it cannot name the terms of (REFUSED[licence-unknown] at load)")

        t = cfg["transport"]
        _need(isinstance(t, dict), f"{where}: `transport` must be a mapping")
        bad = [k for k in t if k not in TRANSPORT_KEYS]
        _need(not bad, f"{where}: transport carries unknown key(s) {bad} — the schema is {list(TRANSPORT_KEYS)}")
        _need(t.get("kind") in TRANSPORTS, f"{where}: transport.kind must be one of {list(TRANSPORTS)}")
        _need(isinstance(t.get("call_timeout_s", 30), (int, float)) and t.get("call_timeout_s", 30) > 0,
              f"{where}: transport.call_timeout_s must be a positive number")
        if t["kind"] == "stdio":
            _need(isinstance(t.get("command"), str) and t["command"],
                  f"{where}: transport.kind stdio needs `command`")
            _need(isinstance(t.get("args"), list) and all(isinstance(a, str) for a in t["args"]),
                  f"{where}: transport.kind stdio needs `args` (a list of strings)")
            _need("url" not in t, f"{where}: transport.kind stdio must not carry `url`")
        else:
            _need(isinstance(t.get("url"), str) and t["url"].startswith(("http://", "https://")),
                  f"{where}: transport.kind http needs an http(s) `url`")
            _need("command" not in t and "args" not in t,
                  f"{where}: transport.kind http must not carry `command`/`args`")

        rq = cfg["request"]
        _need(isinstance(rq, dict), f"{where}: `request` must be a mapping")
        bad = [k for k in rq if k not in REQUEST_KEYS]
        _need(not bad, f"{where}: request carries unknown key(s) {bad} — the schema is {list(REQUEST_KEYS)}")
        for k in REQUEST_KEYS:
            _need(k in rq, f"{where}: request is missing {k!r}")
        _need(isinstance(rq["tool"], str) and rq["tool"], f"{where}: request.tool must name the MCP tool")
        _need(rq["kind"] in REQUEST_KINDS, f"{where}: request.kind must be one of {list(REQUEST_KINDS)}")
        _need(isinstance(rq["arguments"], dict), f"{where}: request.arguments must be a mapping of the "
                                                 f"tool's own argument names")

        rs = cfg["response"]
        _need(isinstance(rs, dict), f"{where}: `response` must be a mapping")
        bad = [k for k in rs if k not in RESPONSE_KEYS]
        _need(not bad, f"{where}: response carries unknown key(s) {bad} — the schema is {list(RESPONSE_KEYS)}")
        _need(isinstance(rs.get("records"), str), f"{where}: response.records must be a JSON pointer")
        pointer_get({}, rs["records"])          # validates the pointer's SHAPE (raises ConfigError)
        _need(isinstance(rs.get("fields"), dict) and rs["fields"],
              f"{where}: response.fields must map canonical fields to pointers (and may not be empty)")
        for name in CANONICAL_MIN:
            _need(name in rs["fields"], f"{where}: response.fields must map a canonical {name!r} — a "
                                        f"record with no {name} cannot be joined, stored or counted")
        for name, ptr in rs["fields"].items():
            _need(isinstance(ptr, str), f"{where}: response.fields.{name} must be a JSON pointer")
            pointer_get({}, ptr)
        for name, ptr in (rs.get("xrefs") or {}).items():
            _need(isinstance(ptr, str), f"{where}: response.xrefs.{name} must be a JSON pointer")
            pointer_get({}, ptr)
        for name, ptr in (rs.get("coords") or {}).items():
            _need(name in COORD_KEYS, f"{where}: response.coords may only name {list(COORD_KEYS)}, got {name!r}")
            _need(isinstance(ptr, str), f"{where}: response.coords.{name} must be a JSON pointer")
            pointer_get({}, ptr)
        opt = rs.get("optional") or []
        _need(isinstance(opt, list) and all(isinstance(o, str) for o in opt),
              f"{where}: response.optional must be a list of field names")
        unknown_opt = [o for o in opt if o not in rs["fields"]]
        _need(not unknown_opt, f"{where}: response.optional names field(s) {unknown_opt} that "
                               f"response.fields does not map")
        if rs.get("total") is not None:
            _need(isinstance(rs["total"], str), f"{where}: response.total must be a JSON pointer")
            pointer_get({}, rs["total"])

        c = cfg["count"]
        _need(isinstance(c, dict), f"{where}: `count` must be a mapping")
        bad = [k for k in c if k not in COUNT_KEYS]
        _need(not bad, f"{where}: count carries unknown key(s) {bad} — the schema is {list(COUNT_KEYS)}")
        _need(c.get("kind") in COUNT_KINDS, f"{where}: count.kind must be one of {list(COUNT_KINDS)}")
        if c["kind"] == "response_field":
            _need(isinstance(c.get("pointer"), str), f"{where}: count.kind response_field needs `pointer`")
            pointer_get({}, c["pointer"])
        elif c["kind"] == "count_tool":
            _need(isinstance(c.get("tool"), str) and c["tool"], f"{where}: count.kind count_tool needs `tool`")
            _need(isinstance(c.get("pointer"), str), f"{where}: count.kind count_tool needs `pointer`")
            pointer_get({}, c["pointer"])
            _need(isinstance(c.get("arguments", {}), dict), f"{where}: count.arguments must be a mapping")
        else:
            # count.kind 'none' is a LEGAL declaration (most MCP servers state no count) whose USE as
            # a source is REFUSED BY NAME at configure() — a run-time refusal with a reason file,
            # like budget and licence-unknown, never a load-time poisoning of the whole roster:
            # one server's refusal may not silence another source that is fine.
            pass

        p = cfg["pin"]
        _need(isinstance(p, dict), f"{where}: `pin` must be a mapping (the pin IS the contract)")
        bad = [k for k in p if k not in PIN_KEYS]
        _need(not bad, f"{where}: pin carries unknown key(s) {bad} — the schema is {list(PIN_KEYS)}")
        _need(isinstance(p.get("package"), str) and p["package"], f"{where}: pin.package must name the server")
        _need(isinstance(p.get("version"), str) and p["version"],
              f"{where}: pin.version must be an EXACT version string — a range is refused: the shape "
              f"this entry maps is version-specific, so the pin IS the contract")
        _need("tool_description_sha256" in p, f"{where}: pin.tool_description_sha256 must be present "
                                              f"(empty string = record it on the first run)")
        _need(isinstance(p["tool_description_sha256"], str), f"{where}: pin.tool_description_sha256 must be a string")
        h = p["tool_description_sha256"]
        _need(not h or re.fullmatch(r"[0-9a-f]{64}", h),
              f"{where}: pin.tool_description_sha256 must be 64 lowercase hex chars or empty, got {h!r}")
        ht = p.get("tools_sha256", "")
        _need(isinstance(ht, str), f"{where}: pin.tools_sha256 must be a string (empty = record it)")
        _need(not ht or re.fullmatch(r"[0-9a-f]{64}", ht),
              f"{where}: pin.tools_sha256 must be 64 lowercase hex chars or empty, got {ht!r}")

        b = cfg["budget"]
        _need(isinstance(b, dict) and all(k in b for k in BUDGET_KEYS),
              f"{where}: budget must carry {list(BUDGET_KEYS)}")
        for k in BUDGET_KEYS:
            _need(isinstance(b[k], (int, float)) and b[k] > 0,
                  f"{where}: budget.{k} must be a positive number, got {b[k]!r}")
        out[sid] = cfg
    return out


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE TRANSPORTS — one client per run, stdio (a child process) or streamable HTTP.
# ─────────────────────────────────────────────────────────────────────────────────────────────
def _expand(text: str) -> str:
    """`{FARM}` in a stdio command/arg is the farm root, so a shipped fixture config runs from any
    working directory. Nothing else is substituted: an entry names a program, not a template."""
    return str(text).replace("{FARM}", FARM)


class _Client:
    """The wire, without a source in it: initialize -> tools/list -> tools/call."""

    def __init__(self, transport: dict):
        self.t = dict(transport)
        self.timeout_s = float(self.t.get("call_timeout_s", 30))
        self.server_info: dict = {}
        self.tools: list[dict] = []
        self._id = 0

    # -- the two wire methods, one per transport ------------------------------------------------
    def _rpc(self, method: str, params: dict) -> dict:
        raise NotImplementedError

    def _notify(self, method: str) -> None:
        raise NotImplementedError

    def open(self) -> dict:
        hello = self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "symbiont-mcp-source", "version": "1.0.0"}})
        self.server_info = dict(hello.get("serverInfo") or {})
        self._notify("notifications/initialized")
        self.tools = list((self._rpc("tools/list", {}) or {}).get("tools") or [])
        return self.server_info

    def call(self, tool: str, args: dict) -> dict:
        """One tool call -> {text, structured}. NO RETRY: an error is the answer."""
        res = self._rpc("tools/call", {"name": tool, "arguments": args})
        if res.get("isError"):
            raise ToolError(f"MCP tool {tool} returned isError with args {json.dumps(args)[:200]}")
        text = "\n".join(c.get("text", "") for c in res.get("content", [])
                         if isinstance(c, dict) and c.get("type") == "text")
        return dict(text=text, structured=res.get("structuredContent"))

    def close(self) -> None:
        pass


class _StdioClient(_Client):
    def __init__(self, transport: dict):
        super().__init__(transport)
        self.proc = None

    def _spawn(self) -> None:
        if self.proc is not None:
            return
        argv = [_expand(self.t["command"])] + [_expand(a) for a in (self.t.get("args") or [])]
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except OSError as e:
            raise TransportError(f"stdio transport: could not start {argv!r} ({type(e).__name__}: {e})") from e

    def _rpc(self, method: str, params: dict) -> dict:
        self._spawn()
        self._id += 1
        want = self._id
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": want, "method": method,
                                          "params": params}) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + self.timeout_s
        while True:
            if time.time() > deadline:
                raise TransportError(f"stdio transport: no answer to {method} in {self.timeout_s:.0f}s "
                                     f"(id={want})")
            line = self.proc.stdout.readline()
            if not line:
                raise TransportError(f"stdio transport: the server died while answering {method} (id={want})")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue                      # a stray log line on stdout: not a reply
            if msg.get("id") != want:
                continue                      # a notification or someone else's reply
            if msg.get("error") is not None:
                e = msg["error"]
                raise ToolError(f"MCP {method} failed: [{e.get('code')}] {e.get('message')}")
            return msg.get("result") or {}
        raise AssertionError("unreachable")

    def _notify(self, method: str) -> None:
        self._spawn()
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        if self.proc is not None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
            self.proc = None


class _HTTPClient(_Client):
    """Streamable HTTP: one JSON-RPC POST per call, `application/json` or `text/event-stream` back."""

    def __init__(self, transport: dict):
        super().__init__(transport)
        self.url = self.t["url"]
        self.session_id = ""

    def _post(self, payload: dict) -> dict | None:
        import urllib.error
        import urllib.request
        headers = {"content-type": "application/json",
                   "accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                sid = resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                ctype = (resp.headers.get("content-type") or "").lower()
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise TransportError(f"http transport: {self.url} answered HTTP {e.code} "
                                 f"({e.reason}) for {payload.get('method')}") from e
        except Exception as e:
            raise TransportError(f"http transport: {self.url} unreachable ({type(e).__name__}: {e})") from e
        if not body.strip():
            return None                        # a notification's 202
        if "text/event-stream" in ctype:
            out = None
            for line in body.splitlines():
                if line.startswith("data:"):
                    try:
                        out = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
            return out
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise TransportError(f"http transport: {self.url} answered non-JSON "
                                 f"({type(e).__name__}) for {payload.get('method')}") from e

    def _rpc(self, method: str, params: dict) -> dict:
        self._id += 1
        msg = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        if msg is None:
            raise TransportError(f"http transport: empty answer to {method} (id={self._id})")
        if msg.get("error") is not None:
            e = msg["error"]
            raise ToolError(f"MCP {method} failed: [{e.get('code')}] {e.get('message')}")
        return msg.get("result") or {}

    def _notify(self, method: str) -> None:
        self._post({"jsonrpc": "2.0", "method": method})


def client_for(transport: dict) -> _Client:
    kind = transport.get("kind")
    if kind == "stdio":
        return _StdioClient(transport)
    if kind == "http":
        return _HTTPClient(transport)
    raise ConfigError(f"unknown transport kind {kind!r} — transports: {list(TRANSPORTS)}")


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE TOOL-DESCRIPTION HASH — ONE definition, recomputable by an operator (--print-pin).
# ─────────────────────────────────────────────────────────────────────────────────────────────
def tool_description_sha256(tool_entry: dict) -> str:
    blob = "\x00".join([str(tool_entry.get("name", "")), str(tool_entry.get("description", "") or ""),
                        json.dumps(tool_entry.get("inputSchema") or {}, sort_keys=True)])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def tools_list_sha256(tools: list) -> str:
    """THE WHOLE `tools/list`, one hash — the DRIFT SIGNAL's subject (deliverable 2).

    A description hash covers ONE tool: a server that quietly ADDS a tool (the fixture in this
    row's gate gains exactly one) leaves every description untouched and would pass a per-tool
    pin. So the trial pins the whole list: the tools SORTED BY NAME, each reduced to its
    `tool_description_sha256` (one definition, above), the digests joined by "\x01" and hashed.
    Order-independent, additive-sensitive: one added, removed or reshaped tool changes the hash,
    and nothing else does. An operator gets the value from `--print-pin` (which prints both
    hashes) and pins it as `pin.tools_sha256`."""
    parts = []
    for t in sorted((t for t in (tools or []) if isinstance(t, dict)),
                    key=lambda t: str(t.get("name", ""))):
        parts.append(tool_description_sha256(t))
    return hashlib.sha256("\x01".join(parts).encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE TRIAL — what the source's sandbox trial pinned, kept in the run's OWN store so a routine
# harvest reads it without a network call and without the operator re-pinning anything.
# ─────────────────────────────────────────────────────────────────────────────────────────────
TRIAL_SCHEMA = "mcp-trial/1"
CONFINED_ENV = "SYMBIONT_SANDBOX_TIER"      # set by symbiont/sandbox.py :: run() for the child
NETWORK_ENV = "SYMBIONT_MCP_NETWORK"        # which network control the child runs under
NETWORK_WHY_ENV = "SYMBIONT_MCP_NETWORK_WHY"


def trial_path(store: str, sid: str) -> str:
    return os.path.join(store, "state", f"{sid}.trial.json")


def trial_read(store: str, sid: str) -> dict:
    """The trial as the last run left it — EMPTY when there is none (a source whose trial record
    is absent is un-pinned, not drifted: the first run RECORDS it, exactly as pin_state does)."""
    try:
        with open(trial_path(store, sid), encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def trial_write(store: str, sid: str, trial: dict) -> dict:
    """Write the trial record and return it (the run record quotes it). "Pinned" is what the next
    run is compared against: the tools/list hash and the version the trial saw; "admitted" is what
    the ADMISSION saw (deliverable 1), kept once and carried forward, never rolled by a later run."""
    rec = dict(trial)
    rec.update(schema=TRIAL_SCHEMA, source=sid, at=rec.get("at") or _utc())
    rec.setdefault("pinned", {})
    os.makedirs(os.path.join(store, "state"), exist_ok=True)
    p = trial_path(store, sid)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, p)
    return rec


def trial_state(trial: dict) -> str:
    """The trial's state as a NAME the runner can branch on (never a prose warning):
    "none" (no trial on disk yet) · "admitted" (armed) · "expired" (a moved tools/list or
    version was seen; the source must be re-trialled before it harvests again)."""
    if not trial:
        return "none"
    return str(trial.get("state") or "none")


def expiry_cleared_by_pin(cfg: dict, trial: dict) -> bool:
    """Is the expiry already answered by the ENTRY? True only when the operator has written a
    `pin.tools_sha256` (and a version) that name EXACTLY what the expired trial saw — i.e. the
    operator has looked at the new tools/list (`--print-pin`) and admitted it. Anything else
    leaves the source skipped: a stale pin, an empty pin and a pin for a THIRD tool list are all
    'not answered', and answering one by half is how an expiry becomes a word again.

    The other way back is `--retrial` (see MCPSourceAdapter.retrial), which re-reads the
    tools/list inside the sandbox and arms the trial on what it finds."""
    seen = dict(trial.get("seen") or {})
    declared_tools = str((cfg.get("pin") or {}).get("tools_sha256") or "")
    return bool(declared_tools) \
        and declared_tools == str(seen.get("tools_sha256") or "") \
        and str((cfg.get("pin") or {}).get("version") or "") == str(seen.get("version") or "")


def network_allowlist(cfg: dict) -> dict:
    """The sandbox allowlist, GENERATED FROM THE ENTRY (deliverable 4).

    http   the endpoint's host and nothing else — and the kernel tier is generated from the same
           host by the station's sandbox module (policy_for_entry), never widened here.
    stdio  EMPTY: the server is a child process and this adapter opens no socket of its own, so any
           connect attempt is a `sandbox-breach` and is refused by name.
    """
    t = cfg["transport"]
    if t["kind"] == "http":
        host = sandbox.host_of(t["url"])
        return dict(kind="http", hosts=[host], policy=sandbox.policy_for_entry(
            {"base_url": t["url"]}), note=f"the endpoint's host ({host}) and nothing else")
    return dict(kind="stdio", hosts=[], policy=None,
                note="no host at all: the server is a child process (command+args from the entry) "
                     "and this adapter opens no socket of its own")


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE ADAPTER — the four calls.
# ─────────────────────────────────────────────────────────────────────────────────────────────
class MCPSourceAdapter:
    """One configured MCP source. `configure(cfg)` validates nothing twice: load_sources did."""

    name = NAME
    role = "mcp_source"        # NOT a gene-arm role, and says so: see OTHER_ARM above

    def __init__(self):
        self.cfg: dict | None = None
        self.client: _Client | None = None
        self._calls = 0
        self._bytes = 0
        self._t0 = time.time()
        self.pin: dict = {}
        self.term = ""
        self._release = ""
        self._source_count = None
        self._count_url = ""
        self.sandbox_refusals: list = []
        self.notes: list[str] = []
        self.tools_sha = ""            # the tools/list hash this run SAW (the drift subject)
        self.seen_version = ""         # the serverInfo.version this run's handshake reported
        self.trial: dict = {}          # the trial this run is checked against (store/state)
        self.trial_after: dict | None = None   # what the next run is compared against

    # -- 0 configure ---------------------------------------------------------------------------
    def configure(self, cfg: dict) -> None:
        """THE COUNT RULE, ENFORCED HERE — the one definition site, before a socket or a child
        process exists: a server that cannot state how many records it has may be used as a TOOL
        (mode: tool) and is REFUSED as a SOURCE, BY NAME (REFUSED[no-count-endpoint])."""
        self.cfg = cfg
        if cfg["count"]["kind"] == "none" and cfg["mode"] == "source":
            raise NoCountEndpoint(
                f"count.kind is 'none' (this server states no count anywhere in "
                f"its protocol surface) and mode is 'source' — a server that cannot state how many "
                f"records it has may be used as a TOOL but is REFUSED as a SOURCE by name. Set "
                f"`mode: tool` for the tool use, or name the count endpoint (a count tool, or a count "
                f"field in the response)")
        self.notes.append(f"sandbox allowlist: {network_allowlist(cfg)['note']}")

    def set_term(self, term: str) -> None:
        """The run's term, admitted by request.kind (see term_for). Set BEFORE discover() so the
        COUNT TOOL is asked the same question the search tool is."""
        self.term = self.term_for(term)
        self.notes.append(f"request.kind={self.cfg['request']['kind']}; term sha256="
                          f"{hashlib.sha256(self.term.encode()).hexdigest()}")
        if self.cfg["request"]["kind"] == "query":
            self.notes.append(f"query: {self.term}")

    # -- the TRIAL (the drift signal's memory) -------------------------------------------------
    def set_trial(self, trial: dict | None) -> None:
        """The trial this run is checked against — read from the run's own store by the caller, so
        the adapter needs no filesystem of its own to answer "has this server's contract moved"."""
        self.trial = dict(trial or {})

    def _expire_trial(self, why: str, seen_tools: str, seen_version: str,
                      pinned: dict | None = None) -> dict:
        """Mark the trial EXPIRED (the row's own word) and keep BOTH sides of the change, so the
        next run, the operator and the report all read the same two hashes. `pinned` is the
        expectation that was violated when it is not the trial's own (an entry that DECLARES its
        pin at admission has no trial record yet and must still say what it was pinned TO)."""
        pinned = dict(pinned if pinned is not None else (self.trial.get("pinned") or {}))
        self.trial_after = dict(
            state="expired", pinned=pinned, expired_at=_utc(), why=why,
            # the ADMISSION record is not rolled by a drift: what was admitted stays quoted beside
            # the expiry, so the operator sees both the baseline and what moved off it.
            admitted=(self.trial.get("admitted") or None),
            seen=dict(tools_sha256=seen_tools, version=seen_version,
                      tools=sorted(str(t.get("name", "?")) for t in (self.client.tools
                                                                      if self.client else []))))
        return self.trial_after

    def _check_trial(self, info: dict) -> None:
        """THE DRIFT SIGNAL — one definition site, before a tool call leaves the box.

        Three states, and each one is a decision, not a warning:
          expired      the trial was marked expired by an earlier run and NOTHING pins the server's
                       new tools/list -> REFUSED[schema-drift], because an expired trial must be
                       re-trialled, not resumed.
          moved        the tools/list hash (or the version) differs from what the trial pinned
                       -> REFUSED[schema-drift] naming BOTH hashes, and the trial is EXPIRED.
          unchanged    nothing to compare yet (first run) or both match -> the trial is ARMED with
                       what this run saw, so the NEXT run has something to compare against.
        """
        declared = str(self.cfg["pin"].get("tools_sha256") or "")
        pinned = dict(self.trial.get("pinned") or {})
        want_tools = declared or str(pinned.get("tools_sha256") or "")
        want_version = str(pinned.get("version") or "") or self.cfg["pin"]["version"]
        seen_version = str(info.get("version", ""))
        seen_tools = self.tools_sha
        names = sorted(str(t.get("name", "?")) for t in (self.client.tools if self.client else []))
        if self.trial.get("state") == "expired" and not declared:
            raise SchemaDrift(
                f"the sandbox trial is EXPIRED (at {self.trial.get('expired_at')}: "
                f"{self.trial.get('why')}) and nothing pins the server's new tools/list — re-trial "
                f"it and pin the result (`python3 -m symbiont.adapters.mcp_source --print-pin "
                f"--config <this entry's config> --source {self.cfg['id']}`) before the next "
                f"harvest (REFUSED[schema-drift])")
        moved = []
        if want_tools and want_tools != seen_tools:
            moved.append(f"tools/list {want_tools} -> {seen_tools}")
        if want_version and seen_version and want_version != seen_version:
            moved.append(f"declared version {want_version} -> serverInfo.version {seen_version}")
        if moved:
            self._expire_trial("; ".join(moved), seen_tools, seen_version,
                               pinned=dict(version=want_version, tools_sha256=want_tools))
            raise SchemaDrift(
                f"SCHEMA DRIFT — {'; '.join(moved)}; the sandbox trial is now EXPIRED, so "
                f"this source must be re-trialled before the next harvest. The pinned tools/list "
                f"{want_tools or '(nothing pinned yet)'} vs the one this run read {seen_tools} "
                f"({len(names)} tool(s): {', '.join(names)}) (REFUSED[schema-drift])")
        self.trial_after = dict(
            state="admitted", pinned=dict(version=seen_version or want_version,
                                           tools_sha256=seen_tools),
            # (1) THE ADMISSION RECORD: the tools/list hash and the declared version as they were
            # when this source was admitted. Written ONCE (the first run that arms the trial) and
            # carried forward untouched, so "what did we admit" and "what does it pin now" can
            # never be the same sentence by accident.
            admitted=(self.trial.get("admitted")
                      or dict(at=_utc(), tools_sha256=seen_tools,
                              version=(seen_version or want_version), by="trial")),
            at=_utc(), trialled_at=(self.trial.get("trialled_at")
                                    or (self.trial.get("pinned") and self.trial.get("at")) or _utc()),
            why=("tools/list and the declared version are unchanged"
                 if (want_tools or want_version) else "first run: the trial recorded what it saw"),
            seen=dict(tools_sha256=seen_tools, version=seen_version, tools=names))

    # -- the RE-TRIAL (the only way back from an expiry, on the operator's word) ---------------
    def retrial_open(self) -> dict:
        """Connect, handshake, read tools/list — and NOTHING else. The re-trial gesture: it is
        run inside the same confinement as a harvest (the caller is the confined child), it makes
        ZERO tool calls, and it exists so the way back from an expiry is a decision with a record
        rather than an edit that a later run cannot tell from the drift itself."""
        self.client = client_for(self.cfg["transport"])
        try:
            info = self.client.open()
        except Refusal:
            raise
        except Exception as e:
            raise TransportError(f"handshake failed ({type(e).__name__}: {e})") from e
        self.tools_sha = tools_list_sha256(self.client.tools)
        self.seen_version = str(info.get("version", ""))
        self.retrial(info)
        return info

    def retrial(self, info: dict) -> dict:
        """Arm the trial on what the server offers NOW, quoting what it used to be — but only for
        a TOOL-LIST move. The declared VERSION is an admission fact (`pin.version` in the entry),
        so a version move is NOT adoptable here: it is REFUSED[schema-drift] naming both versions
        and the entry must be edited, because 'the server says it is 2.0.0 now' is exactly the
        claim an operator must sign, not something a re-trial may grant itself."""
        sid = self.cfg["id"]
        seen_tools = self.tools_sha
        seen_version = self.seen_version or str(info.get("version", ""))
        declared_tools = str(self.cfg["pin"].get("tools_sha256") or "")
        want_version = str(self.cfg["pin"]["version"])
        names = sorted(str(t.get("name", "?")) for t in (self.client.tools if self.client else []))
        if want_version != seen_version:
            self._expire_trial(f"declared version {want_version} -> serverInfo.version {seen_version}",
                               seen_tools, seen_version,
                               pinned=dict(version=want_version, tools_sha256=declared_tools))
            raise SchemaDrift(
                f"the version pin did not move with the server: sources.yml pins {want_version} but "
                f"the handshake reports {seen_version} — a VERSION change is an admission edit "
                f"(update pin.version in the entry), not something a re-trial may adopt. "
                f"tools/list {seen_tools} ({len(names)} tool(s): {', '.join(names)}) "
                f"(REFUSED[schema-drift])")
        if declared_tools and declared_tools != seen_tools:
            self._expire_trial(f"the entry declares tools/list {declared_tools} but the server now "
                               f"reads {seen_tools}", seen_tools, seen_version,
                               pinned=dict(version=want_version, tools_sha256=declared_tools))
            raise SchemaDrift(
                f"the entry declares tools/list {declared_tools} but the server reads {seen_tools} "
                f"now — re-pin what you are admitting (`--print-pin`) before the re-trial, or the "
                f"re-trial would adopt a list nobody has looked at (REFUSED[schema-drift])")
        old = dict(self.trial.get("pinned") or {})
        self.trial_after = dict(
            state="admitted",
            pinned=dict(version=seen_version, tools_sha256=seen_tools),
            admitted=dict(at=_utc(), tools_sha256=seen_tools, version=seen_version, by="re-trial"),
            previous=dict(state=trial_state(self.trial),
                          expired_at=str(self.trial.get("expired_at") or ""),
                          why=str(self.trial.get("why") or ""),
                          pinned=old, seen=dict(self.trial.get("seen") or {})),
            at=_utc(), trialled_at=_utc(),
            why=(f"re-trialled through the sandbox: tools/list {old.get('tools_sha256', '') or '(none)'} "
                 f"-> {seen_tools}, version {seen_version} (the operator's --retrial)"),
            seen=dict(tools_sha256=seen_tools, version=seen_version, tools=names))
        return self.trial_after

    # -- the wire, charged ---------------------------------------------------------------------
    def _charge(self) -> None:
        self._calls += 1
        cap = (self.cfg["budget"] or {}).get("tool_calls")
        if cap is not None and self._calls > cap:
            raise contract.BudgetExceeded(f"{self.cfg['id']}: MCP tool-call budget spent "
                                          f"({self._calls - 1} calls, cap {cap})")
        wall = (self.cfg["budget"] or {}).get("wall_s")
        if wall is not None and time.time() - self._t0 > wall:
            raise contract.BudgetExceeded(f"{self.cfg['id']}: MCP wall budget spent "
                                          f"({time.time() - self._t0:.0f}s > {wall}s)")

    def _call(self, tool: str, args: dict) -> dict:
        self._charge()
        out = self.client.call(tool, args)
        out["doc"] = self._document(out)
        self._bytes += len(json.dumps(out))
        return out

    def _document(self, out: dict):
        """The tool's OUTPUT as a document the pointers are resolved against: the structured
        content when the server offers one (2025-06-18), else its text content parsed as JSON.
        Not JSON at all is a SHAPE FACT we refuse — never an empty record set."""
        if out.get("structured") is not None:
            return out["structured"]
        text = (out.get("text") or "").strip()
        if not text:
            raise SchemaDrift(f"{self.cfg['id']}: the tool returned neither structuredContent nor "
                              f"text content — nothing for the response mapping to point at")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise SchemaDrift(f"{self.cfg['id']}: the tool's text content is not JSON "
                              f"({e.msg} at char {e.pos}); this entry's response mapping is a JSON "
                              f"pointer map, so a non-JSON body is drift, not an empty page") from e

    def _args_for(self, template: dict, term: str) -> dict:
        """The ARGUMENT MAPPING: {TERM} substituted, literals passed through untouched."""
        out = {}
        for key, val in template.items():
            if isinstance(val, str) and TERM in val:
                out[key] = val.replace(TERM, term)
            else:
                out[key] = val
        return out

    def term_for(self, term: str) -> str:
        """How the run's term is admitted, by request.kind — `id` has teeth."""
        kind = self.cfg["request"]["kind"]
        _need(isinstance(term, str) and term.strip(), f"{self.cfg['id']}: the run term is empty "
                                                     f"(request.kind={kind}) — refusing to send it")
        if kind == "id" and not ID_TERM_RE.match(term.strip()):
            raise ConfigError(f"{self.cfg['id']}: request.kind is 'id' but the term {term!r} is not a "
                              f"single identifier — an id request never carries a query string")
        return term.strip()

    # -- 1 discover ----------------------------------------------------------------------------
    def discover(self, http, since: dict) -> Discovery:
        """Connect, handshake, PIN CHECK, then the count rule's answer. A source that cannot state
        its count never becomes a Discovery: mode:tool is the only way to call it at all."""
        sid = self.cfg["id"]
        self.client = client_for(self.cfg["transport"])
        try:
            info = self.client.open()
        except Refusal:
            raise
        except Exception as e:
            raise TransportError(f"handshake failed ({type(e).__name__}: {e})") from e
        # ── THE DRIFT SIGNAL, before ANY tool call (deliverable 2): the whole tools/list hash and
        # the declared version are what the trial pinned. A moved one refuses BY NAME (both hashes
        # quoted) and EXPIRES the trial, so the next harvest cannot start without a re-trial.
        self.tools_sha = tools_list_sha256(self.client.tools)
        self.seen_version = str(info.get("version", ""))
        self._check_trial(info)
        tool = self.cfg["request"]["tool"]
        entry = next((t for t in self.client.tools if t.get("name") == tool), None)
        if entry is None:
            self._expire_trial(f"the tool {tool!r} this entry names is no longer offered",
                               self.tools_sha, str(info.get("version", "")))
            raise SchemaDrift(f"the server does not offer the tool {tool!r} this entry names — "
                              f"tools/list carries {sorted(t.get('name', '?') for t in self.client.tools)} "
                              f"(REFUSED[schema-drift]: the pin IS the contract)")
        desc_sha = tool_description_sha256(entry)
        pin = dict(package=self.cfg["pin"]["package"], version=self.cfg["pin"]["version"],
                   server_name=info.get("name", ""), server_version=info.get("version", ""),
                   tool=tool, tool_description_sha256=desc_sha,
                   tools_sha256=self.tools_sha,
                   declared_version=self.cfg["pin"]["version"])
        declared = self.cfg["pin"]["tool_description_sha256"]
        pin["pin_state"] = "checked" if declared else "recorded"
        if info.get("version") != pin["version"]:
            self._expire_trial(f"declared version {pin['version']} -> serverInfo.version "
                               f"{info.get('version')}", self.tools_sha,
                               str(info.get("version", "")))
            raise SchemaDrift(f"pin broken — sources.yml pins {pin['package']}@{pin['version']} "
                              f"but the handshake reports {info.get('name')}@{info.get('version')}; "
                              f"the shape this entry maps is version-specific (REFUSED[schema-drift])")
        if declared and declared != desc_sha:
            self._expire_trial(f"the declared tool description {declared} -> {desc_sha}",
                               self.tools_sha, str(info.get("version", "")))
            raise SchemaDrift(f"pin broken — sources.yml pins tool description sha256 {declared} "
                              f"but tools/list for {tool!r} hashes to {desc_sha}; the tool's contract "
                              f"moved (REFUSED[schema-drift])")
        self.pin = pin
        release = f"{pin['server_name'] or pin['package']}@{pin['server_version']}#{tool}@{desc_sha[:12]}"

        # THE COUNT RULE, at run time too (the load check already refused 'none' as a source; this
        # is the same rule for a count the server no longer states).
        count = self.cfg["count"]
        source_count, count_url, count_note = None, "", ""
        if count["kind"] == "response_field":
            count_note = f"count field in the search response at {count['pointer']}"
            count_url = f"mcp:{pin['server_name']}@{pin['server_version']}#{count['pointer']}"
        elif count["kind"] == "count_tool":
            args = self._args_for(count.get("arguments") or {}, self.term)
            doc = self._call(count["tool"], args)["doc"]
            got = pointer_get(doc, count["pointer"])
            if got is _MISSING or not isinstance(got, int) or isinstance(got, bool):
                raise SchemaDrift(f"the count tool {count['tool']!r} no longer states an "
                                  f"integer at {count['pointer']} — it answered "
                                  f"{json.dumps(doc)[:200]!r} (REFUSED[schema-drift])")
            source_count = got
            count_note = f"count tool {count['tool']!r} args {json.dumps(args)[:120]} at {count['pointer']}"
            count_url = f"mcp:{pin['server_name']}@{pin['server_version']}#{count['tool']}{count['pointer']}"
        else:
            count_note = ("NO COUNT — mode:tool: this server cannot state how many records it has, "
                          "so it is used as a TOOL and never as a SOURCE (REFUSED[no-count-endpoint] "
                          "is what a source-mode entry gets)")
        if count["kind"] == "response_field":
            # the count lives in the SEARCH response: read and checked in normalise()
            count_note += " — resolved on the search response in normalise()"
        self._release, self._source_count, self._count_url = release, source_count, count_url
        d = Discovery(source=sid, release=release,
                      resources=[dict(reskey="records", url=count_url or f"mcp:{release}",
                                      etag=desc_sha[:32], last_modified="", content_length=0,
                                      ext="json")],
                      published_count=source_count,
                      published_count_url=count_url,
                      licence=dict(name=self.cfg["licence_text"], url=self.cfg.get("licence_url") or "",
                                   quote=self.cfg["licence_text"], terms_sha256=""),
                      notes=[f"transport {self.cfg['transport']['kind']}; tool {tool!r}; "
                             f"pin {pin['package']}@{pin['version']} "
                             f"(server says {pin['server_name']}@{pin['server_version']}); "
                             f"tool_description_sha256 {desc_sha} ({pin['pin_state']})",
                             f"count rule: {count['kind']} — {count_note}"] + self.notes)
        return d

    # -- 2 fetch(since) ------------------------------------------------------------------------
    def fetch(self, http, since: dict) -> FetchPlan:
        """ONE tool call. The raw answer is kept content-addressed by the runner, so a later run
        over an unchanged release normalises bytes from disk and spends no call."""
        rq = self.cfg["request"]
        args = self._args_for(rq["arguments"], self.term)
        out = self._call(rq["tool"], args)
        payload = json.dumps(dict(args=args, tool=rq["tool"], document=out["doc"],
                                  channel="structuredContent" if out.get("structured") is not None
                                  else "text"), indent=1, sort_keys=True)
        return FetchPlan(new=True,
                         reason=f"one MCP tool call: {rq['tool']}({json.dumps(args)[:160]})",
                         resources={"records": {"bytes": payload.encode("utf-8"),
                                                "filename": "records.json"}})

    # -- 3 normalise ---------------------------------------------------------------------------
    def normalise(self, resources: dict) -> Normalised:
        """THE RESPONSE MAPPING. A pointer that stops resolving is DRIFT and refuses the run; a row
        that cannot be a record (no key, duplicate key, not an object) is a REJECT with a reason, so
        rows_in == records + rejects always holds and nothing vanishes quietly."""
        sid = self.cfg["id"]
        rs = self.cfg["response"]
        opt = set(rs.get("optional") or [])
        blob = resources.get("records")
        if blob is None:
            raise ConfigError(f"normalise got no `records` resource")
        doc = json.loads(blob.decode("utf-8"))["document"]
        release = self._release
        rows = pointer_get(doc, rs["records"])
        if rows is _MISSING:
            raise SchemaDrift(f"response.records {rs['records']} no longer resolves — the "
                              f"output shape moved (keys: {sorted(doc)[:12] if isinstance(doc, dict) else type(doc).__name__}). "
                              f"REFUSED[schema-drift]: a moved pointer is never read as an empty page")
        if not isinstance(rows, list):
            raise SchemaDrift(f"response.records {rs['records']} is "
                              f"{type(rows).__name__}, not a list of records — the output shape moved "
                              f"(REFUSED[schema-drift])")

        published_count, count_source = None, self.cfg["count"]["kind"]
        if count_source == "response_field":
            got = pointer_get(doc, self.cfg["count"]["pointer"])
            if got is _MISSING or not isinstance(got, int) or isinstance(got, bool):
                raise SchemaDrift(f"the count field {self.cfg['count']['pointer']} no longer "
                                  f"states an integer (got {got if got is not _MISSING else 'MISSING'!r}) "
                                  f"— REFUSED[schema-drift]")
            published_count = got
        elif count_source == "none":
            published_count = None

        records, rejects = [], []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                rejects.append(dict(row=json.dumps(row)[:400], reason="not_an_object",
                                    detail=f"{type(row).__name__}, expected an object",
                                    reskey="records"))
                continue
            rec = dict(source=sid, source_release=release, xrefs={}, coords={})
            missing: list[str] = []
            drift: list[str] = []
            for field_name, ptr in rs["fields"].items():
                val = pointer_get(row, ptr)
                if val is _MISSING:
                    (missing if field_name in opt else drift).append(f"{field_name}@{ptr}")
                    continue
                if not _scalar(val):
                    drift.append(f"{field_name}@{ptr} is a {type(val).__name__}, expected a scalar")
                    continue
                rec[field_name] = val
            for ns, ptr in (rs.get("xrefs") or {}).items():
                val = pointer_get(row, ptr)
                if val is _MISSING:
                    if ns in opt:
                        continue
                    drift.append(f"xrefs.{ns}@{ptr}")
                elif _scalar(val) and str(val) != "":
                    rec["xrefs"][ns] = val
            for k, ptr in (rs.get("coords") or {}).items():
                val = pointer_get(row, ptr)
                if val is _MISSING or not _scalar(val):
                    continue                     # coords are optional by construction
                rec["coords"][k] = val
            if drift:
                raise SchemaDrift(f"record shape moved — {', '.join(drift)} no longer resolve "
                                  f"(row {json.dumps(row)[:220]}). REFUSED[schema-drift]: the entry's "
                                  f"field map is the contract, and a missing pointer is not an empty value")
            rec["missing"] = missing
            rec["count_source"] = count_source
            rec["published"] = self.cfg["mode"] == "source"
            key = rec.get("key")
            if not isinstance(key, str) or not key.strip():
                rejects.append(dict(row=json.dumps(row)[:400], reason="empty_key",
                                    detail=f"key {key!r} is not a non-empty string", reskey="records"))
                continue
            if key in seen:
                rejects.append(dict(row=json.dumps(row)[:400], reason="duplicate_key",
                                    detail=f"{key!r} already produced by this page", reskey="records"))
                continue
            seen.add(key)
            records.append(rec)

        total = None
        if rs.get("total"):
            got = pointer_get(doc, rs["total"])
            total = got if isinstance(got, int) and not isinstance(got, bool) else None
        return Normalised(
            rows_in=len(rows), records=records, rejects=rejects,
            published_count=published_count if published_count is not None else self._source_count,
            published_count_url=self._count_url,
            extra=dict(
                source_release=release, count_source=count_source, total=total,
                served_as=self.cfg["mode"], pin=dict(self.pin),
                accounting=[dict(reskey="records", url=self._count_url,
                                 rows_in=len(rows), records=len(records),
                                 rejects=len(rejects), used=0,
                                 note="every row either became a canonical record or a reject with "
                                      "a reason; a moved POINTER refuses the run instead")],
                notes=list(self.notes),
            ),
        )

    # -- 4 verify ------------------------------------------------------------------------------
    def verify(self, d: Discovery, n: Normalised) -> Verify:
        """Ours vs the source's OWN count, quoted. `ours` is every row the source handed back
        (records + rejects): the anti-silence rule — nothing the source said may vanish from the
        accounting just because it could not become a record."""
        sid = self.cfg["id"]
        ours = len(n.records) + len(n.rejects)
        theirs = n.published_count
        count_kind = n.extra.get("count_source")
        if count_kind == "none":
            return Verify(ok=True, ours=ours, source_count=None, url="",
                          detail=(f"{sid}: TOOL mode — {len(n.records)} record(s) retrieved and "
                                  f"normalised, {len(n.rejects)} reject(s); this server states no count, "
                                  f"so it is usable as a TOOL and never published as a SOURCE "
                                  f"(a source-mode entry is REFUSED[no-count-endpoint] by name)"))
        ok = theirs is not None and ours == theirs
        split = f"records={len(n.records)} rejects={len(n.rejects)}"
        detail = (f"{sid}: ours={ours} ({split}) vs the source's own count {theirs} "
                  f"({count_kind} at {n.published_count_url}) — {'OK' if ok else 'DISAGREE'}"
                  + (f"; the source's own total for the query is {n.extra.get('total')} "
                     f"(quoted, not verified: this entry takes one page)" if n.extra.get("total") is not None else "")
                  + f"; release {n.extra.get('source_release')}")
        return Verify(ok=ok, ours=ours, source_count=theirs, url=n.published_count_url, detail=detail)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

    @property
    def calls(self) -> int:
        return self._calls


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE RUNNER — the four calls in order, one run record per source, raw kept content-addressed.
# This is the arm's own driver (there is no loop for it: the gene loop's xref join needs exactly
# one id_authority and one xref_provider, and this adapter is neither). Nothing in symbiont/loop.py
# is called, imported or changed — that is the row's "without touching loop.py".
# ─────────────────────────────────────────────────────────────────────────────────────────────
def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "release"


def _jdump(obj, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return path


def _say(msg: str) -> None:
    print(msg, flush=True)


def _record_skeleton(cfg: dict, run_id: str, term: str, refusals: list) -> dict:
    """The run record's fixed part — ONE definition, so the confined outer half and the child that
    does the work cannot drift apart about what a run record is."""
    sid = cfg["id"]
    return dict(schema="mcp-source/1", run_id=run_id, source=sid, mode=cfg["mode"],
                adapter=NAME, started_at=_utc(), term=term,
                term_sha256=_sha(term.encode("utf-8")),
                transport=dict(kind=cfg["transport"]["kind"],
                               command=cfg["transport"].get("command", ""),
                               args=list(cfg["transport"].get("args") or []),
                               url=cfg["transport"].get("url", "")),
                network=dict(kind="", allowlist=[], note="", refusals=refusals),
                sandbox_policy=None,
                licence=dict(text=cfg["licence_text"], url=cfg.get("licence_url") or "",
                             read="stated by the entry, not fetched (see this module's docstring)"),
                entry=dict(id=sid, pin=dict(cfg["pin"]), count=dict(cfg["count"]),
                           request=dict(cfg["request"]), response=dict(cfg["response"])),
                pin={}, cost={}, notes=[], refusals=[], status="",
                # (3) THE DRIFT SIGNAL IN THE RECORD: the tools/list hash and the version this run
                # read, plus the version the entry DECLARES — so a record aged months can answer
                # "did the contract move under this run?" without connecting to anything.
                tools_hash="", version="", version_declared=str(cfg["pin"].get("version") or ""))


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE ROUTINE RUN IS A CONFINED RUN (req-P1-ROUTINE-MCP-RUNS-STAY-CONFINED-AND-T, deliverable 1)
# ─────────────────────────────────────────────────────────────────────────────────────────────
def routine_network_policy(cfg: dict) -> tuple[dict | None, str]:
    """Which NETWORK control a routine run gets, and why — one definition, quoted in the record.

    stdio  THE KERNEL RULE IS INSTALLED (TIER-DEFAULT `deny-private`): the entry opens no socket of
           its own and its server is a child process, so loopback, RFC1918 and link-local are all
           denied by nftables inside the run's own namespace. That is the difference between the
           guard (which NAMES) and the boundary (which REFUSES): selftest_sandbox F5a is a ctypes
           raw connect() that the guard does not see at all. F5b, on this path.
    http   NOT installed, AND SAID SO: the run has to reach its own host, and a network namespace in
           this deployment holds only `lo` — no egress (symbiont/docs/NETWORK-POLICY-TIERS.md §1,
           network.egress). Installing the tier would not confine the run, it would refuse every
           http harvest; claiming the tier without installing it would be the lie this row exists
           to end. So the record carries kernel=false and this sentence.
    """
    if cfg["transport"]["kind"] == "stdio":
        return (sandbox.netpolicy("deny-private"),
                "stdio: the kernel rule is installed (TIER-DEFAULT deny-private — loopback, RFC1918 "
                "and link-local denied), so a raw connect() by the server process is refused by "
                "nftables, not merely named by the python guard")
    return (None,
            "http: the kernel rule is NOT installed — the run must reach its own host and a "
            "namespace in this deployment has no egress (NETWORK-POLICY-TIERS.md §1); the "
            "in-process guard names the host, and the kernel half for http runs is the "
            "deployment's job. Recorded, never implied")


def _entry_config(cfg: dict, store: str, run_id: str) -> str:
    """The ONE-ENTRY config the confined child re-reads, written from the already-validated entry.

    The child must not be handed a cfg dict across a process boundary (that would be a second,
    unchecked way in), so it re-reads an entry — the SAME bytes the parent validated, so
    load_sources in the child re-checks the total schema instead of trusting the parent. JSON is
    YAML, so this needs no writer beyond stdlib json."""
    p = os.path.join(store, "runs", f"{run_id}.{cfg['id']}.entry.yml")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(dict(version=1, sources=[cfg]), fh, indent=1, sort_keys=True)
        fh.write("\n")
    return p


def _confined_run(cfg: dict, store: str, term: str, run_id: str, refresh: bool,
                  config_path: str | None, retrial: bool = False) -> dict:
    """The OUTER half: re-run this module inside the confinement tiers, then adopt and annotate the
    record the confined child wrote. Never runs the harvest here — that is the whole point."""
    sid = cfg["id"]
    for sub in ("runs", "state"):
        os.makedirs(os.path.join(store, sub), exist_ok=True)
    refusals: list = []
    inner_cfg = _entry_config(cfg, store, run_id)
    net, net_why = routine_network_policy(cfg)
    inner = [sys.executable, "-m", "symbiont.adapters.mcp_source",
             "--config", inner_cfg, "--store", store, "--term", term, "--source", sid,
             "--run-id", run_id] + (["--refresh"] if refresh else []) + (["--retrial"] if retrial else [])
    _say(f"CONFINE-IN {sid}  store={store}  entry={os.path.relpath(inner_cfg, store)}"
         f"  network={net['tier'] if net else 'not-installed'}")
    try:
        srec = sandbox.run(inner, class_name="adapter", row_paths=[store], cwd=FARM,
                           label=f"mcp-{sid}", capture_stdout=True, network=net,
                           runs_dir=os.path.join(store, "runs"),
                           extra_env={NETWORK_ENV: net["tier"] if net else "not-installed",
                                      NETWORK_WHY_ENV: net_why})
    except sandbox.SandboxRefused as e:
        rec = _record_skeleton(cfg, run_id, term, refusals)
        rec["refusals"].append(dict(code="unconfined", detail=str(e)))
        rec["status"], rec["code"], rec["exit_code"] = "REFUSED", "unconfined", 2
        rec["finished_at"], rec["cost"] = _utc(), dict(tool_calls=0, wall_s=0.0, bytes_out=0)
        p = _jdump(rec, os.path.join(store, "runs", f"{run_id}.{sid}.json"))
        _say(f"REFUSED[unconfined] {sid}: {e}")
        _say(f"RUN-RECORD {os.path.relpath(p, store)}  status=REFUSED  code=unconfined")
        return rec
    out = srec.get("stdout") or ""
    if out:
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
    rec_path = os.path.join(store, "runs", f"{run_id}.{sid}.json")
    try:
        with open(rec_path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        rec = _record_skeleton(cfg, run_id, term, refusals)
        rec["refusals"].append(dict(
            code="error", detail=f"the confined child (exit {srec['exit']}) wrote no run record at "
                                  f"{os.path.relpath(rec_path, store)} — a run nobody can audit is "
                                  f"not a run; stderr tail: {(srec.get('stderr_tail') or [])}"))
        rec["status"], rec["code"] = "REFUSED", "error"
    # ── THE CONFINEMENT, as the OUTER half measured it (the child's own tier is the same one: it
    # was resolved once, and handed to the child as SYMBIONT_SANDBOX_TIER).
    conf = dict(rec.get("confine") or {})
    conf.update(tier=srec["tier"],
                sandbox=dict(tier=srec["tier"], record=srec.get("record") or "",
                             allowlist=srec.get("allowlist") or [], wall_s=srec.get("wall_s"),
                             network_tier=(srec.get("network") or {}).get("tier"),
                             tier_tried=srec.get("tier_tried") or {},
                             kernel_findings=[f for f in (srec.get("findings") or [])]))
    if conf.get("tier") and rec.get("confine", {}).get("tier") not in ("", None, srec["tier"]):
        conf["disagreement"] = (f"the child recorded tier={rec['confine'].get('tier')} but the "
                                f"sandbox ran it under tier={srec['tier']}")
    rec["confine"] = conf
    exit_code = srec["exit"]
    if srec.get("breach"):
        rec["status"], rec["code"], exit_code = "REFUSED", "sandbox-breach", sandbox.BREACH_EXIT
        rec["refusals"] = list(rec.get("refusals") or []) + [dict(
            code="sandbox-breach",
            detail="the confined harvest was refused a write or a connect outside its allowlist "
                   f"({len(srec.get('findings') or [])} finding(s), named in the sandbox record)")]
    rec["exit_code"] = exit_code
    rec["finished_at"] = rec.get("finished_at") or _utc()
    p = _jdump(rec, rec_path)
    _say(f"CONFINE   {sid}  tier={srec['tier']}  tools_hash={conf.get('tools_hash', '') or '—'}"
         f"  network={(conf.get('network') or {}).get('tier') or 'not-installed'}"
         f"  exit={exit_code}")
    _say(f"RUN-RECORD {os.path.relpath(p, store)}  status={rec['status']}"
         + (f"  code={rec['code']}" if rec.get("code") else ""))
    return rec


def run_source(cfg: dict, store: str, term: str, run_id: str | None = None,
               refresh: bool = False, config_path: str | None = None,
               retrial: bool = False) -> dict:
    """discover -> fetch(since) -> snapshot -> normalise -> verify, for ONE MCP source.

    Every refusal is a Refusal (contract.Refusal subclass) with a CODE; the run record is written
    even when the run refuses (a refusal is a legal answer WITH a reason file). The source's state
    file is written only after verify(), so a refused source never advances `since`.

    AND the run is CONFINED (req-P1-ROUTINE-MCP-RUNS-STAY-CONFINED-AND-T): the process that does
    the work is the child of `symbiont/sandbox.py :: run()`, which knows its tier. A run that
    arrives here WITHOUT a tier is therefore the outer half — it re-executes itself inside one
    instead of harvesting in the station's own process. `config_path` is kept for the caller's
    information (the child re-reads the entry the parent validated, see `_entry_config`)."""
    run_id = run_id or _utc().replace(":", "").replace("-", "")
    tier = os.environ.get(CONFINED_ENV, "")
    if not tier:
        return _confined_run(cfg, store, term, run_id, refresh, config_path, retrial=retrial)
    return _run_in_tier(cfg, store, term, run_id, refresh, tier, retrial=retrial)


def _run_in_tier(cfg: dict, store: str, term: str, run_id: str, refresh: bool,
                 tier: str, retrial: bool = False) -> dict:
    """The INNER half: the harvest itself, in the process the confinement tier started.

    Every refusal is a Refusal (contract.Refusal subclass) with a CODE; the run record is written
    even when the run refuses (a refusal is a legal answer WITH a reason file). The source's state
    file is written only after verify(), so a refused source never advances `since`.
    """
    sid = cfg["id"]
    for sub in ("runs", "state", "raw", "records"):
        os.makedirs(os.path.join(store, sub), exist_ok=True)
    state_path = os.path.join(store, "state", f"{sid}.json")
    since = {}
    if not refresh and os.path.isfile(state_path):
        with open(state_path, encoding="utf-8") as fh:
            since = json.load(fh)

    refusals: list = []
    rec: dict = _record_skeleton(cfg, run_id, term, refusals)
    ad = MCPSourceAdapter()
    ad.set_trial(trial_read(store, sid))
    net_tier = os.environ.get(NETWORK_ENV, "not-installed")
    net_why = os.environ.get(NETWORK_WHY_ENV, "")
    code, exit_code = "", 0
    try:
        ad.configure(cfg)                       # THE COUNT RULE refuses here, by name, before any wire
        allow = network_allowlist(cfg)           # GENERATED FROM THE ENTRY, before anything is spawned
        rec["network"] = dict(kind=allow["kind"], allowlist=list(allow["hosts"]),
                              note=allow["note"], refusals=refusals)
        rec["sandbox_policy"] = allow["policy"]
        rec["notes"] = list(ad.notes)
        with sandbox.network_guard(set(allow["hosts"]), refusals=refusals):
            ad.set_term(term)
            if retrial:
                # ── THE RE-TRIAL, in the SAME confinement as a harvest: read the handshake and the
                # whole tools/list, arm the trial on what the server offers now, and STOP. No tool
                # call, no snapshot, no record — so the way back from an expiry cannot become a
                # harvest with extra steps.
                ad.retrial_open()
                rec["retrial"] = dict(tools_hash=ad.tools_sha, version=ad.seen_version,
                                      previous=(ad.trial_after or {}).get("previous") or {})
                rec["status"] = "RE-TRIAL"
                _say(f"RE-TRIAL {sid}  tools_hash={ad.tools_sha}  version={ad.seen_version}  "
                     f"zero tool calls (the next run harvests against this trial)")
                raise _RetrialDone()
            d = ad.discover(None, since)
            rec["discover"] = dict(release=d.release, published_count=d.published_count,
                                   published_count_url=d.published_count_url,
                                   licence=d.licence, notes=d.notes)
            rec["pin"] = dict(ad.pin)
            _say(f"DISCOVER {sid}  transport={rec['transport']['kind']}  release={d.release}  "
                 f"source_count={d.published_count}")
            _say(f"PIN      {sid}  {ad.pin['package']}@{ad.pin['version']} "
                 f"tool_description_sha256={ad.pin['tool_description_sha256']} "
                 f"({ad.pin['pin_state']})")

            plan = ad.fetch(None, since)
            rec["fetch"] = dict(new=plan.new, reason=plan.reason)
            raw_path = ""
            if plan.new:
                blob = plan.resources["records"]["bytes"]
                digest = _sha(blob)
                rel = os.path.join(store, "raw", sid, _safe(d.release))
                raw_path = os.path.join(rel, f"{digest}.json")
                os.makedirs(rel, exist_ok=True)
                if os.path.exists(raw_path) and _sha(open(raw_path, "rb").read()) != digest:
                    raise RuntimeError(f"raw zone poisoning: {raw_path} exists with different bytes")
                with open(raw_path, "wb") as fh:
                    fh.write(blob)
                rec["fetch"].update(sha256=digest, bytes=len(blob),
                                    path=os.path.relpath(raw_path, store))
                _say(f"FETCH    {sid}  new=1  {plan.reason}")
                _say(f"SNAPSHOT {sid}  {os.path.relpath(raw_path, store)}  sha256={digest}  "
                     f"bytes={len(blob)}")
            else:
                snap = (since.get("snapshots") or {}).get("records") or {}
                if not snap.get("path"):
                    raise RuntimeError(f"nothing fetched and no prior snapshot — refusing to "
                                       f"normalise bytes we cannot name")
                blob = open(os.path.join(store, snap["path"]), "rb").read()
                if _sha(blob) != snap.get("sha256"):
                    raise RuntimeError(f"the stored snapshot does not match its content address")
                raw_path = os.path.join(store, snap["path"])
                rec["fetch"].update(sha256=snap["sha256"], bytes=len(blob),
                                    path=snap["path"], reused_from=snap.get("run_id", ""))
                _say(f"FETCH    {sid}  new=0  {plan.reason}  (normalising {snap['path']})")

            n = ad.normalise({"records": blob})
            errs = contract.check_accounting(n)
            if errs:
                raise RuntimeError(f"rows unaccounted for: {errs}")
            by_reason: dict = {}
            for r in n.rejects:
                by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
            rec["normalise"] = dict(rows_in=n.rows_in, records=len(n.records), rejects=len(n.rejects),
                                    rejects_by_reason=by_reason,
                                    count_source=n.extra.get("count_source"),
                                    source_total=n.extra.get("total"),
                                    source_release=n.extra.get("source_release"),
                                    served_as=n.extra.get("served_as"),
                                    accounting=n.extra.get("accounting", []))
            _say(f"NORMALISE {sid} rows_in={n.rows_in} records={len(n.records)} "
                 f"rejects={len(n.rejects)}" + (f" ({by_reason})" if by_reason else ""))

            # the canonical records, on disk, one JSON object per row — each carrying its OWN
            # source and source_release, so a row lifted out of this file still says where it came
            # from and which release it is (the row's NSC, mechanically)
            rows_path = os.path.join(store, "records", sid, f"{_safe(d.release)}.jsonl")
            os.makedirs(os.path.dirname(rows_path), exist_ok=True)
            with open(rows_path, "w", encoding="utf-8") as fh:
                for r in n.records:
                    fh.write(json.dumps(r, sort_keys=True) + "\n")
            rec["records_file"] = os.path.relpath(rows_path, store)
            rec["records_sha256"] = _sha(open(rows_path, "rb").read())
            _say(f"RECORDS  {sid}  {rec['records_file']}  rows={len(n.records)}  "
                 f"sha256={rec['records_sha256']}")

            v = ad.verify(d, n)
            rec["verify"] = dict(ok=v.ok, ours=v.ours, source_count=v.source_count, url=v.url,
                                 detail=v.detail)
            _say(f"VERIFY   {sid}  {'OK' if v.ok else 'DISAGREE'}  {v.detail}")
            if not v.ok:
                code, exit_code, rec["status"] = "count-mismatch", 1, "REFUSED"

            if rec["status"] != "REFUSED":
                _jdump(dict(release=d.release, run_id=run_id, term_sha256=rec["term_sha256"],
                            updated_at=_utc(), pin=rec["pin"],
                            snapshots={"records": dict(path=rec["fetch"]["path"],
                                                       sha256=rec["fetch"]["sha256"], run_id=run_id)},
                            licence=d.licence),
                       state_path)
                rec["status"] = "PUBLISHED" if cfg["mode"] == "source" else "TOOL-CALL"
    except _RetrialDone:
        pass                                    # the re-trial gesture finished; nothing to harvest
    except Refusal as e:
        code, rec["status"] = e.code, "REFUSED"
        rec["refusals"].append(dict(code=e.code, detail=str(e)))
        exit_code = 7 if e.code == "schema-drift" else 2
        _say(f"REFUSED[{e.code}] {sid}: {e}")
    except (contract.ConfigError, sandbox.SandboxRefused) as e:
        code, rec["status"] = "config", "REFUSED"
        rec["refusals"].append(dict(code="config", detail=str(e)))
        exit_code = 2
        _say(f"REFUSED[config] {sid}: {e}")
    except Exception as e:                    # an unexpected break is still a NAMED reason file
        code, rec["status"] = "error", "REFUSED"
        rec["refusals"].append(dict(code="error", detail=f"{type(e).__name__}: {e}"))
        exit_code = 2
        _say(f"REFUSED[error] {sid}: {type(e).__name__}: {e}")
    finally:
        ad.close()
        rec["cost"] = dict(tool_calls=ad.calls, wall_s=round(time.time() - ad._t0, 3),
                           bytes_out=ad._bytes)
        rec["notes"] = list(ad.notes)
        rec["sandbox_refusals"] = refusals
        rec["finished_at"] = _utc()
        rec["code"] = code
        rec["exit_code"] = exit_code
        # (3) the run record's own drift pair — the tools/list hash and the version this run READ
        # (the skeleton's is the version the entry DECLARES, so both sides are always on disk).
        rec["tools_hash"] = ad.tools_sha
        rec["version"] = ad.seen_version or str(cfg["pin"].get("version") or "")
        # ── WHICH TIER CONFINED THIS RUN, AND THE tools/list IT SAW (deliverable 3). The tier is
        # read from the environment the sandbox handed this child — never guessed, and never the
        # word "none" unless a run really arrived unconﬁned (which run_source makes impossible).
        rec["confine"] = dict(tier=tier or "none",
                              tools_hash=(ad.tools_sha or (ad.pin or {}).get("tools_sha256", "")),
                              network=dict(kernel=(net_tier != "not-installed"),
                                           tier=net_tier, why=net_why)
                              if net_tier else dict(kernel=False, tier="not-installed", why=""),)
        # ── THE TRIAL: what the NEXT run is compared against (expired on drift, armed otherwise).
        # Written even when the run refused, because an expiry that only happens on success would
        # expire nothing at all.
        if ad.trial_after:
            rec["trial"] = trial_write(store, sid, ad.trial_after)
            _say(f"TRIAL    {sid}  state={rec['trial']['state']}  "
                 f"pinned_tools={rec['trial']['pinned'].get('tools_sha256', '') or '—'}  "
                 f"seen_tools={rec['confine']['tools_hash'] or '—'}  "
                 f"why={rec['trial'].get('why', '')}")
        pretty = _jdump(rec, os.path.join(store, "runs", f"{run_id}.{sid}.json"))
        _say(f"RUN-RECORD {os.path.relpath(pretty, store)}  status={rec['status']}"
             + (f"  code={code}" if code else ""))
        _say(f"CONFINE   {sid}  tier={rec['confine']['tier']}  "
             f"tools_hash={rec['confine']['tools_hash'] or '—'}  "
             f"network={rec['confine']['network']['tier']}"
             + ("  (kernel rule installed)" if rec["confine"]["network"]["kernel"] else ""))
        _say(f"COST     {sid}  tool_calls={rec['cost']['tool_calls']} "
             f"wall={rec['cost']['wall_s']}s")
        if refusals:
            _say(f"SANDBOX  {sid}  {len(refusals)} connect attempt(s) refused by the allowlist "
                 f"{rec['network']['allowlist'] or 'EMPTY (stdio: no socket of our own)'}")
    return rec


def _unique_record_path(store: str, run_id: str, sid: str, tag: str) -> str:
    """A skip record must never overwrite another run's record. Run ids have one-second
    resolution, so two invocations inside the same second would otherwise share a file name and
    the second would silently erase the first (measured while collecting this row's evidence: a
    skip record was overwritten by the re-trial that followed it)."""
    p = os.path.join(store, "runs", f"{run_id}.{sid}.json")
    n = 1
    while os.path.exists(p):
        p = os.path.join(store, "runs", f"{run_id}-{tag}{n}.{sid}.json")
        n += 1
    return p


def _skip_record(cfg: dict, store: str, term: str, run_id: str | None, trial: dict) -> dict:
    """THE EXPIRY, ENFORCED AS A SKIP (deliverable 2, second half): a source whose trial is
    expired is not run, not spawned, not asked — it is SKIPPED BY NAME with the reason the expiry
    wrote, and the skip leaves a record of its own so 'it did not harvest' is a fact on disk and
    not an absence somebody has to notice.

    No child process exists for a skipped source: the branch is in the runner, above the sandbox.
    """
    sid = cfg["id"]
    run_id = run_id or _utc().replace(":", "").replace("-", "")
    seen = dict(trial.get("seen") or {})
    reason = (f"trial-expired at {trial.get('expired_at')}: {trial.get('why')} — the tool list or "
              f"the version moved since admission, so this source must be re-trialled before it "
              f"harvests again")
    rec = _record_skeleton(cfg, run_id, term, [])
    rec.update(status="SKIPPED", code="trial-expired", exit_code=0, finished_at=_utc(),
               tools_hash=str(seen.get("tools_sha256") or ""),
               version=str(seen.get("version") or ""),
               trial=dict(trial), skip=dict(reason=reason, since=str(trial.get("expired_at") or "")),
               cost=dict(tool_calls=0, wall_s=0.0, bytes_out=0),
               confine=dict(tier="not-run", tools_hash=str(seen.get("tools_sha256") or ""),
                            network=dict(kernel=False, tier="not-run",
                                         why="skipped before any child process existed")))
    p = _jdump(rec, _unique_record_path(store, run_id, sid, "skip"))
    _say(f"SKIP     {sid}  reason=trial-expired  since={rec['skip']['since']}  "
         f"why={trial.get('why')}  (re-trial it: --source {sid} --retrial, or pin what you admit)")
    _say(f"RUN-RECORD {os.path.relpath(p, store)}  status=SKIPPED  code=trial-expired")
    return rec


def load_and_run(config_path: str, store: str, term: str, only: list[str] | None = None,
                 run_id: str | None = None, refresh: bool = False,
                 retrial: bool = False) -> tuple[dict, int]:
    """Every enabled entry (or `only`), in id order. Returns (records by id, worst exit code).

    TWO ways a source does not run, and they are not the same word: `enabled: false` is the
    operator's off switch (a skip, no record — nothing was expected of it), and a TRIAL-EXPIRED
    state is the drift signal's verdict (a skip WITH a record and the reason, because a harvest
    WAS expected and the source is the thing that changed)."""
    sources = load_sources(config_path)
    if only:
        missing = [s for s in only if s not in sources]
        if missing:
            raise ConfigError(f"{config_path}: no source(s) {missing} — configured: {sorted(sources)}")
    out, worst = {}, 0
    for sid in sorted(sources):
        cfg = sources[sid]
        if only and sid not in only:
            continue
        if not cfg["enabled"]:
            _say(f"SKIP     {sid}  reason=disabled (enabled: false)")
            continue
        trial = trial_read(store, sid)
        if trial_state(trial) == "expired" and not retrial and not expiry_cleared_by_pin(cfg, trial):
            rec = _skip_record(cfg, store, term, run_id, trial)
            out[sid] = rec
            worst = max(worst, rec["exit_code"])
            continue
        rec = run_source(cfg, store, term, run_id=run_id, refresh=refresh,
                         config_path=config_path, retrial=retrial)
        out[sid] = rec
        worst = max(worst, rec["exit_code"])
    if not out:
        _say("REFUSED[config] nothing ran: no enabled source selected — a run that silently did "
             "nothing is the silence this adapter exists to kill")
        return out, 2
    return out, worst


def print_pin(cfg: dict) -> int:
    """The gesture an operator makes before the pin is written: connect, handshake, read the tool's
    own contract, print the hash. It never writes sources.yml — admission is a decision, not a
    side effect of a probe."""
    ad = MCPSourceAdapter()
    ad.configure(cfg)
    try:
        ad.client = client_for(cfg["transport"])
        info = ad.client.open()
        tool = cfg["request"]["tool"]
        entry = next((t for t in ad.client.tools if t.get("name") == tool), None)
        if entry is None:
            _say(f"REFUSED[schema-drift] {cfg['id']}: the server offers no tool {tool!r}; it offers "
                 f"{sorted(t.get('name', '?') for t in ad.client.tools)}")
            return 7
        sha = tool_description_sha256(entry)
        _say(json.dumps(dict(source=cfg["id"], package=cfg["pin"]["package"],
                             server_name=info.get("name", ""),
                             server_version=info.get("version", ""),
                             tools=sorted(t.get("name", "?") for t in ad.client.tools),
                             tool=tool, tool_description_sha256=sha,
                             # THE DRIFT SIGNAL's other half (req-P1-ROUTINE-MCP-RUNS-STAY-
                             # CONFINED-AND-T): the whole tools/list, hashed — this is the value
                             # an operator pins as pin.tools_sha256 to make a moved tool list a
                             # REFUSED[schema-drift] instead of a quiet addition.
                             tools_sha256=tools_list_sha256(ad.client.tools),
                             pin_matches_declared_version=(info.get("version") == cfg["pin"]["version"]),
                             declared_tool_description_sha256=cfg["pin"]["tool_description_sha256"],
                             declared_tools_sha256=cfg["pin"].get("tools_sha256", "")),
                        indent=1, sort_keys=True))
        return 0
    except Refusal as e:
        _say(f"REFUSED[{e.code}] {cfg['id']}: {e}")
        return 7 if e.code == "schema-drift" else 2
    finally:
        ad.close()


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="a generic MCP source adapter (configured from sources.yml)")
    ap.add_argument("--config", default="", help="the MCP arm's sources.yml (see this module's docstring)")
    ap.add_argument("--store", default="", help="where the run record, raw snapshot and records land")
    ap.add_argument("--term", default="", help="the question | query | id this run asks")
    ap.add_argument("--source", default="", help="comma list of entry ids (default: every enabled entry)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--refresh", action="store_true", help="fetch even when the release has not moved")
    ap.add_argument("--print-pin", action="store_true", help="print the server's pin (version + tool hash)")
    ap.add_argument("--retrial", action="store_true",
                    help="re-trial ONE expired source through the sandbox: re-read tools/list and "
                         "version, arm the trial, make zero tool calls (the way back from a drift)")
    ap.add_argument("--print-policy", action="store_true", help="print the network allowlist from the entry")
    a = ap.parse_args(argv)

    if not a.config:
        _say("REFUSED[config] --config is required: this adapter knows nothing a sources.yml entry "
             "does not say, so there is no default source to fall back on")
        return 2
    try:
        sources = load_sources(a.config)
    except contract.ConfigError as e:
        _say(f"REFUSED[{e.code}] {e}")
        return 2
    ids = [s for s in a.source.split(",") if s] or sorted(sources)
    missing = [s for s in ids if s not in sources]
    if missing:
        _say(f"REFUSED[config] no source(s) {missing} in {os.path.abspath(a.config)} — "
             f"configured: {sorted(sources)}")
        return 2

    if a.print_policy:
        for sid in ids:
            allow = network_allowlist(sources[sid])
            _say(f"POLICY {sid}: transport={sources[sid]['transport']['kind']} "
                 f"allowlist={allow['hosts'] or 'EMPTY'} — {allow['note']}")
            if allow["policy"]:
                _say(sandbox.nft_ruleset(allow["policy"]))
        return 0
    if a.print_pin:
        worst = 0
        for sid in ids:
            worst = max(worst, print_pin(sources[sid]))
        return worst
    if not a.store:
        _say("REFUSED[config] --store is required: this arm writes a run record, and a run with "
             "nowhere to write it is a run nobody can audit")
        return 2
    if a.retrial:
        # A re-trial is ONE operator act on ONE named source, and only where an expiry exists: a
        # re-trial of a healthy source would silently re-baseline the drift signal, which is the
        # thing this whole mechanism exists to prevent.
        if len(ids) != 1:
            _say("REFUSED[config] --retrial needs exactly ONE --source: a re-trial is an operator's "
                 "decision about a named server, never a run over a roster")
            return 2
        sid = ids[0]
        tr = trial_read(a.store, sid)
        if trial_state(tr) != "expired":
            _say(f"REFUSED[config] {sid}: its trial is state={trial_state(tr)!r}, not 'expired' — "
                 f"there is nothing to re-trial, and re-arming a healthy trial would erase the "
                 f"baseline the drift signal compares against")
            return 2
        if not os.environ.get(CONFINED_ENV):
            # the OUTER half prints the operator's line once; the confined child re-validates the
            # same expiry on disk and must not print it again as if it were a second decision
            _say(f"RETRIAL  {sid}  expired_at={tr.get('expired_at')}  why={tr.get('why')}")
    try:
        _, worst = load_and_run(a.config, a.store, a.term, ids, run_id=a.run_id, refresh=a.refresh,
                                retrial=a.retrial)
    except contract.ConfigError as e:
        _say(f"REFUSED[{e.code}] {e}")
        return 2
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
