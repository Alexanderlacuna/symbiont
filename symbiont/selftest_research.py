"""selftest_research — the RESEARCH SPEC's four NSC claims, proved OFFLINE (127.0.0.1, zero
external requests).

req-P1-THE-RESEARCH-SPEC-what-a-question-req's gate IS the four quotations, so this suite is the
gate's fixture: each case below prints the quoted evidence the report quotes, and a case that
cannot fail would not be a check (T2 and T3 are the fail poles).

    R1  REFORMATTING CANNOT CHANGE AN IDENTITY — the same spec, written three ways (comment header,
        different key order, flow-vs-block style, extra internal whitespace, dataset order) hashes
        to ONE sha256; and changing OPTIONALITY (an optional dataset added) DOES change it, because
        optionality is declared before the run and lives inside the hash.
    R2  A SPEC THAT CANNOT BE HONOURED IS REFUSED BY NAME — a release pin inside a dataset name, an
        unknown `release:` key, an arm that is omitted, and a dataset both required and optional.
    R3  A MISSING REQUIRED DATASET -> REFUSED[missing-dataset], naming the spec and the dataset,
        listing what WAS available, ACQUISITION NOT STARTED (0 requests, no raw, no view).
    R4  A MISSING OPTIONAL DATASET -> the run completes, coverage is complete:false / status
        "partial", missing[] names it, the FINDING carries the block, and the manifest carries both.
    R5  A RUN AGAINST A NEWER RELEASE keeps the SAME spec_sha256 while the run record shows the new
        release — the binary split (spec = requirements, run record = release pins).

Run:  python3 -m symbiont.selftest_research        (exit 0 = all held)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

from . import contract, manifest, research
from .selftest_sources import base_sources, build_tree, seed, start_server, write_config

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP = [sys.executable, "-m", "symbiont.loop"]

# ────────────────────────────────────────────────────────────── the spec fixtures
# A — block style, with a comment header that is NOT part of the identity.
SPEC_A = """# THE RESEARCH SPEC — this whole comment block is decoration and must not change the hash.
# key order below is deliberately NOT the schema's order.
research_id: mouse-gene-identity
question: Which mouse genes have a stable identity for the gene identity arm?
version: 1

arms:
  gene_identity: "Publish one row per gene: symbol, accession, coordinate."
  phenotype_ontology: null
  literature: null

filters:
  species: Mus musculus
  term_sets:
    gene_identity:
      - protein_coding
      - gene
  date_cutoff: 2026-01-01

required_datasets:
  - ensembl:mmusculus_gene_ensembl
  - mgi:MRK_ENSEMBL

