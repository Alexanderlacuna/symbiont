# PLUGIN AUTHORING GUIDE — adding a source to symbiont

A source is a plug-in file plus one config entry. Nothing in the loop names a source; if you
find yourself editing `loop.py` to add one, the contract has been broken — that is a bug in your
adapter, not in the loop. This guide is the whole path from "a URL" to "a live source", and
every command in it is the real CLI (the same words the MCP tools run).

## 1 · The contract (four calls, and that is the whole interface)

```python
class MySource:
    name  = "my_source"            # the sources.yml id
    role  = "id_authority" | "xref_provider"

    def configure(self, cfg)            -> None            # the ONLY place a URL enters
    def discover(self, http, since)      -> Discovery       # release, the source's OWN published
                                                            #   count, etags, licence terms
    def fetch(self, http, since)         -> FetchPlan        # new=True + bytes, or new=False
                                                            #   with a reason ("provider quiet")
    def normalise(self, resources)       -> Normalised       # records + rejects WITH REASONS
                                                            #   + accounting (see the law below)
    def verify(self, d, n)               -> Verify          # ours vs the source's own count + URL
```

- `http` is the polite fetcher (rate limits, retry, budgets are enforced in `symbiont/http.py`;
  you never sleep, you never count, you never parse a URL by hand — `configure()` receives
  everything network-shaped from sources.yml).
- **the accounting law**: for every resource, `rows_in == records + rejects + used`. A row may be
  rejected, it cannot vanish. The loop refuses to publish a run that cannot account for its rows.
- **the canonical record**: one record per thing, `key` (symbol) · `id` (this source's authority
  id, or "" for an xref provider) · `source` · extra keys the join ignores. Sameness of shape is
  what makes the xref join possible.
- `ADAPTER = MySource()` must sit at module level — that is how the loader finds you.

## 2 · Where everything else lives (you write none of it)

`sources.yml` is the ONE definition site per source: `base_url`, `endpoints`, `rate_limit`,
`licence_url`, `licence_text`, `staleness.policy_days`, `budget`, `quarantined`. Repointing a
mirror, changing a rate limit, capping a run: an edit there, never an edit in your adapter.

## 3 · The pipeline (probe → evaluate → draft → sandbox → card → admit)

```
symbiont admit probe   <url> [--licence-url <page>]   # read it under a hard budget; nothing usable -> NO-SOURCE
symbiont admit evaluate <id>                          # licence (read + hashed), accessions, size, cadence
symbiont admit draft    <id>                          # adapter SKELETON from the template + the
                                                      #   contract's load check (in the drafts bay)
symbiont admit sandbox  <id> [--budget-requests N …]  # run the draft into a throwaway store, on a budget
symbiont admit card     <id> --question "<…>"         # the admission card; registers enabled:false
symbiont admit admit    <id> [--gate "<the word>"]    # THE gated step: flip active, move the adapter in
```

(The same six steps are the MCP tools `source_probe … source_admit`; one definition.)

### The ADAPTER DECISION record

Before `draft`, write the decision in the card's terms — one line naming which shape the source
needs and the evidence for it:

- `USE-MCP` — wrap an existing MCP server (the literature arm wraps `@cyanheads/pubmed-mcp-server`,
  pinned 2.12.0: pagination, retry and rate limits are exactly what we would get wrong);
- `WRAP-MCP` — an MCP exists but needs a thin adapter of ours around it;
- `WRITE` — a REST API or a release dump (Ensembl BioMart, MGI);
- `NO-SOURCE` — nothing usable (the probe says so and lists the URLs it read).

The decision is recorded because the next reader must know WHY the shape is what it is. The
card refuses a source whose licence was never read; treat the decision the same way — an adapter
with no recorded decision is a draft, not a source.

### The sandbox

`sandbox` runs YOUR drafted adapter into a throwaway store under an explicit budget
(requests/bytes/wall). It is where your adapter earns its card: the sandbox receipt is the
evidence the card's `count_match`, `sample_rows`, `xref_rate` and `cost` lines rest on. A sandbox
run that blows its budget answers `REFUSED[budget]` — that is the sandbox doing its job.

### The card

The card is a text file, one line per check — `licence`, `count_match`, `sample_rows`,
`xref_rate`, `cost` — plus the question the source helps answer (a source with no question is a
cost). It is cut by `symbiont admit card` FROM THE EVIDENCE (probe, evaluation, sandbox
receipt); you do not write the checks by hand, you make them true.

### The gate

`admit` is the ONE gated step. The station consults the owner's policy
(`symbiont/docs/POLICY-TEMPLATE.yml` is the blank form; the live file is the owner's numbers): a
source whose card passes EVERY check the policy names AND that has been stable for N consecutive
cycles self-admits; anything less needs the operator's word, quoted. A quarantined source is
never self-admitted — its way back is the re-admission card:
`python3 -m symbiont.loop --readmit <id> --card <file>`.

## 4 · Writing the adapter by hand (if you skip the pipeline)

1. Copy `symbiont/adapters/mgi.py` or `ensembl_biomart.py` — both are the contract in full.
2. Put your file in `symbiont/adapters/`, add the source to `sources.yml`, and run
   `python3 -m symbiont.loop --sources <id> --store /tmp/sandbox` — the registry check refuses
   BY NAME if config and code disagree (an entry with no adapter, an adapter with no entry).
3. Prove it offline before you spend: `--replay-raw <source>:<reskey>=<file>` normalises from
   substituted bytes, no requests leave the box.

## 5 · What the station does with your adapter once live

discover → staleness → fetch → snapshot (content-addressed raw + sha256 + release id) → read back
from disk and re-hash → normalise + accounting → verify (ours vs the source's own count) → xref
join → publish. Your adapter is called for its four calls and nothing else; every refusal the
loop can raise is named (`budget | licence-unknown | source-gone | schema-drift`), every reject
has a reason, and the run record carries the lot.

## 5a · What a hash proves, and what it does not (the drift signal's limit)

A pinned MCP server is trialled at admission and its `tools/list` (plus its declared version) is hashed. On every later run the hash is re-read: a change is
`REFUSED[schema-drift]` and the source's trial EXPIRES, so it must be re-trialled before it can harvest again. That is the most that a boundary check can do,
and its limit is worth stating rather than discovering:

```
A HASH PROVES A SHAPE, NEVER AN INTENT.
   a changed tool list            -> caught (REFUSED[schema-drift], trial expired)
   a changed argument schema      -> caught if it is inside the hash, otherwise only when a call fails
   SAME shape, DIFFERENT meaning  -> NOT CAUGHT. A server can keep three tools and the same arguments and change what "query" returns.
```
The same limit appears where a station consumes another station's handover (`docs/MANIFEST.md` §2b): a schema hash catches a shape change, never a semantic
one. In both places the honest consequence is identical — the standing controls are the COUNT CHECK and the LICENCE GATE, not the hash, and a federated or
MCP-derived row may only ever claim the verification depth it actually got.
