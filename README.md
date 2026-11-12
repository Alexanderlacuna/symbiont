# SYMBIONT — a data-mining station for GeneNetwork

> **This repository is a PUBLIC SUBSET.** A few cited paths are held back by policy; each one — what it is, why, what it would prove, and how to request
> it — is listed in [`docs/WITHHELD.md`](docs/WITHHELD.md).

A **station** that goes and gets biological annotation and literature, checks every number against the source it came from, and hands the result over as
a file you can cite. It runs with **no model and no key**: the data keeps its own receipts.

```
   SOURCES                    THE STATION                                   WHAT LEAVES
   ───────                    ───────────                                   ───────────
   Ensembl 116 ─┐
                ├─► fetch ─► VERIFY ours == theirs ─► store ─┐
   MGI 2026-11 ─┘                                            │
                                 ┌────────────────────────────┘
                                 ▼
                      CORPUS  every row carries  source · release · fetched_at · sha256
                                 │
                    ┌────────────┴─────────────┐
                    ▼                          ▼
             READING DOOR                HAND-OVER
             gene_dossier · gene_xref     staged file + handover.json
             phenotype_evidence · …       a manifest that states its own counts,
             answer — refuses any        rejects, licences and hashes, so the next
             sentence it cannot bind      corpus can re-derive every number
             to a stored record
```

## What you can ask it today

Once a corpus is in place, a researcher — or an assistant acting for one — asks questions in plain language and gets evidence with provenance:

```
Which genes are in the corpus, with which identifiers?        gene_dossier · gene_xref
What phenotypes are recorded for this gene?                  phenotype_evidence      (MP terms with their citation)
Which papers support it?                                     publications_for        (PMID + evidence level)
Which traits contain this marker?                             traits_containing
What changed since last week?                                 what_changed            (per source, with the run record)
Can I ask this question at all, and if not, why?              research_check          (which dataset is missing, and whether it was never
                                                                                       admitted, never enabled, never fetched, or now stale)
```

Every item comes back with **its source, its release, the moment it was fetched, and a hash** — and the `answer` tool refuses any sentence it cannot bind to
a record in the corpus. "I don't know" is a supported answer; a guess is not.

An example of the empty case, because it matters more than the happy one:

```
phenotype_evidence  Brca1 →
   MP:0000221  decreased leukocyte cell number   accession 32359443   evidence_code not-published-in-file
   MP:0000267  abnormal heart development        accession 11420664
```

```
publications_for    Trp53 →  named empty:
   "no publication source is in this store (pubmed); zero PMIDs can be asserted, and zero is reported as a named empty result, not as a blank"
```

## What a dataset looks like

One row per gene, as MGI and Ensembl publish it — joined on identifiers we can check:

```
symbol   mgi_id       ensembl_gene_id       chromosome  start      end
Trp53    MGI:98834    ENSMUSG00000059552    11          69580359   69591797
```

77,144 rows, from **Ensembl 116** (78,348 records) and **MGI 2026-11-09** (77,645 records), each count verified against the source's own number. The
hand-over is a staged file plus a manifest stating the counts, the rejects (712 rows with no symbol), the licences and the hashes.

## What it cannot answer yet

Stated plainly, because an empty answer should never look like a biological absence:

| gap | what you see | why |
|---|---|---|
| human data | nothing | only mouse Ensembl + MGI are declared today |
| mouse phenotypes at scale | the arm is read now, but the corpus holds one small harvest | a bigger `MGI_GenePheno.rpt` harvest is a run, not a rewrite |
| a statistical comparison | nothing | the station assembles evidence; it does not run the test |
| cross-species orthology | nothing | MGI's homology file is reachable and not yet admitted |

The full statement of these limits is [`docs/DESIGN.md`](docs/DESIGN.md) §9a, and `research_check` reports them per question rather than per repository.

## Start here — three ways to run it

```sh
# A · Guix (a pinned toolchain, nothing else to install)
$ guix shell python python-pyyaml -- python3 -m symbiont.selftest_sources     # 25/25
$ guix shell python python-pyyaml -- python3 -m symbiont.selftest_digests     # 15/15

# B · plain python3 (stdlib-first; the only third-party import is PyYAML)
$ python3 -m symbiont.selftest_sources
$ python3 -m symbiont.selftest_health
$ bash annot/selftest.sh                                                     # the read doors, offline

# C · a venv, if you want the `symbiont` console command
$ python3 -m venv .venv && . .venv/bin/activate && pip install -e .
$ symbiont --help
```

**First run of a clone:** it ships *intent* (`sources.yml`) and no *history* (the store), so mining refuses until the initial admission history is
established — explicitly, never silently: `python3 -m symbiont.registry bootstrap --from-seed`. Details in [`docs/DESIGN.md`](docs/DESIGN.md) §12.

Then, with a source enabled:

```sh
$ python3 -m symbiont.loop --sources ensembl_biomart,mgi --since auto --store ./store
$ python3 -m symbiont.manifest --store ./store --entity mouse_gene --out handover.json
```

## Ask it from an agent

The station speaks MCP in two directions, and they are separate authorities: a **read** door for asking (the seven tools above), and a **machine** door for
operating (health, probe, evaluate, sandbox, card, admit). Ingestion is a third actor — the loop. A clone answers from the committed fixture store, so an
assistant can be connected before any data is mined.

## For operators

[`README-TECHNICAL.md`](README-TECHNICAL.md) — the engineering view: how a source becomes data (five stages), the two surfaces, the transports,
and the first-run step. Kept under its own name rather than deleted.
`docs/DESIGN.md` — the lifecycle, the refusal classes, the read door's contract, federation, and the bootstrap.
`docs/MANIFEST.md` — what a consumer of the hand-over may rely on.
[`docs/backups/`](docs/backups/) — earlier versions of this README, kept rather than deleted.

## Licence and terms

The station's code is MIT (see `LICENCE`). The data carries each source's own terms — MGI is CC-BY 4.0, Ensembl has no restrictions, Europe PMC governs
per record — and every row names the terms it was stored under: [`docs/SOURCE-TERMS.md`](docs/SOURCE-TERMS.md).
