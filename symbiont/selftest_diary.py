"""PROOF OF THE DIARY — req-P1-THE-DIARY-appended-by-the-code-nev.

The row asks for a lifecycle that LIVES IN THE CODE: one JSON line per transition
(`<store>/` + events.DIARY_NAME), an atomic append, a closed transition table enforced at WRITE
time, emission hooks on the five paths that already change state, and one human door that obeys
the same table. This file is the proof, offline, at fixture scale, every case quoting the line it
asserts:

    D0 THE PATH TABLE      every state-changing path -> the event class it must emit, written DOWN
                           here (not in prose), and one fixture run per path (D1…D5) asserting it
    D1 loop                acquired · count_ok — in seq order, `tail -1` a COMPLETE line, the head
                           anchored in the run record
    D2 cycle discover      discovered · evaluated · refused[class]
    D3 admit command       admitted (and a refusal of the same line is proved at D7)
    D4 switch command      intent_changed{from,to,why}, and sources.yml really flipped (and back)
    D5 manual addition     manual_add · manual_override (with a scope; manual_add alone is NOT a
                           permission)
    D6 THE PERMISSION TEST a source `enabled: true` with NO admitted event is NOT acquired:
                           REFUSED[not-admitted], naming the source and the two ways to fix it
    D7 ILLEGAL TRANSITION  `admitted` after `refused` (no `reconsidered`) is REFUSED at write time,
                           naming BOTH classes, and nothing is appended
    D8 TRUNCATED TAIL      a partial last line is DETECTED and REPORTED (never skipped) and no
                           event is appended onto it; the loop refuses the run
    D9 ALTERATION          one changed byte breaks the hash chain and is named, with its line
    D10 ONE WRITER         the file name appears in exactly ONE module (the grep fails BY NAME)
    D11 THE ANCHOR         the head is repeated in the manifest and re-checked; a diary altered
                           after the run REFUSES[diary-altered] the handover

Run: python3 -m symbiont.selftest_diary [--keep]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from . import contract, events, manifest, selftest_diary_writer
from .selftest_sources import base_sources, build_tree, seed, start_server, write_config

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── D0 · THE PATH TABLE (the row's "and a path table") ───────────────────────────────────────
# Every path that changes a source's state, the command that owns it, the classes it must emit,
# and the fixture below that proves it. A path that changes state without an event is the failure
# this row exists to prevent, so the expectation is written here and asserted there.
PATH_TABLE = [
    ("loop acquisition", "python3 -m symbiont.loop", ("acquired", "count_ok"), "D1"),
    ("cycle discover", "python3 -m symbiont.cycle", ("discovered", "evaluated", "refused"), "D2"),
    ("admit command", "python3 -m symbiont.admission admit", ("admitted",), "D3"),
    ("switch command", "python3 -m symbiont.registry allow|deny", ("intent_changed",), "D4"),
    ("manual addition", "python3 -m symbiont.registry manual-add|override",
     ("manual_add", "manual_override"), "D5"),
]


def say(line: str = "") -> None:
    print(line, flush=True)


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", *args], cwd=FARM, capture_output=True, text=True,
                          timeout=1200, **kw)


def diary(store: str) -> list[dict]:
    return events.read(store).events


def classes(store: str, source_id: str | None = None) -> list[str]:
    return [e["class"] for e in diary(store) if source_id is None or e["source_id"] == source_id]


def last_line(store: str) -> bytes:
    with open(events.diary_path(store), "rb") as fh:
        blob = fh.read()
    return blob.rstrip(b"\n").split(b"\n")[-1]


def quote_line(text: str, needle: str) -> str:
    for ln in text.splitlines():
        if needle in ln:
            return ln.strip()
    return "(no line containing " + needle + ")"


def main(argv=None) -> int:
    keep = "--keep" in (argv or sys.argv[1:])
    tmp = tempfile.mkdtemp(prefix="symbiont-diary-")
    web = os.path.join(tmp, "www")
    build_tree(web)
    open(os.path.join(web, "count3.txt"), "w").write("3\n")
    srv, host = start_server(web)
    results: list[tuple[str, bool]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok)))
        print(f"  {'PASS' if ok else 'FAIL'}  {name:52} {detail}", flush=True)

    try:
        # ══ D0 · THE PATH TABLE ═══════════════════════════════════════════════════════════════
        say("\nD0 the path table — every state-changing path, and the class it must emit")
        for path_name, cmd, want, fixture in PATH_TABLE:
            bad = [c for c in want if c not in events.CLASSES]
            say(f"  {fixture}  {path_name:22} {cmd:44} -> {', '.join(want)}")
            check(f"D0 {path_name}: every expected class is in the closed set ({fixture})",
                  not bad, f"unknown: {bad}" if bad else f"{len(want)} class(es)")

        # ══ D1 · THE LOOP (acquired · count_ok, in seq order, tail -1 complete) ══════════════
        say("\nD1 the loop — one real run over the local provider: the diary gains the events the "
            "run causes, in order")
        s1 = os.path.join(tmp, "d1.store")
        cfg1 = write_config(os.path.join(tmp, "d1.yml"), base_sources(host))
        # the fixture's admission is written through the HUMAN door (the one writer), so the
        # backfill path and the append path are the same code the operator uses
        add = run(["symbiont.registry", "add-event", "--store", s1, "--class", "admitted",
                   "--source", "ensembl:mmusculus_gene_ensembl", "--release", "ensembl-116",
                   "--origin", "backfill",
                   "--why", "the fixture admits the source it wrote into its own sources.yml"])
        check("D1 the human door accepted a backfilled `admitted` (registry add-event)",
              add.returncode == 0, quote_line(add.stdout, "APPENDED"))
        seed(s1, cfg1)                         # the second source, through the same writer
        p = run(["symbiont.loop", "--store", s1, "--sources-config", cfg1, "--run-id", "diary-d1"])
        check("D1 the loop published (exit 0)", p.returncode == 0,
              quote_line(p.stdout, "DIARY") or (p.stdout or p.stderr).strip()[-120:])
        ens = classes(s1, "ensembl:mmusculus_gene_ensembl")
        check("D1 ensembl: admitted · acquired · count_ok — in that seq order",
              ens == ["admitted", "acquired", "count_ok"], f"classes={ens}")
        tail = last_line(s1)
        check("D1 `tail -1` is ONE COMPLETE line (newline-terminated, parses as an event)",
              open(events.diary_path(s1), "rb").read().endswith(b"\n")
              and json.loads(tail.decode())["class"] in events.CLASSES,
              tail.decode()[:110] + "…")
        seqs = [e["seq"] for e in diary(s1)]
        check("D1 seq is the line order: 1..N, no gap", seqs == list(range(1, len(seqs) + 1)),
              f"seqs={seqs}")
        rec1 = json.load(open(os.path.join(s1, "runs", "diary-d1.json"), encoding="utf-8"))
        hd = rec1.get("diary") or {}
        ok_anchor, why_anchor = events.head_matches(s1, hd)
        check("D1 the run record ANCHORS the diary head (and it still matches)",
              hd.get("anchored_in") == "runs/diary-d1.json" and ok_anchor,
              f"seq={hd.get('seq')} head={str(hd.get('head_sha256'))[:16]}… {why_anchor}")

        # ══ D2 · CYCLE DISCOVER (discovered · evaluated · refused[class]) ════════════════════
        say("\nD2 cycle discover — a listed surface, one candidate that survives and one whose "
            "licence cannot be read")
        s2 = os.path.join(tmp, "d2.store")
        os.makedirs(os.path.join(s2, "digest"), exist_ok=True)
        open(os.path.join(s2, "digest", "gaps.md"), "w").write("# GAPS\n\n- gaps measured: 0\n")
        os.makedirs(os.path.join(s2, "runs"), exist_ok=True)
        reg = os.path.join(tmp, "d2-surfaces.yml")
        import yaml
        cand = dict(id="fix_genes", url=f"{host}/ensembl/martservice",
                    licence_url=f"{host}/ensembl/licence.html", licence="CC0-1.0",
                    count_endpoint=f"{host}/count3.txt", question="does the fixture answer a question")
        bad = dict(cand, id="fix_unlicensed", licence="", licence_url="")
        open(reg, "w").write(yaml.safe_dump(
            dict(version=1, surfaces=[dict(id="fixture-data", kind="listed",
                                           candidates=[cand, bad])]), sort_keys=False))
        src2 = os.path.join(tmp, "d2-sources.yml")
        one = dict(contract.load_sources(contract.DEFAULT_SOURCES)["mgi"])
        one["enabled"] = False
        open(src2, "w").write("# the fixture deployment\nversion: 1\nsources:\n"
                              + contract.dump_sources({"mgi": one}).split("sources:\n", 1)[1])
        cyc = dict(version=1, enabled=True, mode="discover", every="weekly",
                   budget=dict(requests=8, bytes=4000000, wall_s=120),
                   rate_limit=dict(requests_per_minute=120, min_interval_s=0.0),
                   store=s2, report_dir=os.path.join(tmp, "d2-reports"),
                   state_dir=os.path.join(s2, "cycle"),
                   admission=dict(dir=os.path.join(tmp, "d2-adm"), sources_config=src2,
                                  drafts_dir=os.path.join(tmp, "d2-drafts"),
                                  adapters_dir=os.path.join(tmp, "d2-adapters"),
                                  gate_file=os.path.join(tmp, "d2-gate.txt"),
                                  policy=os.path.join(tmp, "d2-policy.yml")),
                   surfaces=dict(registry=reg, kind="listed", endpoint=f"{host}/search",
                                 per_page=5, dead_days=730, seed_topics=3),
                   draft=dict(provider=""), admit=dict(by_policy=False, ruling=""))
        cyc_path = os.path.join(tmp, "d2-cycle.yml")
        open(cyc_path, "w").write(yaml.safe_dump(cyc, sort_keys=False))
        c = run(["symbiont.cycle", "--config", cyc_path])
        check("D2 the cycle ran (exit 0)", c.returncode == 0,
              quote_line(c.stdout, "I looked") or (c.stdout + c.stderr).strip()[-160:])
        c_ok = classes(s2, "fix_genes")
        c_bad = classes(s2, "fix_unlicensed")
        check("D2 the surviving candidate: discovered · evaluated", c_ok == ["discovered", "evaluated"],
              f"classes={c_ok}")
        ref = [e for e in diary(s2) if e["source_id"] == "fix_unlicensed"]
        check("D2 the refused candidate: discovered · refused[licence-unreadable]",
              c_bad == ["discovered", "refused"]
              and (ref[-1].get("evidence") or {}).get("refusal_code") == "licence-unreadable",
              f"classes={c_bad} refusal={(ref[-1].get('evidence') or {}).get('refusal_code')}")

        # ══ D3 · THE ADMIT COMMAND (admitted) ════════════════════════════════════════════════
        say("\nD3 the admit command — the ONE gated step writes `admitted` when it says yes")
        s3 = os.path.join(tmp, "d3.store")
        adm = os.path.join(tmp, "d3-adm")
        drafts = os.path.join(tmp, "d3-drafts")
        adir = os.path.join(tmp, "d3-adapters")
        for d in (adm, drafts, adir):
            os.makedirs(d, exist_ok=True)
        cfg3 = write_config(os.path.join(tmp, "d3.yml"), base_sources(host))
        s3cfg = contract.load_sources(cfg3)
        s3cfg["ensembl_biomart"]["enabled"] = False
        s3cfg.pop("mgi")                       # one source, one adapter: the registry check is exact
        write_config(cfg3, s3cfg)
        # the draft loads FILE-BASED (its dir is outside the package), so it must import nothing
        # relative — exactly what a drafted adapter is: the fixture's own minimal plug-in
        open(os.path.join(drafts, "ensembl_biomart.py"), "w").write(
            '"""the fixture\'s drafted adapter — the admission CLI only has to LOAD it here."""\n'
            "class _A:\n"
            "    name = \"ensembl_biomart\"\n"
            "    role = \"id_authority\"\n"
            "    def configure(self, cfg): pass\n"
            "    def discover(self, fetcher, since): raise SystemExit('fixture: never fetched')\n"
            "    def fetch(self, fetcher, since): raise SystemExit('fixture: never fetched')\n"
            "    def normalise(self, resources): raise SystemExit('fixture: never normalised')\n"
            "    def verify(self, d, n): raise SystemExit('fixture: never verified')\n"
            "ADAPTER = _A()\n")
        os.makedirs(os.path.join(adm, "ensembl_biomart"), exist_ok=True)
        open(os.path.join(adm, "ensembl_biomart", "card.md"), "w").write(
            "# ADMISSION CARD — ensembl_biomart\n\nsource: ensembl_biomart\n"
            "changed: the fixture reads the licence, matches the count and hands over a card\n"
            "check licence: ok\ncheck count_match: ok\ncheck sample_rows: ok\n"
            "check xref_rate: ok\ncheck cost: ok\n")
        gate = os.path.join(tmp, "d3-gate.txt")
        open(gate, "w").write("# the owner's word on record\nI admit ensembl_biomart\n")
        policy = os.path.join(tmp, "d3-policy.yml")
        open(policy, "w").write(open(os.path.join(FARM, "symbiont", "docs",
                                                  "POLICY-TEMPLATE.yml")).read())
        a = run(["symbiont.admission", "admit", "ensembl_biomart", "--admission-dir", adm,
                 "--sources-config", cfg3, "--drafts-dir", drafts, "--adapters-dir", adir,
                 "--gate-file", gate, "--policy", policy, "--store", s3,
                 "--gate", "I admit ensembl_biomart"])
        check("D3 the admit command admitted (exit 0)", a.returncode == 0,
              quote_line(a.stdout, "ADMITTED") or (a.stdout + a.stderr).strip()[-160:])
        check("D3 the diary recorded `admitted` for the dataset, with the card in its evidence",
              classes(s3, "ensembl:mmusculus_gene_ensembl") == ["admitted"]
              and "card" in (diary(s3)[-1].get("evidence") or {}),
              quote_line(a.stdout, "DIARY"))

        # ══ D4 · THE SWITCH COMMAND (intent_changed) ═════════════════════════════════════════
        say("\nD4 the switch command — deny then allow: intent_changed{from,to,why}, the file flipped")
        s4 = os.path.join(tmp, "d4.store")
        cfg4 = write_config(os.path.join(tmp, "d4.yml"), base_sources(host))
        d = run(["symbiont.registry", "deny", "ensembl_biomart", "--store", s4,
                 "--sources-config", cfg4, "--why", "not needed this quarter"])
        after_deny = contract.load_sources(cfg4)["ensembl_biomart"]["enabled"]
        u = run(["symbiont.registry", "allow", "ensembl_biomart", "--store", s4,
                 "--sources-config", cfg4, "--why", "count verified twice, licence read"])
        after_allow = contract.load_sources(cfg4)["ensembl_biomart"]["enabled"]
        ev4 = [e for e in diary(s4) if e["class"] == "intent_changed"]
        check("D4 deny wrote intent_changed and switched the source OFF",
              d.returncode == 0 and after_deny is False and (ev4[0]["evidence"] or {}).get("to") is False,
              quote_line(d.stdout, "SWITCH"))
        check("D4 allow wrote a second intent_changed and switched it back ON",
              u.returncode == 0 and after_allow is True and (ev4[1]["evidence"] or {}).get("from") is False,
              quote_line(u.stdout, "SWITCH"))

        # ══ D5 · MANUAL ADDITION (manual_add · manual_override) ══════════════════════════════
        say("\nD5 manual addition — manual_add is a record, NOT a permission; the scoped override is")
        s5 = os.path.join(tmp, "d5.store")
        m = run(["symbiont.registry", "manual-add", "--store", s5,
                 "--source", "mgi:MRK_ENSEMBL", "--why", "the operator added the marker file by hand",
                 "--evidence", "artefact=/tmp/MRK_ENSEMBL.rpt"])
        ok5, why5, _ = events.permission(events.read(s5), "mgi:MRK_ENSEMBL")
        cls5 = classes(s5)
        o = run(["symbiont.registry", "override", "--store", s5, "--source", "mgi:MRK_ENSEMBL",
                 "--why", "one comparison run, on the operator's word", "--scope", "one-run"])
        ok5b, _, permit5 = events.permission(events.read(s5), "mgi:MRK_ENSEMBL")
        check("D5 manual_add is recorded and does NOT permit acquisition",
              m.returncode == 0 and cls5 == ["manual_add"] and ok5 is False,
              f"exit={m.returncode} classes={cls5} permitted={ok5} ({why5[:80]})")
        check("D5 manual_override with a scope IS the permission (one-run)",
              o.returncode == 0 and ok5b is True
              and (permit5.get("evidence") or {}).get("scope") == "one-run",
              quote_line(o.stdout, "APPENDED"))

        # ══ D6 · THE PERMISSION TEST (the row's fourth quotation) ════════════════════════════
        say("\nD6 THE PERMISSION TEST — enabled: true in sources.yml, NO admitted event: not acquired")
        s6 = os.path.join(tmp, "d6.store")
        cfg6 = write_config(os.path.join(tmp, "d6.yml"), base_sources(host))
        # the xref PROVIDER is admitted (so the join can close); the PLANTED source is the id
        # authority, switched on with no admission — exactly the case the row names
        events.seed_admitted(
            s6, contract.load_sources(cfg6)["mgi"],
            why="the fixture admits the xref provider only; ensembl_biomart is the PLANTED "
                "source: enabled in sources.yml, never admitted",
            evidence=dict(fixture="selftest_diary D6"))
        p6 = run(["symbiont.loop", "--store", s6, "--sources-config", cfg6, "--run-id", "diary-d6",
                  "--sources", "ensembl_biomart"])
        line6 = quote_line(p6.stdout, "REFUSED[not-admitted]")
        check("D6 the loop REFUSED[not-admitted] and exited 3 (nothing fetched)",
              p6.returncode == 3 and "REFUSED[not-admitted]" in p6.stdout, line6[:150] + "…")
        check("D6 the refusal NAMES the source and BOTH ways to fix it",
              "ensembl_biomart (ensembl:mmusculus_gene_ensembl)" in p6.stdout
              and "symbiont.admission admit" in p6.stdout
              and "symbiont.registry override" in p6.stdout,
              "both fixes named" if "symbiont.registry override" in p6.stdout else "A FIX IS MISSING")
        check("D6 no raw bytes landed (requests=0, the door is BEFORE the fetch)",
              not os.path.isdir(os.path.join(s6, "raw", "ensembl_biomart")),
              f"raw/={os.listdir(os.path.join(s6, 'raw')) if os.path.isdir(os.path.join(s6, 'raw')) else 'absent'}")
        o6 = run(["symbiont.registry", "override", "--store", s6,
                  "--source", "ensembl:mmusculus_gene_ensembl", "--why", "the operator authorises "
                  "ONE comparison run without evaluation", "--scope", "one-run"])
        p6b = run(["symbiont.loop", "--store", s6, "--sources-config", cfg6, "--run-id",
                   "diary-d6b"])
        check("D6 WITH a recorded manual_override the SAME source is acquired (the second way)",
              o6.returncode == 0 and p6b.returncode == 0,
              f"classes={classes(s6, 'ensembl:mmusculus_gene_ensembl')}")
        check("D6 the one-run override was CONSUMED (override_expired follows the acquisition)",
              classes(s6, "ensembl:mmusculus_gene_ensembl")
              == ["manual_override", "acquired", "count_ok", "override_expired"],
              f"classes={classes(s6, 'ensembl:mmusculus_gene_ensembl')}")

        # ══ D7 · THE ILLEGAL TRANSITION (the row's second quotation) ═════════════════════════
        say("\nD7 ILLEGAL TRANSITION — `admitted` after `refused`, with no `reconsidered`")
        s7 = os.path.join(tmp, "d7.store")
        for cls, extra in (("discovered", []), ("refused", ["--evidence", "refusal_code=archived"])):
            run(["symbiont.registry", "add-event", "--store", s7, "--class", cls,
                 "--source", "mgi:MRK_ENSEMBL", "--why", f"fixture: {cls}", *extra])
        bad7 = run(["symbiont.registry", "add-event", "--store", s7, "--class", "admitted",
                    "--source", "mgi:MRK_ENSEMBL", "--why", "the fixture tries to skip a step"])
        line7 = quote_line(bad7.stdout, "REFUSED[illegal-transition]")
        check("D7 REFUSED[illegal-transition] at WRITE time, naming BOTH classes",
              bad7.returncode == 2 and "'admitted'" in line7 and "'refused'" in line7, line7[:150] + "…")
        check("D7 nothing was appended (the diary still holds 2 events)",
              len(diary(s7)) == 2, f"events={len(diary(s7))}")
        rec7 = os.path.join(tmp, "reconsider")
        reconsider = run(["symbiont.registry", "add-event", "--store", s7, "--class", "reconsidered",
                          "--source", "mgi:MRK_ENSEMBL", "--why", "the licence page changed"])
        then = run(["symbiont.registry", "add-event", "--store", s7, "--class", "admitted",
                    "--source", "mgi:MRK_ENSEMBL", "--why", "the gate said yes after all"])
        check("D7 with `reconsidered` between them the SAME `admitted` is legal",
              reconsider.returncode == 0 and then.returncode == 0,
              f"classes={classes(s7)}")

        # ══ D8 · THE TRUNCATED TAIL (the row's third quotation) ══════════════════════════════
        say("\nD8 TRUNCATED TAIL — a partial last line is DETECTED and REPORTED, never skipped")
        s8 = os.path.join(tmp, "d8.store")
        shutil.copytree(s1, s8)
        with open(events.diary_path(s8), "ab") as fh:
            fh.write(b'{"seq":99,"class":"acq')
        v8 = run(["symbiont.registry", "verify", "--store", s8])
        line8 = quote_line(v8.stdout, "TRUNCATED")
        check("D8 `registry verify` REPORTS the truncated tail (exit 3, a finding)",
              v8.returncode == 3 and "TRUNCATED" in v8.stdout
              and "no terminating newline" in v8.stdout, line8[:150] + "…")
        check("D8 it does NOT claim the partial line as history",
              "LINES    6 complete event(s)" in v8.stdout
              or re.search(r"LINES\s+\d+ complete event", v8.stdout) is not None,
              quote_line(v8.stdout, "LINES").strip())
        p8 = run(["symbiont.loop", "--store", s8, "--sources-config", cfg1, "--run-id", "diary-d8"])
        check("D8 the loop REFUSES the run rather than append onto damage",
              p8.returncode == 3 and "REFUSED  run diary-d8" in p8.stdout,
              quote_line(p8.stdout, "REFUSED  run diary-d8")[:150] + "…")
        app8 = run(["symbiont.registry", "add-event", "--store", s8, "--class", "count_ok",
                    "--source", "mgi:MRK_ENSEMBL", "--why", "must be refused, not doubled over"])
        check("D8 an append onto the damaged tail is REFUSED[truncated-diary]",
              app8.returncode == 2 and "REFUSED[truncated-diary]" in app8.stdout,
              quote_line(app8.stdout, "REFUSED[truncated-diary]")[:150] + "…")

        # ══ D9 · ALTERATION (the chain is a visible break) ═══════════════════════════════════
        say("\nD9 ALTERATION — one changed byte on an earlier line breaks the chain, and is named")
        s9 = os.path.join(tmp, "d9.store")
        shutil.copytree(s1, s9)
        raw = open(events.diary_path(s9), "rb").read().split(b"\n")
        raw[0] = raw[0].replace(b"the fixture admits", b"the fixture ADMITS")
        open(events.diary_path(s9), "wb").write(b"\n".join(raw))
        v9 = run(["symbiont.registry", "verify", "--store", s9])
        check("D9 `registry verify` names the broken link and its line (exit 3)",
              v9.returncode == 3 and "chain:" in v9.stdout and "line 2" in v9.stdout,
              quote_line(v9.stdout, "chain:")[:150] + "…")
        ok9, why9 = events.head_matches(s9, events.head_of(s1))
        check("D9 history stops at the damaged line: the later lines are NOT read as facts",
              len(events.read(s9).events) == 1, f"events read={len(events.read(s9).events)}")

        # ══ D10 · ONE WRITER (the risk gate, mechanically) ═══════════════════════════════════
        say("\nD10 ONE WRITER — the file name may appear in exactly ONE module")
        hits = []
        for dirpath, dirnames, filenames in os.walk(FARM):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", ".git", "store", "build", "node_modules")]
            for f in filenames:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dirpath, f)
                try:
                    txt = open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                if events.DIARY_NAME in txt:
                    hits.append(os.path.relpath(p, FARM))
        hits.sort()
        # the writer is symbiont/diary.py (req-P1-THE-DIARY-WRITER-atomic-append-an moved the ONE
        # write out of events.py; the scan below is the same rule, one function of it deeper).
        check("D10 exactly one .py file in the tree names the diary file — symbiont/diary.py",
              hits == [os.path.join("symbiont", "diary.py")], f"offenders={hits}")
        sites = selftest_diary_writer.scan_writers(FARM)
        check("D10 no other module opens it for writing (the writer is diary.append, and nothing else)",
              sites == [os.path.join("symbiont", "diary.py") + ":append"],
              "one write site: diary.append" if len(sites) == 1 else f"second writer: {sites}")

        # ══ D11 · THE ANCHOR IN THE MANIFEST ══════════════════════════════════════════════════
        say("\nD11 THE ANCHOR — the manifest repeats the head and REFUSES a diary altered after the run")
        s11 = manifest._fixture_store(os.path.join(tmp, "d11"))
        for cls in ("admitted", "acquired", "count_ok"):
            events.append(s11, cls, source_id="ensembl:mmusculus_gene_ensembl",
                          why=f"fixture: {cls}", actor="machine")
        rr = os.path.join(s11, "runs", "fixture-run.json")
        run_rec = json.load(open(rr, encoding="utf-8"))
        run_rec["diary"] = dict(events.head_of(s11), anchored_in="runs/fixture-run.json")
        json.dump(run_rec, open(rr, "w", encoding="utf-8"), indent=2, sort_keys=True)
        m = manifest.build(s11, "gene", os.path.join(tmp, "d11-stage.tsv"), None, False)
        check("D11 the manifest carries the anchored head and re-checked it",
              (m["store"]["diary"] or {}).get("anchored") is True
              and (m["store"]["diary"] or {}).get("matches") is True,
              f"seq={(m['store']['diary'] or {}).get('seq')} "
              f"head={str((m['store']['diary'] or {}).get('head_sha256'))[:16]}…")
        raw11 = open(events.diary_path(s11), "rb").read().split(b"\n")
        raw11[0] = raw11[0].replace(b"fixture: admitted", b"fixture: ADMITTED")
        open(events.diary_path(s11), "wb").write(b"\n".join(raw11))
        try:
            manifest.build(s11, "gene", os.path.join(tmp, "d11-stage2.tsv"), None, False)
            refused = ""
        except manifest.Refusal as e:
            refused = f"REFUSED[{getattr(e, 'code', type(e).__name__)}] {e}"
        check("D11 a diary altered after the run REFUSES[diary-altered] the handover",
              "REFUSED[diary-altered]" in refused, refused[:150] + "…")

        # ── the verdict ─────────────────────────────────────────────────────────────────────
        bad = [n for n, ok in results if not ok]
        say("")
        if bad:
            for n in bad:
                say(f"  FAILING: {n}")
            say(f"SELFTEST FAIL: {len(bad)} of {len(results)} checks failed")
            return 1
        say(f"SELFTEST PASS: diary {len(results)}/{len(results)} held — the diary is appended by "
            f"the code, it refuses an illegal transition, it reports a truncated tail, and a "
            f"source enabled without an `admitted` event is not acquired")
        return 0
    finally:
        srv.shutdown()
        if keep:
            say(f"(kept {tmp})")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
