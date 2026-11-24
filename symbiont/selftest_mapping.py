"""PROOF that a COMPILED MAPPING carries every key the loop reads
(req-P1-A-COMPILED-MAPPING-MUST-CARRY-THE: `KeyError: 'quarantined'` at symbiont/loop.py:1184).

THE DEFECT, exactly. `contract.py` validates `quarantined` with `cfg.get(...)` because the schema
calls it OPTIONAL (`SOURCE_OPTIONAL`), while `loop.run()` indexes `cfg["quarantined"]` by SUBSCRIPT
in the bookkeeping that runs before any source is even selected. So a `kind: mapping` source whose
entry did not spell the key died on its FIRST run, mid-run, with a traceback where a value belonged.
Measured here (M6): a CODE source's entry without the key dies the same way — the key was never the
adapter's business; what kept the code path alive is only that every shipped entry spells
`quarantined: null`.

WHAT IS PROVEN (each a named check below):

  M1 code == table   the keys `loop.run()` indexes, READ OUT OF loop.py ITSELF, equal
                     `contract.LOOP_KEYS` — the table cannot fall behind the code it describes
  M2 superset        the COMPILED config of TWO mappings ⊇ what the loop indexes, asserted by NAME
                     (before AND after configure), and `load_sources` hands over the same total dict
  M3 no crash        a PLANTED mapping config — a real entry with `quarantined` and its line DELETED
                     from sources.yml — completes a REAL loop run to a PUBLISHED view: the default
                     (`quarantined: null`) is SUPPLIED, not crashed on
  M4 never a crash   the compiler handed no defaults (a compiler that has not learned the key) is
                     REFUSED[mapping-invalid] NAMING 'quarantined' — a refusal, never a KeyError
  M5 second mapping  the same run against a DIFFERENT zip (different columns, different dump)
                     behaves identically — the guarantee is the COMPILER's, not GenAge's
  M6 the code path   a code source's entry without `quarantined` also runs to a published view
                     (the row's diagnosis said only mappings crashed; it was the LOADER)

Nothing here touches the network: a local HTTP server (selftest_sources' own provider — one
definition site) serves the dump, the authority and the licence page, and every config is a copy of
the SHIPPED symbiont/sources.yml with only the URLs repointed at it — so what runs is the real
compiler, the real contract and the real loop.

Usage: python3 -m symbiont.selftest_mapping [--keep]
"""
from __future__ import annotations

import inspect
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

from . import contract, loop as loopmod, mapping
from .selftest_sources import (ENSEMBL_LICENCE, GENES, MART_TSV, base_sources, build_tree,  # noqa: F401
                               loop, record_of, start_server, write_config)

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENAGE_LICENCE = ("<html><body><h1>Legal</h1><p>The GenAge data are made available under a "
                  "Creative Commons Attribution 3.0 Unported License.</p></body></html>\n")
HUMAN_HEADER = "GenAge ID,symbol,name,entrez gene id,uniprot,why\n"
MODELS_HEADER = ("GenAge ID,symbol,name,organism,entrez gene id,avg lifespan change (max obsv),"
                 "lifespan effect,longevity influence\n")
ENTREZ = {"Trp53": "22059", "Actb": "11461", "Brca1": "12189"}


def _csv(header: str, rows: list[str]) -> bytes:
    return (header + "".join(r + "\n" for r in rows)).encode()


def build_mapping_tree(root: str) -> None:
    """The TWO dumps, one zip each, and the licence page they share. genage_human carries a
    `uniprot` column genage_models does not: two DIFFERENT mappings, not one dump twice."""
    human = _csv(HUMAN_HEADER, [f'H{i + 1},{s},{s} name,{ENTREZ[s]},Q9{i},why {s}'
                                for i, (s, *_r) in enumerate(GENES)])
    models = _csv(MODELS_HEADER, [f'M{i + 1},{s},{s} name,Mus musculus,{ENTREZ[s]},5.0,'
                                  f'increases,high' for i, (s, *_r) in enumerate(GENES)])

    def w(rel: str, data: bytes) -> None:
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(data)

    for name, member, body in (("human_genes.zip", "human_genes.csv", human),
                               ("models_genes.zip", "models_genes.csv", models)):
        buf = os.path.join(root, name)
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(member, body)          # ONE matching member, as the mapping declares
        os.utime(buf, (1794258000, 1794258000))    # a release stamp the Last-Modified can name
    w("genes/legal.html", GENAGE_LICENCE.encode())


