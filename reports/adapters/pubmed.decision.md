# SOURCE DECISION — pubmed
#
# Written by `python3 -m harvester.discover pubmed` on 2026-11-10T21:19:51.800037+00:00.
# THE ONLY PLACE 'which way do we fetch' LIVES for this source
# (req-P1-THE-FRAMEWORK-GAINS-A-SOURCE-DISCOVE). The contract reads the
# verdict + decided_at below; a record older than 90 days is REFUSED by name,
# because a source can lose its MCP. Re-run the probe to refresh it.

source: pubmed
adapter: pubmed
verdict: USE-MCP
decided_at: 2026-11-10T21:19:51.800037+00:00
probe: harvester.discover/1
need: search
max_age_days: 90

## Verdict

**USE-MCP** — cyanheads/pubmed-mcp-server: usable MCP (★167, Apache-2.0, pushed 7 days ago) covering the search leg

## The fetch route

Drive the MCP server over stdio (or its Streamable HTTP mode) for the search leg. The station does not re-implement the source's protocol. Pin the server version exactly and record the tarball integrity beside the pin.

## Accessions this source returns

- PMID
- PMCID
- DOI
- EPMC ID

## Licence

- MCP server code `andybrandt/mcp-simple-pubmed`: **MIT** (MIT License) — read from the GitHub API https://api.github.com/repos/andybrandt/mcp-simple-pubmed, not from prose
- MCP server code `cyanheads/pubmed-mcp-server`: **Apache-2.0** (Apache License 2.0) — read from the GitHub API https://api.github.com/repos/cyanheads/pubmed-mcp-server, not from prose
- MCP server code `openags/paper-search-mcp`: **MIT** (MIT License) — read from the GitHub API https://api.github.com/repos/openags/paper-search-mcp, not from prose
- MCP server code `wp-a/nature-academic-search`: **MIT** (MIT License) — read from the GitHub API https://api.github.com/repos/wp-a/nature-academic-search, not from prose
- Data terms: https://www.ncbi.nlm.nih.gov/home/about/policies/ — recorded here, READ AT HARVEST TIME by `contract.licence_terms` (T6). This probe may not fetch it: its permitted calls are Exa, the GitHub API and the source's own count endpoint.

## Rate limit

- keyless: 3 requests/second (per IP)
- keyed: 10 requests/second with NCBI_API_KEY
- source: NCBI E-utilities usage policy + the surveyed server's README config table (survey §6); documented, not fetched by this probe
- source_url: https://www.ncbi.nlm.nih.gov/books/NBK25497/

## URLs read by this probe (bounded, read-only)

| # | kind | method | URL | status | bytes | sha256 |
|---|------|--------|-----|--------|-------|--------|
| 1 | github-search | GET | https://api.github.com/search/repositories?q=pubmed+mcp+in%3Aname%2Cdescription&sort=stars&order=desc&per_page=5 | 200 | 28630 | 838e0e077753a8bd… |
| 2 | github-search | GET | https://api.github.com/search/repositories?q=eutils+mcp+in%3Aname%2Cdescription&sort=stars&order=desc&per_page=5 | 200 | 27566 | 70c44daa2b38c045… |
| 3 | github-readme | GET | https://api.github.com/repos/cyanheads/pubmed-mcp-server/readme | 200 | 20116 | b58492a9cd952be6… |
| 4 | github-readme | GET | https://api.github.com/repos/andybrandt/mcp-simple-pubmed/readme | 200 | 5037 | ec38d33b469e9a51… |
| 5 | github-readme | GET | https://api.github.com/repos/openags/paper-search-mcp/readme | 200 | 42879 | 801f4fa2f2971066… |
| 6 | github-readme | GET | https://api.github.com/repos/wp-a/nature-academic-search/readme | 200 | 28507 | 6446d4defdac90ae… |
| 7 | count | GET | https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=Wt1%5BTitle%5D+AND+mouse%5BMeSH+Terms%5D&rettype=count | 200 | 226 | 3951947837c7203c… |

## Evidence

- count endpoint: https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=Wt1%5BTitle%5D+AND+mouse%5BMeSH+Terms%5D&rettype=count
  -> status 200, count = 399 (the survey measured 399)
- MCP candidate andybrandt/mcp-simple-pubmed: ★171, MIT, archived=False, pushed 2026-03-19T13:33:40Z (236 days ago); README sha256 ec38d33b469e9a51…
  - README keywords: DOI, count, download, rate limit
- MCP candidate cyanheads/pubmed-mcp-server: ★167, Apache-2.0, archived=False, pushed 2026-11-03T08:07:49Z (7 days ago); README sha256 b58492a9cd952be6…
  - README keywords: DOI, PMCID, PMID, count, download, rate limit, release, req/s
- MCP candidate openags/paper-search-mcp: ★3048, MIT, archived=False, pushed 2026-10-02T06:16:38Z (39 days ago); README sha256 801f4fa2f2971066…
  - README keywords: DOI, count, download, rate limit
- MCP candidate wp-a/nature-academic-search: ★384, MIT, archived=False, pushed 2026-10-01T22:10:37Z (39 days ago); README sha256 6446d4defdac90ae…
  - README keywords: DOI, PMCID, PMID, count
- Exa leg SKIPPED BY NAME: EXA_API_KEY is not set — the GitHub API and the source's own count endpoint carried the decision
- survey evidence: reports/MCP-SURVEY-BIOINFORMATICS-REPORT.md† — https://github.com/cyanheads/pubmed-mcp-server · .../esearch.fcgi?db=pubmed&term=Wt1[Title]+AND+mouse[MeSH+Terms]&rettype=count

## Refusals and gaps

- none: every permitted call answered

<!-- decided by harvester.discover at 2026-11-10T21:19:51.800037+00:00 -->

† withheld from this public tree; see docs/WITHHELD.md.
