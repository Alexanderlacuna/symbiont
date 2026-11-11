# SYMBIONT — the data-mining station

> **This repository is a PUBLIC SUBSET.** Some of the paths it cites are held back, by policy.
> Every one of them — what it is, why, what it would prove, how to request it — is in [`docs/WITHHELD.md`](docs/WITHHELD.md).

A **data-mining station** for biological annotation and literature. It fetches from a source, verifies its row
count against the source's own count, stores every row with its **source, release and hash**, and hands the
corpus over as a staged dataset plus a self-describing manifest. Nothing states a number it cannot re-derive.

## How a source becomes data — five stages, one page

| stage | who owns it | what it produces |
|---|---|---|
| **discovered** | the operator's surface registry | a candidate with an id and a url |
| **evaluated** | the machine | the six-point source spec + one sandbox trial whose count must match |
| **admitted** | the machine, recorded | a card and a decision, with evidence |
| **enabled** | the operator, one line | permission to fetch — **admission is not permission** |
| **acquired** | the machine | verified rows, a view and a manifest |

After acquisition the relationship continues: releases move, counts drift, sources go stale or vanish — and a source can be retired on purpose. The full
lifecycle, the event classes, the replay rules and the rules for adding a source by hand are in [`docs/DESIGN.md`](docs/DESIGN.md).

## Two surfaces — asking, and operating

The station speaks MCP in two directions on purpose, and they are separate authorities:

| surface | command | the seven tools | may it change anything? |
|---|---|---|---|
| **read** — asking | `python3 -m annot.mcp_server` | `gene_dossier` · `gene_xref` · `phenotype_evidence` · `publications_for` · `traits_containing` · `what_changed` · `answer` | **no** — read-only over the store; every item carries source, release, fetched_at and accession, and `answer` refuses a sentence it cannot bind to a record |
| **machine** — operating | `python3 -m symbiont.mcpserver` | `source_health` · `source_probe` · `source_evaluate` · `source_draft` · `source_sandbox` · `source_card` · `source_admit` | yes, but only **admission** — it never fetches or stores rows |

Ingestion is a third actor: the loop (`python3 -m symbiont.loop`) is the only thing that widens the corpus, and it cannot widen permissions. And the
**manifest** is a third surface that needs no authority at all — an uploader inside another project, a bucket loader, or `sha256sum` reads the staged file
plus `handover.json` without touching either door. The reading layer exposes evidence; it does not interpret it (see [`docs/DESIGN.md`](docs/DESIGN.md) §9).

## Start here — the quickstart

First command. No install, no network, no key (Python 3.10+ and one YAML reader):

```sh
$ python3 -m symbiont.selftest_sources        # 25/25 — the plug-in contract and the refusals, offline
```

**What that 25/25 covers, and what it does not:** it is the four-call plug-in contract and the four refusal
classes held **offline, against fixtures served from 127.0.0.1, at miniature scale** — contract-level evidence
and nothing more, so it is *not* a full-scale data test (T4's drop-and-rebuild of the 77k-row store is proved
only at miniature scale), T10's retrieval number is not built, and T8 (an agent answering from tool output) is
declared expected-fail in `make check`'s own output.

Then the five things you can do with it:

| you want to… | run this | what you get |
|---|---|---|
| **mine** (no model needed) | `python3 -m symbiont.loop --sources mgi --since auto --store ./store` | rows + a run record; a second run fetches nothing |
| **hand over** | `python3 -m symbiont.manifest --store ./store --entity gene --out manifest.json` | the staged file + the manifest (sha256, counts, rejects, licence) |
| **ask** it questions (MCP) | `python3 -m annot.mcp_server` | 7 read tools with citations: dossier · xref · phenotype evidence · publications · traits · what changed · `answer` |
| **grow / operate** it (MCP) | `python3 -m symbiont.mcpserver` | 7 machine tools: `source_health` · `probe` · `evaluate` · `draft` · `sandbox` · `card` · `admit` |
| **serve** it over HTTP | `python3 -m annot.rest --port 8765` | one route per read tool, the same bytes as the CLI |

An agent needs no more than a config block:

```json
{ "mcpServers": {
    "symbiont-read":    { "command": "python3", "args": ["-m", "annot.mcp_server"] },
    "symbiont-machine": { "command": "python3", "args": ["-m", "symbiont.mcpserver"] } } }
```

**What the station is worth** is under **What you get**, immediately below. **Setup** — Guix, plain `python3`,
venv+pip — is the `## Setup` section after it, and the station's growth policy is under **Growing the corpus**.

**What you get:**

1. **Provenance you can cite.** Every row names the source, the release and a content hash, so a figure in a paper can be re-derived
   a year later — not "latest", not "we think so". That is the difference between a dataset and evidence.
