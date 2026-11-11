# SOURCE DECISION — ensembl_biomart
#
# Written by `python3 -m harvester.discover ensembl_biomart` on 2026-11-10T21:20:09.968556+00:00.
# THE ONLY PLACE 'which way do we fetch' LIVES for this source
# (req-P1-THE-FRAMEWORK-GAINS-A-SOURCE-DISCOVE). The contract reads the
# verdict + decided_at below; a record older than 90 days is REFUSED by name,
# because a source can lose its MCP. Re-run the probe to refresh it.

source: ensembl_biomart
adapter: ensembl_biomart
verdict: WRITE-ADAPTER
decided_at: 2026-11-10T21:20:09.968556+00:00
probe: harvester.discover/1
need: bulk
max_age_days: 90

† the survey is held back from this public subset: it records another organisation's servers on one day. Every
withheld path, why, and how to request it is a row in [`../../docs/WITHHELD.md`](../../docs/WITHHELD.md).

## Verdict

**WRITE-ADAPTER** — a bulk, byte-exact, count-verified release snapshot is the adapter's job: an MCP that reads or indexes the same release files is a lookup layer, not the snapshot (the survey tested both cases: Ensembl per-ID only, MGI an SQLite index over the very MRK_* reports the adapter snapshots) · a documented API exists (https://www.ensembl.org/biomart/martservice) · siy/vibe-ensemble-mcp: REFUSED (last push 387 days old)

## The fetch route

Write the adapter against the documented API (bulk release files, no count endpoint — the release file's own row count is the count). An MCP that only does lookup cannot deliver a byte-exact, count-verified release snapshot.

## Accessions this source returns

- Ensembl gene id
- chromosome
- start
- end
- strand

## Licence

- MCP server code `effieklimi/ensembl-mcp-server`: **MIT** (MIT License) — read from the GitHub API https://api.github.com/repos/effieklimi/ensembl-mcp-server, not from prose
- MCP server code `raiyanyahya/ensemble`: **MIT** (MIT License) — read from the GitHub API https://api.github.com/repos/raiyanyahya/ensemble, not from prose
- MCP server code `siy/vibe-ensemble-mcp`: **Apache-2.0** (Apache License 2.0) — read from the GitHub API https://api.github.com/repos/siy/vibe-ensemble-mcp, not from prose
- MCP server code `musharna/plant-genomics-mcp`: **MIT** (MIT License) — read from the GitHub API https://api.github.com/repos/musharna/plant-genomics-mcp, not from prose
- Data terms: https://www.ensembl.org/info/about/legal/disclaimer.html — recorded here, READ AT HARVEST TIME by `contract.licence_terms` (T6). This probe may not fetch it: its permitted calls are Exa, the GitHub API and the source's own count endpoint.

## Rate limit

- keyless: 20 requests/minute in sources.yml
- keyed: n/a
- source: sources.yml rate_limit
- source_url: https://www.ensembl.org/biomart/martservice

## URLs read by this probe (bounded, read-only)

| # | kind | method | URL | status | bytes | sha256 |
|---|------|--------|-----|--------|-------|--------|
| 1 | github-search | GET | https://api.github.com/search/repositories?q=ensembl+mcp+in%3Aname%2Cdescription&sort=stars&order=desc&per_page=5 | 200 | 28643 | b03da1de334acf92… |
| 2 | github-readme | GET | https://api.github.com/repos/effieklimi/ensembl-mcp-server/readme | 200 | 8077 | 0d0b5acfcb43a813… |
| 3 | github-readme | GET | https://api.github.com/repos/raiyanyahya/ensemble/readme | 200 | 31112 | c538afb2fa4d5f40… |
| 4 | github-readme | GET | https://api.github.com/repos/siy/vibe-ensemble-mcp/readme | 200 | 28649 | df3083010a7b8abf… |
| 5 | github-readme | GET | https://api.github.com/repos/musharna/plant-genomics-mcp/readme | 200 | 8769 | ffdb3666b54ad795… |

## Evidence

- the source publishes NO count endpoint (stated in sources.yml); the release file's own row count is the count the adapter verifies against
- MCP candidate effieklimi/ensembl-mcp-server: ★8, MIT, archived=False, pushed 2026-02-11T03:45:35Z (272 days ago); README sha256 0d0b5acfcb43a813…
  - README keywords: PMCID, PMID, release
- MCP candidate raiyanyahya/ensemble: ★13, MIT, archived=False, pushed 2026-06-04T21:58:57Z (158 days ago); README sha256 c538afb2fa4d5f40…
  - README keywords: count
- MCP candidate siy/vibe-ensemble-mcp: ★8, Apache-2.0, archived=False, pushed 2025-10-19T04:55:15Z (387 days ago); README sha256 df3083010a7b8abf…
  - README keywords: DOI, count, download, release
- MCP candidate musharna/plant-genomics-mcp: ★8, MIT, archived=False, pushed 2026-11-01T02:47:14Z (9 days ago); README sha256 ffdb3666b54ad795…
  - README keywords: DOI, bulk, release, snapshot
- Exa leg SKIPPED BY NAME: EXA_API_KEY is not set — the GitHub API and the source's own count endpoint carried the decision
- survey evidence: reports/MCP-SURVEY-BIOINFORMATICS-REPORT.md† — https://www.ensembl.org/biomart/martservice

## Refusals and gaps

- none: every permitted call answered

<!-- decided by harvester.discover at 2026-11-10T21:20:09.968556+00:00 -->
