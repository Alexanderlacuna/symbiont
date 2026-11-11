# SCHEDULE — when the station runs, what may overlap, and what a missed tick means

(req-P1-THE-SCHEDULE-MODEL-when-the-station. The implementation of this page is ONE file,
`symbiont/station-job.sh`; the fixtures are `symbiont/selftest_schedule.py`. One page, no more.)

## 1 · JOBS — one cron table entry each, each writing its own heartbeat row

| job (heartbeat key) | cadence | cron (one entry per job) | what it does |
|---|---|---|---|
| `station-fetch-ensembl_biomart` | weekly | `17 3 * * 2` | `station-job.sh fetch ensembl_biomart` — the mining loop, one source |
| `station-fetch-mgi` | weekly | `23 5 * * 2` | `station-job.sh fetch mgi` |
| `station-digest` | daily | `41 4 * * *` | one digest page per day: per-source health + tick counts + findings + the four digests' gaps/suggest refresh (`symbiont.digests write`, DIGESTS.md) |
| `station-discovery` | weekly | `37 2 * * 6` | `tools/discover-sweep.sh` (the logged discovery sweep) |
| `station-health` | hourly | `7 * * * *` | `symbiont health` (disk only, zero requests) → the health digest |
| `station-manifest` | daily, **on change only** | `11 5 * * *` | dry-run the manifest, compare the staged sha256; regenerate only if changed |

**Stagger rule:** a fetch slot belongs to a PROVIDER, and two sources of one provider never share a
slot (today one source per provider: Ensembl Tue 03:17, MGI Tue 05:23; a new source of a provider
takes the next free slot in `docs/PLUGIN-AUTHORING.md`). The health probe never fetches (zero
requests by construction, `health.py`), the digest and manifest passes read the store only, and the
discovery sweep talks to GitHub, not to any mining provider — so no job can collide with a fetch on
a provider. Budgets and rate limits stay per source in `sources.yml` (T5); the schedule never
overrides them.

## 2 · OVERLAP — one run per source at a time, and a global cap

- **Per source:** `station-job.sh fetch <source>` takes an exclusive `flock` on
  `<store>/schedule/locks/<source>.lock` and writes its `pid= job= ts= iso=` into the file while
  holding it. A second fire on the same source exits `REFUSED[locked]` **quoting the holder**
  (exit 3), and the refusal is a logged tick (reason `locked`), never a silent drop.
- **Global cap:** before taking its lock a fetch counts the OTHER locks currently held (probe each
  with `flock -n`); at `STATION_MAX_INFLIGHT` (default 2) sources in flight the new fetch is a
  logged miss (reason `cap`) quoting the in-flight holders.
- Daily/hourly/weekly jobs other than fetch are idempotent disk passes; each still logs exactly one
  tick per fire, so two fires are visible as two ticks, not one.

## 3 · MISSED TICK — a tick that did not run is LOGGED, never a silent gap

Every fire of `station-job.sh` appends ONE line to `<store>/schedule/ticks.jsonl`:

    {"ts":…,"iso":"…","job":"fetch","source":"mgi","jobkey":"fetch-mgi","outcome":"ran|missed","reason":"…","detail":"…","pid":…}

`outcome=missed` ALWAYS carries a reason: **held** (the owner's HOLD switch `nurse/the operator's hold switch` is
set — the fetch, digest, discovery and manifest passes stand down; only the health probe keeps
looking) · **locked** (a holder holds the source) · **budget** (the run refused `REFUSED[budget]`,
T5) · **down** (the network is down: a pre-flight connect to the provider refused, zero provider
requests spent, and the log line says `I looked: … did not answer — logged, not silent`) ·
**cap** (the in-flight cap). A fire that never happened at all (cron dead, box off) cannot log
itself — that is what §4 catches.

**Catch-up:** on the first SUCCESSFUL pass after a miss, or after a whole interval skipped, the tick
says `I caught up: last pass <age>` (age from `<store>/schedule/last/<jobkey>.json`). Never a silent
gap: the gap is either a logged miss with a reason, or a named finding (§4), or both.

## 4 · OWNERSHIP — every job is watched; 2x its interval silent is a NAMED finding

- One cron table entry per job (table §1), each entry running `station-job.sh`, which sources the
  farm's heartbeat library (`nurse/hb.sh`) with its own key — so every fire stamps
  `nurse/heartbeats/<key>` with `exit= ts= iso= pid= dur=` and a run marker while alive.
- `nurse/heartbeats/REGISTRY.tsv` pins each station key to its interval; the farm sweep
  (`tools/dead-end-sweep.sh`) reports **HEARTBEAT-MISSING** when a key is silent for 2x its
  interval — a stopped job appears within two intervals, a slow-but-running one does not.
- The station polices itself too: the hourly health probe writes
  `<store>/schedule/health-digest.md`, and any job whose last pass is older than **2x its interval**
  (or that never ran) appears there as a NAMED finding —
  `FINDING station-job <jobkey> OVERDUE: last pass <age>, interval <interval> (2x overdue)`.
- With the network DOWN every job still logs: health, digest and manifest read the disk only; a
  fetch logs `missed/down` + `I looked` and spends zero provider requests; nothing dies quietly.

## 5 · FILES

- `symbiont/docs/SCHEDULE.md` — this page (the model)
- `symbiont/station-job.sh` — the implementation (all five jobs, locks, cap, tick log, catch-up)
- `symbiont/selftest_schedule.py` — the three fixtures (F1 overlap, F2 missed tick, F3 network down)
- `<store>/schedule/` — `ticks.jsonl` (the log), `locks/`, `last/`, `health-digest.md`,
  `digest-<date>.md`, `manifest-<iso>.json`, `cron.log`
