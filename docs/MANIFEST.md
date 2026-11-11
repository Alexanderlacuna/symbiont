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

## 5 · WHAT THE MANIFEST DOES NOT DO
```
· it does not post anything anywhere — the uploader lives on the consumer's side (D6)
· it does not claim a full-scale rebuild, or any scientific conclusion
· it does not name a release it did not fetch, or a count it did not check
```
