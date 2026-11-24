# THE HANDOVER MANIFEST — the contract

`python3 -m symbiont.manifest --store <store> --entity <dataset> --out handover.json` writes three things: the **staged file**, the **manifest**
(`handover.json`), and an **upload receipt**. This page is the normative description of what a consumer may rely on. If the code and this page disagree,
that is a defect.

## 1 · WHY IT EXISTS
The manifest is the only thing that crosses a boundary. It assumes nothing about who reads it — a script, a bucket loader, another station, a person with
`sha256sum`. Nothing about a deployment's internals appears in it, and every number in it can be re-derived.

## 2 · WHAT A CONSUMER MAY RELY ON
```
identity      schema name and version · the dataset_id · the release(s) · generated_at
integrity     the staged file's sha256 and size · per-source raw snapshot hashes · row and byte counts
counts        rows published · rows rejected (with reasons) · the count the SOURCE itself stated, and whether ours agreed
per row       source · release · fetched_at · accession — so any row can be traced without asking us anything
licence       each source's terms verbatim, with the terms page and its sha256, stitched into one attribution string
append-only   the plan for adding these rows to an existing corpus (never a silent overwrite)
receipt       14 columns of what happened, so an uploader can dry-run and then append
```
Nothing else is promised. A consumer must not infer anything the manifest does not state.

## 2a · THE SCHEMA IS PART OF THE CONTRACT — and an unknown one must be refused
A contract of FIELDS is not a contract of MEANING. Two stations can agree on every field and still be incompatible, because one row could be a gene, a
phenotype term, a microbial interaction or a paper, `accession` could point at an MGI id or a DOI, and "release" means something different per dataset.
The manifest therefore carries the schema's **name, version and sha256**, and a consumer has one obligation:

```
REFUSED[schema-unknown]   the schema name or version in the manifest is not one this consumer implements.
                          Do not ingest it hopefully. A verified copy of a document you cannot interpret is not data.
```

