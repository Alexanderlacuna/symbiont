# OBS — heartbeat, last run, staleness: the ONE command

There is no daemon to watch. The station's heartbeat is the run record on disk, and one command
reads it all. A silent station is a finding, not a quiet one — this page is how you notice.

## The ONE command

```
symbiont health --store <store>            # or: python3 -m symbiont.health --store <store>
```

One line per source: state (ok | degraded | down | quarantined), last run and last success ages,
the count check (ours vs the source's own, URL included), rate-limit headroom used this cycle,
licence status, staleness against the per-source policy, and — if the source is not ok — the ONE
reason. `--json` gives the same record as structured JSON (it is the same record the MCP tool
`source_health` returns; one definition, three doors).

On the committed miniature store (no network, nothing fetched):

```
$ symbiont health --store symbiont/fixtures/store
{"ensembl_biomart": {"state": "ok", "last_run_age_s": 6, "last_success_age_s": 6,
 "count_check": {"ours": 5, "theirs": 5, "url": "http://127.0.0.1:…/martservice?query=…"},
 "rate_used_this_cycle": 2, "licence": "known",
 "staleness": {"our_release": "ensembl-116", "upstream_release": "ensembl-116"},
 "reason": "ok: ran in arc-3 6s ago; count check agreed (ours=5 == theirs=5); licence known;
 our release ensembl-116 matches upstream ensembl-116, 0.0d old within the 120d policy; the store
 grounds it: view mouse_gene holds 5 rows over 5 xref pairs"} , {"mgi": …}}
```

## What each field answers

| field | the question it answers |
|---|---|
| `state` | ok · degraded (ran, something disagreed) · down (never ran / no success) · quarantined |
| `last_run_age_s` / `last_success_age_s` | the heartbeat: when did it last try, when did it last succeed |
| `count_check` | ours vs the source's own published count, and the URL both came from (test T2) |
| `rate_used_this_cycle` | requests spent against the per-source budget this cycle |
| `licence` | known / declared / unread — a source whose terms were never read refuses at discovery |
| `staleness` | our stored release vs upstream, its age, and the per-source policy_days |
| `reason` | the ONE reason a source is not ok — empty when it is ok |

## Staleness is stamped on the claim, not whispered

A stored release older than the source's `staleness.policy_days` (in sources.yml) raises a named
finding `SOURCE-STALE`, the run record carries it, and the view's `provenance.json` carries it —
a stale claim is never served quietly. Refreshing the claim (a new fetch) clears it.

## Heartbeat for the whole station

The run records are the station's heartbeat. The cheapest monitor is cadence plus one command:

```
ls -t <store>/runs | head -1          # newest run id — compare against your cron cadence
symbiont health --store <store>       # per-source state, the reasons
```

An agent or a cron may run `symbiont health --json` and alert on: any `state` != ok, any
`last_success_age_s` beyond the cadence, any staleness past policy. Remediation is recorded with
the same tool (`--record-remediation <id> --action retry|quarantine|replace --note "why"`), so
the next health read says what was DONE about the finding, not just that there was one.
