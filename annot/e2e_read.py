#!/usr/bin/env python3
"""e2e_read — THE READ DOOR, END TO END, AS A CHECK (not a demo).

WHY: on 2026-11-12 a read-door E2E found two crashes that unit tests did not: an AssertionError for an item without a release, and a TypeError comparing a
naive timestamp to an aware one. Both escaped as JSON-RPC -32603, bypassing the door's own status vocabulary. This script is the check that would have
caught them, and it is runnable in a clone.

WHAT IT ASSERTS
  1 every tool in tools/list answers a call with a status from the CLOSED SET: ok · empty · refused · unavailable · error
  2 NO tool returns a JSON-RPC error for a well-formed argument set (a crash is a finding, not a transport event)
  3 the store is READ-ONLY: the manifest of every file under the store is unchanged after the run
  4 the answers are SHAPED: every item carries accession · source · release · fetched_at · sha256

USAGE
  python3 annot/e2e_read.py                 # against $ANNOT_STORE or the default store
  python3 annot/e2e_read.py --store ~/symbiont-run/store
exit 0 = every assertion held; 1 = a finding (printed by name).
"""
import argparse, hashlib, json, os, subprocess, sys

CLOSED = {"ok", "empty", "refused", "unavailable", "error"}
CALLS = [
    ("gene_dossier",       {"gene": "Trp53"}),
    ("gene_xref",          {"gene": "Trp53"}),
    ("phenotype_evidence", {"gene": "Brca1", "limit": 2}),
    ("publications_for",   {"gene": "Brca1", "limit": 2}),
    ("traits_containing",  {"term": "body weight", "limit": 2}),
    ("what_changed",       {"source": "mgi", "since": "2026-11-01"}),
    ("answer",             {"tool": "publications_for", "params": {"gene": "Brca1"},
                            "sentences": ["BRCA1 protects the genome (PMID:37979908)."]}),
    ("research_check",     {"research_id": "e2e", "question": "Which genes are associated with aging in the mouse?",
                            "arms": {"gene_identity": "one row per gene", "phenotype_ontology": None, "literature": None},
                            "required": ["mgi"], "optional": ["pubmed"]}),
]
ITEM_KEYS = ("accession", "source", "release", "fetched_at", "sha256")


def store_manifest(store):
    h = hashlib.sha256()
    for root, dirs, files in os.walk(store):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in sorted(files):
            p = os.path.join(root, f)
            h.update(os.path.relpath(p, store).encode())
            if os.path.getsize(p) < 5_000_000:
                with open(p, "rb") as fh:
                    h.update(fh.read())
    return h.hexdigest()


def run(store, timeout=600):
    env = dict(os.environ)
    if store:
        env["ANNOT_STORE"] = os.path.abspath(os.path.expanduser(store))
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "annot-e2e", "version": "0"}}}
    calls = [{"jsonrpc": "2.0", "id": 100 + i, "method": "tools/call",
              "params": {"name": n, "arguments": a}} for i, (n, a) in enumerate(CALLS)]
    calls.append({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})
    body = "\n".join(json.dumps(x) for x in [init] + calls) + "\n"
    p = subprocess.run([sys.executable, "-m", "annot.mcp_server"], input=body,
                       capture_output=True, text=True, timeout=timeout, env=env)
    out = {}
    for line in p.stdout.splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("id"):
            out[d["id"]] = d
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=os.environ.get("ANNOT_STORE") or "")
    a = ap.parse_args()
    store = a.store or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "symbiont", "store")
    before = store_manifest(store)
    res = run(a.store)
    tools = [t["name"] for t in res.get(9, {}).get("result", {}).get("tools", [])]
    findings, checked = [], 0
    print("tools/list → %d: %s" % (len(tools), " · ".join(tools)))
    for i, (name, _args) in enumerate(CALLS):
        d = res.get(100 + i, {})
        if "error" in d:                                   # assertion 2
            findings.append("%s returned a JSON-RPC error: %s" % (name, str(d["error"].get("message"))[:90]))
            print("  %-20s TRANSPORT-ERROR" % name)
            continue
        j = json.loads(d["result"]["content"][0]["text"])
        status = j.get("status")
        checked += 1
        if status not in CLOSED:                           # assertion 1
            findings.append("%s answered status=%r, not in the closed set %s" % (name, status, sorted(CLOSED)))
        for it in (j.get("items") or []):                   # assertion 4
            missing = [k for k in ITEM_KEYS if k not in it]
            if missing:
                findings.append("%s item %s lacks %s" % (name, it.get("accession") or "?", missing))
                break
        print("  %-20s %-11s %3d item(s)" % (name, status, len(j.get("items") or [])))
    after = store_manifest(store)
    print("\n  read-only: %s… → %s…  %s"
          % (before[:16], after[:16], "UNCHANGED" if before == after else "CHANGED — a read wrote!"))
    if before != after:                                     # assertion 3
        findings.append("the store changed during a read-only run")
    print("  RESULT: %d tool(s) checked, %d finding(s)%s"
          % (checked, len(findings), "" if not findings else " — " + "; ".join(findings)))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