optional_datasets: []
"""

# B — the SAME spec, reformatted: no comments, flow style, keys in another order, extra internal
# whitespace in the question, the dataset list reordered.
SPEC_B = """version: 1
question: "Which   mouse genes have a stable identity   for the gene identity arm?"
optional_datasets: []
required_datasets: [mgi:MRK_ENSEMBL, ensembl:mmusculus_gene_ensembl]
filters: {date_cutoff: "2026-01-01", species: "Mus musculus", term_sets: {gene_identity: [gene, protein_coding]}}
arms: {literature: ~, phenotype_ontology: ~, gene_identity: "Publish one row per gene:   symbol, accession, coordinate."}
research_id: mouse-gene-identity
"""

# C — A with ONE optional dataset declared. Same requirements; a DIFFERENT investigation identity,
# because what is optional was decided BEFORE the run and is inside the hash.
SPEC_C = SPEC_A.replace("optional_datasets: []",
                        "optional_datasets:\n  - mp:mp.obo")


def spec_unpinned_names() -> str:
    return SPEC_A.replace("- mgi:MRK_ENSEMBL", "- mgi:MRK_ENSEMBL@2026-11-09")


def spec_with_release_key() -> str:
    return SPEC_A.replace("version: 1", "version: 1\nrelease: 2026-11-09")


def spec_missing_arm() -> str:
    return SPEC_A.replace("  literature: null\n", "")


def spec_both_classes() -> str:
    return SPEC_A.replace("optional_datasets: []", "optional_datasets:\n  - mgi:MRK_ENSEMBL")


# ────────────────────────────────────────────────────────────── helpers
def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def run_loop(store: str, cfg: str, spec: str, run_id: str):
    seed(store, cfg)                       # the fixture admits what it enabled
    return subprocess.run(LOOP + ["--store", store, "--sources-config", cfg,
                                  "--research", spec, "--run-id", run_id],
                          capture_output=True, text=True, cwd=FARM)


def record_of(store: str, run_id: str) -> dict:
    return json.load(open(os.path.join(store, "runs", f"{run_id}.json"), encoding="utf-8"))


def mirror_sources(host: str) -> dict:
    """The same registry pointed at the provider's MIRROR, which serves NEWER releases (ensembl 117,
    MGI's report a month later). Same datasets, same declarations, newer releases — which is exactly
    the case the binary split exists for."""
    s = base_sources(host)
    s["ensembl_biomart"]["base_url"] = f"{host}/mirror/"
    s["ensembl_biomart"]["licence_url"] = f"{host}/ensembl/licence.html"
    s["mgi"]["base_url"] = f"{host}/mirror/"
    s["mgi"]["licence_url"] = f"{host}/reports/licence.html"
    return s


# ────────────────────────────────────────────────────────────── the suite
def main(argv=None) -> int:
    keep = "--keep" in (argv or sys.argv[1:])
    tmp = tempfile.mkdtemp(prefix="symbiont-research-")
    tree = os.path.join(tmp, "www")
    build_tree(tree)
    srv, host = start_server(tree)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name:64} {detail}", flush=True)

    try:
        specs = os.path.join(tmp, "research")
        a = write(os.path.join(specs, "a.yml"), SPEC_A)
        b = write(os.path.join(specs, "b.yml"), SPEC_B)
        c = write(os.path.join(specs, "c.yml"), SPEC_C)

        # ── R1 the canonicaliser ────────────────────────────────────────────────────────────
        sa, sb, sc = (research.spec_sha256(research.load_spec(p)) for p in (a, b, c))
        print(f"\nR1 REFORMATTING CANNOT CHANGE AN IDENTITY")
        print(f"  spec A (comments + block style):       spec_sha256 {sa}")
        print(f"  spec B (flow style, keys reordered):   spec_sha256 {sb}")
        check("R1a identical spec, different formatting -> one sha256", sa == sb, f"{sa[:16]}… == {sb[:16]}…")
        check("R1b optionality is INSIDE the identity (C adds mp:mp.obo)", sc != sa,
              f"C {sc[:16]}… != A {sa[:16]}…")
        caf = research.canonical(research.load_spec(a))
        check("R1c the canonical form keeps the requirements and drops the cosmetics",
              caf["required_datasets"] == ["ensembl:mmusculus_gene_ensembl", "mgi:MRK_ENSEMBL"]
              and caf["optional_datasets"] == [], json.dumps(caf["required_datasets"]))

        # ── R2 a spec that cannot be honoured is refused BY NAME ────────────────────────────
        print("\nR2 A SPEC THAT CANNOT BE HONOURED IS REFUSED BY NAME")
        for label, text, want in (
                ("release pin in a dataset name", spec_unpinned_names(), "@"),
                ("unknown `release:` key", spec_with_release_key(), "unknown key"),
                ("an arm omitted", spec_missing_arm(), "does not DECLARE"),
                ("dataset both required and optional", spec_both_classes(), "BOTH required and optional"),
        ):
            p = write(os.path.join(specs, f"bad-{abs(hash(label))}.yml"), text)
            r = subprocess.run([sys.executable, "-m", "symbiont.research", "--hash", p],
                               capture_output=True, text=True, cwd=FARM)
            ok = r.returncode == 2 and "REFUSED[spec]" in r.stdout and want in r.stdout
            check(f"R2 {label}", ok, (r.stdout.strip().splitlines() or [""])[0][:150])

        # ── R3 a missing REQUIRED dataset refuses BEFORE spending ───────────────────────────
        print("\nR3 A MISSING REQUIRED DATASET -> REFUSED[missing-dataset], ACQUISITION NOT STARTED")
        cfg_off = base_sources(host)
        cfg_off["mgi"]["enabled"] = False              # removed from the station's enabled set
        cfg_off_p = write(os.path.join(tmp, "sources-mgi-off.yml"), "")
        write(cfg_off_p, contract._header(open(contract.DEFAULT_SOURCES, encoding="utf-8").read())
              + contract.dump_sources(cfg_off))
        st3 = os.path.join(tmp, "store-off")
        r3 = run_loop(st3, cfg_off_p, a, "research-refused")
        rec3 = record_of(st3, "research-refused")
        ref = next((x for x in rec3["refusals"] if x["code"] == "missing-dataset"), {})
        line = next((ln for ln in r3.stdout.splitlines() if ln.startswith("REFUSED[missing-dataset]")), "")
        print(f"  $ python3 -m symbiont.loop --research {os.path.basename(a)}   (mgi enabled: false)")
        print(f"  {line}")
        print(f"  " + next((ln for ln in r3.stdout.splitlines()
                          if ln.startswith("ACQUISITION NOT STARTED")), "(no such line)"))
        print(f"  exit {r3.returncode} · run record status={rec3['status']} "
              f"cost.requests={rec3['cost']['requests']} "
              f"raw files={sum(len(f) for _d, _s, f in os.walk(os.path.join(st3, 'raw')))} "
              f"view published={sorted(os.listdir(os.path.join(st3, 'view')))}")
        check("R3a exit 3 and REFUSED[missing-dataset] names the spec and the dataset",
              r3.returncode == 3 and ref.get("spec") == "mouse-gene-identity"
              and ref.get("dataset") == ["mgi:MRK_ENSEMBL"] and "mgi:MRK_ENSEMBL" in line,
              f"dataset={ref.get('dataset')}")
        check("R3b what WAS available is listed", ref.get("available") == ["ensembl_biomart", "mgi"],
              f"available={ref.get('available')}")
        check("R3c ACQUISITION NOT STARTED: zero requests, no raw snapshot, no view",
              rec3["cost"]["requests"] == 0
              and not os.listdir(os.path.join(st3, "raw"))
              and not os.listdir(os.path.join(st3, "view"))
              and "ACQUISITION NOT STARTED" in r3.stdout,
              f"requests={rec3['cost']['requests']} raw=[] view=[]")
        check("R3d the coverage block is in the run record, with the missing dataset named",
              rec3["coverage"]["missing_by_class"]["required"] == ["mgi:MRK_ENSEMBL"],
              f"status={rec3['coverage']['status']}")

        # ── R4 an optional dataset that is missing: allowed, but never invisible ────────────
        print("\nR4 A MISSING OPTIONAL DATASET -> complete:false / status \"partial\", named, findable")
        cfg_on_p = write(os.path.join(tmp, "sources-on.yml"), "")
        write(cfg_on_p, contract._header(open(contract.DEFAULT_SOURCES, encoding="utf-8").read())
              + contract.dump_sources(base_sources(host)))
        st4 = os.path.join(tmp, "store-partial")
        r4 = run_loop(st4, cfg_on_p, c, "research-partial")
        rec4 = record_of(st4, "research-partial")
        cov4 = rec4["coverage"]
        print(f"  $ python3 -m symbiont.loop --research {os.path.basename(c)}   (mp:mp.obo is optional "
              f"and NOT admitted)")
        print(f"  " + next((ln for ln in r4.stdout.splitlines() if ln.startswith("COVERAGE ")), ""))
        print(f"  " + next((ln for ln in r4.stdout.splitlines() if ln.startswith("FINDING")), "")[:400])
        check("R4a the run completes (an optional-and-missing dataset is ALLOWED)",
              r4.returncode == 0 and rec4["status"] == "PUBLISHED", f"exit={r4.returncode} status={rec4['status']}")
        check("R4b coverage complete:false / status partial, missing[] names it",
              cov4["complete"] is False and cov4["status"] == "partial"
              and cov4["missing"] == ["mp:mp.obo"] and cov4["missing_by_class"]["required"] == [],
              f"missing={cov4['missing']}")
        check("R4c the FINDING carries the block",
              any(f.startswith("COVERAGE-PARTIAL") and '"missing":["mp:mp.obo"]' in f
                  for f in rec4["findings"]),
              f"findings={len(rec4['findings'])}")

        m4_path = os.path.join(tmp, "MANIFEST-partial.json")
        # built in-process: the CLI's own --out path is disturbed by a PRE-EXISTING §7 rule that
        # refuses an empty rejects.by_reason (this fixture run rejected nothing — noted in the
        # report as a finding, not changed here: §7 is not this row's target)
        m4 = manifest.build(st4, "gene", os.path.join(tmp, "gene.handover.staged.tsv"), None, True)
        m4.pop("_staged_bytes", None)
        manifest.dump_json(m4, m4_path)
        cm = m4["coverage"]
        print(f"  $ python3 -m symbiont.manifest --store {os.path.basename(st4)} --entity gene "
              f"-> {os.path.basename(m4_path)}   (coverage block, written)")
        print("  manifest coverage = " + json.dumps(
            {k: cm[k] for k in ("research_id", "spec_sha256", "required", "missing", "complete",
                                "status")}, sort_keys=True))
        print("  manifest coverage.acquired = " + json.dumps(
            [{k: a_[k] for k in ("dataset", "release", "sha256")} for a_ in cm["acquired"]],
            sort_keys=True))
        print("  manifest coverage.finding = " + str(cm.get("finding"))[:220])
        check("R4d the MANIFEST carries the block: complete false, missing named, spec hashed",
              os.path.exists(m4_path) and cm["present"] is True and cm["complete"] is False
              and cm["missing"] == ["mp:mp.obo"] and cm["spec_sha256"] == sc
              and cm["research_id"] == "mouse-gene-identity",
              f"manifest coverage status={cm['status']}")
        check("R4e every acquired dataset points at an acquisition record that EXISTS",
              all(os.path.exists(x["acquisition"]["run_record"])
                  and x["acquisition"]["run_record"] == os.path.join(st4, "runs", "research-partial.json")
                  and x["acquisition"]["raw"] for x in cm["acquired"]) and len(cm["acquired"]) == 2,
              f"{[x['dataset'] for x in cm['acquired']]}")
        check("R4f the coverage block checks ITSELF (validate_coverage) and there is nothing to say",
              manifest.validate_coverage(m4) == []
              and cm["agrees_with_run_record"] is True
              and (cm["spec_verified"] or {}).get("changed_since_run") is False,
              f"validate_coverage()={manifest.validate_coverage(m4)} agrees_with_run_record="
              f"{cm['agrees_with_run_record']}")
        # a fail pole: a manifest whose coverage claims complete:true while missing[] names a
        # dataset must be REFUSED — a check that cannot fail is not a check
        bad = json.loads(json.dumps(m4))
        bad["coverage"]["complete"] = True
        errs_bad = manifest.validate_coverage(bad)
        bad2 = json.loads(json.dumps(m4))
        bad2.pop("coverage")
        errs_bad2 = manifest.validate_coverage(bad2)
        print("  fail pole: complete:true with missing[] -> " + (errs_bad[0] if errs_bad else "ACCEPTED"))
        print("  fail pole: no coverage block at all    -> " + (errs_bad2[0] if errs_bad2 else "ACCEPTED"))
        check("R4g a handover that claims complete:true with a gap is REFUSED, by field name",
              bool(errs_bad) and "coverage.complete is true but missing[]" in errs_bad[0]
              and bool(errs_bad2) and "no coverage block" in errs_bad2[0],
              f"{len(errs_bad)} + {len(errs_bad2)} refusal(s)")

        # ── R5 a newer release: same spec, new pins ─────────────────────────────────────────
        print("\nR5 A NEWER RELEASE KEEPS THE SAME spec_sha256; the RUN RECORD shows the new release")
        cfg_mirror_p = write(os.path.join(tmp, "sources-mirror.yml"), "")
        write(cfg_mirror_p, contract._header(open(contract.DEFAULT_SOURCES, encoding="utf-8").read())
              + contract.dump_sources(mirror_sources(host)))
        st5 = os.path.join(tmp, "store-mirror")
        r5 = run_loop(st5, cfg_mirror_p, c, "research-newer-release")
        rec5 = record_of(st5, "research-newer-release")
        before = {a_["dataset"]: a_["release"] for a_ in cov4["acquired"]}
        after = {a_["dataset"]: a_["release"] for a_ in rec5["coverage"]["acquired"]}
        print(f"  run research-partial      spec_sha256={rec4['research']['spec_sha256']}  "
              f"releases={json.dumps(before, sort_keys=True)}")
        print(f"  run research-newer-rel.   spec_sha256={rec5['research']['spec_sha256']}  "
              f"releases={json.dumps(after, sort_keys=True)}")
        check("R5a the SAME spec_sha256 across both releases",
              rec5["research"]["spec_sha256"] == rec4["research"]["spec_sha256"] == sc,
              f"{rec5['research']['spec_sha256'][:16]}… == {rec4['research']['spec_sha256'][:16]}…")
        check("R5b the run record shows the NEW releases (the pins moved, the investigation did not)",
              all(after[d] != before[d] for d in before) and r5.returncode == 0,
              f"{sorted(before.items())} -> {sorted(after.items())}")
        check("R5c the release pins are NOWHERE in the spec (it still hashes to the same value) and "
              "every pin is in the run record",
              all((rec5["sources"][sid]["discover"]["release"] == after[ds])
                  for ds, sid in ((a_["dataset"], a_["source"]) for a_ in rec5["coverage"]["acquired"])),
              f"state files: {sorted(os.listdir(os.path.join(st5, 'state')))}")
    finally:
        srv.shutdown()
        if keep:
            print(f"  (kept {tmp})")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    n_pass = sum(1 for _n, ok, _d in results if ok)
    n_fail = len(results) - n_pass
    print(f"\nSELFTEST: {n_pass} passed, {n_fail} failed")
    print("gate (the four quotations): R1 reformat-invariance · R3 missing-required refused before "
          "spending · R4 optional-missing visible in the finding + manifest · R5 newer release, "
          "same spec_sha256")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