2. **Agents can use it, without a bespoke integration.** It speaks **MCP** in both directions: your agent asks questions through the
   read doors (dossier · xref · phenotype evidence · publications · traits · what changed · citation-required `answer`), and it can
   *operate* the station through the machine doors (health · probe · evaluate · draft · sandbox · card · admit). Two servers on purpose:
   asking can be exposed widely, admitting stays with an operator.
3. **The corpus can grow itself — but only if you say so.** Sophie's discovery light finds new sources, evaluates their licence, drafts an
   adapter, proves its count in a sandbox and proposes admission; it ships OFF so no deployment inherits our appetite for growth, while her
   watching light (health · staleness · gaps) ships ON. The details are in **Growing the corpus** below.

It runs with **no model and no key**; models appear only in those optional parts, and a deployment supplies its own. No secrets are mounted
into the sandbox, and an adapter may reach only the host it is for.

## Setup — Guix first (recommended), two fallbacks below

**A · Guix (exact toolchain, nothing installed globally)** — best if you have Guix; GeneNetwork's machines do.

```sh
$ guix shell python python-pyyaml -- python3 -m symbiont.selftest_sources
$ guix shell -C python python-pyyaml -- python3 -m symbiont.selftest_digests     # sandboxed shell
$ guix shell bubblewrap python python-pyyaml -- python3 -m symbiont.selftest_sandbox   # with bubblewrap, if your box lacks it
```

**B · Plain `python3` (nothing to install, if you have no Guix)** — the station is stdlib-only apart from one YAML reader.

```sh
$ git clone <this repo> && cd <repo>
$ python3 -m symbiont.selftest_sources        # 25/25 — no install, no network (scope: the sentence above)
$ bash annot/selftest.sh                     # 34/34 the read doors, offline, same contract-level scope
```

**C · venv + pip (only if you want the `symbiont` console command)**

```sh
$ python3 -m venv .venv && . .venv/bin/activate && pip install -e .
$ symbiont --help
```

Minimum requirements: Python 3.10+, `python-pyyaml`, and (for the sandbox) `bubblewrap` or rootless namespaces —
if neither exists the station **refuses to run untrusted code**, by name, rather than running it unsandboxed.

---

Every command below can be run **inside Guix** by prefixing it:

```sh
$ guix shell python python-pyyaml -- <the command>
```


### The Guix package definition (optional, for Guix sites)

The repo carries a `guix.scm`, in the same idiom as `genenetwork4`'s own:

```sh
$ guix build -f guix.scm                       # build the station as a package
$ guix shell -D -f guix.scm                    # a development shell with the right toolchain
$ guix shell -D -f guix.scm -- python3 -m symbiont.selftest_sources    # 25/25
$ guix pack -f guix.scm                        # a relocatable tarball, for a handover
```

**One dependency decision, made once and repeated in every file that names it** (`guix.scm`, `pyproject.toml`, `symbiont/README.md`, `CHANGELOG.md`):
**the requirement is one range, `PyYAML>=6.0,<7`** — the suites ran on 6.0.3, the Guix recipe builds 6.0.2, and the
and propagates, so the two recipes cannot disagree. `make check` asserts it rather than leaving it to
a reader: the dependency-version step greps every file in the tree and fails on a second version, on
a range, and on a file that names the dependency with no version. A cold first
`guix build` takes minutes; that is the cost of a pinned toolchain, not a fault.

---

## Using the station from an agent (MCP)

The station speaks MCP in two directions. Both servers are **stdio** JSON-RPC — no daemon, no port, no network needed to answer.

```sh
# 1 · the READ DOORS — ask the corpus, with citations
$ python3 -m annot.mcp_server
    gene_dossier · gene_xref · phenotype_evidence · publications_for · traits_containing · what_changed · answer
    (`answer` is the citation-required mode: a sentence without an accession we hold is returned in `rejected`, with the reason)

# 2 · the MACHINE DOORS — the admission pipeline as tools
$ python3 -m symbiont.mcpserver
    source_health · source_probe · source_evaluate · source_draft · source_sandbox · source_card · source_admit
```

In an MCP client (Claude Desktop/Code, pi, or any stdio client):

```json
{ "mcpServers": {
    "symbiont-read":    { "command": "python3", "args": ["-m", "annot.mcp_server"] },
    "symbiont-machine": { "command": "python3", "args": ["-m", "symbiont.mcpserver"] } } }
```

A clone answers out of the box: with no environment set the read doors answer from the committed fixture store `symbiont/fixtures/store-doors` (241 published rows, hashes and licence lines intact) until you mine your own store, and every response says which store answered in its `store:` line — `store: fixtures/store-doors — no live store found`, or `store: symbiont/store — the live store` once a harvested store is present.

