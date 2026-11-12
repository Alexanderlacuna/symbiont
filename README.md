# SYMBIONT — a data-mining machine you can point at any source

> **Public subset.** A few cited paths are held back by policy; each one — what it is, why, what it would prove, how to request it — is in
> [`docs/WITHHELD.md`](docs/WITHHELD.md).

SYMBIONT is a **machine for turning sources into a corpus you can defend**. It finds candidate sources, judges whether they can be used, writes the
adapter, trials it in a sandbox, writes the evidence card, and admits it — then fetches, verifies, stores and hands over. Point it at anything that publishes
rows and states a count of its own; **the biological corpus in this repo is one example, not the product.**

## What the machine does

```
· FETCHES        public releases and APIs over plain HTTPS — no SDK, no scraping of pages, no browser
· VERIFIES       the row count against the source's own count, and refuses the run if they disagree
· STORES         every row with source · release · fetched_at · sha256 — a claim you cannot re-derive is not evidence
· REFUSES BY NAME  no count endpoint? no readable licence? stale? vanished? each refusal is a class, never a blank
· DETECTS CHANGE  releases move, counts drift, sources go quiet — the station notices and says so
· HANDS OVER     a staged file plus a manifest that states its own counts, rejects, licences and hashes
· ANSWERS        through an MCP door where every item carries provenance and `answer` refuses a sentence it cannot bind to a record
· FEDERATES      one station's hand-over is another station's source, with the chain and the licences kept
```

## It grows its own corpus — under governance

The machine does not only fetch sources you already trust. It can find new ones and take them through a gate in which **each step has a refusal**:

```
1 DISCOVER   search the surfaces you declared (a registry file you own)      REFUSED[no-surfaces] if you declared none
2 EVALUATE   can it be used? rows · a count of its own · a release · a readable licence · a schema · a stable URL
                                                                            REFUSED[no-rows] [no-count-endpoint] [no-release]
                                                                            [licence-unreadable] [no-schema] [derived-without-authority]
3 DRAFT      write the adapter from the template, run the contract's load check
4 SANDBOX    run it into a THROWAWAY store under a budget, with a per-host network allowlist    REFUSED[budget] [sandbox-breach]
5 CARD       the evidence page a person decides on: licence · counts matched · sample rows · cost · the question it helps answer
6 ADMIT      THE gated step — flips the source active. Consults the owner's policy first; with `admit.by_policy: false` it refuses without a written ruling
```

### Two layers of looking, and they ship differently

```
WATCH     always on, read-only.   What you already trust: releases moving, counts drifting, digests, gaps, staleness. It changes nothing and costs
                                  ~0 requests (it re-reads the store and the run records). This is the layer you leave running.
DISCOVER  off by default.         The outward one: search declared surfaces, evaluate candidates, draft, sandbox, card, PROPOSE. It leaves the station
                                  for the network, so it ships disabled — `cycle.yml: enabled: false` means it does nothing at all, not even a DNS lookup.
```
Two lights, one sentinel: **reading is harmless, changing the corpus is not** — so the reading light is on and the outward one is a deliberate act.

So the corpus can grow without a human reading every candidate — and it cannot grow *past* a human, because step 6 is a permission and steps 1–5 only
propose. That is the part worth pointing at: **a station that expands its own inputs while staying auditable**, with every refusal kept as evidence
(we have 61 refused repositories, 3 refused data portals and their reasons on file).

## The architecture is the product

Everything else is one deployment of it. Five invariants hold for every source and every domain:

```
1 NO ROW WITHOUT ITS SOURCE, RELEASE AND HASH
2 A COUNT IS VERIFIED AGAINST THE SOURCE'S OWN NUMBER
3 NO SILENT FAILURE — every refusal names its class; a short run refuses rather than shipping quietly
4 ADMISSION IS NOT PERMISSION — a recorded decision with a card ≠ a human's one line ≠ the loop that actually fetches
5 THE MANIFEST IS THE ONLY CROSS-BOUNDARY ARTEFACT — it states its own integrity and its own limits, and assumes nothing about its reader
```

```
   SOURCES                    THE STATION                                   WHAT LEAVES
   ───────                    ───────────                                   ───────────
   Ensembl 116 ─┐
   MGI 2026-11 ─┤
                ├─► fetch ─► VERIFY ours == theirs ─► store ─┐
   another ─────┘        (a source must state its own count) │
   SYMBIONT's
   hand-over
                                 ┌────────────────────────────┘
                                 ▼
                      CORPUS   every row: source · release · fetched_at · sha256
                                 │
                    ┌────────────┴─────────────┐
                    ▼                          ▼
             READING DOOR                HAND-OVER
             gene_dossier · gene_xref     staged file + handover.json
             phenotype_evidence · …       counts · rejects · licences · hashes
             answer — refuses any         — a next station or a script can
             sentence it cannot bind      re-derive every number
             to a stored record
```

## What you can ask it today

A researcher — or an assistant acting for one — asks in plain language and gets evidence with provenance:

