# COMPOSITION, WORKED — one biological chain, three stations, and who the consumer is

The diagram in the README is abstract on purpose. This page is the same picture with real data, real files and the honest status of each part.

## 1 · Registry vs sources — the two words people confuse
```
REGISTRY (surfaces)   WHERE discovery may look. Put a neighbour's manifest URL here and Sophie will notice its new releases.
                      It is an address list for LOOKING; it grants nothing and fetches no data.
SOURCES (sources.yml) WHAT this station may fetch, with licence · rate limit · budget · enabled.
                      Connecting to another station = one entry here + the manifest adapter. That is the connection.
```

## 2 · A — a collector (built)
```
sources      Ensembl 116 via BioMart · MGI 2026-11-09 (MRK_ENSEMBL.rpt + MRK_List1.rpt.gz)
verify       78,348 = 78,348 · 77,645 = 77,645        (the sources' own counts, re-run by the station)
hands over   77,144 rows — symbol · mgi_id · ensembl_gene_id · chromosome · start · end
             + 712 rejects (no symbol, counted not dropped) + per-source licence + sha256 of every payload
```

## 3 · B — a synthesiser (parts exist, station not built)
```
takes        A's hand-over as ONE of its sources, and also collects:
               MGI_GenePheno.rpt           mouse phenotype annotations: allele · MP term · PMID · gene   (699 rows in our test harvest)
               HOM_MouseHumanSequence.rpt  mouse ↔ human ortholog pairs (MGI HomoloGene)
               MONDO release v2026-11-03   disease terms + OMIM/ORPHA/DOID cross-references (CC-BY-4.0, read by hand)
               HPO release                 human phenotype terms
joins        gene (A) × MP term × ortholog pair × MONDO disease × HPO term — every join by an identifier we hold
hands over   a table like
               mouse_gene · MP:0000221 "decreased leukocyte cell number" · PMID 32359443 · human_ortholog ENSG… · MONDO:… · HPO:…
             and a manifest that declares its TRANSFORM: the join keys and the version of every input.
             Without the transform record the chain proves COPYING, not COMPUTING — that is the rule §4 of the manifest contract exists for.
```

## 4 · C — the consumer, which is usually not a station
```
asks         "which mouse genes have an aging phenotype, a human ortholog, an aging-related disease term, and a paper?"
touches      B's hand-over only — never Ensembl, never MGI, never the raw internet
gets         rows with accession · source · release · sha256, and `verified: manifest`
             (the copy agrees with B's manifest; it is NOT re-derived from MGI — only B could claim that)
```
A consumer comes in three shapes:
```
1 a CLIENT of a station's READ door         a lab's assistant, a course, a reproducibility script — no install, no store, no machine door
2 a LOADER of a MANIFEST                    GeneNetwork's MCP, a bucket loader, `sha256sum` — it needs NO door; the manifest is the interface
3 its OWN STATION                           a group that also collects: then it has both doors (read for its users, machine for its operator) and
                                            "consumer" is only the label for how it treats A
```
**The machine door is never a consumer's door.** Consuming is reading; the machine door is for changing what a station may hold. A consumer that starts
admitting its own sources has become a station.

## 5 · What the chain buys, in one comparison
```
from A alone            C cannot ask the question: no phenotypes, no orthologs, no disease terms
from raw sources        C would need four more fetches, four licence reads, and a join nobody could audit
through B               C asks one question of one corpus, and every row still walks back:  C → B → A → MGI
```

## 6 · Status (honest)
```
A                     BUILT (this repository)
B's parts             BUILT individually — the xref join (with its conflict counts), the phenotype arm, the literature arm; MONDO/HPO/orthology
                      reachable and licence-checked but not admitted
B as a station        NOT BUILT
the manifest adapter  FILED, PENDING (`manifest_source`) — so today B would ingest A's hand-over with a script, not automatically
```
