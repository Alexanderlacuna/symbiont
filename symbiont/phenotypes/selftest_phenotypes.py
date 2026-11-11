"""PROOF of the phenotypes arm's NSCs — offline fixtures under fixtures/conflict/, zero requests
(req-P1-THE-PHENOTYPES-ARM-the-second-dataset). The GATE names three things; this selftest is
two of them plus the accounting law:

  T1 the two-source conflict fixture: sources A and B disagree on (Brca1, MP:0001541)
     (evidence EXP vs IAGP) -> TWO rows in the store PLUS ONE counted conflict in the log;
     the identical cross-source pair on MP:0002098 is kept (both rows) and counted
     corroborated, NOT a conflict.
  T2 the missing-ontology refusal: a phenotype row with no ontology term id is REFUSED BY NAME
     (the gene is named), counted as a reject; an evidence code outside the source's declared
     vocabulary is refused by name too.
  T3 the door query: gene -> phenotypes with evidence; EVERY item carries its accession
     (MP id) and its release.
  T4 the store's accounting closes: rows_in = records + rejects + used, and a release file's
     every row is named (file_rows = rows_in + out_of_scope + malformed).
  T5 idempotency: the second run stores ZERO records and republishes byte-identical tables.
  T6 the scope refusal: a gene_scope symbol the xref table does not publish refuses the run
     BY NAME before anything is fetched.

Usage: python3 -m symbiont.phenotypes.selftest_phenotypes      (exit 0 only when all pass)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile

from . import contract as pheno
from . import loop

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "conflict")
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


def sha_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def read_tsv(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        cols = fh.readline().rstrip("\n").split("\t")
        return [dict(zip(cols, line.rstrip("\n").split("\t"))) for line in fh if line.strip()]


def make_store() -> str:
    """A temp store carrying a minimal xref table: the join keys come from the gene arm's
    published xref table — the fixture store stands in for it offline."""
    store = tempfile.mkdtemp(prefix="pheno-selftest.")
    os.makedirs(os.path.join(store, "xref"), exist_ok=True)
    with open(os.path.join(store, "xref", "xref.tsv"), "w", encoding="utf-8") as fh:
        fh.write("key\tauthority_id\tprovider_id\tstatus\treason\n")
        fh.write("Brca1\tENSMUSG00000017146\tMGI:105051\tok\t\n")
        fh.write("Actb\tENSMUSG00000029580\tMGI:88056\tok\t\n")
    return store


def main() -> int:
    print("== phenotypes selftest (offline fixtures under symbiont/phenotypes/fixtures/conflict/) ==")
    store = make_store()
    cfg = os.path.join(FIXTURES, "sources.yml")
    try:
        # run one
        r1 = loop.run(store, "pheno-fix-1", cfg)
        rec1 = json.load(open(os.path.join(store, "phenotypes", "runs", "pheno-fix-1.json")))
        ptab = os.path.join(store, "phenotypes", "phenotypes",
                            f"{pheno.PHENOTYPE_VIEW}.v{pheno.PHENOTYPE_VERSION}.tsv")
        view = os.path.join(store, "phenotypes", "view",
                            f"{pheno.GENE_PHENOTYPE_VIEW}.v{pheno.GENE_PHENOTYPE_VERSION}.tsv")
        clog = os.path.join(store, "phenotypes", "conflicts.tsv")
        rows = read_tsv(ptab)
        conflicts = read_tsv(clog)

        # T1 — the two-source disagreement: TWO rows kept, ONE conflict counted
        pair_rows = [r for r in rows if r["phenotype_term_id"] == "MP:0001541"]
        srcs = sorted({r["source"] for r in pair_rows})
        check("T1 two disagreeing sources produce TWO rows plus a counted conflict",
              len(pair_rows) == 2 and srcs == ["source-a", "source-b"]
              and {r["evidence_code"] for r in pair_rows} == {"EXP", "IAGP"}
              and len(conflicts) == 1
              and conflicts[0]["key"] == "MGI:105051|MP:0001541"
              and conflicts[0]["reason"] == "phenotype_disagreement",
              f"rows={len(pair_rows)} sources={srcs} conflict_count={len(conflicts)} "
              f"key={conflicts[0]['key'] if conflicts else '—'}")
        # the corroboration: identical payloads on MP:0002098 — both rows kept, no conflict
        corr = [r for r in rows if r["phenotype_term_id"] == "MP:0002098"]
        check("T1b agreement is kept, both rows, and is NOT a conflict",
              len(corr) == 2 and len(conflicts) == 1 and rec1["corroborated"] == 1,
              f"rows={len(corr)} corroborated={rec1['corroborated']} conflicts={len(conflicts)}")

        # T2 — the missing-ontology refusal, BY NAME; and the vocabulary refusal
        rd = rec1.get("rejects_detail", [])
        no_ont = [d for d in rd if d["reason"] == "no-ontology-id"]
        no_evid = [d for d in rd if d["reason"] == "evidence-outside-vocabulary"]
        check("T2 a phenotype without an ontology id is refused BY NAME",
              len(no_ont) == 1 and no_ont[0]["gene_id"] == "MGI:105051"
              and "refused by name" in no_ont[0]["detail"]
              and "MGI:105051" in no_ont[0]["detail"]
              and "without an ontology term id" in no_ont[0]["detail"]
              and "phenotype without an ontology term id" not in "",  # wording sanity
              f"detail={no_ont[0]['detail'][:100] if no_ont else 'MISSING'}…")
        check("T2b an evidence code outside the source's vocabulary is refused by name",
              len(no_evid) == 1 and "HEARSAY" in no_evid[0]["detail"]
              and "declared vocabulary" in no_evid[0]["detail"],
              f"detail={no_evid[0]['detail'][:100] if no_evid else 'MISSING'}…")

        # T3 — the door query: every item with accession + release
        vrows = read_tsv(view)
        brca1 = [r for r in vrows if r["symbol"] == "Brca1"]
        acc_ok = all(r["phenotype_term_id"].startswith("MP:") and r["source_release"]
                     and r["gene_id"] == "MGI:105051" and r["evidence_code"]
                     and r["phenotype_term"] for r in brca1)
        check("T3 the door query returns every item with accession + release",
              len(brca1) == 4 and acc_ok,
              f"Brca1 -> {len(brca1)} item(s): "
              + "; ".join(f"{r['phenotype_term_id']}/{r['phenotype_term']} via {r['source']} "
                          f"({r['evidence_code']}, release {r['source_release'][:16]}…)"
                          for r in brca1[:3]))

        # T4 — the accounting closes
        a = rec1["accounting"]
        srcs_acc = {sid: s["accounting"] for sid, s in rec1["sources"].items()
                    if s.get("role") == "association_provider"}
        closes = (a["rows_in"] == a["records"] + a["rejects"] + a["used"] and a["closes"] is True
                  and all(s["rows_in"] == s["records"] + s["rejects"] + s["used"]
                          and s["file_rows"] == s["rows_in"] + s["out_of_scope"] + s["malformed"]
                          for s in srcs_acc.values()))
        check("T4 the store's accounting closes (rows_in = records + rejects + used)",
              closes and a["rows_in"] == 6 and a["records"] == 4 and a["rejects"] == 2
              and a["used"] == 0,
              f"rows_in={a['rows_in']} = records {a['records']} + rejects {a['rejects']} + "
              f"used {a['used']}; source-a out_of_scope="
              f"{srcs_acc['source-a']['out_of_scope']} (Actb, named, not dropped)")

        # T5 — idempotency: run two stores nothing, republishes byte-identical tables
        before_tab, before_view = sha_file(ptab), sha_file(view)
        r2 = loop.run(store, "pheno-fix-2", cfg)
        rec2 = json.load(open(os.path.join(store, "phenotypes", "runs", "pheno-fix-2.json")))
        check("T5 the second run stores ZERO records and republishes byte-identical tables",
              rec2["accounting"]["records"] == 0
              and rec2["accounting"]["used"] + rec2["accounting"]["rejects"] == 6
              and sha_file(ptab) == before_tab and sha_file(view) == before_view,
              f"records={rec2['accounting']['records']} used={rec2['accounting']['used']} "
              f"rejects={rec2['accounting']['rejects']} (the two bad rows re-refused, as they must "
              f"be) table sha256 unchanged={sha_file(ptab) == before_tab}")

        # T6 — a scope gene the xref table does not publish refuses the run BY NAME
        try:
            loop.run(store, "pheno-fix-3", cfg, genes=["NotAGene"])
            refused, msg = False, ""
        except Exception as e:
            refused = "KeyError" not in type(e).__name__ and "NotAGene" in str(e)
            msg = str(e)[:110]
        check("T6 a scope gene missing from the xref table is refused BY NAME",
              refused, f"refusal={msg}…")
    finally:
        shutil.rmtree(store, ignore_errors=True)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"NC: {len(RESULTS) - len(failed)}/{len(RESULTS)} PASS"
          + (f" — FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
