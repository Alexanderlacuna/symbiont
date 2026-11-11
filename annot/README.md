# annot — the ship's ONE core, and four doors that are only readers

`annot` answers six genetics questions as JSON. It is a **reader of the symbiont's store** — it
never fetches, never writes, never re-implements a rule that `symbiont/` already owns. The four
doors pass the core's bytes through untouched, so a CLI answer, an MCP answer, a REST answer and the
GN page are the *same bytes* for the same query (proved: `annot/selftest.sh` check B, `cmp` == 0).

    annot/core.py               THE core: store reader · 6 queries · provenance · canonical JSON
    annot/__main__.py           door (a) CLI      python3 -m annot <tool> --param …
    annot/mcp_server.py         door (b) MCP      python3 -m annot.mcp_server   (stdio JSON-RPC)
    annot/rest.py               door (c) REST     python3 -m annot.rest --port 8765
    annot/web/gn_dossier.html   door (d) GN page   served by rest.py at `/`, or embedded by `annot page`
    annot/page.py               renders the page headlessly from the same bytes (`python3 -m annot page`)
    annot/selftest.sh           the NSC harness: writes · one-definition · accession · empty · offline

## The six tools

| tool | params | answers |
|---|---|---|
| `gene_dossier` | `gene`, `limit` | identity, xref, phenotype evidence, publications, traits — in named sections |
| `gene_xref` | `gene`, `limit` | Ensembl gene id ⇄ MGI accession ⇄ symbol, plus conflicts and retired symbols |
| `phenotype_evidence` | `gene`, `limit` | phenotype annotations with accessions (named empty when no phenotype source is in the store) |
| `publications_for` | `gene`, `limit` | publications by PMID (named empty when no publication source is in the store) |
| `traits_containing` | `term`, `limit` | trait markers (MGI `Marker Type` = QTL/phenotype) whose name or symbol contains `term` |
| `what_changed` | `source`, `since` | which harvest run changed what, since an ISO-8601 instant |

`gene` accepts a symbol (case-insensitive), an `MGI:` accession, an `ENSG…` gene id, or a retired
symbol that MGI folded into a current one.

## The opt-in `answer` mode — citation-required at the dossier door

`answer` is a RESPONSE MODE, not a seventh source of facts: it takes `{tool, params}` — a read
tool and that tool's own parameters — plus a composed answer (a JSON array of sentences), and it
checks the answer against the store. Beside `items` it returns `sentences`, one entry per submitted
sentence — `{text, accessions, grounded, reason}`. A sentence is grounded ONLY if every accession
it cites (MGI:… / ENSMUSG…) is present in the response's own items AND the stored record RE-READ
for that accession (a fresh open of the view file, not the memoised index) agrees with the value
the sentence names. Otherwise `grounded` is false and `reason` says which failure it was:
`no accession` / `accession not in items` / `stored value differs: X vs Y` (the sentence's value
X against the stored value Y). A refused sentence is returned with its reason, never silently
dropped. `provenance.answer_mode` names the accessions checked and how many sentences grounded.
It is opt-in: the six read tools answer unchanged and carry no `mode` key.

```bash
python3 -m annot call answer --json '{"tool":"gene_dossier","params":{"gene":"Trp53"},"sentences":["Trp53 is the mouse gene with MGI accession MGI:98834.","The moon is made of cheese, as recorded for MGI:99999999."]}'
```

The three citation cases live in `annot/selftest_citation.sh` (and, as fixtures, in
`annot/fixtures/answer-mode/`, run by `annot/selftest.sh` check G) together with one real answer
composed from the current store.

## The item shape — one definition, enforced

Every item in every response is

```json
{"value": "…", "accession": "MGI:98834", "source": "mgi", "release": "mgi-2026-11-09",
 "fetched_at": "2026-11-09T20:32:57.679529+00:00",
 "sha256": "4891f2543ed5a7366299d77e96e8abd2a91a4906dbdc0144b43e8902bdf96a6a"}
```

`core._assert_items()` walks every response before it leaves the core and raises if an item has no
accession (or no source/release/fetched_at/sha256). A blank item is not representable.

* `fetched_at` is **never a clock reading at query time** — it is the `finished_at` of the harvest
  run whose snapshot sha256 equals the one the published view's provenance names, so two doors
  asking a second apart return identical bytes. That rule is written into
  `provenance.rules.fetched_at` in every response.
* `sha256` is the content hash of the bytes the item was read from (the content-addressed raw
  release snapshot, or the run/state record for a change item).
* For a change event (`what_changed`) the accession is the release id — the id in that source's own
  namespace — and the rule is stated in `provenance.rules.accession`.

