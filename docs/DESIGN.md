# DESIGN — how a source becomes data

This is the authority for the station's source lifecycle as it SHIPS. If the code and this page disagree, that is a defect and should be reported.

## 1 · FIVE STAGES, EACH WITH ONE OWNER AND ONE REFUSAL
```
1 DISCOVERED   a scout found a potential source.          owner: the operator's surface registry        REFUSED[no-surfaces]
2 EVALUATED    it is checked against the source spec      owner: the machine                           REFUSED[no-rows] [no-count-endpoint]
               and one sandbox trial must count-match                                                   [no-schema] [licence-unreadable] [count-mismatch]
3 ADMITTED     it passed the admission gate, WITH EVIDENCE owner: the machine, recorded               REFUSED[admission-denied]
               (card · count ours=theirs · licence read)                                               (and admit.by_policy=false without a written ruling)
4 ENABLED      the operator authorises fetching            owner: the operator, one line, with a why  (no refusal — this IS the permission)
5 ACQUIRED     data is fetched, verified and stored        owner: the machine                           REFUSED[count-mismatch] [schema-drift] [source-gone] [budget]
and after acquisition the relationship continues:
6 CONTINUED    release moved · stale · count drift · vanished   owner: the machine, on every later run
7 RETIRED      we stop on purpose, naming a replacement          owner: the operator
```

**The distinction that matters most: ADMITTED is a DECISION, ENABLED is a PERMISSION.** They can sit months apart and that is not an inconsistency — it is a
queue. A source can be admitted and never enabled; it can be re-enabled years later without re-admission. Admission answers *what evidence justifies
accepting this source*; enablement answers *why are we authorising its acquisition now*.

## 2 · THREE FILES, THREE AUTHORITIES
```
sources.yml            INTENT      written by a human, rarely. "May we fetch this?" — plus a why. The only hand-written file in the chain.
source-events.jsonl    FACTS       appended by the code at every transition. Append-only. A hash chain makes a broken link visible.
registry summary       DERIVED     rebuilt from the two above. Never edited, never authoritative. Delete it and it comes back.
```
The feared contradiction ("sources.yml says admitted, the summary says refused") is not a contradiction once split this way: intent says you *may* fetch,
the latest fact says the last count did *not* match. Both true; the join makes the disagreement legible.

**Where the three files can live.** Nothing in this design requires a service: `sources.yml` and the event log can live in a git repository, where
git's own primitives do the governance — a fork or branch is a candidate, CI is the evaluator, review is the admission decision, merge is the permission,
a tag is a registry release, a revert is a retirement, and CODEOWNERS names who may permit. Three rules make that safe: shard the event log per writer
(`events/<deployment>.jsonl`) so appends never conflict, never commit the derived index as if it were authored, and anchor the diary's head hash in the
manifest — git history is tamper-evident, not tamper-proof.

## 3 · EVENT CLASSES
```
discovered · evaluated · sandboxed · admitted · intent_changed · acquired · release_moved · count_ok · count_mismatch ·
stale · unreadable · vanished · retired · manual_add · manual_override · override_expired
each event: {seq, ts, source_id, release?, class, actor: machine|operator, why, evidence{...}, origin: live|backfill, prev_event_sha256}
```

## 4 · FOUR RULES THAT MAKE REPLAY DETERMINISTIC
```
R1 IDENTITY carries no release.   id = <publisher>:<dataset> (stable). The release is an attribute of each event, so "the previous version" is free.
R2 ORDER IS FILE ORDER.           seq decides which event came first; ts is evidence of when. Sorting by ts produces nondeterministic replay.
R3 ILLEGAL TRANSITIONS ARE REFUSED AT WRITE TIME (e.g. `admitted` directly after `refused` without `reconsidered`). A log with impossible sequences
                                  cannot be replayed without heuristics.
R4 FLIPPING THE SWITCH IS AN EVENT (`intent_changed{from,to,why}`), written by the command that flips it — so even the human's decision has history.
```

## 5 · MANUAL ADDITION — evidence OR exception, never a disguise
```
admitted          proves the source PASSED a process (card · count · licence read)
manual_add        proves an operator INTRODUCED it — never evaluated; labelled as such in the summary, permanently, until evaluated
manual_override   proves an operator AUTHORISED one exception — carries a why, a SCOPE (one run or an end date) and appears in the run record and
                  in the MANIFEST, because the manifest is the only thing that crosses the boundary
an override never writes an `admitted` event; evaluation clears the flag; the summary prints how many overrides were granted in the last 90 days.
```

