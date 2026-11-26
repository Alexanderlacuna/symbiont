# Two worked examples — and what they prove

Both are real runs of this station. The numbers are the ones the run records hold; nothing here is projected.

## 1 · A human pangenome (a dataset that is not gene-keyed)

```
SOURCE   Human Pangenome Reference Consortium, release-2 assembly index
         228,054 bytes · 466 data rows · MIT · a real header, an accession and an md5 per row
TREATED AS a mapping source: ten lines of YAML (archive none · delimiter · columns by header · count: file) — no Python adapter
RUN      DISCOVER  release hprc-r2-v1.0.1
         NORMALISE rows_in=466  records=466  rejects=0
         VERIFY    ours=466  source=466  OK              ← the file is its own count
         STANDALONE standalone: no join — role 'dataset', so its own rows are the claim
         PUBLISH   view/assembly.v1.tsv  rows=466  sha256=8eec32d2…
SECOND RUN  bytes_in=0.0  data_bytes=0 · same sha256 — nothing re-fetched, nothing re-published
REBUILD     --rebuild-from-raw reproduces the view from the raw payload alone
```

What it proves: the station is not tied to genes. A new domain arrives as **data** (a mapping block), not as code, and the count rule still
holds it to the publisher's own number.

### 1b · The rest of the pangenome: the graphs, and the people

The assembly index is one face of the pangenome. Two more arrive the same way — a URL, a header, a count:

```
view/pangenome_alignment.v1.tsv   12 rows    the release-2 pangenome GRAPHS
    key · file_size · filename · filetype · hprc_version · location · pipeline · reference_coordinates
    rows are the artefacts themselves: minigraph-cactus (CHM13) wave VCFs and their indexes, PGGB, and so on,
    each with its size and its S3 path — the thing a consumer would actually download
    VERIFY ours=12 source=12 OK · sha256 8280adf9…

view/sample.v1.tsv               234 rows    the individuals behind the assemblies
    key(sample) · biosample_id · population · population_abbreviation · trio_available · family_id
    VERIFY ours=234 source=234 OK · sha256 c2a559c1…
```

Three datasets, three entities, one machine: an assembly, a graph, a person. None of them is a gene, and the count rule held all three to
the publisher's own number. Two defects were found by doing it, and both were fixed rather than worked around:

```
· the mapping contract requires `release:` and `licence_pattern:` — a block without them is REFUSED by name, never published empty
· raw.githubusercontent.com sends no Last-Modified header, and the release reader crashed with a traceback instead of taking its own
  refusal path; it now refuses by name, and a block may name its release literally (`release: {kind: literal, value: hprc-release2}`)
```

## 2 · A cross-species aging corpus (three datasets, three licences)

```
A · mouse aging genes      GenAge model organisms (hagr)      CC-BY-3.0   view/genage_models.v1.tsv   2,205 rows, count verified
B · human aging genes      GenAge human (hagr)                CC-BY-3.0   view/genage_human.v1.tsv      307 rows, count verified
C · the join key           MGI HOM_MouseHumanSequence          CC-BY-4.0   view/ortholog.v1.tsv       46,521 rows, count verified
```

Each is stored as its own dataset: raw payload kept and hashed, records derived from it, a view with a schema sidecar and a provenance sidecar
that names the release, the licence and the per-row source. The next step — the join — is a declared **transform**: the join keys, the release of
each input, and the code's own sha256 travel in the derived view's provenance, so a reader can walk back to GenAge and to MGI without asking us.

Licences differ per source and the store keeps them apart: CC-BY-3.0 for the two gene lists, CC-BY-4.0 for the ortholog table. Nothing is stored
that a source's terms forbid, and the view says which source each row came from.

## What both examples have in common

```
· the count is checked BEFORE anything is published (a mismatch refuses the run)
· rejects are counted by reason, never dropped silently
· a rerun fetches zero new bytes when nothing changed upstream
· the raw payload survives, so the corpus can be rebuilt offline
· every view ships with its schema and its provenance: a consumer needs no email to read it
```

### Stage 3 · the join, and what it actually says

```
input            view/genage_models.v1.tsv        2,205 mouse aging genes
join key         view/ortholog.v1.tsv             46,521 MGI mouse↔human rows (grouped by DB Class Key: a human-side row
                                                  carries its mouse id only in the class's sibling row)
output           view/aging_ortholog.v1.tsv         147 rows
                 view/aging_ortholog.v1.absent.tsv  2,074 rows — genes with NO human ortholog, reported, not dropped
coverage         input_rows=2205  joined=127  one_to_many=4  none=2074  sum=2205  partition_holds=true
one-to-many      named in the sidecar (e.g. Cdc14b has 2 human ids)
provenance       both input releases, the join keys and the transform's own sha256 travel in the derived view's sidecar
```

Read that as a sentence: **of 2,205 mouse aging genes, 127 have a human ortholog — four of them one-to-many — and 2,074 are reported absent rather
than quietly dropped.** The report that accompanies the row states what may *not* be concluded: an ortholog is not evidence of ageing in humans.

That is the difference between a corpus and a finding: the counts partition, the absent set exists as a file, and every row can be walked back to
GenAge and to MGI without asking us.

## Appendix · the mapping blocks, as text you can paste

These are the dataset sources used above. They are **mapping blocks**, and this station compiles mappings only from the shipped
`symbiont/sources.yml` (one definition site, by design), so the honest way to use them is to paste them into your own copy of that file
rather than run a second config:

```yaml
  - id: genage_models
    kind: mapping
    adapter: genage_models
    publisher: hagr
    enabled: false
    base_url: https://genomics.senescence.info/genes/
    endpoints: {data: models_genes.zip, metadata: models_genes.zip}
    licence_url: https://genomics.senescence.info/legal.html
    licence_text: "GenAge data: CC-BY 3.0 (attribution required)"
    mapping: {role: dataset, archive: zip, member: "*.csv", count: {kind: file}}

  - id: hprc_assemblies
    kind: mapping
    adapter: hprc_assemblies
    publisher: hprc
    enabled: false
    base_url: https://raw.githubusercontent.com/human-pangenomics/HPRC_intermediate_assembly/main/data_tables/
    endpoints: {index: assemblies_release2_v1.0.index.csv}
    licence_url: https://github.com/human-pangenomics/HPRC_intermediate_assembly/blob/main/LICENSE
    licence_text: "MIT"
    mapping: {role: dataset, archive: none, delimiter: ",", header: true, count: {kind: file}}
```

Then: `python3 -m symbiont.loop --sources genage_models --since auto --store ./store`