```
Which genes are in the corpus, with which identifiers?    gene_dossier · gene_xref
What phenotypes are recorded for this gene?              phenotype_evidence   (MP terms with their citation)
Which papers support it?                                 publications_for     (PMID + evidence level)
Which traits contain this marker?                        traits_containing
What changed since last week?                            what_changed         (per source, with the run record)
Can I ask this at all, and if not, why?                   research_check       (which dataset is missing — never admitted,
                                                                              never enabled, never fetched, or now stale)
```

Two real answers, because the empty case matters more than the happy one:

```
phenotype_evidence  Brca1 →
   MP:0000221  decreased leukocyte cell number   accession 32359443   evidence_code not-published-in-file
   MP:0000267  abnormal heart development        accession 11420664

publications_for    Trp53 →  named empty:
   "no publication source is in this store (pubmed); zero PMIDs can be asserted, and zero is reported as a named empty result, not as a blank"
```

## It is not tied to these sources

The corpus below is mouse annotation because that is what this deployment **points at**. Any source that publishes **rows**, states **a count of its own**,
and carries a **readable licence** can be added — one config entry and one adapter, then the same funnel: probe → evaluate → sandbox → card → admit.
Different domains, different languages of data, same machine.

What changes per source is only **what may be claimed**: `verified: upstream` when the source states its count, `verified: file` when ours equals the rows
in the file it shipped. A repository with no count stays a *tool*, and is refused as a source by name.

## It composes — one station can be another station's source

A hand-over is a staged file plus a manifest: rows, counts, release, licence, per-row provenance, hashes. That is the same shape as any other source, so
**station A can point at station C's hand-over through B** and get a corpus of corpora. The chain is kept: every row names the manifest it came through *and*
the authority that produced it, licences are copied forward transitively, and a chain looping back to a dataset you already publish is refused. Contract:
[`docs/MANIFEST.md`](docs/MANIFEST.md) §4; honest limit: a federated row can claim `verified: manifest`, never `verified: upstream`.

## What a run produces (a test drive, not a delivery)

Nothing here has been handed over to anyone yet. This is the artefact shape from one run we made on our own machine, from public data, in ~35 s:

```
symbol   mgi_id       ensembl_gene_id       chromosome  start      end
Trp53    MGI:98834    ENSMUSG00000059552    11          69580359   69591797

77,144 rows   Ensembl 116 (78,348 records) + MGI 2026-11-09 (77,645 records) — both counts verified against the sources' own
              712 rows rejected (no symbol), counted not dropped
sha256        view 4c9795b9…   schema c985bdd5…
cost          6 requests · 3.7 MB · 33.6 s · a second run fetched 0 new bytes
hand-over     staged file + handover.json (counts · rejects · licence per source · per-row source+release+fetched_at+sha256)
```

Reproducible from the two public releases named above. Not a curated delivery, and no claim about completeness — only that every number can be re-derived.

## What this deployment has not wired *yet*

A to-do list, not a property of the machine:

| not wired here | what you would add | already checked |
|---|---|---|
| human side (HPO, MONDO, human Ensembl) | an entry + adapter per source | MONDO's release assets and CC-BY-4.0 licence verified by hand |
| cross-species orthology | MGI's `HOM_MouseHumanSequence.rpt` | reachable, HTTP 200, on the host this station already trusts |
| a phenotype harvest at scale | `MGI_GenePheno.rpt` end to end | the door reads the arm; the store holds one small harvest |
| statistics and interpretation | nothing here — that layer sits above the corpus | the station assembles evidence; it does not run the test |

Full statement: [`docs/DESIGN.md`](docs/DESIGN.md) §9a. `research_check` answers it per question rather than per repository.

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

MCP in two directions, deliberately separate authorities: a **read** door for asking (the seven tools above) and a **machine** door for operating (health,
probe, evaluate, sandbox, card, admit). Ingestion is a third actor — the loop. A clone answers from the committed fixture store, so an assistant can be
connected before any data is mined.

**What needs a model or a key, exactly:** no model is used anywhere. The core needs no key — it reads public files over plain HTTPS, and answers offline once
a store exists. Discovery asks a public search API (an optional token only raises its rate limit), and the literature arm talks to a pinned MCP server over
HTTP. The receipts never depend on either.

## For operators

[`README-TECHNICAL.md`](README-TECHNICAL.md) — the engineering view: the five stages, the two surfaces, the transports, the first-run step.
[`docs/DESIGN.md`](docs/DESIGN.md) — lifecycle, refusal classes, the read door's contract, federation, bootstrap.
[`docs/MANIFEST.md`](docs/MANIFEST.md) — what a consumer of the hand-over may rely on.
[`docs/reference/`](docs/reference/) — earlier versions of this README, kept as reference.

## Licence and terms

The station's code is MIT (`LICENCE`). The data carries each source's own terms — MGI is CC-BY 4.0, Ensembl has no restrictions, Europe PMC governs per
record — and every row names the terms it was stored under: [`docs/SOURCE-TERMS.md`](docs/SOURCE-TERMS.md).