def mapping_configs(host: str, tmp: str) -> dict[str, str]:
    """A copy of the SHIPPED config per mapping: the GenAge entries repointed at the local provider
    and enabled, NOTHING else changed (the authority stays ensembl_biomart, so a view is published)."""
    out = {}
    for sid in ("genage_human", "genage_models"):
        path = os.path.join(tmp, f"cfg-{sid}.yml")
        s = base_sources(host)
        for other in ("genage_human", "genage_models"):
            s[other]["enabled"] = (other == sid)
            s[other]["base_url"] = f"{host}/"
            s[other]["endpoints"] = {"archive": ("human_genes.zip" if other == "genage_human"
                                                 else "models_genes.zip")}
            s[other]["licence_url"] = f"{host}/genes/legal.html"
            s[other]["rate_limit"] = {"requests_per_minute": 0, "min_interval_s": 0}
            s[other]["budget"] = {"requests": 8, "bytes": 4000000, "wall_s": 90}
            s[other]["storage"] = {"archive": {"class": "store",
                                               "reason": "licence permits storage (CC-BY 3.0)"}}
        out[sid] = write_config(path, s)
    return out


def strip_quarantined(path: str, sid: str) -> str:
    """THE PLANT: delete one source's `quarantined: null` line from a real config file — the exact
    shape of the Monarch mapping entry whose first run died at loop.py:1184."""
    lines = open(path, encoding="utf-8").read().split("\n")
    out, current, dropped = [], "", 0
    for line in lines:
        if line.startswith("  - id: "):
            current = line.split("  - id: ", 1)[1]
        if line.strip() == "quarantined: null" and current == sid:
            dropped += 1
            continue
        out.append(line)
    planted = os.path.join(os.path.dirname(path), f"cfg-{sid}-planted.yml")
    open(planted, "w", encoding="utf-8").write("\n".join(out))
    return planted, dropped


