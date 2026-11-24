# SOURCE DECISION — mondo_term
#
# Written by `python3 -m harvester.discover mondo_term` on 2026-11-21T00:00:19.906752+00:00.
# THE ONLY PLACE 'which way do we fetch' LIVES for this source
# (req-P1-THE-FRAMEWORK-GAINS-A-SOURCE-DISCOVE). The contract reads the
# verdict + decided_at below; a record older than 90 days is REFUSED by name,
# because a source can lose its MCP. Re-run the probe to refresh it.

source: mondo_term
adapter: mondo_term
verdict: WRITE-ADAPTER
decided_at: 2026-11-21T00:00:19.906752+00:00
probe: harvester.discover/1
need: bulk
max_age_days: 90

## Verdict

**WRITE-ADAPTER** — a bulk, byte-exact, count-verified release snapshot is the adapter's job: an MCP that reads or indexes the same release files is a lookup layer, not the snapshot (the survey tested both cases: Ensembl per-ID only, MGI an SQLite index over the very MRK_* reports the adapter snapshots) · a documented API exists (https://purl.obolibrary.org/obo/mondo.obo)

## The fetch route

Write the adapter against the documented API (bulk release files, no count endpoint — the release file's own row count is the count). An MCP that only does lookup cannot deliver a byte-exact, count-verified release snapshot.

## Accessions this source returns

- MONDO term id
- term label
- is_a parents
- obsolete flag

## Licence

- MCP server code `monarch-initiative/mondo`: **CC-BY-4.0** (Creative Commons Attribution 4.0 International) — read from the GitHub API https://api.github.com/repos/monarch-initiative/mondo, not from prose
- MCP server code `pipeworx-io/mcp-mondo`: **MIT** (MIT License) — read from the GitHub API https://api.github.com/repos/pipeworx-io/mcp-mondo, not from prose
- MCP server code `berntpopp/mondo-link`: **MIT** (MIT License) — read from the GitHub API https://api.github.com/repos/berntpopp/mondo-link, not from prose
- Data terms: https://github.com/monarch-initiative/mondo/blob/master/LICENSE — recorded here, READ AT HARVEST TIME by `contract.licence_terms` (T6). This probe may not fetch it: its permitted calls are Exa, the GitHub API and the source's own count endpoint.

## Rate limit

- keyless: bulk release asset, no per-request limit
- keyed: n/a
- source: OBO Foundry PURL / GitHub release assets
- source_url: https://obofoundry.org/ontology/mondo

## URLs read by this probe (bounded, read-only)

| # | kind | method | URL | status | bytes | sha256 |
|---|------|--------|-----|--------|-------|--------|
| 1 | github-search | GET | https://api.github.com/search/repositories?q=mondo+disease+ontology+mcp+in%3Aname%2Cdescription&sort=stars&order=desc&per_page=5 | 200 | 10970 | ee1a0349d8e15ba8… |
| 2 | github-repo | GET | https://api.github.com/repos/monarch-initiative/mondo | 200 | 6896 | e6e3709537a5935e… |
| 3 | github-readme | GET | https://api.github.com/repos/monarch-initiative/mondo/readme | 200 | 4845 | 687a99c4c18db682… |
| 4 | github-readme | GET | https://api.github.com/repos/pipeworx-io/mcp-mondo/readme | 200 | 9517 | d8665f7dbd00ac8f… |
| 5 | github-readme | GET | https://api.github.com/repos/berntpopp/mondo-link/readme | 200 | 6568 | 8c651db8b09c897b… |

## Evidence

- the source publishes NO count endpoint (stated in sources.yml); the release file's own row count is the count the adapter verifies against
- MCP candidate monarch-initiative/mondo: ★333, CC-BY-4.0, archived=False, pushed 2026-11-20T21:56:39Z (0 days ago); README sha256 687a99c4c18db682…
  - README keywords: DOI, download, release
- MCP candidate pipeworx-io/mcp-mondo: ★0, MIT, archived=False, pushed 2026-11-09T14:20:55Z (11 days ago); README sha256 d8665f7dbd00ac8f…
  - README keywords: DOI, count, release
- MCP candidate berntpopp/mondo-link: ★0, MIT, archived=False, pushed 2026-11-20T15:56:38Z (0 days ago); README sha256 8c651db8b09c897b…
  - README keywords: DOI, count, download, release
- Exa leg SKIPPED BY NAME: EXA_API_KEY is not set — the GitHub API and the source's own count endpoint carried the decision
- survey evidence: reports/MCP-SURVEY-BIOINFORMATICS-REPORT.md† — https://github.com/monarch-initiative/mondo

## Refusals and gaps

- none: every permitted call answered

<!-- decided by harvester.discover at 2026-11-21T00:00:19.906752+00:00 -->
