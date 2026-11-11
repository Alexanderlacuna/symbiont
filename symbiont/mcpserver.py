"""THE MCP SERVER — the seven source tools as MCP tools, the SAME CLI beneath (one definition).

The row (req-P1-SOURCE-HEALTH-AND-AGENT-ADDED-SOURCES) asks for the tools "as MCP tools with the
same CLI beneath (one definition)". This server is that thin layer: every tools/call builds the
argv of the CLI that DEFINES the tool and runs it as a subprocess — nothing is reimplemented here,
so the MCP tool an agent calls and the CLI an operator runs by hand are the same words, and the
two can never drift.

    source_health   ->  python3 -m symbiont.health …          (structured record, --json)
    source_probe    ->  python3 -m symbiont.admission probe <url> …
    source_evaluate ->  python3 -m symbiont.admission evaluate <id>
    source_draft    ->  python3 -m symbiont.admission draft <id>
    source_sandbox  ->  python3 -m symbiont.admission sandbox <id> [--budget-*]
    source_card     ->  python3 -m symbiont.admission card <id> --question <…>
    source_admit    ->  python3 -m symbiont.admission admit <id> [--gate <the gate's word>] …
                        (consults the owner's policy first: a source whose card passes every
                        check the policy names AND that is stable for N cycles self-admits)

THE UPDATE/VERSION TOOLS (req-P1-UPDATE-AS-AN-MCP-TOOL-check-propose-n) — read-only, and the last
one only PROPOSES; NONE of them applies an update (an update is a row: the agent quotes these and
the operator pulls):

    source_update_check -> python3 -m symbiont.update check <source>      (metadata endpoint only)
    source_update_plan  -> python3 -m symbiont.update plan  <source>      (rows · cost · licence)
    station_version     -> python3 -m symbiont.update version             (station · store · pins)

A nonzero CLI exit (a REFUSED[…], a NO-SOURCE) comes back as an isError result carrying the
CLI's own words verbatim — code, reason and all — never a flattened "something went wrong".

THE SENTINEL CYCLE (req-P1-THE-SENTINEL-CYCLE-the-stations-own) adds exactly two tools, both on
THIS door and never on the read doors (DESIGN-SENTINEL §5: a page or a casual agent must never be
able to trigger growth):

    cycle_status    -> python3 -m symbiont.cycle --status      (read-only: enabled · mode · last
                       run · last report + sha256 · budget left · next due · the governance gate)
    cycle_run_once  -> python3 -m symbiont.cycle               (ONE bounded run of the configured
                       mode, budgeted, returning the CYCLE-REPORT's sha256; it obeys enabled:false
                       and refuses admit.by_policy without a ruling on record)

Wire: newline-delimited JSON-RPC 2.0 over stdio (the same framing the literature arm's MCP client
uses). Methods: initialize · notifications/initialized · tools/list · tools/call.

Run:  python3 -m symbiont.mcpserver            (then speak JSON-RPC on stdin, one per line)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTOCOL = "2025-06-18"

OPT = {"type": "string", "description": "path override; default is the farm's own"}


def _tool(name: str, description: str, props: dict, required: list[str] | None = None):
    return dict(name=name, description=description,
                inputSchema=dict(type="object", properties=props,
                                 required=required or []))


TOOLS = [
    _tool("source_health",
          "Per-source health: state (ok|degraded|down|quarantined), last_run age, last_success "
          "age, count_check (ours vs theirs + URL), rate-limit headroom used this cycle, licence "
          "status, staleness, and the ONE reason a source is not ok. An agent can record its "
          "remediation (retry|quarantine|replace) with `record`.",
          dict(sources_config=OPT, store=OPT,
               record=dict(type="object",
                           description="record the agent's act before reporting",
                           properties=dict(source=dict(type="string"),
                                           action=dict(type="string",
                                                       enum=["retry", "quarantine", "replace"]),
                                           note=dict(type="string")),
                           required=["source", "action", "note"]))),
    _tool("source_probe",
          "Read a URL under a hard probe budget. A usable body becomes a candidate; nothing usable "
          "answers NO-SOURCE with the URLs it read.",
          dict(url=dict(type="string", description="the source URL to probe"),
               id=dict(type="string", description="candidate id ([a-z][a-z0-9_]*)"),
               licence_url=dict(type="string", description="the licence page, if known"),
               admission_dir=OPT),
          ["url"]),
    _tool("source_evaluate",
          "Evaluate a candidate: licence (read and hashed), accessions in the sample, rate limit, "
          "size, cadence.", dict(id=dict(type="string"), admission_dir=OPT), ["id"]),
    _tool("source_draft",
          "Write the adapter skeleton from the template into the drafts bay and run the contract's "
          "load check on it.", dict(id=dict(type="string"), admission_dir=OPT,
                                    drafts_dir=OPT), ["id"]),
    _tool("source_sandbox",
          "Run the drafted adapter into a THROWAWAY store under a budget (REFUSED[budget] over "
          "it).", dict(id=dict(type="string"), admission_dir=OPT, drafts_dir=OPT,
                       budget_requests=dict(type="integer"),
                       budget_bytes=dict(type="integer"),
                       budget_wall_s=dict(type="integer")), ["id"]),
    _tool("source_card",
          "Cut the admission card (licence · counts matched · sample rows · xref rate · cost · "
          "the question it helps answer) and register the source in sources.yml as enabled:false.",
          dict(id=dict(type="string"),
               question=dict(type="string", description="the question this source helps answer"),
               admission_dir=OPT, sources_config=OPT), ["id", "question"]),
    _tool("source_admit",
          "THE gated step: flip the source active in sources.yml and move its adapter in. It "
          "CONSULTS THE OWNER'S POLICY (/reports/symbiont-policy.yml) FIRST — a source whose "
          "card passes every check the policy names AND that has been stable for N consecutive "
          "cycles self-admits, and its receipt says 'by policy, rule "
          "stable_cycles_before_self_admit, N=<n>'. Anything else still needs the operator's "
          "word: REFUSED[gate] without it. A missing/malformed policy, or an N the owner has "
          "not set, fails CLOSED for self-admission only (named), never a second blocker.",
          dict(id=dict(type="string"),
               gate=dict(type="string", description="the gate's word, quoted (needed only when "
                                                   "the policy does not self-admit the source)"),
               admission_dir=OPT, sources_config=OPT, drafts_dir=OPT, adapters_dir=OPT,
               gate_file=OPT, policy=OPT, store=OPT), ["id"]),
    _tool("source_update_check",
          "Is this source out of date? Our stored release vs the upstream release read from the "
          "source's METADATA ENDPOINT (never a data fetch), plus our own heartbeat: {stored, "
          "upstream, moved, stale, age_days, last_success}. release_kind names how the upstream "
          "release was read (json_release | http_last_modified | none | unreadable); a source with "
          "no release id (the literature arm's pinned MCP) answers release_kind=none rather than "
          "inventing a version, and an unreadable endpoint answers moved=unknown with the reason. "
          "READ-ONLY: no tool here applies an update.",
          dict(source=dict(type="string", description="the source id, as sources.yml defines it"),
               sources_config=OPT, store=OPT, literature_config=OPT, literature_store=OPT),
          ["source"]),
    _tool("source_update_plan",
          "What an update WOULD do, without doing it: rows added/changed/retired where the "
          "metadata endpoint can say so (otherwise 'unknown' AND the reason — never a guess), the "
          "cost measured from the last run (kind=measured) or 'unknown' naming why, the source's "
          "declared budget as the CAP, and the licence in force. It PROPOSES: an update is a row "
          "the operator pulls.",
          dict(source=dict(type="string", description="the source id, as sources.yml defines it"),
               sources_config=OPT, store=OPT, literature_config=OPT, literature_store=OPT),
          ["source"]),
    _tool("station_version",
          "Am I out of date? In one call: the running station's version, the store's schema "
          "version (refused BY NAME if the store is absent), the adapters' pinned releases, and "
          "whether the deployment's declared release bundle is ahead of those pins.",
          dict(sources_config=OPT, store=OPT), []),
    _tool("cycle_status",
          "THE SENTINEL CYCLE, read-only (req-P1-THE-SENTINEL-CYCLE): is the cycle enabled? "
          "which mode, on what cadence · the last run and its outcome · the last CYCLE-REPORT "
          "and its sha256 · the budget left · the next run due · and whether admit.by_policy is "
          "permitted on record. It reads cycle.yml and the last run record and writes NOTHING.",
          dict(config=OPT), []),
    _tool("cycle_run_once",
          "THE SENTINEL CYCLE, one bounded run (a WRITE door, operator-only): telemetry from the "
          "digests -> discovery over the deployment's own registry -> evaluate -> draft (only if "
          "a provider is configured) -> sandbox with the count verified -> card -> admit ONLY by "
          "policy with the ruling on record. It spends only the cycle's own budget and returns "
          "the CYCLE-REPORT path and sha256. With `enabled: false` (the shipped default) it does "
          "NOTHING AT ALL and says so; an unreadable cycle.yml or admit.by_policy without a "
          "ruling REFUSES by name (exit 2).",
          dict(config=OPT,
               mode=dict(type="string", enum=["watch", "discover", "both"],
                         description="override the configured mode for THIS run only")), []),
]


def _cli(args: list[str]) -> dict:
    """Run the CLI that defines the tool, in the farm, and wrap its words as a tool result."""
    p = subprocess.run([sys.executable] + args, cwd=FARM, capture_output=True, text=True,
                       timeout=1800)
    text = p.stdout.rstrip("\n")
    if p.stderr.strip():
        text += ("\n" if text else "") + "stderr: " + p.stderr.strip()
    return dict(content=[dict(type="text", text=text or f"(exit {p.returncode}, no output)")],
                isError=(p.returncode != 0), cli_exit=p.returncode)


def _call(name: str, a: dict) -> dict:
    a = dict(a or {})
    if name == "source_health":
        args = ["-m", "symbiont.health", "--json"]
        if a.get("sources_config"):
            args += ["--sources-config", str(a["sources_config"])]
        if a.get("store"):
            args += ["--store", str(a["store"])]
        r = a.get("record") or {}
        if r:
            args += ["--record-remediation", str(r["source"]), "--action", str(r["action"]),
                     "--note", str(r["note"])]
        return _cli(args)
    if name == "source_probe":
        args = ["-m", "symbiont.admission", "probe", str(a["url"])]
        if a.get("id"):
            args += ["--id", str(a["id"])]
        if a.get("licence_url"):
            args += ["--licence-url", str(a["licence_url"])]
        if a.get("admission_dir"):
            args += ["--admission-dir", str(a["admission_dir"])]
        return _cli(args)
    if name == "source_evaluate":
        args = ["-m", "symbiont.admission", "evaluate", str(a["id"])]
        if a.get("admission_dir"):
            args += ["--admission-dir", str(a["admission_dir"])]
        return _cli(args)
    if name == "source_draft":
        args = ["-m", "symbiont.admission", "draft", str(a["id"])]
        for k in ("admission_dir", "drafts_dir"):
            if a.get(k):
                args += [f"--{k.replace('_', '-')}", str(a[k])]
        return _cli(args)
    if name == "source_sandbox":
        args = ["-m", "symbiont.admission", "sandbox", str(a["id"])]
        for k in ("admission_dir", "drafts_dir"):
            if a.get(k):
                args += [f"--{k.replace('_', '-')}", str(a[k])]
        for k in ("budget_requests", "budget_bytes", "budget_wall_s"):
            if a.get(k) is not None:
                args += [f"--{k.replace('_', '-')}", str(a[k])]
        return _cli(args)
    if name == "source_card":
        args = ["-m", "symbiont.admission", "card", str(a["id"]),
                "--question", str(a["question"])]
        for k in ("admission_dir", "sources_config"):
            if a.get(k):
                args += [f"--{k.replace('_', '-')}", str(a[k])]
        return _cli(args)
    if name in ("source_update_check", "source_update_plan"):
        args = ["-m", "symbiont.update", name.rsplit("_", 1)[1], str(a["source"])]
        for k in ("sources_config", "store", "literature_config", "literature_store"):
            if a.get(k):
                args += [f"--{k.replace('_', '-')}", str(a[k])]
        return _cli(args)
    if name == "station_version":
        args = ["-m", "symbiont.update", "version"]
        for k in ("sources_config", "store"):
            if a.get(k):
                args += [f"--{k.replace('_', '-')}", str(a[k])]
        return _cli(args)
    if name == "cycle_status":
        args = ["-m", "symbiont.cycle", "--status"]
        if a.get("config"):
            args += ["--config", str(a["config"])]
        return _cli(args)
    if name == "cycle_run_once":
        args = ["-m", "symbiont.cycle"]
        if a.get("config"):
            args += ["--config", str(a["config"])]
        if a.get("mode"):
            args += ["--mode", str(a["mode"])]
        return _cli(args)
    if name == "source_admit":
        args = ["-m", "symbiont.admission", "admit", str(a["id"])]
        if a.get("gate") is not None:
            args += ["--gate", str(a["gate"])]
        for k in ("admission_dir", "sources_config", "drafts_dir", "adapters_dir", "gate_file",
                  "policy", "store"):
            if a.get(k):
                args += [f"--{k.replace('_', '-')}", str(a[k])]
        return _cli(args)
    raise KeyError(name)


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message -> one result (or None for notifications / foreign replies)."""
    if msg.get("id") is None:                      # a notification: nothing to answer
        return None
    method = msg.get("method", "")
    mid = msg.get("id")
    if method == "initialize":
        return dict(jsonrpc="2.0", id=mid, result=dict(
            protocolVersion=(msg.get("params") or {}).get("protocolVersion") or PROTOCOL,
            capabilities=dict(tools={}),
            serverInfo=dict(name="symbiont-sources", version="1.0.0")))
    if method == "tools/list":
        return dict(jsonrpc="2.0", id=mid, result=dict(tools=TOOLS))
    if method == "tools/call":
        p = msg.get("params") or {}
        try:
            return dict(jsonrpc="2.0", id=mid, result=_call(p.get("name", ""), p.get("arguments")))
        except KeyError:
            return dict(jsonrpc="2.0", id=mid, error=dict(
                code=-32602, message=f"unknown tool {p.get('name')!r} — tools/list names them all"))
        except Exception as e:
            return dict(jsonrpc="2.0", id=mid, result=dict(
                content=[dict(type="text", text=f"{type(e).__name__}: {e}")], isError=True))
    if method.startswith("notifications/"):
        return None
    return dict(jsonrpc="2.0", id=mid, error=dict(code=-32601, message=f"no method {method!r}"))


def main() -> int:
    for line in sys.stdin:                        # newline-delimited JSON-RPC 2.0 on stdio
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