def main(argv=None) -> int:
    keep = "--keep" in (argv or sys.argv[1:])
    tmp = tempfile.mkdtemp(prefix="symbiont-mapping-")
    tree = os.path.join(tmp, "www")
    build_tree(tree)                 # the authority + its licence (selftest_sources' provider)
    build_mapping_tree(tree)         # the two dumps under test
    srv, host = start_server(tree)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name:52} {detail}", flush=True)

    try:
        # ── M1 the table and the code agree, BY NAME ────────────────────────────────────────
        run_src = inspect.getsource(loopmod.run)
        indexed = sorted(set(re.findall(r'cfg\[["\']([A-Za-z_]+)["\']\]', run_src)))
        rebuild = sorted(set(re.findall(r'cfg\[["\']([A-Za-z_]+)["\']\]',
                                        inspect.getsource(loopmod._rebuild_from_raw))))
        check("M1 loop.run()'s subscripts == contract.LOOP_KEYS",
              sorted(contract.LOOP_KEYS) == indexed
              and set(rebuild) <= set(contract.LOOP_KEYS)
              and sorted(k for k in indexed if k in contract.SOURCE_OPTIONAL) == ["quarantined"],
              f"indexed={indexed} LOOP_KEYS={sorted(contract.LOOP_KEYS)} "
              f"rebuild={rebuild} defaults={contract.LOOP_DEFAULTS}")

        # ── M2 the COMPILED config of two mappings ⊇ what the loop indexes ─────────────────
        cfgs = mapping_configs(host, tmp)
        compiled = mapping.adapters_from_sources(contract.load_sources(contract.DEFAULT_SOURCES))
        details, ok = [], True
        for sid in ("genage_human", "genage_models"):
            ad = compiled[sid]
            before = set(ad.cfg)                     # the dict an UNCONFIGURED mapping carries
            entry = contract.load_sources(cfgs[sid])[sid]
            ad.configure(entry)
            after = set(ad.cfg)
            ok = ok and set(indexed) <= before and set(indexed) <= after
            details.append(f"{sid}: before={sorted(before & set(indexed))} "
                           f"after_configure_missing={sorted(set(indexed) - after)}")
        check("M2 compiled keys ⊇ loop.run()'s keys, by name, for TWO mappings",
              ok and all(set(indexed) <= set(contract.load_sources(cfgs[s])[s])
                         for s in ("genage_human", "genage_models")),
              " | ".join(details) + f" | loaded_entry_quarantined="
              f"{[contract.load_sources(cfgs[s])[s]['quarantined'] for s in cfgs]}")

        # ── M3 the PLANTED config runs to a PUBLISHED view (no traceback, default supplied) ─
        planted_h, dropped_h = strip_quarantined(cfgs["genage_human"], "genage_human")
        raw_text = open(planted_h, encoding="utf-8").read()
        human_block = raw_text.split("  - id: genage_human")[1].split("  - id: genage_models")[0]
        st3 = os.path.join(tmp, "m3.store")
        p3 = loop(st3, planted_h, "--sources", "ensembl_biomart,genage_human",
                  "--since", "none", "--run-id", "M3-planted")
        rec3 = record_of(st3, "M3-planted")
        view3 = os.path.join(st3, "view", "mouse_gene.v1.tsv")
        check("M3 planted mapping (no `quarantined`) -> PUBLISHED, no traceback",
              p3.returncode == 0 and "KeyError" not in (p3.stdout + p3.stderr)
              and "Traceback" not in (p3.stdout + p3.stderr)
              and "PUBLISHED run M3-planted" in p3.stdout and os.path.isfile(view3)
              and rec3["sources"]["genage_human"]["quarantined"] is None
              and dropped_h == 1 and "quarantined" not in human_block,
              f"exit={p3.returncode} planted_lines_dropped={dropped_h} "
              f"status={rec3['status']} rows={rec3['sources']['genage_human'].get('verify', {}).get('ours')} "
              f"run_record_quarantined={rec3['sources']['genage_human']['quarantined']!r} "
              f"view={'view/mouse_gene.v1.tsv' if os.path.isfile(view3) else 'MISSING'}")

        # ── M4 a compiler with no defaults is REFUSED[mapping-invalid] NAMING the key ──────
        entry = contract.load_sources(cfgs["genage_human"])["genage_human"]
        holed = {k: v for k, v in entry.items() if k != "quarantined"}
        refusal, how = "", ""
        try:
            mapping.compiled_config(holed, "source 'genage_human'", defaults={})
        except contract.MappingInvalid as e:
            refusal, how = str(e), f"REFUSED[{e.code}] {e}"
        except Exception as e:                       # a KeyError here would be the DEFECT
            refusal, how = "", f"WRONG EXCEPTION {type(e).__name__}: {e}"
        check("M4 compiled config lacking `quarantined` -> REFUSED[mapping-invalid], by name",
              how.startswith("REFUSED[mapping-invalid]") and "['quarantined']" in refusal
              and "WRONG EXCEPTION" not in how,
              how[:150])
        print(f"       {how[:190]}", flush=True)
        # ...and the SAME entry WITH the compiler's defaults is whole: the key is supplied, not demanded
        whole = mapping.compiled_config(holed, "source 'genage_human'")
        check("M4b the same entry WITH the defaults -> whole (`quarantined: null` supplied)",
              whole["quarantined"] is None and set(indexed) <= set(whole),
              f"quarantined={whole['quarantined']!r} "
              f"missing={sorted(set(indexed) - set(whole))}")

        # ── M5 the SECOND mapping (a different zip, different columns) behaves identically ─
        planted_m, dropped_m = strip_quarantined(cfgs["genage_models"], "genage_models")
        st5 = os.path.join(tmp, "m5.store")
        p5 = loop(st5, planted_m, "--sources", "ensembl_biomart,genage_models",
                  "--since", "none", "--run-id", "M5-second")
        rec5 = record_of(st5, "M5-second")
        view5 = os.path.join(st5, "view", "mouse_gene.v1.tsv")
        st5b = os.path.join(tmp, "m5b.store")
        p5b = loop(st5b, cfgs["genage_models"], "--sources", "ensembl_biomart,genage_models",
                   "--since", "none", "--run-id", "M5-whole")
        sha5 = hashlib.sha256(open(view5, "rb").read()).hexdigest() if os.path.isfile(view5) else ""
        v5b = os.path.join(st5b, "view", "mouse_gene.v1.tsv")
        sha5b = hashlib.sha256(open(v5b, "rb").read()).hexdigest() if os.path.isfile(v5b) else "expected"
        check("M5 SECOND mapping (models_genes.zip) identical; planted view == whole view",
              p5.returncode == 0 and p5b.returncode == 0 and sha5 == sha5b and bool(sha5)
              and "Traceback" not in (p5.stdout + p5.stderr)
              and "PUBLISHED run M5-second" in p5.stdout and dropped_m == 1
              and rec5["sources"]["genage_models"]["quarantined"] is None,
              f"exits={p5.returncode},{p5b.returncode} dropped={dropped_m} "
              f"rows_second={rec5['sources']['genage_models'].get('verify', {}).get('ours')} "
              f"view_sha={(sha5 or 'MISSING')[:12]}=={sha5b[:12]}")

        # ── M6 the CODE path's entry without `quarantined` (the loader, not the compiler) ───
        code_cfg, dropped_c = strip_quarantined(cfgs["genage_human"], "mgi")
        st6 = os.path.join(tmp, "m6.store")
        p6 = loop(st6, code_cfg, "--sources", "ensembl_biomart,mgi", "--since", "none",
                  "--run-id", "M6-code")
        check("M6 CODE source without `quarantined` -> PUBLISHED, no traceback",
              p6.returncode == 0 and dropped_c == 1
              and "Traceback" not in (p6.stdout + p6.stderr)
              and "PUBLISHED run M6-code" in p6.stdout,
              f"exit={p6.returncode} planted_lines_dropped={dropped_c} "
              f"out={(p6.stdout + p6.stderr).strip().splitlines()[-1][:90]}")
    finally:
        srv.shutdown()
        if keep:
            print(f"  (kept {tmp})")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    bad = [r for r in results if not r[1]]
    print(f"SELFTEST {'PASS' if not bad else 'FAIL'}: {len(results) - len(bad)}/{len(results)} "
          f"fixtures held — a compiled mapping carries every key the loop reads, and a mismatch "
          f"between the two is a REFUSAL, never a KeyError", flush=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
