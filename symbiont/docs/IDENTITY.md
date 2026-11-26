# IDENTITY — three levels, and how to migrate a store that predates them

(req-P1-IDENTITY-THREE-LEVELS-adapter-data · delivered with `symbiont/contract.py`,
`symbiont/sources.yml`, the run record, the view provenance and the manifest)

## Why the levels exist

A diary keyed on ONE string cannot tell *the previous version of this dataset* from *a different
dataset*. `release_moved`, `count_mismatch` and *"what did we admit in November"* all depend on that
distinction, so identity is kept at three levels and each level answers exactly one question:

| level | id | answers | where it is declared / derived |
|---|---|---|---|
| 1 ADAPTER | `mgi` | *how do we fetch it?* | `sources.yml` `adapter:` = `ADAPTER.name` = the key of `state/`, `raw/` and the run record — **UNCHANGED** |
| 2 DATASET | `mgi:MRK_ENSEMBL` | *what is fetched?* (STABLE across releases) | declared as `publisher:` + `dataset:`; derived by `contract.dataset_id(source)` |
| 3 SOURCE-VERSION | `mgi:MRK_ENSEMBL:2026-11-09` | *which release of that dataset?* | derived by `contract.version_id(source, release)` — never hand-typed |

Levels 2 and 3 are **derived, never typed**: the only definition site is
`symbiont/contract.py::dataset_id` / `version_id`, and every writer (run record, view provenance,
manifest) calls them. A version nobody derived cannot be written — `dataset_id()` REFUSES BY NAME
when a source declares no `publisher`/`dataset`, rather than inventing one.

`id` stays the **adapter** key. It is deliberately not the dataset identity: a mirror of the same
dataset (a new `base_url`, a new `id` if an operator ever wants one) must not become a *different
dataset*, or every release comparison across the mirror would read as a dataset change.

## The declaration (two lines per source)

```yaml
sources:
  - id: mgi
    adapter: mgi
    publisher: mgi
    dataset: MRK_ENSEMBL      # -> dataset_id  = mgi:MRK_ENSEMBL
    ...
```

`publisher`/`dataset` are declared per source, and validated whenever present (neither half may
contain `:` — the derived id is `<publisher>:<dataset>`, so a colon inside a half would make it
ambiguous). They are **optional** in the schema on purpose: a config written before this row (a
trial adapter's one-source config, a store's persisted `sources.yml`) must still load and run. What
is not optional is the honesty of the id — `dataset_id()` refuses by name rather than invent one.

## Migrating an existing store — nothing is renamed

There is **no migration script, no rename and no rewritten state**, because the ids are *derived*:

* `state/<id>.json`, `raw/<id>/<release>/`, `runs/<run_id>.json`, `rejects/<run_id>.<id>.tsv` keep
  their names — they are keyed by the ADAPTER id (`id`) and the adapter's own release stamp, exactly
  as before.
* A store becomes "identity-aware" the moment its `sources.yml` declares `publisher` + `dataset`
  for its sources (two lines each). The next run writes `dataset_id`/`version_id` into its run
  record and its view provenance; the previous run records simply do not carry them (they are
  history, and history is not rewritten — `manifest` states the absence instead of guessing).
* `manifest` derives a row's dataset identity from the run's own view provenance, and falls back to
  the `sources.yml` persisted beside the store (`digests._store_cfg` writes it). When neither
  declares it, the manifest names the gap in `warnings` and `row_provenance.identity_unknown`
  rather than publishing an ambiguous row silently.
* The release stamp keeps its own spelling (`mgi-2026-11-09`, `ensembl-116`): it names the
  directories that exist on disk. `version_id` quotes it verbatim, so a version id always points at
  something the store can open — see "Known finding" below.

## The version test (proved, repeatably)

`python3 -m symbiont.selftest_identity` runs one store twice — a run at MGI release `2026-11-09`
and then a run at `2026-11-16` — against a loopback fixture of both providers:

```
I3a  mgi:MRK_ENSEMBL:mgi-2026-11-09  vs  mgi:MRK_ENSEMBL:mgi-2026-11-16
I3b  runs/=['I2A.json', 'I2B.json']            both run records survive, each names its version
I3c  live view provenance = 2026-11-16; frozen releases/ensembl-116|mgi-2026-11-09/ = 2026-11-09
I3d  raw/mgi/=['mgi-2026-11-09', 'mgi-2026-11-16']; state/mgi.json names 2026-11-16 as CURRENT
```

Two releases of one dataset therefore produce two distinct version ids that coexist, sharing one
dataset id, and neither overwrites the other's history.

## Known finding (out of scope here)

The adapters' release stamps still carry their own source prefix (`mgi-2026-11-09`), so a live
version id reads `mgi:MRK_ENSEMBL:mgi-2026-11-09` while `version_id('mgi','2026-11-09')` reads
`mgi:MRK_ENSEMBL:2026-11-09`. The prefix is redundant, not ambiguous, and stripping it would be a
change to an adapter's **discovery** (the row puts adapter fetch logic out of scope) plus a rename of
the directories that already exist. It is recorded as a finding for the row that owns release stamps.
