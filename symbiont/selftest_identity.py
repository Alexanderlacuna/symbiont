"""PROOF of the three levels of identity (req-P1-IDENTITY-THREE-LEVELS-adapter-data).

A diary keyed on ONE string cannot tell 'the previous version of this dataset' from 'a DIFFERENT
dataset', and release_moved / count_mismatch / 'what did we admit in November' all depend on that
distinction. So identity is in three levels, and this suite proves each of them:

  (1) ADAPTER identity     the adapter class `name` (mgi) — UNCHANGED, the loader still resolves the
                           adapter by `name`, and nothing is renamed by the two levels below.
  (2) DATASET identity     <publisher>:<dataset> (mgi:MRK_ENSEMBL) — DECLARED in sources.yml,
                           STABLE across releases, DERIVED by contract.dataset_id().
  (3) SOURCE-VERSION       <dataset_id>:<release> (mgi:MRK_ENSEMBL:2026-11-09) — DERIVED by
                           contract.version_id(), never hand-typed.

Fixtures (all local: a loopback HTTP server serves a three-gene miniature of both providers, and
no external host is contacted):

  I1  the declaration and the derived ids       both live sources declare publisher+dataset; the
                                                loader still resolves the adapter by `name`;
                                                dataset_id('mgi') and version_id('mgi','2026-11-09')
                                                return the two ids; a source that declares neither
                                                is REFUSED by name (an id is never invented); the
                                                load->dump round-trip is still byte-identical.
  I2  the three levels travel                  run record + view provenance + manifest all carry
                                                adapter, dataset_id and version_id — derived once by
                                                the loop, quoted (never re-spelt) by the readers.
  I3  THE VERSION TEST                         two releases of the SAME dataset in one store: a run
                                                at 2026-11-09 followed by a run at 2026-11-16 leaves
                                                BOTH visible in the run records and in the view's
                                                provenance (the live one + the frozen bundle), and
                                                neither overwrites the other's history.

OUT OF SCOPE here (the row's own words): writing events, an index, and any adapter's fetch logic.
That is why this suite proves the IDS and the two writers that already exist, and writes no event.

Usage: python3 -m symbiont.selftest_identity [--keep]
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile

from . import contract
from .selftest_sources import (FARM, MRK_ENSEMBL, MRK_LIST1, base_sources, build_tree, loop,
                               record_of, start_server, write_config)

REAL_CONFIG = contract.DEFAULT_SOURCES


def _epoch(day: str) -> float:
    """UTC midnight of an ISO date, as an epoch — the fixture's Last-Modified, which is where both
    providers' release stamps come from."""
    return dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp()


def _set_release(tree: str, day: str, extra_row: bool = False) -> None:
    """Move BOTH of MGI's release files to `day` (mtime IS the release stamp the adapter reads),
    and optionally grow the release file so its ETag moves with it.

    The release file ALWAYS carries one malformed row (14 fields, 13 expected): the handover's §7
    list requires `rejects.by_reason` to be stated, and a store with no rejects at all refuses to
    manifest — so the fixture is a store that HAS a reject, with its reason."""
    for rel in ("MRK_ENSEMBL.rpt", "MRK_List1.rpt.gz"):
        p = os.path.join(tree, "reports", rel)
        if rel == "MRK_ENSEMBL.rpt":
            body = MRK_ENSEMBL + "MGI:888888\tBadgene\tBAD ROW\n"       # 3 fields, expected 13
            if extra_row:
                body += "\t".join(["MGI:999999", "Newgene", "Newgene name", "0.0", "1",
                                    "ENSMUSG00000000001", "1 transcript", "-", "protein coding",
                                    "1", "2", "+", "protein_coding"]) + "\n"
            open(p, "w", encoding="utf-8").write(body)
        os.utime(p, (_epoch(day), _epoch(day)))


