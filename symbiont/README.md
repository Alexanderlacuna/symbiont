# symbiont — a provenance-first mining station

One question, one fixed route, any source:

    question → source adapter → fetch(since) → verify(our count vs the source's own)
             → snapshot(content-addressed raw + sha256 + release id)
             → xref(stable ids, conflict log) → publish(one view) → cost

The station is named SYMBIONT (owner's decision, 2026-11-10): it lives with whatever it attaches
to, it learns its sources' releases, and it reports what changed.

**The quickstart lives in one file: [`../README.md`](../README.md)** — install, the first offline
command, the five run modes (mine · hand over · ask · operate · serve), and the sentence that says
what each number covers and what it does not. That file owns the commands; this one is the package
reference (`pyproject.toml` ships it as the package readme) and does not repeat them.

The design record is `reports/DESIGN-STATION.md`†, operations are `symbiont/docs/OPS.md`,
observability is `symbiont/docs/OBS.md`, the four digests (schema, cadence, refusal rules) are
`symbiont/docs/DIGESTS.md`, adding a source is `symbiont/docs/PLUGIN-AUTHORING.md`,
and the support boundary is `SUPPORT.md`.

† held back from this public subset — the design record carries farm-internal detail. Every withheld path, with
why and how to request it, is a row in [`../docs/WITHHELD.md`](../docs/WITHHELD.md).

## Install, build, run — the quickstart owns those commands

The install and run commands are not repeated here: [`../README.md`](../README.md) is their one
definition site (Guix · plain `python3` · venv+pip · the five run modes). What this file keeps is
what a package user needs and a stranger does not: the contract, the store layout, the exit codes.

One dependency, the range `PyYAML>=6.0,<7` (the suites ran on 6.0.3; Guix's channel provides 6.0.2); Python >= 3.10. `guix.scm` in the
repo root builds the same station for Guix sites, and a cold first build takes minutes — the first
run fetches substitutes for python and setuptools, then builds PyYAML and this package from source.
That is the honest number; warm rebuilds are quick.

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

## What the test numbers cover — T1..T9 pass offline at fixture scale, and T8/T10 do not

`make check` (run it from the quickstart, [`../README.md`](../README.md)) runs T1..T9 with every
fixture served from 127.0.0.1 — zero external requests. **What those numbers cover:** the contract
and the refusals, offline, at **fixture scale** (a five-gene miniature store) — never a full-scale
data test, never a claim about a live source. T4's drop-and-rebuild of the 77k-row store is proved
only at miniature scale; T10's retrieval number is designed (design §9b) and no command produces it;
T8 is declared expected-fail in `make check`'s own output.

## The nine tests (design §9: T1..T9) — what passes and what is expected to fail

| test | what it proves | status under `make check` |
|---|---|---|
| T1 IDEMPOTENCY | run twice → second run `data_bytes=0`, view byte-identical | PASS (five-gene miniature, offline) |
| T2 COUNTS | ours == the source's own published count, URL recorded | PASS (miniature; the 77k-row ship run proved it on the real sources — 78,348/78,348 · 77,645/77,645, measured on the private store; the receipt is `runs/harvest-run-1.json`, store path redacted, available on request) |
| T3 CORRUPTION | five corrupted rows rejected BY REASON, none silently dropped | PASS (miniature; `python3 -m symbiont.selftest_corrupt` runs the same five corruptions against a real store when you have one) |
| T4 PROJECTION | the view rebuilt from raw alone → the same sha256 | PASS (miniature) |
| T5 BUDGET | a run over budget stops with REFUSED[budget] | PASS |
| T6 LICENCE | a source with unread terms refuses at discovery | PASS |
| T7 STALENESS | a stale source is named on the claim, not served quietly | PASS |
| T8 AGENT | an agent answers from tool output; every sentence maps to an accession | EXPECTED FAIL — not built yet (named in `make check` output) |
| T9 SOURCE-GONE | a 404 lands as REFUSED[source-gone] + quarantine, never a silent skip | PASS |
| T10 RETRIEVAL | the literature arm's recall@10/20/50 on a frozen public gold set (design §9b) | NOT BUILT — no command produces the number yet |

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
