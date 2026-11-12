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

### Sophie — the sentinel

The thing that does the looking has a name: **Sophie**. She is the sentinel — the part of the station that keeps an eye on the sources you already trust and,
when you allow it, goes looking for new ones. The name is a nickname, not a product mark (the word is already taken by a genomics company); in code she is
`symbiont/cycle.py`, and you address her three ways:

```
cycle_status     what she is set to do, when she last did it, what it cost, what the last report said — read-only
cycle_run_once   one bounded run now (telemetry, then discovery if enabled), on her own budget, writing a CYCLE-REPORT and a run record
cycle.yml        her settings: enabled · mode (watch | discover | both) · cadence · surface registry · budget · the admission default
```

Her two lights, again, because it is the whole idea: the **watching** light ships on and never changes anything; the **discovery** light ships off and only
proposes. A quiet week sounds like this — *"I looked: 1 surface · 7 queries · 0 new"* — and a busy one ends in candidates with their refusals, never in
something admitted behind your back.

### Keeping it current — the schedule

Nothing here depends on you remembering to run it. Six small jobs, one wrapper (`symbiont/station-job.sh`), each with a heartbeat so a missed run is visible:

```
weekly (Tue)     fetch  ensembl_biomart        re-fetch one enabled source, verify its count, publish or refuse
weekly (Tue)     fetch  mgi
daily            digest                        re-measure the store: releases, gaps, staleness, counts
hourly           health                        per-source state: ok · degraded · down · quarantined
daily            manifest                      refresh the hand-over artefact
weekly (Sat)     discovery                     the outward light (only does anything when cycle.yml says enabled: true)
```

`cycle.yml` owns the cadence, the surface registry, the budget and the admission default — so "check for updates" and "go looking for new sources" are the
same interface, one switch apart. A run that finds nothing says so in words: *"I looked: 1 surface · 7 queries · 0 new"*.

### Plugging it in — what orchestration means here

The machine is driven by files, not by a daemon, so anything that can write a file can orchestrate it: a cron line, a CI job, an agent, another station.

```
surfaces registry   WHERE it may look            (a text file you own; no registry ⇒ REFUSED[no-surfaces])
research spec       WHAT a question requires     (arms · filters · required/optional datasets; hashed into the run record)
cycle.yml           HOW FAR it may go            (mode · cadence · budget · admit default)
sources.yml         WHAT it may fetch            (per-source licence, rate limit, budget, enabled)
```

A *research spec* is the interesting one: it is an executable contract — run it and the station either proves coverage or refuses with `REFUSED[missing-dataset]`
before spending a request. Owner-governed orchestration (a route with checkpoints that inherits, and can only narrow, the station's policy) is designed and
**not built**: [`docs/DESIGN.md`](docs/DESIGN.md) §11.

### Owner-defined orchestration — designed, not built

Discovery can already be pointed, budgeted and switched off. What is **designed** is for an owner to define a whole *route* — an expedition — and the policy
it runs under, with the station's own authority as the ceiling:

```
an expedition   a route of checkpoints (look here → probe these → sandbox the ones that pass → stop)
owner policy    three ORTHOGONAL grants, never one "autonomy level":   WRITE (none · propose · acquire · publish)
                                                                       SPEND (requests · bytes · wall · per-host)
                                                                       ATTENTION (which transitions need a human — admit by default)
inheritance     effective = meet(station, expedition):  permissions intersected · prohibitions unioned · budgets minimised · approvals unioned
                ⇒ a route may narrow what it is allowed to do, and can never widen it
refusals        every one names its layer:  REFUSED[admission-denied by=station] · REFUSED[expedition-prohibits op=admit] · REFUSED[budget by=…]
accounting      the effective budget flows into the ONE place that charges a request, so a budget is enforced, not respected
provenance      the expedition is a third actor in the log (actor: expedition:<name>) and the route's sha is pinned in the run record and the manifest
```

Status: proposal only — [`docs/DESIGN.md`](docs/DESIGN.md) §11 carries the note marked **PROPOSED, NOT BUILT**, and two p2 rows hold the work
(`GRANTS-AND-INHERITANCE`, `THE-EXPEDITION-IS-A-THIRD-ACTOR`). The principle it protects: **an expedition may organise authority; it cannot manufacture it.**

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

A hand-over is a staged file plus a manifest: rows, counts, release, licence, per-row provenance, hashes. That is the same shape as any other source, so a
station can point at **another station's hand-over** — and keep pointing, station to station:

```
   ┌───────────┐   hand-over: staged file + manifest     ┌───────────┐   hand-over: staged file + manifest     ┌───────────┐
   │ STATION A │ ────────────────────────────────────►   │ STATION B │ ────────────────────────────────────►   │ STATION C │
   │ collects  │   rows · counts · release · licence     │ enriches  │   rows · counts · release · licence     │ researches│
   │ raw       │   per-row source+release+sha256         │ + joins   │   per-row source+release+sha256         │           │
   └───────────┘                                        └───────────┘                                        └───────────┘
        ▲                                                     ▲                                                     │
        │  the original authority (e.g. a public database)    │  C can walk the chain back: C → B → A → authority    │
        └─────────────────────────────────────────────────────┴─────────────────────────────────────────────────────┘
```

**Connections are not transitive; provenance is.** C never talks to A, but every row C holds can name the manifest it came through *and* the authority that
produced it, because each hop keeps both levels and copies the licences forward. A chain that loops back to a dataset you already publish is refused
(`REFUSED[provenance-cycle]`). Contract: [`docs/MANIFEST.md`](docs/MANIFEST.md) §4. Honest limit: a federated row can claim `verified: manifest` (the copy
agrees with its manifest) and never `verified: upstream` (re-derived from the authority) — federation gives you the integrity of a copy, not the truth of a
re-derivation.

### An MCP server can be a source

Any MCP server that exposes a **count** can be pointed at like any other source — the station has a generic adapter (`symbiont/adapters/mcp_source.py`) and
ships one pinned example: `@cyanheads/pubmed-mcp-server` 2.12.0, consumed over stdio as the literature arm. The rule is the same as for every source: *a
tool that cannot say how many records it has is a tool, not a source* — `REFUSED[no-count-endpoint]`. That single rule is why a repository is refused as a
source and an MCP that answers "0 of 399" is accepted.

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

## What you could build with it

Because the guarantees are generic, the same machine answers very different questions. Four that we have actually run or wired:

```
CROSS-SECTIONAL SPECIES COMPARISON   mouse evidence × human evidence at one time point: gene identity + phenotypes + orthologs + disease terms + papers,
                                     assembled with a hashed spec that refuses when a required dataset is missing
REGIONAL SURVEILLANCE                reference resistance mechanisms × a region's published surveillance reports — with the honest note that aggregate
                                     percentages cannot be joined to individual isolates
MARKET / COST SCOUTING               provider tables × measured latency, each row pinned to the vendor's own revision of the table
A CORPUS A LAB CAN RE-RUN            any of the above handed over as a manifest, so a reviewer re-derives the numbers years later
```

And the composability above means a research group can **share collections without sharing infrastructure**: publish a hand-over, and the next group ingests it
with the chain and the licences intact.

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

### The reading door is extensible

The seven tools are a starting set, not a limit. A deployment adds a read tool the way `research_check` was added — as an eighth — and it inherits the same
contract automatically: one of the closed statuses (`ok · empty · refused · unavailable · error`), provenance on every item (`source · release · fetched_at ·
sha256`), a refusal by name rather than a crash, and **read-only** — so extending the door can never widen what the station is allowed to do. That is the
point of the split: asking grows with the questions; permission does not grow with the answers.

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
