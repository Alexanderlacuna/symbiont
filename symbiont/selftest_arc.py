"""THE ARC TESTS (T1–T4) — the station's first four tests, runnable with the sources firewalled.

Everything here runs against a MINIATURE provider: a local HTTP server on 127.0.0.1 serving a
five-gene copy of both real sources' shapes (the same server class selftest_sources uses, so the
real adapters, the real contract and the real loop are what runs — only the endpoints are local).
Zero external requests leave the box, which is what makes this the quickstart a stranger can run
before fetching anything.

  T1 IDEMPOTENCY  run twice -> run 2 data_bytes=0, the view byte-identical, and the run record
                  says so
  T2 COUNTS       our count == the source's own published count, both sources, the URL recorded
  T3 CORRUPTION   five corrupted rows rejected BY REASON (bad_ensembl_id · non_integer_coordinate ·
                  start_after_end · field_count · bad_mgi_id), none silently dropped, the corrupted
                  genes absent from the published view, a clean survivor still published
  T4 PROJECTION   the view rebuilt from raw alone (replay of the store's own snapshots) -> the SAME
                  sha256 as the first publish: the view is a projection of raw, nothing more

  --write-fixture-store DIR   after the clean runs, copy the miniature store to DIR — this is how
                              symbiont/fixtures/store (the committed store a stranger reads before
                              fetching anything) is produced; `make fixtures-store` runs it.

Usage: python3 -m symbiont.selftest_arc [--keep] [--write-fixture-store DIR]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

from .selftest_sources import _Handler, base_sources, write_config, ENSEMBL_LICENCE, MGI_LICENCE
from .selftest_corrupt import corrupt

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from http.server import ThreadingHTTPServer

GENES = [  # symbol, ensembl id, chrom, start, end — the miniature's five subjects
    ("Trp53", "ENSMUSG00000059552", "11", 69580359, 69591767),
    ("Actb", "ENSMUSG00000029580", "5", 142903097, 142906754),
    ("Brca1", "ENSMUSG00000017146", "11", 101488405, 101537537),
    ("Mertk", "ENSMUSG00000029021", "2", 130730552, 130752058),
    ("Egfr", "ENSMUSG00000033022", "11", 16887223, 16900512),
]
MGI_ID = {"Trp53": "MGI:104798", "Actb": "MGI:88056", "Brca1": "MGI:105051",
          "Mertk": "MGI:1304630", "Egfr": "MGI:95299"}

MART_TSV = ("MGI symbol\tGene stable ID\tChromosome/scaffold name\tGene start (bp)\tGene end (bp)\n"
            + "".join(f"{s}\t{e}\t{c}\t{a}\t{b}\n" for s, e, c, a, b in GENES))
MRK_ENSEMBL = "".join(
    "\t".join([MGI_ID[s], s, f"{s} name", "0.0", c, e, "1 transcript", "-", "protein coding",
               str(a), str(b), "+", "protein_coding"]) + "\n" for s, e, c, a, b in GENES)
MRK_LIST1 = ("MGI Accession ID\tChr\tcM Position\tcm Offset\tMGI Name\tMarker Synonym(s)\t"
             "Marker Symbol\tStatus\tMarker Type\tFeature Type\tGene Name\tGene ID(s)\t"
             "Current MGI Accession ID\tCurrent Symbol\n")
MRK_LIST1 += "".join(
    "\t".join([MGI_ID[s], c, "0.0", "0.0", f"{s} name", "", s, "O", "Gene", "protein coding",
               f"{s} name", "0", "", ""]) + "\n" for s, e, c, a, b in GENES)

# T3: five corruptions, five NAMED reasons, one clean survivor (Egfr) still published.
CORRUPTIONS = [
    # (source, reskey, symbol, how, expected reject reason)
    ("ensembl_biomart", "biomart_tsv", "Trp53", "junk_ensembl_id", "bad_ensembl_id"),
    ("ensembl_biomart", "biomart_tsv", "Actb", "float_start", "non_integer_coordinate"),
    ("ensembl_biomart", "biomart_tsv", "Brca1", "swap_coords", "start_after_end"),
    ("mgi", "mrk_ensembl", "Trp53", "drop_field", "field_count"),
    ("mgi", "mrk_ensembl", "Mertk", "junk_mgi_id", "bad_mgi_id"),
]
SURVIVOR = "Egfr"
SYMBOL_COL = {"biomart_tsv": 0, "mrk_ensembl": 1}


def build_tree(root: str) -> None:
    def w(rel: str, data: bytes, mtime: float | None = None) -> None:
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(data)
        if mtime:
            os.utime(p, (mtime, mtime))

    w("ensembl/software.json", json.dumps({"release": 116}).encode())
    w("ensembl/licence.html", ENSEMBL_LICENCE.encode())
    w("reports/licence.html", MGI_LICENCE.encode())
    w("reports/MRK_ENSEMBL.rpt", MRK_ENSEMBL.encode(), 1794258000)   # fixed: 2026-11-10
    w("reports/MRK_List1.rpt.gz", gzip.compress(MRK_LIST1.encode()), 1794258000)


def start_server(root: str):
    from http.server import SimpleHTTPRequestHandler  # noqa: F401  (same handler class as selftest_sources)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: _Handler(*a, directory=root, **k))
    srv.mart_tsv = MART_TSV
    srv.count_body = b"5\n"            # the miniature's own published count — T2 compares against it
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def loop(store: str, cfg: str, *extra: str):
    return subprocess.run([sys.executable, "-m", "symbiont.loop", "--store", store,
                           "--sources-config", cfg, *extra],
                          capture_output=True, text=True, cwd=FARM)


def record(store: str, run_id: str) -> dict:
    return json.load(open(os.path.join(store, "runs", f"{run_id}.json"), encoding="utf-8"))


def cost_line(out: str) -> str:
    return next(l for l in out.splitlines() if l.startswith("COST"))


def view_sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def replay_specs(store: str, tmp: str, mangle=None) -> list[str]:
    """--replay-raw for EVERY snapshot the store holds (a rebuild from raw); mangle() corrupts a copy."""
    specs = []
    for sid in ("ensembl_biomart", "mgi"):
        state = json.load(open(os.path.join(store, "state", f"{sid}.json"), encoding="utf-8"))
        for reskey, snap in state["snapshots"].items():
            dst = os.path.join(tmp, f"{sid}.{reskey}")
            shutil.copyfile(snap["path"], dst)
            if mangle:
                mangle(sid, reskey, dst)
            specs.append(f"{sid}:{reskey}={dst}")
    return specs


def read_text(path: str) -> str:
    raw = open(path, "rb").read()
    return gzip.decompress(raw).decode() if path.endswith(".gz") else raw.decode()


def write_text(path: str, text: str) -> None:
    blob = gzip.compress(text.encode()) if path.endswith(".gz") else text.encode()
    open(path, "wb").write(blob)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--write-fixture-store", default="", metavar="DIR")
    a = ap.parse_args(argv)

    tmp = tempfile.mkdtemp(prefix="symbiont-arc-")
    if not a.keep:
        pass  # cleaned below unless --keep; kept simple and explicit for the transcript
    store = os.path.join(tmp, "store")
    web = os.path.join(tmp, "web")
    os.makedirs(web)
    build_tree(web)
    srv, host = start_server(web)
    cfg = write_config(os.path.join(tmp, "sources.yml"), base_sources(host))

    def fail(msg: str) -> int:
        print(f"  FAIL  {msg}")
        print("SELFTEST FAIL: the arc tests did not hold")
        return 1

    # ── run 1: the first fetch (clean) ───────────────────────────────── T2 + the publish
    r1 = loop(store, cfg, "--run-id", "arc-1")
    if r1.returncode != 0:
        print(r1.stdout, r1.stderr)
        return fail("T2 run 1 exited nonzero — the miniature fetch itself failed")
    rec1 = record(store, "arc-1")
    v_e, v_m = rec1["sources"]["ensembl_biomart"]["verify"], rec1["sources"]["mgi"]["verify"]
    if not (v_e["ok"] and v_m["ok"] and v_e["ours"] == v_e["source_count"] == 5
            and v_e["url"].startswith("http://127.0.0.1:")):
        return fail(f"T2 counts disagreed: {v_e} · {v_m}")
    print(f"  PASS  T2 counts   ours == the source's own count, both sources "
          f"(ensembl {v_e['ours']}/{v_e['source_count']} · mgi {v_m['ours']} records), url recorded")
    view_path = os.path.join(store, "view", "mouse_gene.v1.tsv")
    sha1 = view_sha(view_path)
    rows1 = len(open(view_path, encoding="utf-8").read().strip().split("\n")) - 1
    if rows1 != 5:
        return fail(f"T1 publish: expected 5 rows, view has {rows1}")

    # ── run 2: the idempotency proof ─────────────────────────────────── T1
    r2 = loop(store, cfg, "--run-id", "arc-2")
    if r2.returncode != 0:
        print(r2.stdout, r2.stderr)
        return fail("T1 run 2 exited nonzero")
    rec2 = record(store, "arc-2")
    if rec2["cost"]["data_bytes"] != 0:
        return fail(f"T1 run 2 data_bytes={rec2['cost']['data_bytes']}, not 0")
    if view_sha(view_path) != sha1:
        return fail("T1 run 2 rewrote the view — it must be byte-identical")
    print(f"  PASS  T1 idem     run 2 data_bytes=0, view byte-identical "
          f"(sha256={sha1[:12]}…); cost line: {cost_line(r2.stdout).strip()[:88]}…")

    # ── run 3: rebuild the view from RAW alone ───────────────────────── T4
    r3 = loop(store, cfg, "--run-id", "arc-3", *[
        f"--replay-raw={s}" for s in replay_specs(store, tmp)])
    if r3.returncode != 0:
        print(r3.stdout, r3.stderr)
        return fail("T4 replay run exited nonzero")
    rec3 = record(store, "arc-3")
    if rec3["publish"]["view_sha256"] != sha1:
        return fail(f"T4 rebuild sha {rec3['publish']['view_sha256'][:12]} != publish sha {sha1[:12]}")
    print(f"  PASS  T4 project  view rebuilt from raw alone -> SAME sha256={sha1[:12]}… "
          f"(the view is a projection of raw; drop it, rebuild it, the bytes come back)")

    # ── the committed fixture store, on request — written while the store is still CLEAN ──
    if a.write_fixture_store:
        dest = os.path.abspath(a.write_fixture_store)
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(store, dest)
        print(f"  PASS  fixture store written to {os.path.relpath(dest, FARM)} "
              f"(view: 5 rows, run records arc-1 fetch · arc-2 data_bytes=0 · arc-3 rebuild-from-raw; "
              f"regenerate with `make fixtures-store`)")

    # ── run 4: five corrupted rows, five NAMED rejects ────────────────── T3
    def mangle(sid: str, reskey: str, path: str) -> None:
        for csrc, cres, symbol, how, _reason in CORRUPTIONS:
            if (sid, reskey) != (csrc, cres):
                continue
            text = read_text(path)
            lines = text.split("\n")
            hit = next((i for i, ln in enumerate(lines)
                        if ln and ln.split("\t")[SYMBOL_COL[reskey]] == symbol), None)
            if hit is None:
                raise SystemExit(f"T3: {symbol!r} not found in {sid}:{reskey}")
            lines[hit] = "\t".join(corrupt(lines[hit].split("\t"), how, reskey))
            write_text(path, "\n".join(lines))

    r4 = loop(store, cfg, "--run-id", "arc-4", *[
        f"--replay-raw={s}" for s in replay_specs(store, tmp, mangle)])
    if r4.returncode != 0:
        print(r4.stdout, r4.stderr)
        return fail("T3 corruption run exited nonzero (a reject is data with a reason, not a crash)")
    rec4 = record(store, "arc-4")
    got = {**rec4["sources"]["ensembl_biomart"]["normalise"]["rejects_by_reason"],
           **rec4["sources"]["mgi"]["normalise"]["rejects_by_reason"]}
    want = {reason: 1 for _s, _r, _sym, _h, reason in CORRUPTIONS}
    if got != want:
        return fail(f"T3 rejects_by_reason {got} != {want}")
    rows4 = [l.split("\t") for l in open(view_path, encoding="utf-8").read().strip().split("\n")[1:]]
    syms4 = {r[0] for r in rows4}
    dirty = {sym for _s, _r, sym, _h, _r2 in CORRUPTIONS}
    if not (syms4 == {SURVIVOR}):
        return fail(f"T3 view must hold only the clean survivor {SURVIVOR!r}, holds {sorted(syms4)}")
    rej_lines = open(os.path.join(store, "rejects", f"arc-4.mgi.tsv"), encoding="utf-8").read()
    if "Mertk" not in rej_lines:
        return fail("T3 the corrupted row itself must appear in the rejects file — nothing vanishes")
    print(f"  PASS  T3 corrupt  five rows rejected BY REASON ({', '.join(sorted(want))}), "
          f"the corrupted genes ({', '.join(sorted(dirty))}) absent from the view, "
          f"{SURVIVOR} still published, every reject written to rejects/")

    if not a.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST PASS: arc 4/4 held — T1 idempotency · T2 counts · T3 corruption · T4 projection "
          "(miniature provider on 127.0.0.1; zero external requests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
