# symbiont — a provenance-first mining station

One question, one fixed route, any source:

    question → source adapter → fetch(since) → verify(our count vs the source's own)
             → snapshot(content-addressed raw + sha256 + release id)
             → xref(stable ids, conflict log) → publish(one view) → cost

The station is named SYMBIONT (owner's decision, 2026-11-10): it lives with whatever it attaches
to, it learns its sources' releases, and it reports what changed. This README is the 10-minute
quickstart; the design record is `reports/DESIGN-STATION.md`†, operations are `symbiont/docs/OPS.md`,
observability is `symbiont/docs/OBS.md`, the four digests (schema, cadence, refusal rules) are
`symbiont/docs/DIGESTS.md`, adding a source is `symbiont/docs/PLUGIN-AUTHORING.md`,
and the support boundary is `SUPPORT.md`.

† held back from this public subset — the design record carries farm-internal detail. Every withheld path, with
why and how to request it, is a row in [`../docs/WITHHELD.md`](../docs/WITHHELD.md).

## Install

    python3 -m venv .venv
    . .venv/bin/activate
    pip install -e .

One dependency, `PyYAML==6.0.3` (the version the suites ran on); Python >= 3.10. Then:

    $ symbiont --help
    usage: symbiont [-h] [--version] {fetch,health,digest,admit,manifest} ...

    symbiont — the console door: ONE command, every subcommand delegated to its owner module.
    …
    $ symbiont --version
    symbiont 0.1.0

## Build and run under Guix (the channel Genenetwork4 already speaks)

No venv, no pip: one recipe, `guix.scm`, in the repo root. The commands, exactly:

    guix build -f guix.scm                  # the package: /gnu/store/…-symbiont-0.1.0
    guix shell -D -f guix.scm               # dev shell: python, setuptools, wheel, PyYAML
    guix shell -D -f guix.scm -- python3 -m symbiont.selftest_sources   # 25/25, offline
    guix shell -D -f guix.scm -- symbiont --help

Or install it into a profile and run the installed station (not the checkout):

    guix package -p ~/.guix-extra-profiles/symbiont -i python-wrapper -f guix.scm
    source ~/.guix-extra-profiles/symbiont/etc/profile
    python3 -m symbiont.selftest_sources    # 25/25
    symbiont --version                      # symbiont 0.1.0

**A cold first build takes minutes** — the first run fetches substitutes for python and setuptools,
then builds PyYAML and this package from source. That is the honest number; warm rebuilds are quick.

What ships is the shipped set and nothing else — the package, `annot`, and the run-time inputs the
station reads out of `reports/` (`reports/adapters/` decision records, the policy, the queue, the
drop reasons, the journal): the same set `acceptance.sh`'s A1-CLONE ships. The recipe's
`#:select?` filter excludes the store (446 MB), `models/`, `.venv*`, `node_modules/`, `__pycache__`
and `bench/data`, so the source copy in the store is about 5 MB, not 4 GB.

One dependency decision, made once and repeated in `guix.scm`, `pyproject.toml`, `CHANGELOG.md` and
here so the two recipes cannot disagree: **DECIDED 2026-11-11
(req-P1-ONE-DEPENDENCY-ONE-VERSION-AND-A-CHECK): PyYAML 6.0.3 is THE version** — the version the
suites ran on. `guix.scm` builds that same 6.0.3 (a variant of the channel's `python-pyyaml`, with
its own hash) and propagates it, so the pip recipe and the Guix recipe name one version. And the
decision is asserted, not described: `make check`'s dependency-version step greps every file in the
tree and fails on a second value, on a range, and on a file that names the dependency with no
version.

## See it work OFFLINE first (the sources can be firewalled)

`make check` runs the nine tests (T1..T9) with every fixture served from 127.0.0.1 — zero
external requests:

    $ make check
    …
    make check: T1-T7 and T9 GREEN (offline) · T8 expected-fail, named above

The committed miniature store shows the shape of a real one before you fetch anything:

    $ symbiont health --store symbiont/fixtures/store
    {"ensembl_biomart": {"state": "ok", "last_run_age_s": 6, … "reason": "ok: ran in arc-3 6s ago;
      count check agreed (ours=5 == theirs=5); … the store grounds it: view mouse_gene holds 5 rows
      over 5 xref pairs"} …}
    $ head symbiont/fixtures/store/view/mouse_gene.v1.tsv
    symbol	mgi_id	ensembl_gene_id	chromosome	start	end
    Actb	MGI:88056	ENSMUSG00000029580	5	142903097	142906754
    …

## The real fetch (about ten minutes, network required)

Fetch into a throwaway store — never into the shipped tree — with the two shipped sources
(Ensembl BioMart as the id authority, MGI as the xref provider):

    $ symbiont fetch --store /tmp/symbiont-demo
    HARVEST run 20261110T…Z  question: what mouse gene identity and position can we publish
    …
    PUBLISH  view/mouse_gene.v1.tsv rows=77144 sha256=<hash>
    COST     requests=… data_bytes=<bytes> … rows_out=77144 …

    $ head -3 /tmp/symbiont-demo/view/mouse_gene.v1.tsv
    symbol	mgi_id	ensembl_gene_id	chromosome	start	end
    0610005C13Rik	MGI:1918911	ENSMUSG00000109644	7	45217213	45224751
    0610006L08Rik	MGI:1923503	ENSMUSG00000108652	7	74468414	74503613
    …

Run it again — the second run fetches NOTHING (this is test T1, and it is the whole point of
`state/`):

    $ symbiont fetch --store /tmp/symbiont-demo
    …
    COST     requests=… data_bytes=0 …
    …

(The exact outputs a stranger sees, pasted from a clean clone, are quoted in the repository's
top-level README.md — this file is the package readme; that one is the quickstart transcript.)

## The nine tests (design §9: T1..T9) — what passes and what is expected to fail

| test | what it proves | status under `make check` |
|---|---|---|
| T1 IDEMPOTENCY | run twice → second run `data_bytes=0`, view byte-identical | PASS (five-gene miniature, offline) |
| T2 COUNTS | ours == the source's own published count, URL recorded | PASS (miniature; the 77k-row ship run proved it on the real sources: 78,348/78,348 · 77,645/77,645) |
| T3 CORRUPTION | five corrupted rows rejected BY REASON, none silently dropped | PASS (miniature; `python3 -m symbiont.selftest_corrupt` runs the same five corruptions against a real store when you have one) |
| T4 PROJECTION | the view rebuilt from raw alone → the same sha256 | PASS (miniature) |
| T5 BUDGET | a run over budget stops with REFUSED[budget] | PASS |
| T6 LICENCE | a source with unread terms refuses at discovery | PASS |
| T7 STALENESS | a stale source is named on the claim, not served quietly | PASS |
| T8 AGENT | an agent answers from tool output; every sentence maps to an accession | EXPECTED FAIL — not built yet (named in `make check` output) |
| T9 SOURCE-GONE | a 404 lands as REFUSED[source-gone] + quarantine, never a silent skip | PASS |

## A source is a plug-in file

`adapters/<source>.py` exposes `ADAPTER` with `configure · discover · fetch · normalise · verify`
and a `role` (`id_authority` or `xref_provider`). `symbiont/contract.py::load_adapters()` walks
the directory — nothing in `loop.py` names a source, and nothing in `xref.py` knows the words
"ensembl" or "MGI". Drop a file in, add its `sources.yml` entry, the loop picks it up. If you
have to edit `loop.py` to add a source, the contract was broken. The full path from a URL to a
live source (probe → evaluate → draft → sandbox → card → admit) is
`symbiont/docs/PLUGIN-AUTHORING.md`.

The loop refuses to publish when a row is unaccounted for (`contract.check_accounting`): for
every resource, `rows_in == records + rejects + used`. A row can be rejected, it cannot vanish.

## What is on disk (the store)

    raw/<source>/<release>/<sha256>.<ext>          the release file, named by its own hash, byte-exact
    raw/<source>/<release>/<sha256>.meta.json      url, etag, last-modified, bytes, sha256, run
    state/<source>.json                            what "since" means: release, count, etags, snapshots
    xref/xref.tsv                                  every pairing decision (ok / ok_with_conflict / unresolved)
    view/mouse_gene.v1.tsv                         the published projection + schema + provenance sidecars
    runs/<id>.json                                 the run record: cost, counts, licence, refusals, findings
    rejects/<run>.<source>.tsv                     rejected rows WITH REASONS
    digest/<source>.md                             the release-watch + staleness digest, one block per run
                                                  (docs/DIGESTS.md; the T9 quarantine trail appends
                                                  above the run blocks)
    digest/gaps.md · digest/suggest.md            counts with the queries that produced them; ranked
                                                  options with cost, licence, question — never a decision

## Exit codes are the contract

    0 produced output · 1 a source's count disagreed · 2 refused before the run (bad input /
    config disagreement) · 3 a source refused (budget | licence-unknown | source-gone |
    source-unreachable | count-mismatch) or the claim could not be joined · 8 all rows rejected.
    A refusal is a legal answer with a reason file. Silent acceptance is the only forbidden outcome.

## Licence

MIT for the code (LICENCE). The data carries its sources' own terms — MGI CC-BY 4.0, Ensembl no
restrictions, EuropePMC per-record — recorded per run with the terms page's sha256; see the
per-source terms table in LICENCE and `SUPPORT.md` for the boundary.
