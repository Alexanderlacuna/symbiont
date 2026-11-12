"""THE FOUR DIGESTS — the selftest (req-P1-THE-FOUR-DIGESTS-schemas-and-cadence).

Proves the schema, the cadence and the refusal rules against REAL RUNS of the real loop — a
local provider on 127.0.0.1 whose release genuinely MOVES between run 1 and run 2, so the
what-changed line is COMPUTED from two real runs, not invented:

  D1 per-run digest      run dg-1 writes digest/<source>.md blocks with all five schema lines
  D2 real change         the provider adds a gene and withdraws a marker between runs -> run dg-2
                         digest says markers +1, retired +1, releases moved=yes — from the two
                         run records on disk, reproduced by the embedded queries
  D3 idempotency         run dg-3 (no provider change) -> data_bytes=0 and every delta +0
  D4 gaps digest         all four gap classes with counts AND the queries that produced them
  D5 suggest digest      ranked options with cost, licence, question — and it never decides
  D6 daily refresh       `digests write` (the daily pass) re-measures gaps+suggest, verify holds
  R1 no numbers          a prose digest is REFUSED[digest-no-numbers], exit 3
  R2 not reproducible    a tampered count is REFUSED[digest-not-reproducible], exit 3, file+line named
  R3 missing count       a schema line deleted is REFUSED[digest-missing-count], the digest named
  R4 suggest decides     a decision sentence in suggest.md is REFUSED[suggest-decision], exit 3

Usage: python3 -m symbiont.selftest_digests [--keep] [--store-out DIR]
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

from .selftest_sources import _Handler, ENSEMBL_LICENCE, MGI_LICENCE, write_config
from . import contract

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from http.server import ThreadingHTTPServer

# run 1's miniature: three genes, no withdrawn markers
GENES1 = [
    ("Trp53", "ENSMUSG00000059552", "MGI:104798", "11", 69580359, 69591767),
    ("Actb", "ENSMUSG00000029580", "MGI:88056", "5", 142903097, 142906754),
    ("Brca1", "ENSMUSG00000017146", "MGI:105051", "11", 101488405, 101537537),
]
# run 2's miniature: +Pax6 (a new marker in BOTH sources) and Brca1 withdrawn (folded into
# Actb, as MGI does) — a real provider-style release move, not a fixture trick.
GENES2 = GENES1 + [("Pax6", "ENSMUSG00000028023", "MGI:97490", "2", 90067521, 90159353)]
WITHDRAWN = {"Brca1": ("MGI:105051", "Actb", "MGI:88056")}
MGI_MT1, MGI_MT2 = 1794258000, 1796850000          # 2026-11-10 / 2026-12-10: the release stamps


def _mart(genes):
    return ("MGI symbol\tGene stable ID\tChromosome/scaffold name\tGene start (bp)\tGene end (bp)\n"
            + "".join(f"{s}\t{e}\t{c}\t{a}\t{b}\n" for s, e, _m, c, a, b in genes))


def _mrk_ensembl(genes):
    return "".join("\t".join([m, s, f"{s} name", "0.0", c, e, "1 transcript", "-", "protein coding",
                              str(a), str(b), "+", "protein_coding"]) + "\n"
                   for s, e, m, c, a, b in genes)


def _mrk_list1(genes, withdrawn=None):
    head = ("MGI Accession ID\tChr\tcM Position\tcm Offset\tMGI Name\tMarker Synonym(s)\t"
            "Marker Symbol\tStatus\tMarker Type\tFeature Type\tGene Name\tGene ID(s)\t"
            "Current MGI Accession ID\tCurrent Symbol\n")
    rows = "".join(
        "\t".join([m, c, "0.0", "0.0", f"{s} name", "", s, "O", "Gene", "protein coding",
                   f"{s} name", "0", "", ""]) + "\n" for s, e, m, c, a, b in genes)
    w = "".join(
        "\t".join([mid, "0", "0.0", "0.0", f"{s} name", "", s, "W", "Gene", "protein coding",
                   f"{s} name", "0", cur_id, cur_sym]) + "\n"
        for s, (mid, cur_sym, cur_id) in (withdrawn or {}).items())
    return head + rows + w


def build_tree(root, genes, withdrawn, mtime):
    def w(rel, data, mt=None):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(data)
        if mt:
            os.utime(p, (mt, mt))

    w("ensembl/software.json", json.dumps({"release": 116 if mtime == MGI_MT1 else 117}).encode())
    w("ensembl/licence.html", ENSEMBL_LICENCE.encode())
    w("reports/licence.html", MGI_LICENCE.encode())
    w("reports/MRK_ENSEMBL.rpt", _mrk_ensembl(genes).encode(), mtime)
    w("reports/MRK_List1.rpt.gz", gzip.compress(_mrk_list1(genes, withdrawn).encode()), mtime)


def start_server(root):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: _Handler(*a, directory=root, **k))
    srv.mart_tsv = _mart(GENES1)
    srv.count_body = b"3\n"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def loop(store, cfg, *extra):
    from .selftest_sources import seed
    seed(store, cfg)                      # the fixture admits what it enabled
    return subprocess.run([sys.executable, "-m", "symbiont.loop", "--store", store,
                           "--sources-config", cfg, *extra],
                          capture_output=True, text=True, cwd=FARM)


def digests(*args):
    return subprocess.run([sys.executable, "-m", "symbiont.digests", *args],
                          capture_output=True, text=True, cwd=FARM)


def base_sources(host):
    from .selftest_sources import base_sources as _bs
    return _bs(host)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--store-out", default="", metavar="DIR",
                    help="copy the finished store here (for the report's pasted digests)")
    a = ap.parse_args(argv)

    tmp = tempfile.mkdtemp(prefix="symbiont-digests-")
    store = os.path.join(tmp, "store")
    web = os.path.join(tmp, "www")
    os.makedirs(web)
    build_tree(web, GENES1, None, MGI_MT1)
    srv, host = start_server(web)
    cfg = write_config(os.path.join(tmp, "sources.yml"), base_sources(host))

    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name:34} {detail}", flush=True)

    def fail_out(r):
        print(r.stdout, r.stderr)

    try:
        # ── D1: run 1 writes the per-source digest blocks (the schema, five lines) ────────
        r1 = loop(store, cfg, "--run-id", "dg-1")
        if r1.returncode != 0:
            fail_out(r1)
            check("D1 run dg-1", False, f"exit {r1.returncode}")
            return 1
        blk = open(os.path.join(store, "digest", "mgi.md"), encoding="utf-8").read()
        lines = [ln.split(":")[0][2:] for ln in blk.split("\n") if ln.startswith("- ")]
        check("D1 per-run digest block", lines == ["release", "counts", "changed", "refusals",
                                                   "last-success"], f"schema lines {lines}")
        check("D1 written per run", "## run dg-1 - PUBLISHED" in blk, "the run block is in digest/mgi.md")

        # ── D2: the provider REALLY moves; run 2 computes what changed from run 1 ─────────
        build_tree(web, GENES2, WITHDRAWN, MGI_MT2)            # +Pax6, Brca1 withdrawn, new stamp
        srv.mart_tsv = _mart(GENES2)
        srv.count_body = b"4\n"
        r2 = loop(store, cfg, "--run-id", "dg-2")
        if r2.returncode != 0:
            fail_out(r2)
            check("D2 run dg-2", False, f"exit {r2.returncode}")
            return 1
        blk2 = open(os.path.join(store, "digest", "mgi.md"), encoding="utf-8").read()
        want = "markers +1 (3->4) | retired +1 (0->1) | papers +0 (0->0) | records(ours) +1 (3->4)"
        check("D2 what changed, two real runs",
              f"- changed: {want}" in blk2, want)
        check("D2 release moved + staleness fields",
              "stored=mgi-2026-11-09 current=mgi-2026-12-09 upstream=mgi-2026-12-09 moved=yes"
              in blk2,
              "stored=mgi-2026-11-09 current=mgi-2026-12-09 upstream=mgi-2026-12-09 moved=yes "
              "(the upstream metadata endpoint moved; this run refreshes it)")
        check("D2 ensembl release too",
              "stored=ensembl-116 current=ensembl-117 upstream=ensembl-117 moved=yes" in
              open(os.path.join(store, "digest", "ensembl_biomart.md"), encoding="utf-8").read(),
              "ensembl-116 -> ensembl-117")

        # ── D3: no change -> every delta +0, zero bytes fetched (the T1 shape, digested) ───
        r3 = loop(store, cfg, "--run-id", "dg-3")
        if r3.returncode != 0:
            fail_out(r3)
            check("D3 run dg-3", False, f"exit {r3.returncode}")
            return 1
        rec3 = json.load(open(os.path.join(store, "runs", "dg-3.json"), encoding="utf-8"))
        blk3 = open(os.path.join(store, "digest", "mgi.md"), encoding="utf-8").read()
        check("D3 idempotent run digested",
              rec3["cost"]["data_bytes"] == 0 and
              "- changed: markers +0 (4->4) | retired +0 (1->1) | papers +0 (0->0) | "
              "records(ours) +0 (4->4)" in blk3,
              f"data_bytes=0 and every delta +0 (prior run {rec3['digest']['prior_run']})")

        # ── D4/D5: the gaps and suggest digests, verified ─────────────────────────────────
        gaps = open(os.path.join(store, "digest", "gaps.md"), encoding="utf-8").read()
        check("D4 gaps: all four classes with counts",
              all(f"- {k}: " in gaps for k in
                  ("genes-without-phenotypes", "papers-without-abstracts", "ids-without-xref",
                   "sources-with-no-release"))
              and "- genes-without-phenotypes: 4" in gaps and "- ids-without-xref: 0" in gaps,
              "genes-without-phenotypes: 4 (no phenotype view) · ids-without-xref: 0 (every key "
              "paired; Brca1 went to the retired map while its pair still publishes)")
        sug = open(os.path.join(store, "digest", "suggest.md"), encoding="utf-8").read()
        check("D5 suggest: ranked, costs, licence, question",
              "## option 1" in sug and "- question: " in sug and "- cost: " in sug
              and "- licence: " in sug and "- cost: none measurable yet" not in sug,
              "option 1 closes genes-without-phenotypes=4 with cost, licence and the question "
              "it would answer")
        check("D5 suggest never decides",
              "it never states a decision" in sug and "choosing: the operator" in sug,
              "every option ends: choosing is the operator's")

        # ── verify: the four digests from the real runs hold ──────────────────────────────
        v = digests("verify", *[os.path.join(store, "digest", f)
                                for f in ("ensembl_biomart.md", "mgi.md", "gaps.md", "suggest.md")])
        check("D6 verify green on all four", v.returncode == 0, v.stdout.strip())

        if a.store_out:                      # before the daily pass: the report pastes the
            dest = os.path.abspath(a.store_out)   # digests AS THE RUNS WROTE THEM
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(store, dest)
            print(f"  store kept at {dest}")

        # ── D7: the daily refresh pass re-measures, verify still holds ────────────────────
        w = digests("write", "--store", store)
        v2 = digests("verify", os.path.join(store, "digest", "gaps.md"),
                     os.path.join(store, "digest", "suggest.md"))
        check("D7 daily refresh holds", w.returncode == 0 and v2.returncode == 0,
              "digests write (the daily pass) -> gaps+suggest re-measured, verify green")

        # ── R1: a digest with no numbers is REFUSED (prose is not a digest) ────────────────
        prose = os.path.join(store, "digest", "prose.md")
        open(prose, "w", encoding="utf-8").write(
            "# A TALE OF TWO STORES\n\nIt was the best of releases, it was the worst of "
            "releases. Many markers came and went, and the station felt stale.\n")
        v = digests("verify", prose)
        check("R1 no numbers -> REFUSED",
              v.returncode == 3 and "REFUSED[digest-no-numbers] prose.md" in v.stdout
              and "prose is not a digest" in v.stdout,
              (v.stdout.strip().split("\n") or [""])[0])

        # ── R2: a tampered count cannot be reproduced by its own query ────────────────────
        bad = os.path.join(store, "digest", "gaps-tampered.md")
        open(bad, "w", encoding="utf-8").write(
            open(os.path.join(store, "digest", "gaps.md"), encoding="utf-8").read()
            .replace("- genes-without-phenotypes: 4", "- genes-without-phenotypes: 999"))
        v = digests("verify", bad)
        check("R2 tampered count -> REFUSED",
              v.returncode == 3 and "REFUSED[digest-not-reproducible] gaps-tampered.md" in v.stdout
              and "the query printed '4'" in v.stdout and "claims '999'" in v.stdout,
              (v.stdout.strip().split("\n") or [""])[0])

        # ── R3: a missing count is refused BY NAME ────────────────────────────────────────
        miss = os.path.join(store, "digest", "gaps-holey.md")
        txt = open(os.path.join(store, "digest", "gaps.md"), encoding="utf-8").read().split("\n")
        txt = [ln for i, ln in enumerate(txt)
               if not (ln.startswith("- ids-without-xref:") or i > 0 and txt[i-1].startswith(
                   "- ids-without-xref:") and ln.startswith("    "))]
        open(miss, "w", encoding="utf-8").write("\n".join(txt))
        v = digests("verify", miss)
        check("R3 missing count -> REFUSED by name",
              v.returncode == 3 and "REFUSED[digest-missing-count] gaps-holey.md" in v.stdout
              and "ids-without-xref count line is missing" in v.stdout,
              (v.stdout.strip().split("\n") or [""])[0])

        # ── R4: a suggest digest that states a decision is REFUSED ─────────────────────────
        bossy = os.path.join(store, "digest", "suggest-bossy.md")
        open(bossy, "w", encoding="utf-8").write(
            open(os.path.join(store, "digest", "suggest.md"), encoding="utf-8").read()
            + "\n- next: we will fetch the MPO file tomorrow\n")
        v = digests("verify", bossy)
        check("R4 suggest decides -> REFUSED",
              v.returncode == 3 and "REFUSED[suggest-decision] suggest-bossy.md" in v.stdout,
              (v.stdout.strip().split("\n") or [""])[0])

        if not a.keep:
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:  # noqa: BLE001 — a selftest reports, never hides
        import traceback
        traceback.print_exc()
        return 1

    bad = [n for n, ok, _ in results if not ok]
    print(f"SELFTEST {'PASS' if not bad else 'FAIL'}: digests {len(results) - len(bad)}/{len(results)} "
          f"held — the four digests from real runs, and the refusals"
          + ("" if not bad else f" (failed: {bad})"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
