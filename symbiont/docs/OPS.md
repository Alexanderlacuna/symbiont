# OPS — running the symbiont store (backup · restore · size)

For the operator who runs the box. The station's contract with you: it never edits raw, it never
publishes a claim it cannot join, and it tells you the cost of every run. Your side of the
contract: back the store up, restore it honestly, and decide size policy — the station measures,
you decide.

## What the store is

```
raw/<source>/<release>/<sha256>.<ext>          content-addressed payloads — the truth, never edited
raw/<source>/<release>/<sha256>.meta.json      url, etag, last-modified, bytes, sha256, run
records/ (via rejects + view)                  everything else is DERIVED from raw
state/<source>.json                            since-memory: release, counts, etags, snapshots
xref/xref.tsv · conflicts.tsv · retired.tsv   every pairing decision (ok / ok_with_conflict / unresolved)
view/<entity>.v<N>.tsv                         the published projection + schema.json + provenance.json
runs/<id>.json                                 the run record: cost, counts, licence, refusals, findings
rejects/<run>.<source>.tsv                     rejected rows WITH REASONS (nothing vanishes)
digest/<source>.md                             the release-watch + staleness digest, one block per run
                                                (docs/DIGESTS.md; the T9 quarantine trail appends
                                                above the run blocks)
digest/gaps.md · digest/suggest.md            counts with the queries that produced them; ranked
                                                options with cost, licence, question — never a decision
```

## Backup

What is irreplaceable, in order: `raw/` (the bytes the sources gave), `state/` (the since-memory
that makes run 2 fetch zero), `sources.yml` (the one definition site), `runs/` and `digest/`
(the audit trail). `view/` and `xref/` are projections — they are rebuildable from raw (see
Restore), but back them up anyway: a backup that needs a rebuild before it can answer is half a
backup.

```
rsync -a --delete <store>/  <backup-target>/symbiont-store/
```

Back up after every successful run (the run record's `cost` line is your change signal), or on a
cron of your own. The store is append-mostly: raw accumulates one directory per source per
release, so an `rsync` after a run sends only the new release.

## Restore

1. Copy the backup back: `rsync -a <backup-target>/symbiont-store/ <store>/`.
2. Prove the projection still equals its raw — run the loop in replay mode (no requests leave
   the box; the view is rebuilt from the restored raw):
   ```
   python3 -m symbiont.loop --store <store> \
       $(python3 - <<'PY'
   import json, os
   store = os.environ.get("STORE", "symbiont/store")
   for sid in ("ensembl_biomart", "mgi"):
       st = json.load(open(os.path.join(store, "state", f"{sid}.json")))
       for rk, s in st["snapshots"].items():
           print(f"--replay-raw={sid}:{rk}={s['path']}")
   PY
   )
   ```
   The `PUBLISH … sha256=<hash>` line must equal the sha256 recorded in
   `view/mouse_gene.v1.provenance.json`. If it does, raw and view agree and the restore is whole.
3. `python3 -m symbiont.health --store <store>` — every source must read `state: ok` with the
   restored release named.

## Size: measure, then decide (size is an OPS conversation, not a schema constraint)

The station measures so you can decide; it never thins data to save disk.

- Per run, the cost line and the run record give you `bytes_in`, `data_bytes` (new payload bytes)
  and wall time per source.
- Per release, the store's size is visible the boring way:
  ```
  du -sh <store>/raw/*/* | sort -h          # per source per release
  du -sh <store>                            # the whole station
  ```
- Retention of raw by release is now the OWNER'S NUMBER in the policy, not prose here:
  `retention_releases_per_source` (N) and `retention_disk_consequence_mb` in
  reports/symbiont-policy.yml — the shipped rule is N=2, the floor this page always stated
  (current release + the one before it), with the MB arithmetic shown in the policy itself.
  The sweep is `python3 -m symbiont.retention sweep --store <store> [--apply]`: without --apply
  it is a dry run, and with it every drop is ARCHIVED (moved to `archive/<source>/<release>/`
  with every sha256 in RETENTION.json, each payload re-hashed before the move) or REFUSED by
  name (`current-release` · `hash-mismatch` · `no-meta`) — a silent removal is not
  representable, and the station still never deletes raw on its own. Freeze the current view
  before the next release lands (`retention snapshot`, or just mint a claim — minting freezes):
  `releases/<relset>/` is what keeps `symbiont claims answer --as-of <relset>` answerable after
  the release it names is dropped from raw.

## What the station refuses to do (so you never have to guess)

- It never edits a byte in `raw/` (content-addressed; a mismatch is a named `RuntimeError`).
- It never publishes a claim with a hole in it: a refused source means no view that run, the
  refusal in the run record.
- It never runs a quarantined source: the skip is by name, in the run record and the digest.
- It never spends over budget: `REFUSED[budget]`, exit 3, the cap named.

## The schedule (when you want one)

There is no daemon. One run at a time, under `flock`, is the whole recipe:

```
17 3 * * 2  flock -n /run/lock/symbiont.lock  cd <station> && python3 -m symbiont.loop >> /var/log/symbiont.log 2>&1
```

A silent station is a finding, not a quiet one: if `runs/` has no record newer than your
cadence, `symbiont health` says so (see OBS.md).
