# SOURCE DECISION — hpo_term
#
# Written by `python3 -m harvester.discover hpo_term` on 2026-11-21T00:00:10.182771+00:00.
# THE ONLY PLACE 'which way do we fetch' LIVES for this source
# (req-P1-THE-FRAMEWORK-GAINS-A-SOURCE-DISCOVE). The contract reads the
# verdict + decided_at below; a record older than 90 days is REFUSED by name,
# because a source can lose its MCP. Re-run the probe to refresh it.

source: hpo_term
adapter: hpo_term
verdict: WRITE-ADAPTER
decided_at: 2026-11-21T00:00:10.182771+00:00
probe: harvester.discover/1
need: bulk
max_age_days: 90

## Verdict

**WRITE-ADAPTER** — a bulk, byte-exact, count-verified release snapshot is the adapter's job: an MCP that reads or indexes the same release files is a lookup layer, not the snapshot (the survey tested both cases: Ensembl per-ID only, MGI an SQLite index over the very MRK_* reports the adapter snapshots) · a documented API exists (https://purl.obolibrary.org/obo/hp.obo) · obophenotype/human-phenotype-ontology: REFUSED (no readable licence: 'NOASSERTION')

## The fetch route

Write the adapter against the documented API (bulk release files, no count endpoint — the release file's own row count is the count). An MCP that only does lookup cannot deliver a byte-exact, count-verified release snapshot.

## Accessions this source returns

- HP term id
- term label
- is_a parents
- obsolete flag

## Licence

- MCP server code `obophenotype/human-phenotype-ontology`: **NOASSERTION** (Other) — read from the GitHub API https://api.github.com/repos/obophenotype/human-phenotype-ontology, not from prose
- MCP server code `berntpopp/hpo-link`: **MIT** (MIT License) — read from the GitHub API https://api.github.com/repos/berntpopp/hpo-link, not from prose
- MCP server code `pipeworx-io/mcp-hpo-api`: **MIT** (MIT License) — read from the GitHub API https://api.github.com/repos/pipeworx-io/mcp-hpo-api, not from prose
- Data terms: https://hpo.jax.org/app/license — recorded here, READ AT HARVEST TIME by `contract.licence_terms` (T6). This probe may not fetch it: its permitted calls are Exa, the GitHub API and the source's own count endpoint.

## Rate limit

- keyless: bulk release asset, no per-request limit
- keyed: n/a
- source: OBO Foundry PURL / GitHub release assets
- source_url: https://obofoundry.org/ontology/hp

## URLs read by this probe (bounded, read-only)

| # | kind | method | URL | status | bytes | sha256 |
|---|------|--------|-----|--------|-------|--------|
| 1 | github-search | GET | https://api.github.com/search/repositories?q=human+phenotype+ontology+mcp+in%3Aname%2Cdescription&sort=stars&order=desc&per_page=5 | 200 | 11007 | 66c182eb02bdf427… |
| 2 | github-repo | GET | https://api.github.com/repos/obophenotype/human-phenotype-ontology | 200 | 7302 | 73e3e5c2ce788717… |
| 3 | github-readme | GET | https://api.github.com/repos/obophenotype/human-phenotype-ontology/readme | 200 | 3586 | e15aed4f357fcba6… |
| 4 | github-readme | GET | https://api.github.com/repos/berntpopp/hpo-link/readme | 200 | 7098 | 0969e01f8779a4f9… |
| 5 | github-readme | GET | https://api.github.com/repos/pipeworx-io/mcp-hpo-api/readme | 200 | 4470 | 9e2d5573eecdbb85… |

## Evidence

- the source publishes NO count endpoint (stated in sources.yml); the release file's own row count is the count the adapter verifies against
- MCP candidate obophenotype/human-phenotype-ontology: ★388, NOASSERTION, archived=False, pushed 2026-11-18T08:46:15Z (2 days ago); README sha256 e15aed4f357fcba6…
  - README keywords: download
- MCP candidate berntpopp/hpo-link: ★1, MIT, archived=False, pushed 2026-11-20T16:02:08Z (0 days ago); README sha256 0969e01f8779a4f9…
  - README keywords: DOI, bulk, count, download, release, snapshot
- MCP candidate pipeworx-io/mcp-hpo-api: ★0, MIT, archived=False, pushed 2026-11-09T14:08:37Z (11 days ago); README sha256 9e2d5573eecdbb85…
  - README keywords: count, release
- Exa leg SKIPPED BY NAME: EXA_API_KEY is not set — the GitHub API and the source's own count endpoint carried the decision
- survey evidence: reports/MCP-SURVEY-BIOINFORMATICS-REPORT.md† — https://github.com/obophenotype/human-phenotype-ontology

## Refusals and gaps

- none: every permitted call answered

<!-- decided by harvester.discover at 2026-11-21T00:00:10.182771+00:00 -->