## 6 · WHAT THE TESTS PROVE, AND WHAT THEY DO NOT
```
PROVE      the contract and the refusals, offline, at fixture scale; counts verified against the source's own number; a copy of the store rebuilt from raw.
DO NOT     prove a full-scale rebuild of a 446 MB store; prove any source's science; prove that a discovery surface will still exist next year.
```

## 7 · ONE SENTENCE
```
A source may enter through evidence or through an exception; intent stays with the operator, facts are appended by the code, the catalogue is a view you
can always rebuild — and nothing in the system may claim to be vetted when it was only switched on.
```

## 8 · PORTING IT TO ANOTHER DOMAIN
The station never says anything about biology. The governance below transfers unchanged; four organs are replaced, and one assumption must be re-answered.

```
TRANSFERS AS-IS   the stages (discovered → evaluated → admitted → enabled → acquired → continued → retired) · intent/facts/derived-index ·
                  the hash-chained diary and its four replay rules · admission ≠ permission · manual_add / manual_override with scope and manifest
                  visibility · coverage refusal · the manifest (spec sha256 · releases · hashes · rejects) · "no row without its source, release and hash"
REPLACED          (1) the surface registry — where data lives in that domain
                  (2) the VERIFIER — what "true" means there (in biology: count, coordinates, cross-references)
                  (3) the GOLD SET — a frozen, expert-labelled answer set to score the arm against
                  (4) the LEGAL GATE — that domain's licence map and named refusal classes
UNCHANGED IN SHAPE  the research spec: question → arms → filters → required/optional datasets → coverage refusal → manifest coverage block
```

**The assumption that does not always travel: a source that can state its own count.** Biology sources mostly can (row counts, releases, archives).
Many other domains cannot — price tables, procurement portals, dashboards with no export. So a port must answer one question first: *what is this
domain's count proxy?* (an API envelope's total · a paginated index · a checksummed manifest · a page count). That proxy becomes the domain's weakest
link, and where none exists the honest state is the one the AMR test reached: **tools to evaluate, no sources to admit.**

**The transfer test, one line per candidate domain:** can you name the *publisher*, the *release* and the *count*, and does the row carry enough
identifiers to join what we already hold? If yes, the seven stages apply with four organs swapped. If no, it is a tool, not a source, and the station
refuses it by rule.

**Example (no biology):** rows = model × provider × context window × price per 1M tokens × measured latency · release = the vendor's pricing revision ·
count = the vendor's own table size, or `REFUSED[no-count-endpoint]` · verifier = reproduce the price from that revision · research spec = "cheapest
≥1M-context model with ≤2 s TTFT", required datasets [provider_tables, latency_bench] — the same shape as any biological question, different nouns.

## 9 · THE READING LAYER (and the third actor nobody names)
The station exposes evidence through a surface that is deliberately weaker than the one that changes anything. Two authorities, three actors, and one
artefact that needs neither.

```
READING LAYER    annot/mcp_server.py — read-only over the store, never writes it
   gene_dossier · gene_xref · phenotype_evidence · publications_for · traits_containing · what_changed · answer
   every item carries source · release · fetched_at · accession; `answer` refuses a sentence it cannot bind to a record

MACHINE LAYER    symbiont/mcpserver.py — manages ADMISSION, does not ingest
   source_health · source_probe · source_evaluate · source_draft · source_sandbox · source_card · source_admit
   these shell out to the admission path; nothing here fetches or stores rows

THE LOOP         symbiont/loop.py — the only thing that widens the corpus: fetch → verify count → store → view → manifest, with its own budget
```

**The three actors, not two.** Admission and ingestion are separate on purpose: whoever may permit a source is not whoever pulls its rows, and neither of
them is whoever asks a question. That is what makes "asking can be exposed widely, admitting stays with an operator" true rather than decorative.

**The boundary is WRITE AUTHORITY, not data access.** Both layers read the same store. One may change permissions; the other may only ask. A reader with
the machine door cannot silently widen the corpus; a lane with the loop cannot silently widen the permissions.

**The third surface needs no authority at all: the MANIFEST.** A recipient — an uploader inside another project, a bucket loader, `sha256sum` — consumes
the staged file plus `handover.json` and never touches either MCP. That is the surface that makes the station hand-over-able.

**The reading layer is NOT the analysis layer.** It exposes evidence with provenance; a grounded-science process consumes those doors and produces
interpretation — candidates, claims, refutation conditions. One answers questions about records; the other proposes what the records might mean. Keeping
those apart is what stops an interpretation from being mistaken for a record.