Identity is producer-scoped (`mgi:MRK_ENSEMBL` names a dataset in the producer's registry, not a global name), so what makes a federated row traceable is
the `via:` chain, never the resemblance of a name.

## 2b · FEDERATION COMPATIBILITY — four gates, cheapest first, each with a named refusal
A consumer of another station's handover passes four independent gates before ingestion. They answer four different questions, and passing one says
nothing about the next.

```
1 HANDOVER CONTRACT    can I verify the package?            sha256 + row/byte counts against the manifest      REFUSED[hash-mismatch] [count-mismatch]
2 DATASET SCHEMA       do I understand what a row MEANS?    schema name · version · sha256 against what I implement   REFUSED[schema-unknown]
3 PROVENANCE CHAIN     can I trace where it came from?      via: links, recursively, plus each row's upstream source+release   REFUSED[provenance-cycle] [via-missing]
4 LICENCE OBLIGATIONS  may I consume and redistribute it?   the stitched attribution, transitively, plus my own redistribution terms   REFUSED[licence-unreadable]
        │
        ▼
   the consumer's EXISTING admission funnel — probe · evaluate · sandbox (throwaway store, counts checked) · card · admit
```
The funnel is unchanged: a federated source is admitted like any other, and its sandbox trial checks the copy's counts — which is transport integrity, not
re-derivation.

### The two limits this checklist does NOT remove
```
· SCHEMA VERSION IS TEMPORAL. A row ingested under schema v1 does not mean what a v2 row means. So the schema version is stored per run (and per row), and a
  schema bump is a re-admission event, exactly as a release move is — otherwise a corpus silently mixes two meanings under one name.
· A SCHEMA IS PUBLISHED, NOT ONLY HASHED. A hash identifies a schema; it does not explain one. So every schema version a producer emits must also exist as a
  versioned, human-readable definition (columns, types, units, and what each value MEANS) that a consumer can implement against — otherwise
  `REFUSED[schema-unknown]` is the only reply a consumer can ever give, and v2 can never be supported because nobody knows what v2 says. The manifest carries
  name · version · sha256; the definition is what makes that triple usable.
· A SCHEMA CHANGE IS ITS OWN LIFECYCLE TRANSITION. `release_moved` means the same meaning, newer rows; a schema change means the meaning moved. It needs its own
  event (`schema_changed`), its own re-admission, and it holds even when the release numbers do not move — and the count check cannot see it, because 10 000
  rows are still 10 000 rows.
· A SCHEMA HASH CATCHES SHAPE, NOT INTENT. A producer can keep the schema byte-identical and change what the values mean. No boundary check can see that;
  only re-derivation can — which federation, by definition, cannot do. So the honest claim for federated rows stays `verified: manifest`, and the card must say
  the authority was not re-derived.
```

### The principle in one line
```
Stations may be independent in implementation, independent in governance and different in specialisation — they need compatible MEANINGS at the boundary,
not identical internals.
```

## 2c · TWO RELATIONSHIPS, NOT ONE — data federation vs verification federation
```
DATA FEDERATION          A consumes B's handover. A larger corpus; every row `verified: manifest`; A applies its own licence gate.
VERIFICATION FEDERATION   A and B each derive the SAME authority release independently, then COMPARE. No rows change hands; the output is a
                          disagreement record. This is the stronger relationship and the more valuable one.
```
A product is not the other, and a station may do either without the other. Data federation needs only the contract; verification federation needs a
comparison protocol and a shared comparison key — which is the schema contract again (`upstream_source`, `upstream_accession`, `upstream_release`).

### THE INDEPENDENCE CAVEAT — and the one mechanism that makes it real
```
Two stations running the SAME code are not independent implementations. Agreement between them isolates INPUT and ENVIRONMENT differences
(a partial read, a truncated page, a rate-limited window, a mirror that lags, a run at a different minute) — it does not test our parser, our xref
rules or our reject logic, because both sides run them.
⇒ agreement across independent inputs is a control on the WORLD; only agreement across independent IMPLEMENTATIONS is a control on OUR CODE.
The cheapest way to get the second without a second station: a DIFFERENTIAL ORACLE inside one station — fetch the same authority by two independent paths
(e.g. Ensembl via the BioMart API and via the FTP dump; MGI via its report files and via its count endpoint) and compare the two. Two code paths, one
authority, no federation required.
```

### WHAT A DISAGREEMENT DOES AND DOES NOT MEAN
```
it means AT LEAST ONE derivation is wrong. It never says which. The tie-break is re-derivation from the raw payload (`--rebuild-from-raw`), which both
stations already support and both already store — so the disagreement record can name the exact accessions and the reason class, and the resolution is an
artefact rather than an argument.
And one danger to state plainly: agreement between two stations sharing our code can manufacture false confidence ("both say 77,645, so it is right").
Agreement is evidence about the world only to the extent the derivations were actually independent.
```

## 3 · VERIFICATION DEPTH — the field that keeps a corpus honest
Every row and every source block carries a `verified` field:
```
verified: upstream    the count was re-derived from the AUTHORITY (MGI's own count, Ensembl's own count) — the strongest claim
verified: manifest    the copy agrees with ITS manifest (sha256 + row count) — transport integrity, as in a federated case where the
                      "source" is another station's handover
```
The two must never be silently mixed. A row that says `verified: manifest` was not checked against the authority, and a corpus that presents both kinds
without distinguishing them overstates itself.

## 4 · FEDERATION — when the source is another station
A manifest is source-shaped: rows, counts, release, licence, hashes. So one station's handover can be another station's source. Four obligations:
```
1 KEEP BOTH PROVENANCE LEVELS   `via:` names the immediate manifest (dataset_id · release · sha256); the upstream source and release the row carries
                                are preserved, never overwritten. A row that forgets its upstream is provenance laundering.
2 LICENCES ARE TRANSITIVE       the stitched attribution is copied forward unchanged; an unreadable link anywhere in the chain is REFUSED[licence-unreadable].
3 NO CYCLES                     a manifest whose `via:` chain contains a dataset_id this station publishes is REFUSED[provenance-cycle].
                                A←B←A is a corpus that cites itself.
4 DEPTH STAYS HONEST            federated rows say `verified: manifest`. They never claim `verified: upstream`.
```
Status: the rules above are the contract; the adapter that implements them (`manifest_source`) is filed and not yet built. Until it exists, a manifest is
consumed by a script or an uploader, not by another station.

## 4a · THE CHAIN — what transitivity gives, and what it does not
Connections are NOT transitive; provenance IS. Station C never talks to Station A, yet A's facts reach C with receipts — provided the chain is walked, not
believed. Four obligations make that true.

```
1 VERIFIABLE, NOT UNBREAKABLE
   C's hash proves C received B's file. It says nothing about B's copy of A. If the chain is to be a proof, C (or C's auditor) must be able to FETCH each
   ancestor manifest named in `via:` and check it. A chain you cannot walk is a claim; a chain you can walk is evidence. So a producer must publish its
   manifests at stable, hash-pinned locations for as long as its rows are in circulation.
2 DERIVATIONS MUST BE DECLARED — the hop is not always a copy
   A midstream station SYNTHESISES (matches mouse genes to human diseases). Its rows are then not A's rows, they are derived. So every hop carries a
   transform record: `transform: {id, version, code_sha256, inputs: [...via...]}`. Without it the chain proves that C's row came from B's row and says
   nothing about how B's row was computed from A's — which is the difference between provenance and hand-waving.
3 CHAIN AGE IS PART OF THE CLAIM
   C's "current" is B's "current" is A's "current". A hop that caches silently makes C's data older than C believes. So the manifest carries, per hop, the
   upstream release and the time it was fetched — and the reader can compute the chain's age. A consumer that cannot state the age of the oldest link has
   not read the chain.
4 NOBODY NOTARISES
   There is no root authority in this ecosystem. A dishonest or broken hop is invisible to hashes; only re-derivation (where it is possible) exposes it.
   That is why the honest claim stays `verified: manifest` for every federated row, and why the count check and the licence gate — not the chain — are the
   standing controls.
```

### What the chain DOES give you
```
· an auditor can walk C → B → A → the original authority and check every link, without asking any of them for permission
· a corrupted or stale upstream hop is caught at the FIRST hop that re-verifies (B, if B re-runs its count check on update)
· blast radius is real: a broken A does not reach C, because B refuses the bad update and C never sees it — but only for failures a check can see
```

## 5 · WHAT THE MANIFEST DOES NOT DO
```
· it does not post anything anywhere — the uploader lives on the consumer's side (D6)
· it does not claim a full-scale rebuild, or any scientific conclusion
· it does not name a release it did not fetch, or a count it did not check
```
