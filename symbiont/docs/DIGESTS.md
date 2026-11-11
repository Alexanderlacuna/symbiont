# DIGESTS — the four digests: schema, cadence, refusal rules

(req-P1-THE-FOUR-DIGESTS-schemas-and-cadence. The implementation is ONE file,
`symbiont/digests.py`; the proofs are `symbiont/selftest_digests.py` (`make check` runs them).
DESIGN-STATION.md §4 names four digests — RELEASE-WATCH · STALENESS · GAPS · SUGGEST — and
until this page none had a schema. One page, no more.)

## 1 · THE FOUR DIGESTS AND WHERE THEY LIVE

| digest (DESIGN §4) | file | cadence |
|---|---|---|
| RELEASE-WATCH | `digest/<source>.md` — ONE block appended PER RUN per source | every run |
| STALENESS | the `release:` line of the same per-run block (stored vs upstream, moved, stale, age, policy — T7) | every run |
| GAPS | `digest/gaps.md` — REWRITTEN whole every run and by the daily pass | every run + daily |
| SUGGEST | `digest/suggest.md` — REWRITTEN whole every run and by the daily pass | every run + daily |

Release-watch and staleness are ONE file per source because staleness IS a per-source fact:
the `release:` line reads `stored=<the release we had> current=<the release this run holds>
upstream=<what the metadata endpoint states now> moved=yes|no stale=yes|no age_days=…
policy_days=…`. The old T9 event lines (quarantine · skip · re-admission) keep appending to the
same file above the run blocks — that trail predates the schema and carries no query.

**Written by the run, not about it.** `symbiont.loop` measures the digest section into the run
record BEFORE dumping it, then writes the digests after; a run that cannot write its digest
refuses — it never publishes quietly. On days no source ran, the daily `station-digest` job
(SCHEDULE.md §1, wired in `station-job.sh do_digest`) keeps gaps and suggest current by running
`python3 -m symbiont.digests write --store <store>` — the module's `write` subcommand, which
re-measures the store disk-only (zero requests) and never touches the per-source blocks; a
store with no harvest run record has nothing to re-measure and the daily page says so. The
per-source blocks are per RUN and are never backfilled — "no run today" is itself the fact
the last block's date states.

## 2 · LINE GRAMMAR (every digest file)

```
- <label>: <value>
    why: <optional commentary>        (any number of lines, between the label and its query)
    query: <a shell command, run with cwd = the store root>
```

`value` is EXACTLY what `sh -c <query>` prints — a reader re-runs the query and gets the value
back, or the digest is refused. A query may span lines (a `python3 -c '…'` payload carries its
own newlines). Indented `why:`/`note:` lines are commentary and carry no reproduction duty:
ages and timestamps are time-dependent facts, quoted not measured (the `last-success` line
carries the age in a note for exactly this reason — a measured age would be stale one second
later). Values and queries are ASCII, and a generated `python3 -c` payload uses double quotes
only (`_py()` asserts it) so a query can never end early on a quote.

### the per-source run block (`digest/<source>.md`)

```
## run <run_id> - <PUBLISHED|REFUSED|skipped(reason)|refused(code)> - finished <iso>
- release: stored=… current=… upstream=… moved=… stale=… age_days=… policy_days=…
- counts: ours=<n> theirs=<n> agree=<yes|no>          (or: none (<why the source did not reach verify>))
- changed: markers +N (a->b) | retired +M (a->b) | papers +P (a->b) | records(ours) +R (a->b)
- refusals: <n> <codes|none>
- last-success: <run_id> at <iso>                     (+ a note: the age at write)
```

`changed` is COMPUTED FROM TWO REAL RUN RECORDS — this run's `digest.stats` pinned in
`runs/<id>.json` against the prior run's. A first run says `first run - markers N - retired M …`.
Skipped and refused sources get blocks too: `counts: none (quarantined)`, the refusal counted on
its own line — a source that did not run is a fact on disk, not an absence.

### the gaps digest (`digest/gaps.md`) — the four classes, each with a COUNT and the query

| class | query measures | today |
|---|---|---|
| genes-without-phenotypes | published view rows | no phenotype view exists in the store, so the count is every published gene; when the phenotypes arm lands the query becomes the join |
| papers-without-abstracts | publication rows with an empty abstract column (never returned, or withheld per-record for licence — the reason is in the row) | literature arm, `literature/publications/publication.v<N>.tsv` |
| ids-without-xref | `xref/xref.tsv` rows with status=unresolved | the keys that failed to pair |
| sources-with-no-release | this run's sources with no state file or no recorded release | the literature arm is release-less BY DESIGN (the owner's change) and is not a gene-arm source, so it is not counted |

### the suggest digest (`digest/suggest.md`) — recommending, never choosing

A `gaps-now` summary line with all four counts, then one block per NONZERO gap, ranked by gap
size, each with `closes` (the gap and its count), `question` (the question fetching it would
help answer), `cost` and `licence`. A cost estimate cites only real things: the sources.yml
budget and a measured run on disk (`within the sources.yml budget for mgi …; measured last mgi
run <id> (requests=…, bytes_in=…, wall_s=…)`); where nothing is measurable yet the line says so
instead of inventing a number. Every option ends `- choosing: the operator — this page ranks
options with costs and licences; it never states a decision`.

## 3 · THE REFUSAL RULES (enforced by `verify`, exit 3)

```
python3 -m symbiont.digests verify digest/gaps.md digest/suggest.md digest/mgi.md …
```

| rule | refusal |
|---|---|
| a digest with no numbers is REFUSED — prose is not a digest (no `- label: value` line carrying its own `query:`) | `REFUSED[digest-no-numbers] <file>: prose is not a digest` |
| a digest whose counts cannot be reproduced by its own query is REFUSED (query exits nonzero, times out, or prints something else) | `REFUSED[digest-not-reproducible] <file>: line <n> (<label>) - the query printed '…', the digest claims '…'` |
| a digest with a missing schema-mandated count is REFUSED BY NAME (the four gap classes; the suggest summary; the five per-source lines of the latest run block) | `REFUSED[digest-missing-count] <file>: the <label> count line is missing` |
| a suggest digest that states a decision is REFUSED (NSC: the list never states a decision, only options with costs) | `REFUSED[suggest-decision] <file>: line <n> states a decision` |

A file is classified by name: `gaps*` → the gaps schema, `suggest*` → the suggest schema,
anything else under `digest/` with a `## run ` block → the per-source schema (the LATEST run
block is the one held to the schema; T9-only event logs predate the schema and are not digests).

## 4 · THE PROOFS

`python3 -m symbiont.selftest_digests` runs the real loop against a local provider whose
release genuinely MOVES between two runs (a gene added, a marker withdrawn, both release stamps
bumped), then checks: the per-run blocks (D1), the what-changed line computed from the two real
runs and the moved releases (D2), the idempotent third run with every delta +0 (D3), the gaps
digest (D4), the suggest digest and its no-decision rule (D5), `verify` green on all four
digests (D6), the daily refresh pass (D7), and the four refusals R1-R4 (prose, tampered count,
missing count, a suggest page that decides). `make check` runs it.
