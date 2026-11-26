# Example P — a pangenome, in three datasets, step by step

Same machine, three entities, none of them a gene: an **assembly**, a **graph**, a **person**. All three are plain files with headers, so all
three are mapping sources — nine lines of YAML each, no Python.

## What you need
```
python3 (3.10+) · pyyaml · network access to raw.githubusercontent.com (human-pangenomics/HPRC_intermediate_assembly) · MIT licence
```

## Step 1 — paste the blocks
From `docs/EXAMPLES.md` → **Appendix · the mapping blocks** take `hprc_assemblies`; the other two are below. Paste all three into your
`symbiont/sources.yml`, set `enabled: true`, and admit them:
```
python3 -m symbiont.registry add-event --source human-pangenomics:assembly            --class admitted --why "P, my own run"
python3 -m symbiont.registry add-event --source human-pangenomics:pangenome_alignment --class admitted --why "P, my own run"
python3 -m symbiont.registry add-event --source human-pangenomics:sample              --class admitted --why "P, my own run"
```
```
  - id: hprc_pangenomes
    adapter: hprc_pangenomes
    kind: mapping
    publisher: human-pangenomics
    dataset: pangenome_alignment
    enabled: true
    base_url: https://raw.githubusercontent.com/human-pangenomics/HPRC_intermediate_assembly/main/data_tables/
    endpoints: {index: "pangenomes/alignments_v2.0.csv"}
    licence_url: https://raw.githubusercontent.com/human-pangenomics/HPRC_intermediate_assembly/main/LICENSE
    licence_text: "HPRC intermediate assembly data: MIT License"
    storage: {index: {class: store, reason: "MIT permits storage"}}
    rate_limit: {requests_per_minute: 20, min_interval_s: 1.0}
    staleness: {policy_days: 730}
    budget: {requests: 6, bytes: 2000000, wall_s: 180}
    mapping:
      role: dataset
      urls: ["index"]
      archive: none
      delimiter: ","
      header: true
      columns: {key: alignment, location: location, filename: filename, hprc_version: hprc_version,
                filetype: filetype, pipeline: pipeline, reference_coordinates: reference_coordinates,
                file_size: file_size}
      release: {kind: literal, value: hprc-release2}     # this host sends no Last-Modified header
      count: {kind: file}
      licence_pattern: "MIT"
```
(`hprc_samples` is the same shape over `sample/hprc_release2_sample_metadata.csv`, keys
`sample_id · biosample_id · population_descriptor · population_abbreviation · trio_available · family_id`.)

## Step 2 — run them
```
python3 -m symbiont.loop --sources hprc_assemblies --since auto --store ./store   # 466 assemblies
python3 -m symbiont.loop --sources hprc_pangenomes --since auto --store ./store   #  12 pangenome graphs
python3 -m symbiont.loop --sources hprc_samples    --since auto --store ./store   # 234 samples
```
Our numbers:
```
assembly             rows_in=466   ours=466   source=466   OK   → view/assembly.v1.tsv             sha256 8eec32d2…
pangenome_alignment  rows_in=12    ours=12    source=12    OK   → view/pangenome_alignment.v1.tsv  sha256 8280adf9…
sample               rows_in=234   ours=234   source=234   OK   → view/sample.v1.tsv                sha256 c2a559c1…
```
Each run ends with `STANDALONE … standalone: no join` — a dataset that publishes its own rows, not a gene arm.

## Step 3 — prove it costs nothing to re-run
```
python3 -m symbiont.loop --sources hprc_pangenomes --since auto --store ./store   # FETCH new=0 · bytes_in=0.0 · same sha256
python3 -m symbiont.loop --rebuild-from-raw --store ./store                      # view rebuilt from raw/ alone
```

## Step 4 — read the view
```
head -3 store/view/pangenome_alignment.v1.tsv
#   key                                    file_size  filename                              pipeline  reference_coordinates
#   HPRC Release2: Minigraph-Cactus (CHM13) 2.15 MB   hprc-v2.0-mc-chm13.wave.vcf.gz.tbi    …         …
```
Those are the artefacts a consumer would actually download — with their size and S3 path.

## Two things this example teaches
```
· a new domain arrives as DATA (a mapping block), not as code — no adapter was written for any of the three
· if a host sends no Last-Modified header, name the release literally: release: {kind: literal, value: …} — the station refuses
  to invent a release, and (before the 2026-11-24 fix) it would crash instead of refusing
```