def main(argv=None) -> int:
    keep = "--keep" in (argv or sys.argv[1:])
    tmp = tempfile.mkdtemp(prefix="symbiont-identity-")
    tree = os.path.join(tmp, "www")
    build_tree(tree)
    _set_release(tree, "2026-11-09")
    srv, host = start_server(tree)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name:46} {detail}", flush=True)

    try:
        # ── I1 the declaration and the derived ids ──────────────────────────────────────────
        s = contract.load_sources(REAL_CONFIG)
        declared = {sid: (s[sid]["publisher"], s[sid]["dataset"], s[sid]["adapter"],
                          s[sid]["adapter"] == s[sid]["id"]) for sid in sorted(s)}
        check("I1a sources.yml declares publisher+dataset for both live sources",
              declared == {"ensembl_biomart": ("ensembl", "mmusculus_gene_ensembl", "ensembl_biomart", True),
                           "mgi": ("mgi", "MRK_ENSEMBL", "mgi", True)},
              f"{declared}")
        adapters = contract.load_adapters()
        contract.check_registry(s, adapters)      # level 1: the adapter is still resolved BY NAME
        check("I1b the loader still resolves the adapter by `name` (registry check passes)",
              all(s[sid]["adapter"] in adapters and adapters[s[sid]["adapter"]].name == s[sid]["adapter"]
                  for sid in s),
              f"adapters={sorted(adapters)}")
        check("I1c dataset_id('mgi')", contract.dataset_id("mgi") == "mgi:MRK_ENSEMBL",
              f"-> {contract.dataset_id('mgi')}")
        check("I1d version_id('mgi','2026-11-09')",
              contract.version_id("mgi", "2026-11-09") == "mgi:MRK_ENSEMBL:2026-11-09",
              f"-> {contract.version_id('mgi', '2026-11-09')}")
        refused = []
        for bad, why in ((dict(id="x", adapter="x"), "declares no publisher/dataset"),
                         ("nosuch", "no such source")):
            try:
                contract.dataset_id(bad)
                refused.append(f"{why}: NOT REFUSED")
            except contract.ConfigError as e:
                refused.append(f"{why}: {str(e)[:52]}…")
        try:
            contract.version_id("mgi", "")
            refused.append("empty release: NOT REFUSED")
        except contract.ConfigError as e:
            refused.append(f"empty release: {str(e)[:40]}…")
        check("I1e an identity is DERIVED or refused by name, never invented",
              all("NOT REFUSED" not in r for r in refused), "; ".join(refused))
        text = open(REAL_CONFIG, encoding="utf-8").read()
        rt = contract._header(text) + contract.dump_sources(contract.load_sources(REAL_CONFIG))
        check("I1f sources.yml load->dump round-trip still byte-identical", rt == text,
              f"{len(text)} bytes, byte-identical={rt == text}")

        # ── I2 the three levels travel (run record · view provenance · manifest) ────────────
        cfg = write_config(os.path.join(tmp, "cfg.yml"), base_sources(host))
        st = os.path.join(tmp, "store")
        p = loop(st, cfg, "--since", "none", "--run-id", "I2A")
        rec = record_of(st, "I2A")
        ident = {sid: (rec["sources"][sid].get("identity") or {}) for sid in rec["sources"]}
        check("I2a run record carries adapter+dataset_id+version_id per source",
              p.returncode == 0
              and ident["mgi"].get("adapter") == "mgi"
              and ident["mgi"].get("dataset_id") == "mgi:MRK_ENSEMBL"
              and ident["mgi"].get("version_id") == "mgi:MRK_ENSEMBL:mgi-2026-11-09",
              f"exit={p.returncode} mgi={ident['mgi'].get('version_id')}")
        check("I2b the run's DISCOVER line names the version it is about to fetch",
              any("version_id=mgi:MRK_ENSEMBL:mgi-2026-11-09" in ln for ln in p.stdout.splitlines()),
              next((ln[:96] for ln in p.stdout.splitlines() if ln.startswith("DISCOVER mgi")), "?"))
        prov = json.load(open(os.path.join(st, "view", "mouse_gene.v1.provenance.json"),
                             encoding="utf-8"))["sources"]
        check("I2c view provenance carries adapter+dataset_id+version_id per source",
              prov["mgi"].get("dataset_id") == "mgi:MRK_ENSEMBL"
              and prov["mgi"].get("version_id") == "mgi:MRK_ENSEMBL:mgi-2026-11-09"
              and prov["mgi"].get("adapter") == "mgi"
              and prov["ensembl_biomart"].get("dataset_id") == "ensembl:mmusculus_gene_ensembl",
              f"mgi={prov['mgi'].get('version_id')} ensembl={prov['ensembl_biomart'].get('dataset_id')}")
        mpath = os.path.join(tmp, "M.json")
        mp = subprocess.run([sys.executable, "-m", "symbiont.manifest", "--store", st,
                             "--entity", "gene", "--out", mpath],
                            capture_output=True, text=True, cwd=FARM)
        man = json.load(open(mpath, encoding="utf-8"))
        rp = man["row_provenance"]
        check("I2d manifest carries the dataset ids and the derived versions per source",
              mp.returncode == 0
              and rp["dataset_ids"] == {"ensembl_biomart": "ensembl:mmusculus_gene_ensembl",
                                        "mgi": "mgi:MRK_ENSEMBL"}
              and rp["version_ids"]["mgi"] == "mgi:MRK_ENSEMBL:mgi-2026-11-09"
              and not rp["identity_unknown"]
              and all(x.get("dataset_id") and x.get("version_id") for x in rp["sources"]),
              f"exit={mp.returncode} dataset_ids={rp['dataset_ids']}")
        check("I2e the manifest still passes its §7 check with the identity fields added",
              "§7 CHECK 13/13" in mp.stdout,
              next((ln[:80] for ln in mp.stdout.splitlines() if "§7" in ln), "?"))

        # ── I3 THE VERSION TEST: two releases of ONE dataset, one store ─────────────────────
        snap1 = subprocess.run([sys.executable, "-m", "symbiont.retention", "snapshot",
                                "--store", st], capture_output=True, text=True, cwd=FARM)
        _set_release(tree, "2026-11-16", extra_row=True)     # the SAME dataset, a LATER release
        p2 = loop(st, cfg, "--run-id", "I2B")
        snap2 = subprocess.run([sys.executable, "-m", "symbiont.retention", "snapshot",
                                "--store", st], capture_output=True, text=True, cwd=FARM)
        a, b = record_of(st, "I2A"), record_of(st, "I2B")
        va = a["sources"]["mgi"]["identity"]["version_id"]
        vb = b["sources"]["mgi"]["identity"]["version_id"]
        check("I3a two releases of ONE dataset -> two DISTINCT version_ids, one dataset_id",
              p2.returncode == 0 and va != vb
              and a["sources"]["mgi"]["identity"]["dataset_id"] == b["sources"]["mgi"]["identity"]["dataset_id"],
              f"{va}  vs  {vb}")
        check("I3b BOTH run records are on disk, each naming its own version",
              os.path.isfile(os.path.join(st, "runs", "I2A.json"))
              and os.path.isfile(os.path.join(st, "runs", "I2B.json"))
              and record_of(st, "I2A")["sources"]["mgi"]["identity"]["version_id"] == va
              and record_of(st, "I2B")["sources"]["mgi"]["identity"]["version_id"] == vb,
              f"runs/={sorted(f for f in os.listdir(os.path.join(st, 'runs')))}")
        live = json.load(open(os.path.join(st, "view", "mouse_gene.v1.provenance.json"),
                             encoding="utf-8"))
        relsets = sorted(os.listdir(os.path.join(st, "releases")))
        frozen = {rs: json.load(open(os.path.join(st, "releases", rs, "mouse_gene.v1.provenance.json"),
                                     encoding="utf-8"))
                  for rs in relsets}
        check("I3c the view's provenance names BOTH versions (live = later; frozen bundle = earlier)",
              live["sources"]["mgi"]["version_id"] == vb
              and frozen["ensembl-116|mgi-2026-11-09"]["sources"]["mgi"]["version_id"] == va
              and frozen["ensembl-116|mgi-2026-11-16"]["sources"]["mgi"]["version_id"] == vb,
              f"live={live['sources']['mgi']['version_id']} frozen="
              + str({k: v["sources"]["mgi"]["version_id"] for k, v in frozen.items()}))
        check("I3d neither overwrites the other's history (both raw releases survive; current named)",
              all(os.path.isdir(os.path.join(st, "raw", "mgi", r))
                  for r in ("mgi-2026-11-09", "mgi-2026-11-16"))
              and json.load(open(os.path.join(st, "state", "mgi.json"), encoding="utf-8"))["release"]
              == "mgi-2026-11-16",
              f"raw/mgi/={sorted(os.listdir(os.path.join(st, 'raw', 'mgi')))}")
        check("I3e the two frozen bundles were written by the two runs (snapshot exited 0 each time)",
              snap1.returncode == 0 and snap2.returncode == 0 and len(relsets) == 2,
              f"snapshot exits={snap1.returncode},{snap2.returncode} relsets={relsets}")
    finally:
        srv.shutdown()
        if keep:
            print(f"  (kept {tmp})")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    bad = [r for r in results if not r[1]]
    print(f"SELFTEST {'PASS' if not bad else 'FAIL'}: {len(results) - len(bad)}/{len(results)} "
          f"fixtures held — identity is three levels, and a version nobody derived cannot be written",
          flush=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
