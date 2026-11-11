# SUPPORT — the boundary, one page

## What we support

- The station **at this tag** (`v0.1.0`): install from the README, `make check`, the miniature
  offline quickstart, the fixture store, and the documented commands (`symbiont fetch · health ·
  admit · manifest`). If a quoted command in README.md or symbiont/docs/ does not do what its
  quoted output says, that is a bug and we want it.
- The plugin path: adding a source through probe → evaluate → draft → sandbox → card → admit,
  exactly as symbiont/docs/PLUGIN-AUTHORING.md walks it.

## What we do not support (the honest list)

- **the live store's contents** — the data a deployment has fetched is that deployment's
  responsibility: sources change, releases move, licences differ per record. We support the
  station's *behaviour* toward the store (counts verified, rejects named, idempotency), not any
  particular dataset's fitness for your analysis.
- **downstream ingestion** — the handover manifest (DESIGN-STATION.md §7) is the only artefact
  that crosses to a corpus like GeneNetwork. Their schema, their QA/QC, their indexing.
- **the literature arm's third-party server** — we pin `@cyanheads/pubmed-mcp-server@2.12.0`
  (Apache-2.0) and wrap it; its API quotas, keys and availability are between you and it.
- **full-text fetching** — the station stores metadata and abstracts under per-record licences;
  full text is out (design §3e, evidence-level rules).
- **your schedule and your disk** — OPS.md gives the recipe (flock, cron, retention); the
  cadence and the machine are the operator's decisions (design §11b).

## Where to report a break

- **open an issue on this repository** (genenetwork-mining-research) with the command, its exit
  code and the run record path — a refusal is a legal answer with a reason file; paste the reason.
- a **quarantined source** is not a bug: re-admission goes through the card
  (`python3 -m symbiont.loop --readmit <id> --card <file>`) — see PLUGIN-AUTHORING.md.
- a **licence question** about a source's data is a question for that source, not for us; the
  station records each source's terms and their sha256 precisely so you can read them yourself.

## Versioning

Semver from here: `v0.1.0` is the first land. The CHANGELOG names what each version does and —
just as important — what it does not do yet.
