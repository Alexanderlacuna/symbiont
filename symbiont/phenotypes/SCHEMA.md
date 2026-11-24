# THE PHENOTYPES ARM — SCHEMA, DECIDED BEFORE ITS CODE

req-P1-THE-PHENOTYPES-ARM-the-second-dataset. This file is written BEFORE `loop.py`, the adapters
and the selftest exist; the record, the rules and the refusals below are the contract those files
implement. Where this doc and a later file disagree, this doc wins and the file is a defect.

The arm is a SIBLING of the gene loop (`symbiont/`) and the literature arm
(`symbiont/literature/`), not a source inside either: the gene arm's adapter registry admits only
roles `id_authority`/`xref_provider`, and a phenotype association is neither. It READS the gene
arm's published xref table (`store/xref/xref.tsv`, read-only) and never writes it.

## 0 · THE CONFLICT RULE, STATED FIRST

> When two sources disagree about the same gene+term, BOTH rows are kept and the conflict is
> COUNTED — never averaged, never dropped, never silently resolved.

Mechanically:

* the conflict unit is the pair `(gene_id, phenotype_term_id)`;
* each source's claim for that pair is the SET of its payload tuples — everything on the record
  except `source`, `source_release`, `fetched_at`, `first_seen_run`;
* the two sources DISAGREE iff their payload sets for the pair are not equal;
* on disagreement: every row from every source is stored (a row is never dropped for
  disagreeing), ONE conflict is counted for the pair, and the conflict is written to the arm's
  conflict log (`store/phenotypes/conflicts.tsv`) with both sources and the differing payloads;
* on agreement (equal payload sets): every row is still kept — the source is part of the
  record's identity, and dropping a corroborating row would be a drop — but NO conflict is
  counted; the pair is tallied `corroborated` in the run record;
* conflicts are counted, and the count is in the run record and the conflict log — a conflict
  nobody counted is a conflict nobody had.

Why a conflict log of this arm's own, not an append into `store/xref/conflicts.tsv`: the gene
loop REWRITES that file wholesale on every one of its runs (`symbiont/loop.py`, the conflicts.tsv
write), so a phenotype conflict appended there would be DROPPED by the next gene run — the exact
sin this rule forbids. The arm therefore keeps the xref conflict log's INSTITUTION (every
disagreement kept on the record, counted, never dropped) in its own file with the same column
spirit: `key`, `reason`, the two sources' ids, and the `detail` quoting both sides.

## 1 · THE RECORD (phenotypes/phenotype.v1.tsv)

One row per (source, gene_id, phenotype_term_id, allele_marker_id, genotype_id, evidence_code,
citation, statement) — a source's own association row, stored as published. Columns, in order:

| column | rule |
|---|---|
| `gene_id` | the MGI accession the association is anchored on, resolved against the gene arm's xref table (an xref accession — see §2). Non-empty, always. |
| `symbol` | the gene's symbol, from the xref table (never from the association source). |
| `ensembl_gene_id` | the gene's Ensembl id, from the xref table (the second xref accession on the record). |
| `allele_marker_id` | the allele/marker accession(s) the source names, pipe-joined when several. Non-empty, always: an association with no allele or marker id cannot be re-derived and is refused by name. |
| `genotype_id` | the MGI genotype accession when the source names one; `''` when it does not. |
| `phenotype_term_id` | the ontology term id, e.g. `MP:0001179`. REQUIRED: a phenotype without an ontology id is REFUSED BY NAME (§4). |
| `phenotype_term` | the term's own name — resolved ONLY from the term-authority file (§2), never from the association source; a term id the ontology authority does not know is refused by name. |
| `evidence_code` | the source's own evidence token for the association, REQUIRED, and it must be inside the source's evidence vocabulary declared in sources.yml (`evidence_codes`) — a code outside it is refused by name. Where the source's file publishes no per-row code token, the arm records the NAMED value `not-published-in-file` and refuses to invent one; the evidence the source does publish per row — the citation and the genotype context — is carried in `citation` and `statement`. |
| `citation` | the source's own citation for the association (a PubMed id as the source writes it); `''` when the row carries none. |
| `statement` | the source's own statement, quoted, short: the row's own allelic-composition and background words, flattened to one line. Never paraphrased, never summarised. |
| `source` | which source published the association (§2: the association authority). |
| `source_release` | the source's release stamp — for a release-file source, the file's own `Last-Modified` date (MGI publishes no release id; the gene arm already uses this stamp for MGI). |
| `fetched_at` | the ISO instant the record was FIRST stored; preserved across runs (idempotency: run two republishes byte-identical tables). |
| `first_seen_run` | the phenotype run id that first stored this record. |