**Where the store comes from.** The store is chosen when the SERVER STARTS (environment or the server's own config), not per tool call.
A call that passes a `store` argument gets a named error rather than a guess:

```
$ tools/call gene_dossier {"gene": "Trp53", "store": "…"}
  → {"status":"error","reason":"bad_request",
     "message":"gene_dossier does not take store; it takes gene, limit",
     "tools":["gene_dossier","gene_xref",…]}
```

**What an agent can actually do**, end to end: `source_probe` (does a source exist?) → `source_evaluate` (licence, accessions, rate limit) →
`source_draft` (a first adapter) → `source_sandbox` (run it in a throwaway store, budgeted) → `source_card` (the admission card) →
`source_admit` (flip it on, gated by policy) — and meanwhile `source_health` answers "is this source healthy, and if not, why" with one
reason per source. None of that needs a model on our side; the agent supplies its own.

The station also **consumes** MCP: the literature arm wraps the pinned PubMed MCP (`@cyanheads/pubmed-mcp-server` 2.12.0, Apache-2.0 read on
disk) rather than hand-writing E-utilities.

**Sophie has two lights.** The *watcher* light (ships ON) reports what moved, what is stale, what is missing. The *discovery* light (ships OFF)
finds sources you do not have and proposes them. Neither light decides anything: admitting is a separate, gated act.

**The discovery light is what sends scouts.** A scout is a named question with a destination — "does an MCP exist for this source?",
"how do cBioPortal and Monarch ingest?", "what is the cheapest model that actually answers a 4 KB brief?" — sent out over the outward senses
(Exa for prose and docs, the surface registry for code/package/paper/dataset indexes). A scout returns a page with its evidence, not an opinion:
every claim carries the URL and the date it was read.

**An expedition is a scout with a route** — still inside the discovery light, only longer-lived. Instead of one question, a route of checkpoints: *find → evaluate licence → draft adapter → prove the
count in a sandbox → write the card → admit (gated)*, each checkpoint falsifiable and none droppable, with a budget and a manifest at the end.
Cargo, not commentary. Expeditions are **designed, not built** (see `docs/BLUEPRINT-SHIP-TEMPLATE.md`); scouts are how this repository's own
research was done — the three scout reports in `reports/` are their output.

## Growing the corpus — discover, evaluate, admit (with or without an agent)

The station is not a fixed corpus. It can find new sources, judge them, test them, and hand them to a human
or a policy to admit. Three levels, honestly labelled:

| level | what happens | state |
|---|---|---|
| **1 · add a source you already know** | one adapter file + one entry in `sources.yml` — no code, no model (the recipe is spelled out right below) | **works today** |
| **2 · an agent does the paperwork** | the machine doors: `source_probe` → `source_evaluate` → `source_draft` → `source_sandbox` → `source_card` → `source_admit`. A human (or policy) still admits. | **works today** — the tools exist and answer |
| **3 · the station grows itself** | Sophie: **watch** ships ON, **discover** ships OFF, admit-by-policy ships OFF | **designed, not built** — see `docs/DESIGN-SENTINEL.md` |

```sh
# level 2, today, from any MCP client: check a source, then evaluate a candidate
$ python3 -m symbiont.mcpserver        # source_health · source_probe · source_evaluate · source_draft · source_sandbox · source_card · source_admit
$ bash tools/discover-sweep.sh†        # the sweep: listed queries, each logged with its count, dedupe across weeks, refusals by rule
```

† held back from this public subset: the sweep is a farm instrument, not station code — see [`docs/WITHHELD.md`](docs/WITHHELD.md).

What the sweep looks at is **data**, not code: a surface registry of code hosts, package indexes, MCP hubs,
papers, dataset registries, standards and prose — so a deployment points it at the corners of the world *it*
cares about. Its first run: 8 queries · 123 candidates seen · **62 refused** by licence/dead-archive rules.

**Sophie — the sentinel** — the station watching its own sources and growing its own corpus — is **designed; watch ships ON, discovery ships OFF**:
`symbiont/cycle.py` with `cycle.yml`: **watch ON** (read-only) and **discover OFF**. Read `docs/DESIGN-SENTINEL.md` before enabling discovery.

She has two modes with **different defaults**: `--mode watch` (reports what moved, what is stale, what is missing — she only reads) ships
**ON**, because a station that cannot tell you your corpus is stale is not a station; `--mode discover` (finds sources you do not have,
drafts, sandboxes, and asks to admit) ships **OFF**, because it changes the corpus. Two authorities stay separate on purpose: **asking** (the read doors) can be exposed
widely, **admitting** (the machine doors) stays with an operator.



### Adding a source by hand — the whole recipe

Two files. That is it. (`symbiont/docs/PLUGIN-AUTHORING.md` is the long version.)

```sh
# 1 · the file: symbiont/adapters/<name>.py — four calls, and that is the whole interface
#     discover(http, since) · fetch(http, since) · normalise(resources) · verify(discovery, normalised)
#     the class declares  name = "<name>"  — the SAME string you will use in the config
$ $EDITOR symbiont/adapters/my_source.py

# 2 · the entry: sources.yml — one block, and every field is data
#     id · base_url · endpoints · rate_limit · licence_url · licence_text · enabled · budget{requests,bytes,wall_s}
$ $EDITOR symbiont/sources.yml

# 3 · prove it, in a throwaway store, before it touches yours
$ python3 -m symbiont.loop --source my_source --since auto --store /tmp/try --budget-requests 5
```

Three rules the loader enforces, all learned the hard way:

* **the name is the key.** The adapter's `name`, the `sources.yml` id and the `--source` argument must be the same string. A file whose
  declared name already exists is ignored — so rename the class attribute too, not just the filename.
* **a source must be able to say how many rows it has.** `verify()` compares our count with the source's own count; a source that cannot
  produce a count cannot be admitted, whatever transport it uses (REST, dump file, or an MCP server).
* **no code change is ever needed in `loop.py`.** If adding a source requires editing the loop, the contract has been broken — that is a bug
  in the adapter, not in the station.
### If you need a new source — three ways, all legitimate

| if the source… | the way | what you write |
|---|---|---|
| **speaks REST or ships a dump file** | **A · two files** | `symbiont/adapters/<name>.py` (the four calls) + a block in `sources.yml` |
| **is an MCP server** | **B · two files, MCP as the transport** | the same two files — the adapter calls the MCP tool instead of HTTP. The loop does not care about transport; it cares that `verify()` can compare counts. Our literature arm already does this with the pinned PubMed MCP. |
| **you would rather not hand-write** | **C · let the station do the paperwork** | nothing: an agent drives `source_probe → source_evaluate → source_draft → source_sandbox → source_card → source_admit`, and a human or the policy admits |

```sh
# all three end the same way: prove it in a throwaway store before it touches yours
$ python3 -m symbiont.loop --sources <name> --since auto --store /tmp/try
```

**The one hard requirement, whatever the transport:** the source must be able to say **how many rows it has**, so `verify()` can compare our
count with the source's own. A thing that cannot produce a count can still be used as a **tool** (search, lookup, an agent's helper) but it can
**never be admitted as a source** — the station refuses it by name. A corpus you cannot count is a corpus you cannot defend.

