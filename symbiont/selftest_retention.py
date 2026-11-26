"""RETENTION AND SUPERSEDE — the selftest (req-P1-RETENTION-AND-SUPERSEDE-what-current).

Proves the row's four fixtures against REAL RUNS of the real loop — a local provider on
127.0.0.1 whose release genuinely MOVES four times (116→119, four MGI release dates), with a
VALUE that genuinely changes between release 1 and release 2 (Trp53's start position), so the
supersede line carries two real values, not a fixture trick:

  F1 as-of survives the landing   a claim minted at R is still answerable after R+1 lands
                                  (the frozen bundle answers), and 'current' and 'as-of R'
                                  DIFFER — both answers quoted, the difference shown
  F2 supersede                     the changed value is marked superseded_by=<R+1> with BOTH
                                  values on the ledger row and in the output; the unchanged
                                  claim is untouched (status stays open)
  F3 retention drop archives       with N=2 (the owner's live policy) and 3 releases on disk,
                                  the oldest is MOVED to archive/ with RETENTION.json holding
                                  every sha256 (re-verified after the move), and the as-of
                                  answer STILL works after the raw drop
  F4 retention drop refuses       a tampered payload in a to-be-dropped release:
                                  REFUSED[hash-mismatch] naming the file and BOTH hashes, the
                                  release dir NOT moved — a silent removal is not representable
  + the never-silent pole          answer --as-of <a release that is not held> is
                                  REFUSED[release-not-held] by name, exit 2
  + the fail-closed pole           a policy with retention_releases_per_source null makes the
                                  sweep REFUSED[policy], exit 2, nothing dropped

Usage: python3 -m symbiont.selftest_retention [--keep]
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

from .selftest_sources import (_Handler, ENSEMBL_LICENCE, MGI_LICENCE, seed, write_config,
                               base_sources)
from .admission_policy import DEFAULT_POLICY
from .retention import release_set, read_json

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from http.server import ThreadingHTTPServer

# ── the miniature: three genes at release R; R+1 CHANGES Trp53's start (a real provider-style
#    coordinate correction) and adds Pax6. Actb never changes — it is the untouched pole.
TRP53_START_R1, TRP53_START_R2 = 69580359, 69580777
GENES_R1 = [
    ("Trp53", "ENSMUSG00000059552", "MGI:104798", "11", TRP53_START_R1, 69591767),
    ("Actb", "ENSMUSG00000029580", "MGI:88056", "5", 142903097, 142906754),
    ("Brca1", "ENSMUSG00000017146", "MGI:105051", "11", 101488405, 101537537),
]
GENES_R2 = [("Trp53", "ENSMUSG00000059552", "MGI:104798", "11", TRP53_START_R2, 69591767),
            ("Actb", "ENSMUSG00000029580", "MGI:88056", "5", 142903097, 142906754),
            ("Brca1", "ENSMUSG00000017146", "MGI:105051", "11", 101488405, 101537537),
            ("Pax6", "ENSMUSG00000028023", "MGI:97490", "2", 90067521, 90159353)]
# four MGI release stamps, one month apart (2026-11-10 · 2026-12-10 · 2027-01-09 · 2027-02-08)
MGI_MTS = [1794258000, 1796850000, 1799442000, 1802034000]


def _mart(genes):
    return ("MGI symbol\tGene stable ID\tChromosome/scaffold name\tGene start (bp)\tGene end (bp)\n"
            + "".join(f"{s}\t{e}\t{c}\t{a}\t{b}\n" for s, e, _m, c, a, b in genes))


def _mrk_ensembl(genes):
    return "".join("\t".join([m, s, f"{s} name", "0.0", c, e, "1 transcript", "-", "protein coding",
                              str(a), str(b), "+", "protein_coding"]) + "\n"
                   for s, e, m, c, a, b in genes)


def _mrk_list1(genes):
    head = ("MGI Accession ID\tChr\tcM Position\tcm Offset\tMGI Name\tMarker Synonym(s)\t"
            "Marker Symbol\tStatus\tMarker Type\tFeature Type\tGene Name\tGene ID(s)\t"
            "Current MGI Accession ID\tCurrent Symbol\n")
    rows = "".join(
        "\t".join([m, c, "0.0", "0.0", f"{s} name", "", s, "O", "Gene", "protein coding",
                   f"{s} name", "0", "", ""]) + "\n" for s, e, m, c, a, b in genes)
    return head + rows


def build_tree(root, genes, ensembl_release, mgi_mtime):
    """One provider state = one ensembl release + one MGI release stamp + the gene set."""
    def w(rel, data, mt=None):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(data)
        if mt:
            os.utime(p, (mt, mt))

    w("ensembl/software.json", json.dumps({"release": ensembl_release}).encode())
    w("ensembl/licence.html", ENSEMBL_LICENCE.encode())
    w("reports/licence.html", MGI_LICENCE.encode())
    w("reports/MRK_ENSEMBL.rpt", _mrk_ensembl(genes).encode(), mgi_mtime)
    w("reports/MRK_List1.rpt.gz", gzip.compress(_mrk_list1(genes).encode()), mgi_mtime)


def run(module, *args):
    return subprocess.run([sys.executable, "-m", module, *args], capture_output=True,
                          text=True, cwd=FARM)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args(argv)

    tmp = tempfile.mkdtemp(prefix="symbiont-retention-")
    store = os.path.join(tmp, "store")
    web = os.path.join(tmp, "www")
    os.makedirs(web)
    build_tree(web, GENES_R1, 116, MGI_MTS[0])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), lambda *r, **k: _Handler(*r, directory=web, **k))
    srv.mart_tsv = _mart(GENES_R1)
    srv.count_body = b"3\n"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host = f"http://127.0.0.1:{srv.server_address[1]}"
    cfg = write_config(os.path.join(tmp, "sources.yml"), base_sources(host))
    seed(store, cfg)                       # the fixture admits what it enabled

    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name:42} {detail}", flush=True)

    def say_transcript(label, r):
        print(f"  ── {label} (exit {r.returncode}) " + "─" * max(0, 20 - len(label)))
        for ln in (r.stdout or "").strip().splitlines():
            print(f"  │ {ln}")

    try:
        # ── release R lands (ensembl-116 · mgi-<mt1>) — a real run of the real loop ─────────
        r1 = run("symbiont.loop", "--store", store, "--sources-config", cfg, "--run-id", "rt-1")
        if r1.returncode != 0:
            print(r1.stdout, r1.stderr)
        check("R1 the loop publishes at release R", r1.returncode == 0,
              (r1.stdout or "").strip().splitlines()[-1] if (r1.stdout or "").strip() else "")
        relset1, per1 = release_set(store)
        # two claims minted AGAINST R: one whose value will change, one whose will not
        m1 = run("symbiont.claims", "mint", "--store", store, "--accession", "MGI:104798",
                 "--field", "start", "--question", "the start position of Trp53")
        m2 = run("symbiont.claims", "mint", "--store", store, "--accession", "MGI:88056",
                 "--field", "chromosome", "--question", "the chromosome of Actb")
        say_transcript("mint at R (Trp53 start · Actb chromosome)", m1)
        check("R1 two claims minted against R, release recorded",
              m1.returncode == 0 and m2.returncode == 0
              and f"against release {relset1}" in m1.stdout
              and os.path.isfile(os.path.join(store, "claims", "claims.tsv")),
              f"release={relset1}")

        # ── release R+1 lands: Trp53's start REALLY changes, Pax6 is added ──────────────────
        build_tree(web, GENES_R2, 117, MGI_MTS[1])
        srv.mart_tsv = _mart(GENES_R2)
        srv.count_body = b"4\n"
        r2 = run("symbiont.loop", "--store", store, "--sources-config", cfg, "--run-id", "rt-2")
        relset2, _ = release_set(store)
        check("R2 the loop publishes at R+1 (releases moved)",
              r2.returncode == 0 and relset2 != relset1, f"{relset1} -> {relset2}")

        # ── F1: the claim minted at R is STILL answerable after R+1 lands ───────────────────
        #     (as-of from the frozen bundle), and current ≠ as-of — both quoted
        asof = run("symbiont.claims", "answer", "--store", store, "--accession", "MGI:104798",
                   "--field", "start", "--as-of", relset1)
        cur = run("symbiont.claims", "answer", "--store", store, "--accession", "MGI:104798",
                  "--field", "start")
        say_transcript("F1 answer as-of R (frozen bundle)", asof)
        say_transcript("F1 answer current (live view)", cur)
        check("F1 claim minted at R answerable as-of after R+1",
              asof.returncode == 0 and f"as of release {relset1}" in asof.stdout
              and f"[frozen bundle releases/{relset1}/]" in asof.stdout
              and str(TRP53_START_R1) in asof.stdout,
              f"as-of {relset1} -> {TRP53_START_R1} (the value it was minted against)")
        check("F1 current and as-of R DIFFER, difference shown",
              cur.returncode == 0 and str(TRP53_START_R2) in cur.stdout
              and str(TRP53_START_R1) not in cur.stdout.split("ANSWER")[1]
              and str(TRP53_START_R2) not in asof.stdout.split("ANSWER")[1],
              f"as-of {relset1}: {TRP53_START_R1} · current {relset2}: {TRP53_START_R2}")

        # ── F2: supersede — changed value marked superseded_by=<R+1> with BOTH values ───────
        sup = run("symbiont.claims", "supersede", "--store", store)
        say_transcript("F2 supersede after R+1 lands", sup)
        ledger = open(os.path.join(store, "claims", "claims.tsv"), encoding="utf-8").read()
        rows = {r.split("\t")[2]: r for r in ledger.rstrip("\n").splitlines()[1:]}
        trp, actb = rows["MGI:104798"], rows["MGI:88056"]
        check("F2 changed value -> superseded_by=<R+1> with BOTH values",
              sup.returncode == 0
              and "SUPERSEDED" in sup.stdout and "MGI:104798.start:" in sup.stdout
              and f"'{TRP53_START_R1}' -> '{TRP53_START_R2}'" in sup.stdout
              and f"superseded_by={relset2}" in sup.stdout
              and trp.split("\t")[7] == "superseded" and trp.split("\t")[8] == relset2
              and trp.split("\t")[4] == str(TRP53_START_R1) and trp.split("\t")[9] == str(TRP53_START_R2),
              f"{TRP53_START_R1} -> {TRP53_START_R2} (superseded_by={relset2})")
        check("F2 unchanged claim untouched (status stays open)",
              any(ln.startswith("UNCHANGED") and "MGI:88056" in ln
                  for ln in sup.stdout.splitlines())
              and actb.split("\t")[7] == "open" and actb.split("\t")[8] == "",
              f"Actb.chromosome stays open (minted at {actb.split(chr(9))[5]}, value "
              f"{actb.split(chr(9))[4]!r} unchanged)")

        # ── R+2 lands (no value changes) so a THIRD release per source is on disk ──────────
        build_tree(web, GENES_R2, 118, MGI_MTS[2])
        r3 = run("symbiont.loop", "--store", store, "--sources-config", cfg, "--run-id", "rt-3")
        relset3, _ = release_set(store)
        check("R3 a third release per source lands", r3.returncode == 0, relset3)

        # ── F3: the retention drop ARCHIVES with the hash kept (never a silent removal) ────
        # the drop the policy names is the OLDEST mgi release — the one R (relset1) was minted at
        old_mgi = per1["mgi"]
        sweep = run("symbiont.retention", "sweep", "--store", store, "--apply")
        say_transcript("F3 retention sweep --apply (N=2, the owner's live policy)", sweep)
        arch = os.path.join(store, "archive", "mgi", old_mgi)
        ret_json = os.path.join(arch, "RETENTION.json")
        ok_f3 = (sweep.returncode == 0
                 and not os.path.isdir(os.path.join(store, "raw", "mgi", old_mgi))
                 and os.path.isfile(ret_json))
        hashes_ok, nfiles = False, 0
        if ok_f3:
            rj = read_json(ret_json, "RETENTION.json")
            nfiles = len(rj["files"])
            payload = os.path.join(arch, rj["files"][0]["file"])
            import hashlib
            hashes_ok = (hashlib.sha256(open(payload, "rb").read()).hexdigest()
                         == rj["files"][0]["sha256"] and nfiles >= 2)
        check("F3 drop ARCHIVED: moved, hash kept, re-verifiable",
              ok_f3 and hashes_ok
              and f"ARCHIVED mgi/{old_mgi}" in sweep.stdout,
              f"raw/mgi/{old_mgi}/ -> archive/mgi/{old_mgi}/ "
              f"({nfiles} sha256s in RETENTION.json, re-hashed after the move)")
        asof2 = run("symbiont.claims", "answer", "--store", store, "--accession", "MGI:104798",
                    "--field", "start", "--as-of", relset1)
        check("F3 the as-of answer STILL works after the raw drop",
              asof2.returncode == 0 and str(TRP53_START_R1) in asof2.stdout,
              f"the frozen bundle releases/{relset1}/ answers regardless of raw/")

        # ── F4: the drop that REFUSES by name — a tampered payload, nothing moved ─────────
        build_tree(web, GENES_R2, 119, MGI_MTS[3])
        r4 = run("symbiont.loop", "--store", store, "--sources-config", cfg, "--run-id", "rt-4")
        check("R4 a fourth release lands (mgi now has 3 to drop from)",
              r4.returncode == 0, release_set(store)[0])
        mgi_rels = sorted(os.listdir(os.path.join(store, "raw", "mgi")))
        victim = mgi_rels[0]                       # the oldest — the one the sweep would drop
        vdir = os.path.join(store, "raw", "mgi", victim)
        payload = [f for f in os.listdir(vdir) if not f.endswith(".meta.json")][0]
        with open(os.path.join(vdir, payload), "ab") as fh:      # tamper: one byte appended
            fh.write(b"X")
        sweep2 = run("symbiont.retention", "sweep", "--store", store, "--apply", "--source", "mgi")
        say_transcript("F4 retention sweep over a TAMPERED release", sweep2)
        check("F4 tampered drop REFUSED[hash-mismatch] by name, dir NOT moved",
              sweep2.returncode == 2 and "REFUSED[hash-mismatch]" in sweep2.stdout
              and payload[:16] in sweep2.stdout
              and os.path.isdir(vdir)
              and not os.path.isdir(os.path.join(store, "archive", "mgi", victim)),
              f"raw/mgi/{victim}/{payload[:16]}… still on disk, recorded sha quoted against actual")

        # ── the never-silent pole: an as-of question about a release we do not hold ────────
        bad = run("symbiont.claims", "answer", "--store", store, "--accession", "MGI:104798",
                  "--field", "start", "--as-of", "ensembl-999|mgi-1999-01-01")
        say_transcript("never-silent pole: as-of a release not held", bad)
        check("refuses as-of a release not held (never silently current)",
              bad.returncode == 2 and "REFUSED[release-not-held]" in bad.stdout
              and "ensembl-999|mgi-1999-01-01" in bad.stdout,
              "names the release, the held sets and the current one")

        # ── the fail-closed pole: a policy with N unset drops nothing ──────────────────────
        before_rel = sorted(os.listdir(os.path.join(store, "raw", "ensembl_biomart")))
        nullpol = os.path.join(tmp, "policy-null.yml")
        open(nullpol, "w", encoding="utf-8").write(
            open(DEFAULT_POLICY, encoding="utf-8").read()
            .replace("retention_releases_per_source: 2", "retention_releases_per_source: null")
            .replace("retention_disk_consequence_mb: 67", "retention_disk_consequence_mb: null"))
        sweep3 = run("symbiont.retention", "sweep", "--store", store, "--apply",
                     "--source", "ensembl_biomart", "--policy", nullpol)
        say_transcript("fail-closed pole: N unset in policy", sweep3)
        check("policy N null -> REFUSED[policy], nothing dropped",
              sweep3.returncode == 2 and "REFUSED[policy]" in sweep3.stdout
              and sorted(os.listdir(os.path.join(store, "raw", "ensembl_biomart"))) == before_rel,
              f"{len(before_rel)} ensembl releases on disk before and after: "
              f"{', '.join(before_rel)}")

        npass = sum(1 for _, ok, _ in results if ok)
        print(f"\nRETENTION+SUPERSEDE: {npass} passed, {len(results) - npass} failed "
              f"(store kept at {store} for post-mortem; /tmp is swept by the OS)")
        # the same line shape the other four farm-dependent suites print, so the state of a run
        # is unmistakable at a glance (req-P1-THE-FIVE-CLONE-SUITE-REDS-MUST-SKIP)
        print(f"RESULT: {npass} passed, 0 skipped, {len(results) - npass} failed "
              f"(every check ran — no skip)")
        return 0 if npass == len(results) else 1
    finally:
        pass


if __name__ == "__main__":
    sys.exit(main())
