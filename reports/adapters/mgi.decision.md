# SOURCE DECISION — mgi
#
# Written by `python3 -m harvester.discover mgi` on 2026-11-10T21:20:07.995581+00:00.
# THE ONLY PLACE 'which way do we fetch' LIVES for this source
# (req-P1-THE-FRAMEWORK-GAINS-A-SOURCE-DISCOVE). The contract reads the
# verdict + decided_at below; a record older than 90 days is REFUSED by name,
# because a source can lose its MCP. Re-run the probe to refresh it.

source: mgi
adapter: mgi
verdict: WRITE-ADAPTER
decided_at: 2026-11-10T21:20:07.995581+00:00
probe: harvester.discover/1
need: bulk
max_age_days: 90

## Verdict

**WRITE-ADAPTER** — a bulk, byte-exact, count-verified release snapshot is the adapter's job: an MCP that reads or indexes the same release files is a lookup layer, not the snapshot (the survey tested both cases: Ensembl per-ID only, MGI an SQLite index over the very MRK_* reports the adapter snapshots) · a documented API exists (https://www.informatics.jax.org/downloads/reports/) · berntpopp/mgi-link: REFUSED (no readable licence: 'NOASSERTION')

## The fetch route

Write the adapter against the documented API (bulk release files, no count endpoint — the release file's own row count is the count). An MCP that only does lookup cannot deliver a byte-exact, count-verified release snapshot.

## Accessions this source returns

- MGI accession
- symbol
- Ensembl xref
- withdrawn markers

## Licence

- MCP server code `berntpopp/mgi-link`: **NOASSERTION** (Other) — read from the GitHub API https://api.github.com/repos/berntpopp/mgi-link, not from prose
- Data terms: https://www.informatics.jax.org/mgihome/other/copyright.shtml — recorded here, READ AT HARVEST TIME by `contract.licence_terms` (T6). This probe may not fetch it: its permitted calls are Exa, the GitHub API and the source's own count endpoint.

## Rate limit

- keyless: bulk files, no per-request limit
- keyed: n/a
- source: MGI downloads page
- source_url: https://www.informatics.jax.org/downloads/

## URLs read by this probe (bounded, read-only)

| # | kind | method | URL | status | bytes | sha256 |
|---|------|--------|-----|--------|-------|--------|
| 1 | github-search | GET | https://api.github.com/search/repositories?q=mgi+mouse+genome+mcp+in%3Aname%2Cdescription&sort=stars&order=desc&per_page=5 | 200 | 5478 | 8ebba1f81cd7e9e6… |
| 2 | github-readme | GET | https://api.github.com/repos/berntpopp/mgi-link/readme | 200 | 6226 | da9e243a72740481… |

## Evidence

- the source publishes NO count endpoint (stated in sources.yml); the release file's own row count is the count the adapter verifies against
- MCP candidate berntpopp/mgi-link: ★1, NOASSERTION, archived=False, pushed 2026-11-02T03:22:44Z (8 days ago); README sha256 da9e243a72740481…
  - README keywords: DOI, bulk, count, download, release
- Exa leg SKIPPED BY NAME: EXA_API_KEY is not set — the GitHub API and the source's own count endpoint carried the decision
- survey evidence: reports/MCP-SURVEY-BIOINFORMATICS-REPORT.md† — https://github.com/berntpopp/mgi-link

## Refusals and gaps

- none: every permitted call answered

<!-- decided by harvester.discover at 2026-11-10T21:20:07.995581+00:00 -->

† withheld from this public tree; see docs/WITHHELD.md.