## 2 · THE AUTHORITY QUESTION, ANSWERED EXPLICITLY

Two roles, two authorities, declared per source in `sources.yml`:

* `term_authority` — the ontology. For this arm: the MP vocabulary as MGI publishes it
  (`VOC_MammalianPhenotype.rpt`: term id, term, definition). The ontology is the ONLY authority
  for the phenotype TERM: `phenotype_term` is resolved exclusively from its file, and an
  association whose term id it does not know is refused by name. No association source may
  supply a term's name — a term named by an association source is a term nobody authored.
* `association_provider` — the source that published the ASSOCIATION. For this arm: MGI's
  curated gene-to-phenotype annotations (`MGI_GenePheno.rpt`). It is the authority for the
  assertion "this gene shows this phenotype in this genotype, on this citation": the row is
  stored as IT published it, and its own name is on the record (`source`).

When two association providers disagree, no authority resolves it: both rows stand (§0).

## 3 · THE DOOR QUERY (store/phenotypes/view/gene_phenotype.v1.tsv)

gene → phenotypes with evidence, one row per (gene, term, source) record:

```
symbol · gene_id · ensembl_gene_id · phenotype_term_id · phenotype_term · evidence_code ·
citation · allele_marker_id · statement · source · source_release · fetched_at
```

Every item carries its accession (`phenotype_term_id`, the MP id, plus both gene accessions) and
its release (`source_release`). A query is `awk -F'\t' '$1=="<gene>"' view/gene_phenotype.v1.tsv`.

## 4 · REFUSALS, BY NAME

The arm refuses, naming the gene and the allele/marker, BEFORE storing anything:

* a phenotype row whose `phenotype_term_id` is empty or not `<NAMESPACE>:<digits>` — the row's
  own words: "refused by name: a phenotype without an ontology id";
* a term id the term authority does not publish;
* an `evidence_code` outside the source's declared vocabulary;
* an empty `allele_marker_id`;
* a gene_scope symbol that is not an `ok` row of the gene arm's xref table (the arm is KEYED BY
  THE XREF TABLE and may not join on a gene the gene arm did not publish);
* a source whose licence page no longer states its terms (licence-unknown — the gene arm's rule).

A refused row is counted as a `reject` with its reason; it is never silently skipped.

## 5 · THE ACCOUNTING IDENTITY (the store's books must close)

```
rows_in = records + rejects + used          (per association source, and in total)
file_rows = rows_in + out_of_scope + malformed   (a release file's every row is named)
```

* `malformed` — rows too short to name even a gene (fewer fields than the file's own format
  carries); counted, never skipped.

* `rows_in` — rows of the source's file that belong to a scope gene;
* `records` — rows stored new this run;
* `rejects` — rows refused by name (§4), with reasons;
* `used` — rows whose record was already in the store from a prior run, or an exact duplicate
  of a row already stored this run (its content is already represented; it is never dropped
  silently — it is counted);
* `out_of_scope` — rows for genes outside the arm's scope: not refused, not used, but COUNTED
  and named, because a release file's every row must be accounted for (MGI publishes no count
  endpoint; like the gene arm, the file's own row count IS the source's count, and our parse
  must match it — the verify leg).

The loop ASSERTS the identity and dies (exit 2) if it does not close.

## 6 · IDEMPOTENCY

Run two against an unchanged store: `records=0`, every row `used`, the published tables
byte-identical (same sha256) — the phenotype arm's zero-new proof, the same NSC the literature
arm answered. `fetched_at`/`first_seen_run` are stamped at first store and preserved, which is
what makes the second publication byte-identical.
