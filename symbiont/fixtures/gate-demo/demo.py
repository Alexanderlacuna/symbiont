"""GATE HARNESS — the evidence run for req-P1-SOURCE-HEALTH-AND-AGENT-ADDED-SOURCES.

The row's gate asks for four quotations, and this harness produces all four against the REAL
CLIs (`python3 -m symbiont.health`, `python3 -m symbiont.admission`) — no mock, no stubbed
printer, the same code the MCP tools run:

    1. health for the two live sources, one ok and one DELIBERATELY BROKEN -- the broken one is a
       real run against a dead endpoint, so the record quotes a real run record, not a synthetic
       dict;
    2. the refusal on admission without the gate's word (which quotes the owner's line on record);
    3. one sandbox run of a drafted adapter (and the REFUSED[budget] over its cap);
    4. an agent's remediation recorded, so a non-ok source carries what was DONE about it.

It also probes a URL with nothing usable (NO-SOURCE, with the URLs it read) and drives the MCP
server over stdio, to show the tool words and the CLI words are the same words.

Nothing outside its own workspace is written: every CLI gets --sources-config / --store /
--admission-dir / --adapters-dir pointed at symbiont/store/gate-demo/, so the shipped
symbiont/sources.yml, symbiont/store and symbiont/adapters are untouched (the report diffs
their bytes before and after). The one shipped path it does use is the DRAFTS bay
(symbiont/adapters_draft/), because the point of the bay is that a draft lives there until the
gate admits it — and admission moves it out.

Run:  python3 symbiont/fixtures/gate-demo/demo.py           (workspace wiped and rebuilt)
      workspace: symbiont/store/gate-demo/                  (left on disk as the evidence)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FARM = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))   # .../app-farm
sys.path.insert(0, FARM)

from symbiont import contract                                     # noqa: E402
from symbiont.selftest_sources import build_tree, start_server    # noqa: E402  the local provider

WORK = os.path.join(FARM, "symbiont", "store", "gate-demo")
SHIPPED_CONFIG = contract.DEFAULT_SOURCES
DRAFTS_BAY = os.path.join(FARM, "symbiont", "adapters_draft")
STEP = [0]


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(title: str) -> None:
    STEP[0] += 1
    say()
    say(f"════ STEP {STEP[0]} · {title}")


def run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    """Run a command, quote it verbatim, print what it said, and quote its exit code."""
    shown = " ".join(args)
    say(f"$ {shown}" + ("   (stdin: JSON-RPC lines)" if stdin else ""))
    p = subprocess.run(args, cwd=FARM, capture_output=True, text=True, input=stdin)
    if p.stdout.strip():
        say(p.stdout.rstrip("\n"))
    if p.stderr.strip():
        say("stderr: " + p.stderr.rstrip("\n"))
    say(f"EXIT={p.returncode}")
    return p


def py(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return run([sys.executable, *args], stdin=stdin)


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def main() -> int:
    # ── workspace ────────────────────────────────────────────────────────────────────────────
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    for sub in ("www", "store", "adapters"):
        os.makedirs(os.path.join(WORK, sub), exist_ok=True)
    admission_dir = os.path.join(WORK, "admission")

    header = contract._header(open(SHIPPED_CONFIG, encoding="utf-8").read())

    # ── the local provider (the same fixture tree the source selftest uses) ─────────────────
    build_tree(os.path.join(WORK, "www"))
    # a candidate source for the admission half: a small TSV with real id namespaces + a licence
    src_dir = os.path.join(WORK, "www", "agent-src")
    write(os.path.join(src_dir, "genes.tsv"),
          "symbol\tmgi_id\tensembl_gene_id\tchrom\tstart\tend\n"
          "Mertk\tMGI:96965\tENSMUSG00000014361\t2\t128690000\t128730000\n"
          "Egfr\tMGI:95294\tENSMUSG00000020122\t11\t16750000\t16900000\n"
          "Tp53\tMGI:98834\tENSMUSG00000059552\t11\t69570000\t69588000\n")
    write(os.path.join(src_dir, "licence.html"),
          "<html><body>Demo source: synthetic fixture built by the gate harness in this farm; "
          "no restrictions on use.</body></html>\n")

    srv, host = start_server(os.path.join(WORK, "www"))
    say(f"local provider: {host}  (fixture tree {os.path.join(WORK, 'www')})")

    # the two live sources: ensembl_biomart OK, mgi DELIBERATELY BROKEN (dead endpoint -> 404)
    from symbiont.selftest_sources import base_sources, write_config
    health_cfg = base_sources(host)
    health_cfg["mgi"]["base_url"] = f"{host}/gone/"          # the break: nothing is served there
    health_cfg["mgi"]["endpoints"] = {"metadata": "MRK_ENSEMBL.rpt",
                                      "mrk_ensembl": "MRK_ENSEMBL.rpt",
                                      "mrk_list1_gz": "MRK_List1.rpt.gz"}
    health_cfg["mgi"]["quarantined"] = None
    cfg_health = write_config(os.path.join(WORK, "cfg-health.yml"), health_cfg)

    # the admission half gets its own config: the shipped sources switched OFF, so the registry
    # check at admission is over what actually exists in the demo's adapters dir.
    adm = contract.load_sources(SHIPPED_CONFIG)
    for sid in adm:
        adm[sid]["enabled"] = False
    cfg_adm = write(os.path.join(WORK, "cfg-admission.yml"),
                    header + contract.dump_sources(adm))

    store = os.path.join(WORK, "store")
    common = ["--admission-dir", admission_dir, "--sources-config", cfg_adm,
              "--store", store, "--adapters-dir", os.path.join(WORK, "adapters")]

    # ══ 1 · the run that makes one source ok and one broken ═════════════════════════════════
    step("the REAL loop over the two live sources (one ok, one deliberately broken)")
    py("-m", "symbiont.loop", "--store", store, "--sources-config", cfg_health)

    # ══ 2 · source_health, human lines and the structured record ════════════════════════════
    step("source_health — the CLI an operator reads (the ONE reason per non-ok source)")
    py("-m", "symbiont.health", "--sources-config", cfg_health, "--store", store)

    step("source_health --json — the structured record an agent reads")
    p = py("-m", "symbiont.health", "--sources-config", cfg_health, "--store", store, "--json")
    rec = json.loads(p.stdout)
    for r in rec["sources"]:
        say(json.dumps({k: r[k] for k in ("id", "state", "reason", "count_check",
                                          "rate_headroom", "licence", "staleness")},
                       indent=2, sort_keys=True))

    # ══ 3 · a quarantined source is SKIPPED by the loop, by name ════════════════════════════
    step("the second loop run: the quarantined source is SKIPPED BY NAME, never silently")
    p = py("-m", "symbiont.loop", "--store", store, "--sources-config", cfg_health)
    runs = sorted(f for f in os.listdir(os.path.join(store, "runs")) if f.endswith(".json"))
    newest = json.load(open(os.path.join(store, "runs", runs[-1]), encoding="utf-8"))
    say(f"run record {runs[-1]}: mgi skipped = {json.dumps(newest['sources']['mgi'].get('skipped'))}")
    digest = os.path.join(store, "digest", "mgi.md")
    say(f"digest {digest}:")
    say("  " + [ln for ln in open(digest, encoding="utf-8").read().splitlines()
                if ln.startswith("skipped")][-1])

    # ══ 4 · an agent's remediation is recorded ══════════════════════════════════════════════
    step("the agent ACTS: its remediation is recorded on the record it read")
    py("-m", "symbiont.health", "--sources-config", cfg_health, "--store", store,
       "--record-remediation", "mgi", "--action", "replace",
       "--note", "404 on every endpoint since the mirror moved; proposing the mirror at "
                 "/mirror/MRK_ENSEMBL.rpt and a readmit card")

    # ══ 5 · source_probe on a URL with nothing usable: NO-SOURCE, with the URLs it read ═════
    step("source_probe on nothing usable -> NO-SOURCE, naming the URLs it read")
    py("-m", "symbiont.admission", "probe", f"{host}/agent-src/missing.tsv",
       "--id", "agent_src_dead", *common)

    # ══ 6 · the admission pipeline, agent work: probe · evaluate · draft · sandbox · card ═══
    step("source_probe — a usable body becomes a candidate")
    py("-m", "symbiont.admission", "probe", f"{host}/agent-src/genes.tsv",
       "--id", "agent_src", "--licence-url", f"{host}/agent-src/licence.html", *common)

    step("source_evaluate — licence read+hashed, accessions, rate limit, size, cadence")
    py("-m", "symbiont.admission", "evaluate", "agent_src", *common)

    step("source_draft — adapter skeleton + THE CONTRACT'S LOAD CHECK")
    py("-m", "symbiont.admission", "draft", "agent_src", *common,
       "--drafts-dir", DRAFTS_BAY)

    step("source_sandbox over budget (--budget-requests 1) -> REFUSED[budget]")
    py("-m", "symbiont.admission", "sandbox", "agent_src", *common,
       "--drafts-dir", DRAFTS_BAY, "--budget-requests", "1")

    step("source_sandbox — the drafted adapter run into a THROWAWAY store")
    py("-m", "symbiont.admission", "sandbox", "agent_src", *common, "--drafts-dir", DRAFTS_BAY)

    step("source_card — the admission card, and the registration enabled:false")
    py("-m", "symbiont.admission", "card", "agent_src", *common,
       "--question", "which MGI/Ensembl ids does this provider add to the mouse gene view?")
    say(f"card on disk: {os.path.join(admission_dir, 'agent_src', 'card.md')}")
    say(open(os.path.join(admission_dir, "agent_src", "card.md"), encoding="utf-8").read().rstrip())

    # ══ 7 · THE GATED STEP ══════════════════════════════════════════════════════════════════
    step("source_admit WITHOUT the gate's word -> REFUSED[gate], the owner's line quoted")
    py("-m", "symbiont.admission", "admit", "agent_src", *common)
    say("sources.yml after the refusal (the candidate is still enabled: false):")
    lines = open(cfg_adm, encoding="utf-8").read().splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith("- id:"):
            sid = ln.split("id:")[1].strip()
            en = next((x.split("enabled:")[1].strip() for x in lines[i:i + 4]
                       if x.strip().startswith("enabled:")), "?")
            say(f"  {sid}: enabled={en}")

    gate_word = [ln.strip() for ln in open(os.path.join(FARM, "symbiont", "admission-gate.txt"),
                                           encoding="utf-8").read().splitlines()
                 if ln.strip() and not ln.strip().startswith("#")][0]
    step("source_admit WITH the gate's word, quoted from the file on record")
    py("-m", "symbiont.admission", "admit", "agent_src", *common, "--gate", gate_word)

    # ══ 8 · the MCP tools are the same CLI ══════════════════════════════════════════════════
    step("the MCP server: tools/list names the seven, tools/call returns the CLI's own words")
    lines = [
        dict(jsonrpc="2.0", id=1, method="initialize",
             params=dict(protocolVersion="2025-06-18")),
        dict(jsonrpc="2.0", id=2, method="tools/list"),
        dict(jsonrpc="2.0", id=3, method="tools/call",
             params=dict(name="source_health",
                         arguments=dict(sources_config=cfg_health, store=store))),
        dict(jsonrpc="2.0", id=4, method="tools/call",
             params=dict(name="source_admit", arguments=dict(
                 id="agent_src", sources_config=cfg_adm, admission_dir=admission_dir,
                 store=store, adapters_dir=os.path.join(WORK, "adapters")))),
    ]
    p = run([sys.executable, "-m", "symbiont.mcpserver"],
            stdin="".join(json.dumps(m) + "\n" for m in lines))
    replies = [json.loads(ln) for ln in p.stdout.splitlines() if ln.strip()]
    say(f"replies: {[r.get('id') for r in replies]}")
    say(f"tools/list: {[t['name'] for t in replies[1]['result']['tools']]}")
    say("tools/call source_health isError="
        f"{replies[2]['result']['isError']} cli_exit={replies[2]['result']['cli_exit']}")
    say("tools/call source_health first line: "
        f"{replies[2]['result']['content'][0]['text'].splitlines()[0]}")
    say("tools/call source_admit (no gate word) isError="
        f"{replies[3]['result']['isError']} cli_exit={replies[3]['result']['cli_exit']}")
    say("  " + replies[3]['result']['content'][0]['text'].splitlines()[0])

    say()
    say(f"══ the workspace is the evidence: {WORK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
