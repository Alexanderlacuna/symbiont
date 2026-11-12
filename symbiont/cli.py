"""symbiont — the console door: ONE command, every subcommand delegated to its owner module.

The route (fixed, source-agnostic):

    question -> source adapter -> fetch(since) -> verify(ours vs the source's own count)
             -> snapshot(content-addressed raw + sha256 + release id)
             -> xref(stable ids, conflict log) -> publish(one view) -> cost

Each subcommand delegates to the module that DEFINES it (loop, health, digests, admission,
manifest) — nothing is reimplemented here, so the console door and the python -m door can never
drift.
Exit codes are the contract: 0 produced output · 1 a count disagreed · 2 refused before the run
(bad input / config disagreement) · 3 a source refused (budget | licence-unknown | source-gone |
source-unreachable | count-mismatch) or the claim could not be joined · 8 all rows rejected.

    symbiont fetch    [loop args]     run the mining loop (see `symbiont fetch --help`)
    symbiont health   [health args]   one line per source: state, last run, staleness, ONE reason
    symbiont digest   [digest args]   the four digests (docs/DIGESTS.md): show · verify (the
                                      refusal rules) · write (the daily gaps/suggest refresh)
    symbiont admit    [admission …]   the agent-added-sources pipeline (probe→evaluate→draft→
                                      sandbox→card→admit; the one gated step)
    symbiont manifest [manifest args] the handover manifest (the only write: staged file + manifest)
    symbiont claim    [claim args]    the claim ledger + the as-of/current door: mint a claim against
                                      the CURRENT release, supersede when a newer release lands,
                                      answer 'as of release R' or 'current' (never one for the other)
    symbiont resolve [resolve args]   the point-at door: fetch a point-at accession ON DEMAND and
                                      answer with fetched_now: true + the cost (a store-class
                                      resource is refused here by name — it lives in raw/)
    symbiont retention [retention …]  the N-releases rule: freeze release bundles, sweep raw with
                                      archive-or-refuse (a drop is ARCHIVED with the hash kept or
                                      REFUSED by name — never silently removed)
    symbiont registry [registry …]    THE DIARY's human door (symbiont/events.py holds the only
                                      append): add-event · allow/deny (the switch, with
                                      intent_changed) · manual-add · override · verify · head ·
                                      show (THE INDEX: a projection of sources.yml + the diary,
                                      rebuilt every time, disposable, sha256 in its own header).
                                      The closed transition table applies to a human's word too.
"""
from __future__ import annotations

import argparse
import sys

VERSION = "0.1.0"

# subcommand -> (help line, module, main) — the module is imported lazily so `symbiont --help`
# stays fast and a broken optional door cannot take the whole door down with it.
COMMANDS = {
    "fetch": ("run the mining loop: discover -> fetch -> verify -> snapshot -> xref -> publish", "loop"),
    "health": ("one line per source: state, last run, staleness — the ONE command (OBS.md)", "health"),
    "digest": ("the four digests: show · verify (the refusal rules) · write (the daily refresh)", "digests"),
    "admit": ("the agent-added-sources pipeline; `admit` is the one gated step", "admission"),
    "manifest": ("the handover manifest: staged file + manifest + receipt (the only write)", "manifest"),
    "claim": ("the claim ledger + the as-of/current door (mint · supersede · answer)", "claims"),
    "resolve": ("the point-at door: fetch a point-at accession on demand (fetched_now + cost)",
                 "resolve"),
    "retention": ("the N-releases rule: freeze release bundles, sweep raw (archive or refuse)",
                  "retention"),
    "registry": ("the diary's human door: add-event · allow · deny · manual-add · override · "
                 "bootstrap (first run of a clone: the shipped backfill) · "
                 "verify · head · show (THE INDEX: a projection, rebuilt every time) — the ONE "
                 "writer for humans", "registry"),
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # delegate FIRST, before any parsing: the first word names the command, and the module that
    # owns it defines its own arguments and its own --help (one definition, no drift, no stub).
    if argv and argv[0] in COMMANDS and argv[0] not in ("-h", "--help", "--version"):
        import importlib
        mod = importlib.import_module(f".{COMMANDS[argv[0]][1]}", package="symbiont")
        return mod.main(argv[1:])
    ap = argparse.ArgumentParser(
        prog="symbiont",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"symbiont {VERSION}")
    sub = ap.add_subparsers(dest="cmd")
    for name, (help_line, _mod) in COMMANDS.items():
        sub.add_parser(name, help=help_line)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    import importlib
    mod = importlib.import_module(f".{COMMANDS[a.cmd][1]}", package="symbiont")
    return mod.main([])
