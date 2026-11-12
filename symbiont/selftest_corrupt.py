"""PROOF that a corrupted row is rejected with a reason and never silently dropped.

Method: take the raw snapshots this store already holds, corrupt FIVE rows *in place*
(substitution, so the row count does not move), replay them through the loop with
--replay-raw, then check the run record and the view.

  Trp53  ensembl id replaced with junk      -> reject bad_ensembl_id
  Actb   start coordinate made non-integer  -> reject non_integer_coordinate
  Brca1  start and end swapped              -> reject start_after_end
  Mertk  one tab field deleted (MGI side)   -> reject field_count
  Egfr   MGI accession replaced with junk   -> reject bad_mgi_id

Expected: exit 0 (a reject is data with a reason, not a crash); every corrupted line present
in the rejects file under its expected reason; the corrupted genes absent from the published
view (i.e. the reject really did keep bad data out); replay never writes state, so the
store's since-memory is untouched.

Usage: python3 -m symbiont.selftest_corrupt [--store symbiont/store]
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

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = [
    # (source, reskey, symbol, corruption, expected reject reason)
    ("ensembl_biomart", "biomart_tsv", "Trp53", "junk_ensembl_id", "bad_ensembl_id"),
    ("ensembl_biomart", "biomart_tsv", "Actb", "float_start", "non_integer_coordinate"),
    ("ensembl_biomart", "biomart_tsv", "Brca1", "swap_coords", "start_after_end"),
    ("mgi", "mrk_ensembl", "Mertk", "drop_field", "field_count"),
    ("mgi", "mrk_ensembl", "Egfr", "junk_mgi_id", "bad_mgi_id"),
]
# where the symbol lives, per resource (the two providers lay their rows out differently)
SYMBOL_COL = {"biomart_tsv": 0, "mrk_ensembl": 1}


def corrupt(cols: list[str], how: str, reskey: str) -> list[str]:
    mart = reskey == "biomart_tsv"
    eid, start, end = (1, 3, 4) if mart else (5, 9, 10)
    if how == "junk_ensembl_id":
        cols[eid] = "ENSMUSG_NOT_A_GENE"
    elif how == "float_start":
        cols[start] = "12.5"
    elif how == "swap_coords":
        cols[start], cols[end] = cols[end], cols[start]
    elif how == "drop_field":
        cols.pop(eid + 1)
    elif how == "junk_mgi_id":
        cols[0] = "MGIBAD"
    else:
        raise SystemExit(f"unknown corruption {how!r}")
    return cols


def loop_run(store: str, run_id: str, extra: list[str]):
    from . import contract
    from .selftest_sources import seed
    seed(store, contract.DEFAULT_SOURCES)   # the fixture admits what the shipped config enables
    return subprocess.run([sys.executable, "-m", "symbiont.loop", "--store", store,
                           "--run-id", run_id, "--since", "auto"] + extra,
                          capture_output=True, text=True, cwd=FARM)


def file_sha(path: str) -> str:
    import hashlib
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def read(path: str) -> str:
    raw = open(path, "rb").read()
    return gzip.decompress(raw).decode() if path.endswith(".gz") else raw.decode()


# THE ONE FARM-ONLY INPUT. This fixture corrupts rows IN THE LIVE STORE's raw snapshots and
# replays them; the store (446 MB) is not shipped by DESIGN-STATION §1b, so a clone has nothing
# to corrupt. Absent -> a NAMED SKIP, counted, exit 0. Present-but-wrong (a state file that is
# not JSON) -> a FAIL by name, never a traceback: a store that exists and is lying is a finding.
WHAT_IS_MISSING = ("the live store this fixture corrupts and replays (raw snapshots + state + "
                   "the published view) — the 446 MB store is not shipped by DESIGN-STATION §1b")


def skip(name: str, path: str) -> None:
    print(f"  SKIP  {name}: {WHAT_IS_MISSING} at {path}", flush=True)


def write(path: str, text: str) -> None:
    blob = gzip.compress(text.encode()) if path.endswith(".gz") else text.encode()
    open(path, "wb").write(blob)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=os.path.join(FARM, "symbiont", "store"))
    a = ap.parse_args(argv)
    store = os.path.abspath(a.store)

    # ── ABSENT INPUT -> SKIP (named, counted, exit 0). PRESENT-BUT-WRONG -> FAIL (never a crash) ──
    inputs = [os.path.join(store, "state", f"{s}.json") for s in ("ensembl_biomart", "mgi")]
    inputs.append(os.path.join(store, "view", "mouse_gene.v1.tsv"))
    absent = [p for p in inputs if not os.path.isfile(p)]
    if absent:
        for p in absent:
            skip(os.path.relpath(p, store), p)
        print(f"SELFTEST SKIP: {len(absent)} input(s) this tree does not carry — nothing was "
              f"corrupted, nothing was claimed", flush=True)
        print(f"RESULT: 0 passed, {len(absent)} skipped, 0 failed (clone mode)", flush=True)
        print("SKIPPED: " + "; ".join(os.path.relpath(p, store) for p in absent), flush=True)
        return 0
    try:
        state = {s: json.load(open(os.path.join(store, "state", f"{s}.json"), encoding="utf-8"))
                 for s in ("ensembl_biomart", "mgi")}
    except (OSError, ValueError) as e:
        print(f"  FAIL  the store's state is PRESENT but unreadable: {type(e).__name__}: {e}",
              flush=True)
        print(f"SELFTEST FAIL: the store's state cannot be read — a store that exists and is lying "
              f"is a finding, not a skip", flush=True)
        print("RESULT: 0 passed, 0 skipped, 1 failed", flush=True)
        return 1
    view_path = os.path.join(store, "view", "mouse_gene.v1.tsv")

    # 0 a clean run first, so the baseline is the honest published view (and not the leavings
    #   of an earlier corruption demo)
    base = loop_run(store, "corrupt-baseline", [])
    if base.returncode != 0:
        print(base.stdout, base.stderr)
        return 1
    base_sha = file_sha(view_path)
    base_lines = [l for l in open(view_path, encoding="utf-8").read().split("\n") if l]
    base_pairs = len(base_lines) - 1
    base_symbols = {l.split("\t")[0] for l in base_lines[1:]}
    state_mtimes = {s: os.path.getmtime(os.path.join(store, "state", f"{s}.json")) for s in state}

    tmp = tempfile.mkdtemp(prefix="symbiont-corrupt-")
    copies, expected = {}, []
    for src, reskey, symbol, how, reason in CASES:
        orig = state[src]["snapshots"][reskey]["path"]
        if orig not in copies:
            dst = os.path.join(tmp, f"{src}.{reskey}")
            shutil.copyfile(orig, dst)
            copies[orig] = dst
        text = read(copies[orig])
        lines = text.split("\n")
        col = SYMBOL_COL[reskey]
        hit = next((i for i, ln in enumerate(lines) if ln and ln.split("\t")[col] == symbol), None)
        if hit is None:
            raise SystemExit(f"case {symbol!r} not found in {src}:{reskey} — the demo has no subject")
        lines[hit] = "\t".join(corrupt(lines[hit].split("\t"), how, reskey))
        write(copies[orig], "\n".join(lines))
        expected.append(dict(source=src, reskey=reskey, symbol=symbol, how=how, reason=reason,
                             row=lines[hit]))

    flags = []
    for (src, reskey) in sorted({(c[0], c[1]) for c in CASES}):
        flags += ["--replay-raw", f"{src}:{reskey}={copies[state[src]['snapshots'][reskey]['path']]}"]
    print("REPLAY " + " ".join(flags), flush=True)
    p = loop_run(store, "corrupt-demo", flags)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr)
        print(f"SELFTEST FAIL: the loop exited {p.returncode} on replayed raw", flush=True)
        return 1

    rec = json.load(open(os.path.join(store, "runs", "corrupt-demo.json"), encoding="utf-8"))
    reason_of = {}
    for src in ("ensembl_biomart", "mgi"):
        for ln in open(rec["sources"][src]["rejects_file"], encoding="utf-8").read().split("\n")[1:]:
            if ln:
                f = ln.split("\t", 3)   # the raw row keeps its own tabs, so split from the left only
                reason_of[f[3]] = f[1]

    ok = True
    print("=== the corrupted rows as the symbiont recorded them ===")
    for e in expected:
        got = reason_of.get(e["row"], "")
        ok &= got == e["reason"]
        print(f"  {'REJECTED' if got == e['reason'] else 'MISSING ':8} {e['source']:16} {e['symbol']:6} "
              f"{e['how']:18} expected={e['reason']:24} recorded={got or '(absent)'}")

    vtext = open(view_path, encoding="utf-8").read()
    still = [e["symbol"] for e in expected if any(l.split("\t")[0] == e["symbol"] for l in vtext.split("\n") if l)]
    print(f"  corrupted symbols still in the published view: {still or 'none'}")
    ok &= not still

    pairs = rec["xref"]["pairs"]
    loss = sum(1 for e in expected if e["symbol"] in base_symbols)
    st = " ".join(f"{s}:rows_in={d['normalise']['rows_in']},records={d['normalise']['records']},"
                  f"rejects={d['normalise']['rejects']}" for s, d in rec["sources"].items())
    print(f"  pairs: baseline {base_pairs} -> replayed {pairs} (lost exactly the {loss} corrupted genes)")
    print(f"  accounting {st}")
    ok &= pairs == base_pairs - loss
    untouched = all(os.path.getmtime(os.path.join(store, "state", f"{s}.json")) == m
                    for s, m in state_mtimes.items())
    print(f"  since-memory untouched by the replay: {untouched} (state files not rewritten)")
    ok &= untouched

    # the replay left the corrupted view on disk: put the true one back and prove it is byte-identical
    restored = loop_run(store, "corrupt-restore", [])
    back = file_sha(view_path) == base_sha
    print(f"  view restored by a normal run afterwards: {back} (sha256 "
          f"{file_sha(view_path)[:16]}… == baseline {base_sha[:16]}…)")
    ok &= back and restored.returncode == 0

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'}: corrupt rows rejected with a reason, never silently dropped")
    print(f"RESULT: {len(expected) if ok else 0} passed, 0 skipped, {0 if ok else 1} failed "
          f"(every check ran — no skip)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