**This repository is a public subset** — the engine, its tests, the fixtures and the design records. Files held back (and why) are listed in
[`docs/WITHHELD.md`](docs/WITHHELD.md). A **†** on a cited path in this file means exactly that: held back, see that page.

## What is in this repository

| path | what it is |
|---|---|
| `symbiont/` | the station: `loop.py` (the orchestrator), `contract.py` (the 4-call plug-in contract), `adapters/` (ensembl_biomart, mgi), `health.py`, `admission.py` + `admission_policy.py` (the gated source pipeline), `manifest.py` (the handover manifest), `literature/` (the second arm, wrapping the pinned pubmed MCP server), `mcpserver.py` (the source tools over MCP), `cli.py` (the `symbiont` console door), selftests, `sources.yml` (the one definition site) |
| `symbiont/fixtures/store/` | the committed miniature store: raw + state + xref + view + three run records (fetch · zero-byte second run · rebuild-from-raw) — read it before fetching anything; regenerate with `make fixtures-store` |
| `symbiont/fixtures/store-doors/` | the committed fixture store the read doors' suite runs against in a clean clone: 241 published rows, raw content-addressed + run/state records + xref + view, hashes and licence lines kept — no install, no network |
| `symbiont/docs/` | OPS.md (backup, restore, size) · OBS.md (heartbeat, the ONE command) · PLUGIN-AUTHORING.md (the contract, the pipeline, sandbox, card, gate) · POLICY-TEMPLATE.yml (numbers blank, fail-closed) |
| `annot/` | the read doors over a store (six tools, one definition, four surfaces: CLI · MCP · REST · page) |
| `reports/` | what this repository was built from and can publish: the decision records and the owner's live `reports/symbiont-policy.yml`. The rest of the record — the design (DESIGN-STATION.md†), the ship-run report†, the MCP survey†, the incidents — is held back, each one a row in [`docs/WITHHELD.md`](docs/WITHHELD.md) |
| `harvester-view/` | the published view from the first ship run (77,144 rows — measured on the private store; the receipt is `runs/harvest-run-1.json`, store path redacted, available on request) |
| `LICENCE` · `SUPPORT.md` · `CHANGELOG.md` · `Makefile` · `pyproject.toml` | the licence (MIT for code; per-source data terms recorded) · the support boundary · the version history · the nine tests · the package |