## Provenance and unreachable sources

Every response carries

```json
"provenance": {"read_only": true, "store": "…/symbiont/store",
  "view": {"name": "mouse_gene", "version": 1, "rows": 77144, "sha256": "4c9795…", "intact": true},
  "sources": {"mgi": {"release": "mgi-2026-11-09", "fetched_at": "…", "sha256": {"mrk_ensembl": "…"},
                      "licence": "MGI data and annotations: CC-BY 4.0 (attribution required)"}, "…": {}},
  "unreachable": [{"source": "mgi_phenotype_annotations", "why": "…", "needed": "…"}]}
```

`unreachable` names every source the query would have used and the store does not have. `view.intact`
is `sha256` of the published view recomputed at read time, compared with the hash recorded in the
provenance sidecar — a store that was edited under the core cannot pass silently.

### A view travels with its receipt — the door will not read it otherwise

The station's rule, enforced at the door: **a view is a `.tsv` plus its two sidecars, or it is not a
view.** In `view/` beside `mouse_gene.v1.tsv` there must be

    mouse_gene.v1.provenance.json   source(s) · release(s) · fetched_at · licence (with attribution)
                                    · row count · the tsv's sha256 · the schema's sha256
    mouse_gene.v1.schema.json       the columns, their types and the view's key

Both are written by the same publish run that writes the view (`symbiont/loop.py`'s publish step, or
`python3 -m symbiont.loop --rebuild-from-raw`, which re-derives all three from the store's raw zone
and refuses on the first hash that disagrees) — never by hand. A `.tsv` with no sidecar is a number
nobody can trace, so `annot` REFUSES to read it, from every door, with a class of its own:

```
REFUSED[view-unreceipted]: the view …/view/mouse_gene.v1.tsv is on disk but its receipt is not —
looked for …/view/mouse_gene.v1.provenance.json and …/view/mouse_gene.v1.schema.json. … Regenerate
them from this store's own raw zone with: python3 -m symbiont.loop --rebuild-from-raw --store …. Nothing
was read and nothing was written.
```

`status: "refused"`, `reason: "view-unreceipted"`, `items: []`, exit code 3, and the missing sidecars
named in `refusal.missing`. This is deliberately NOT `store-missing`: there the data is absent, here the
data is present and its receipt is gone — a different defect, and one that would otherwise be answered
around. The published dataset (`harvester-view/`) follows the same rule: the tsv there is accompanied by
its two sidecars, and `annot/selftest.sh` check H6 proves the sidecar hash equals `sha256sum` of the tsv.

## Doors, verbatim

```bash
python3 -m annot --tools                                   # the surface as JSON (names + inputSchema)
python3 -m annot gene_dossier --gene Trp53                 # door (a)
python3 -m annot call gene_dossier --json '{"gene":"Trp53"}'   # the canonical form the MCP uses
python3 -m annot.mcp_server                                # door (b): MCP over stdio
python3 -m annot.rest --port 8765                          # door (c): GET /annot/<tool>?args, page at /
python3 -m annot page --gene Trp53 > /tmp/dossier.html      # door (d), headless, same bytes embedded
bash annot/selftest.sh                                     # the NSC harness
```

### Registering the MCP server with an LLM client

Any client that speaks MCP over stdio can take it; nothing about this server is host-specific:

```json
{"mcpServers": {"annot": {"command": "python3", "args": ["-m", "annot.mcp_server"],
                          "cwd": "/home/kabui/app-farm"}}}
```

It advertises `tools: {}`, seven tools (six read + the opt-in `answer` mode), and
`annotations.readOnlyHint: true` on each. A gene with no
record returns `status: "empty"` with a `reason` and a `message`; it never returns an empty string.

## What this core deliberately does NOT answer

The store holds mouse **identity** (Ensembl gene ids + coordinates × MGI symbols/accessions) and
MGI's **marker list**. It holds no phenotype annotations and no publications, so
`phenotype_evidence` and `publications_for` return *named* empty results that say which source would
answer them (`provenance.unreachable`). That is a measurement of the store, not a stub: when a
phenotype or publication source is harvested into `symbiont/store`, these two queries gain data
without a single line changing in the doors — the tools read the store, not a hard-coded source list.

## Offline

`annot` has no network code at all (no `socket`, `urllib`, `http.client`, `requests` — see
`annot/selftest.sh` check A). With the network torn away the answers are identical:

```bash
unshare -rn python3 -m annot gene_dossier --gene Trp53 | cmp - <(python3 -m annot gene_dossier --gene Trp53)
```
