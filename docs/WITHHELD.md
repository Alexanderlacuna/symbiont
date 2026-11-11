# WHAT IS WITHHELD FROM THIS PUBLIC TREE — the one file a dead citation lands on

This repository is a **public subset** of a working mining station. The engine, its tests, the fixtures and
three design records are here. Some paths that this tree *cites* are not here; each one is a row below, with
**what it is · why it is not here · what it would prove · how to request it**. Nothing is withheld silently:
`tools/withheld-reconcile.py` reconciles the paths this tree cites against the paths it has, and fails if a
citation is neither present nor declared here.

A **†** next to a citation in a reader-facing document (a `*.md`, the `Makefile`, `symbiont/cycle.yml`) means
"this path is a row below". In source comments (`.py`, `.sh`) the same names appear as provenance annotations
— `DESIGN-STATION §4b` in `admission.py` says where a rule came from — and those are declared here too.

## WITHHELD BY POLICY — † on every citation

| withheld path | what it is | why it is withheld | what it would prove | how to request |
|---|---|---|---|---|
| `reports/DESIGN-STATION.md` | the full station design — cited throughout as "design §1b / §4b / §4c / §6 / §7 / §9 / §9b / §11b" | farm-only by DESIGN §1b: it carries farm-internal vocabulary (lanes, caps, the queue, the owner's hours) that is not part of the station | the design rationale behind every refusal class, the nine tests, the sandbox matrix and the manifest fields | open an issue titled `withheld: design record` |
| `reports/MCP-SURVEY-BIOINFORMATICS-REPORT.md` | the survey of the bioinformatics MCP servers and hubs that the literature arm was chosen from | it records a private measurement of other organisations' servers on one day — the numbers were true then, not a claim about now | why the PubMed MCP server is the pinned one, and why every other candidate was refused | request by name |
| `reports/BIOINFO-RESEARCH-SHIP-1-REPORT.md` | the first ship run's report, the 77,144-row run | it carries private measurements, and the store it cites is 446 MB and not redistributable | the receipts behind the row counts quoted in `symbiont/README.md` (T2: 78,348/78,348 · 77,645/77,645) | request by name |
| `reports/INCIDENT-2026-10-22-FALSE-LICENSE.md` | the incident that produced the rule "read the licence on disk, never from a README" | it describes a failure of the operating farm, not of the station | the provenance of the rule in `docs/POLICY-THIRD-PARTY.md` | request by name |
| `reports/SCOUT-AI-MINING-STATIONS.md` | the outward scout report that set the agent-task budgets and the no-progress detector | it carries an editorial view of other projects, and commercial analysis of the field | why the caps in `agent_tasks.py` are the numbers they are | request by name |
| `reports/SCOUT-*.md` · `reports/INCIDENT-*.md` · `reports/RULINGS/*` | the rest of the outward surveys, and the incident precedents and rulings (e.g. the PENDING ruling `cycle.py` cites) | the same reasons as the two rows above, per file | the provenance of rules this tree states as settled | request by name |
| `reports/MARKET-*.md` · `reports/BUSINESS-MODELS-*.md` · `reports/PROPOSAL-*.md` | commercial analysis and a venture sketch of mining stations | commercial in confidence | — | not offered |
| `tools/discover-sweep.sh` | the logged discovery sweep `README.md` cites as `bash tools/discover-sweep.sh` | a farm instrument: it sweeps with the farm's own credentials and writes into the farm's report tree | how the "8 queries · 123 candidates · 62 refused" line was produced | request by name |
| `tools/discover-sweep.queries.txt` | the surface registry that `symbiont/cycle.yml` names as `surfaces.registry` | which surfaces a deployment watches is a **local policy choice**, not a fact about the station | which corners of the world this deployment chose to look at | point `cycle.yml` at your own registry — see the comment there |
| `tools/dead-end-sweep.sh` · `tools/farm-hold.sh` · `tools/rows.sh` | the heartbeat/dead-end sweep, the hold gate and the queue tool the station's schedule, `update.py` and `station-job.sh` call | the farm's own queue machinery: they operate the farm, not the station | how `HEARTBEAT-MISSING` and a held row are detected | not offered — a deployment does not have a farm to hold |
| `reports/STANDING-JOBS.tsv` · `reports/DROP-REASONS.tsv` · `reports/rows-journal.log` | the farm's queue, its drop reasons and its journal — the run-time inputs `guix.scm` lists | they are the farm's records; they name work that is not this station's | how the queue and the drop reasons were shaped | request by name |
| `nurse/hb.sh` · `nurse/fire-lane.sh` · `nurse/heartbeats/REGISTRY.tsv` | the operating farm's heartbeat library, lane runner and heartbeat registry — what `station-job.sh` sources and `SCHEDULE.md` names | they are the farm's machine: a lane and a heartbeat are how *this* farm runs work, not how a station runs | how a stopped job is noticed within two intervals | a deployment uses its own scheduler; nothing here needs these files |
| `data/bioasq.jsonl` · `bench/bioasq/upstream/score.py` | the BioASQ retrieval-quality bench's own corpus extract and the challenge's official scorer, named by `run_bioasq_subset.py` and `bioasq_matcher.py` | the corpus is third-party and not redistributable; the scorer is the challenge's file, not ours | the T10 recall@k number, once that test exists | request by name — the subset matcher (`symbiont/bench/`) ships without the data |

## NOT WITHHELD — the file is here, under another path

These are cited from inside a package, where the link is correct for the reader but reads as missing from the
repository root. Nothing is withheld; the path in the citation is the short one.

| path as cited | where it actually is | note |
|---|---|---|
| `docs/DIGESTS.md` | `symbiont/docs/DIGESTS.md` | cited from `symbiont/docs/OPS.md`; correct from that file's own directory |
| `docs/PLUGIN-AUTHORING.md` | `symbiont/docs/PLUGIN-AUTHORING.md` | cited from `symbiont/docs/SCHEDULE.md`; correct from that file's own directory |
| `reports/DESIGN-SENTINEL.md` | `docs/DESIGN-SENTINEL.md` | the sentinel design is **published**; only the `reports/` prefix is stale |

## NOT WITHHELD — written at run time, never shipped

A clone has the miniature fixtures and no live store. These paths exist only once a station has run, so a
citation of them is a citation of a *place a run writes*, not of a file that was left out.

| path as cited | what writes it | why it is not in the tree |
|---|---|---|
| `symbiont/store/digest/gaps.md` · `symbiont/store/digest/suggest.md` | `python3 -m symbiont.digests write` | the live store is a deployment's, not ours; the shipped miniature is `symbiont/fixtures/store/` |
| `symbiont/store/view/mouse_gene.v1.provenance.json` | the publish step of a run | same; the committed copy is `symbiont/fixtures/store/view/` |
| `reports/MRK_ENSEMBL.rpt` · `reports/MRK_List1.rpt.gz` · `reports/licence.html` | the selftests, inside a throwaway tree, so no network is needed | they are fixtures a test *builds*, never artefacts of the repository |
| `reports/UPLOADS.tsv` | the uploader's ledger — `manifest.py` emits a receipt in its shape | it is a *shape*, and the file belongs to whoever uploads |

## NOT WITHHELD — a plant that must never exist

| path as cited | what it is | why it is not in the tree |
|---|---|---|
| `reports/SANDBOX-FIXTURE-BREACH.md` | the file `sandbox.py` is asked to write, and must **refuse** to write | its absence *is* the test: the kernel refuses, nothing is repaired, and the refusal names the path |

## NOT WITHHELD — an example filename in a recipe

| path as cited | what it is |
|---|---|
| `symbiont/adapters/my_source.py` | the placeholder in the "add a source by hand" recipe: you write this file, the repository does not ship it |

**The rule, in one line:** every path cited in this tree is either present, resolvable from the file that cites
it, or a row above. If you find a citation that is none of those, that is a defect — please open an issue, and
`python3 tools/withheld-reconcile.py` will name it.
