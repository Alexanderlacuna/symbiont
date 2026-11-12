"""THE REGISTRY DOOR — `python3 -m symbiont.registry …`: the ONLY writer for humans.

The diary (`<store>/` + the one file name symbiont/events.py owns, DIARY_NAME) is appended by
the CODE at every
transition. A human never edits it, and never appends to it by hand: they speak one of the words
below, and the same closed transition table that the code obeys is applied to their word. An
`admitted` after a `refused` is REFUSED[illegal-transition] here exactly as it is in the loop.

    registry add-event  --class <c> --source <publisher:dataset> --why "…" [--release R]
                        [--actor operator|machine] [--origin live|backfill]
                        [--evidence k=v …] [--bump]      the ONE generic writer (see §5 of the row)
    registry allow  <id> --why "…"      the operator's SWITCH: enabled: true  + intent_changed
    registry deny   <id> --why "…"      the operator's SWITCH: enabled: false + intent_changed
    registry manual-add <publisher:dataset> --why "…" [--evidence k=v …]
                                        a source the OPERATOR introduced — never evaluated, and it
                                        says so permanently (`manual_add`, not `admitted`)
    registry override <publisher:dataset> --why "…" --scope one-run|until:YYYY-MM-DD
                                        ONE exception, with a scope: this is the only thing that
                                        lets an un-admitted source be fetched (the door's
                                        REFUSED[not-admitted] names it)
    registry verify                     read the diary, verify every line and the hash chain,
                                        REPORT a truncated tail (exit 3), print the head
    registry head                       just the anchor: seq + sha256 of the last line

WHY `allow`/`deny` WRITE sources.yml AND THE DIARY: flipping the switch is a DECISION, and a
decision with no history is a rumour. The switch file stays the operator's one hand-written file
(the intent), and `intent_changed{from,to,why}` records who flipped it, when, and why — so the
switch can always be REBUILT from the diary if it is lost or mangled.

Exit codes: 0 wrote it · 2 REFUSED, the reason named and nothing written · 3 `verify` found damage
(a finding: the diary is reported, never rewrapped or skipped).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

from . import contract, events

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORE = os.path.join(ROOT, "store")


def say(line: str = "") -> None:
    print(line, flush=True)


def _refuse(code: str, msg: str) -> int:
    print(f"REFUSED[{code}] {msg}", flush=True)
    return 2


def _kv(pairs: list[str]) -> dict:
    out: dict = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(_refuse("usage", f"--evidence wants k=v, got {p!r}"))
        k, v = p.split("=", 1)
        out[k.strip()] = v
    return out


def _sources(path: str) -> dict:
    try:
        return contract.load_sources(path)
    except contract.ConfigError as e:
        raise SystemExit(_refuse(getattr(e, "code", "config"), str(e))) from e


def _find(sources: dict, key: str) -> tuple[str, dict]:
    """Resolve a source by its adapter id OR by its dataset identity — one namespace, one lookup."""
    if key in sources:
        return key, sources[key]
    for sid, cfg in sources.items():
        try:
            if contract.dataset_id(cfg) == key:
                return sid, cfg
        except contract.ConfigError:
            continue
    raise SystemExit(_refuse(
        "no-such-source", f"{key!r} names no source in the config (neither an `id` nor a "
                          f"<publisher>:<dataset>): {sorted(sources)}"))


def _write_switch(path: str, sid: str, enabled: bool) -> str:
    """Atomic rewrite of ONE source's switch, header preserved, proved by a re-read."""
    with open(path, encoding="utf-8") as fh:
        header = contract._header(fh.read())
    sources = _sources(path)
    before = sources[sid]["enabled"]
    sources[sid]["enabled"] = enabled
    text = header + contract.dump_sources(sources)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    back = _sources(path)
    if back[sid]["enabled"] is not enabled:
        raise SystemExit(_refuse("switch", f"{path}: enabled:{str(enabled).lower()} for {sid} did "
                                           f"not survive a re-read — nothing in the switch moved"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── the commands ─────────────────────────────────────────────────────────────────────────────
def cmd_add_event(a) -> int:
    evidence = _kv(a.evidence)
    try:
        ev = events.append(a.store, a.event_class, source_id=a.source, why=a.why, actor=a.actor,
                           release=a.release, evidence=evidence, origin=a.origin)
    except events.IllegalTransition as e:
        return _refuse(e.code, str(e))
    except events.DiaryRefused as e:
        say(str(e))
        return _refuse(e.code, f"nothing was appended to {events.diary_path(a.store)}")
    say(f"APPENDED seq {ev['seq']}  {ev['class']}  {ev['source_id']}  ({ev['actor']}, "
        f"{ev['origin']})")
    say("         " + events.line_bytes(ev).decode("utf-8"))
    say(f"DIARY    {events.diary_path(a.store)}  head={events.sha(events.line_bytes(ev))[:16]}…")
    return 0


def cmd_switch(a, enabled: bool) -> int:
    sources = _sources(a.sources_config)
    sid, cfg = _find(sources, a.id)
    source_id, how = events.dataset_of(cfg)
    was = bool(sources[sid]["enabled"])
    if was is enabled and not a.force:
        return _refuse("no-change", f"{sid} is already enabled: {str(enabled).lower()} — a switch "
                                    f"flip that flips nothing is not a transition (--force records "
                                    f"the intent again)")
    sha_after = _write_switch(a.sources_config, sid, enabled)
    evidence = dict(sources_config=os.path.abspath(a.sources_config), adapter=sid,
                    dataset_id=source_id, source_id_source=how, config_sha256=sha_after)
    evidence["from"] = was
    evidence["to"] = enabled
    try:
        ev = events.append(a.store, "intent_changed", source_id=source_id, why=a.why,
                           actor="operator", release=a.release, evidence=evidence)
    except events.DiaryRefused as e:
        # THE SWITCH MOVED BUT THE DIARY REFUSED: put the switch back, or the two authorities
        # disagree and the next run would obey a decision with no history behind it.
        _write_switch(a.sources_config, sid, was)
        say(str(e))
        return _refuse(e.code, f"the switch was flipped back to enabled: {str(was).lower()} because "
                               f"the diary refused the event — intent and facts may not disagree")
    say(f"SWITCH   {sid} enabled: {str(was).lower()} -> {str(enabled).lower()}  "
        f"({os.path.abspath(a.sources_config)})")
    say(f"APPENDED seq {ev['seq']}  intent_changed  {source_id}")
    say("         " + events.line_bytes(ev).decode("utf-8"))
    return 0


def cmd_manual_add(a) -> int:
    evidence = _kv(a.evidence)
    release = a.release
    if a.sources_config:
        sources = _sources(a.sources_config)
        sid, cfg = _find(sources, a.source)
        evidence.setdefault("sources_config", os.path.abspath(a.sources_config))
        evidence.setdefault("adapter", sid)
        release = release or None
    try:
        ev = events.append(a.store, "manual_add", source_id=a.source, why=a.why, actor="operator",
                           release=release, evidence=evidence)
    except events.DiaryRefused as e:
        say(str(e))
        return _refuse(e.code, "nothing was appended")
    say(f"APPENDED seq {ev['seq']}  manual_add  {a.source}  (never evaluated — the diary says so "
        f"permanently until an `evaluated` event clears it)")
    say("         " + events.line_bytes(ev).decode("utf-8"))
    say("NOTE     manual_add is NOT a permission: the door still returns REFUSED[not-admitted] "
        "until the source is evaluated and admitted, or a scoped manual_override is recorded")
    return 0


def cmd_override(a) -> int:
    if a.scope != "one-run" and not a.scope.startswith("until:"):
        return _refuse("scope", f"--scope must be 'one-run' or 'until:<YYYY-MM-DD>', got {a.scope!r} "
                                f"— an exception with no end is a policy change wearing a flag")
    if a.scope.startswith("until:"):
        end = a.scope.split(":", 1)[1]
        try:
            import datetime as dt
            dt.date.fromisoformat(end)
        except ValueError:
            return _refuse("scope", f"--scope {a.scope!r}: {end!r} is not a date (YYYY-MM-DD)")
    evidence = _kv(a.evidence)
    evidence["scope"] = a.scope
    try:
        ev = events.append(a.store, "manual_override", source_id=a.source, why=a.why,
                           actor="operator", release=a.release, evidence=evidence)
    except events.DiaryRefused as e:
        say(str(e))
        return _refuse(e.code, "nothing was appended")
    say(f"APPENDED seq {ev['seq']}  manual_override  {a.source}  scope={a.scope}")
    say("         " + events.line_bytes(ev).decode("utf-8"))
    say("NOTE     an override never writes `admitted`: the receipt travels in the run record and "
        "the manifest, and evaluation clears it")
    return 0


def cmd_verify(a) -> int:
    r, problems = events.verify(a.store)
    say(f"DIARY    {events.diary_path(a.store)}")
    say(f"LINES    {len(r.events)} complete event(s) · {r.bytes} byte(s) · "
        f"{'present' if r.exists else 'ABSENT (no history yet)'}")
    say(f"HEAD     seq={r.seq} sha256={r.head}")
    if problems:
        say(events.report(events.diary_path(a.store), problems))
        return 3
    say("CHAIN    every prev_event_sha256 matches; every seq equals its line number")
    say("LIMIT    the chain makes an ALTERATION visible, not a whole-file rewrite: the head above "
        "is only trustworthy because a run record and the manifest ANCHOR it")
    return 0


def cmd_head(a) -> int:
    h = events.head_of(a.store)
    say(f"seq={h['seq']} events={h['events']} head_sha256={h['head_sha256']} "
        f"truncated={str(h['truncated']).lower()} path={h['path']}")
    return 3 if h["truncated"] else 0


# ── the CLI ──────────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="symbiont.registry",
        description="the diary's human door — the ONE writer for humans (symbiont/events.py holds "
                    "the only append)")
    sub = ap.add_subparsers(dest="cmd")

    def common(p, store=True):
        if store:
            p.add_argument("--store", default=DEFAULT_STORE, help="the store whose diary is written")

    p = sub.add_parser("add-event", help="append one event (the closed table still applies)")
    common(p)
    p.add_argument("--class", dest="event_class", required=True, help=f"one of {sorted(events.CLASSES)}")
    p.add_argument("--source", required=True, help="the DATASET identity: <publisher>:<dataset>")
    p.add_argument("--why", required=True, help="one sentence: what changed, on what evidence")
    p.add_argument("--release", default=None)
    p.add_argument("--actor", default="operator", choices=list(events.ACTORS))
    p.add_argument("--origin", default="live", choices=list(events.ORIGINS))
    p.add_argument("--evidence", action="append", default=[], metavar="k=v")
    p.set_defaults(fn=cmd_add_event)

    for name, enabled, helptext in (("allow", True, "enabled: true + intent_changed"),
                                    ("deny", False, "enabled: false + intent_changed")):
        p = sub.add_parser(name, help=f"the operator's switch: {helptext}")
        common(p)
        p.add_argument("id", help="the source's `id` or its <publisher>:<dataset>")
        p.add_argument("--why", required=True)
        p.add_argument("--sources-config", default=contract.DEFAULT_SOURCES)
        p.add_argument("--release", default=None)
        p.add_argument("--force", action="store_true", help="record the intent even if nothing flips")
        p.set_defaults(fn=lambda a, e=enabled: cmd_switch(a, e))

    p = sub.add_parser("manual-add", help="a source the operator introduced (never evaluated)")
    common(p)
    p.add_argument("--source", required=True, help="<publisher>:<dataset>")
    p.add_argument("--why", required=True)
    p.add_argument("--release", default=None)
    p.add_argument("--sources-config", default="", help="optional: the config it was added to")
    p.add_argument("--evidence", action="append", default=[], metavar="k=v")
    p.set_defaults(fn=cmd_manual_add)

    p = sub.add_parser("override", help="one scoped exception (the only way past not-admitted)")
    common(p)
    p.add_argument("--source", required=True, help="<publisher>:<dataset>")
    p.add_argument("--why", required=True)
    p.add_argument("--scope", required=True, help="one-run | until:YYYY-MM-DD")
    p.add_argument("--release", default=None)
    p.add_argument("--evidence", action="append", default=[], metavar="k=v")
    p.set_defaults(fn=cmd_override)

    p = sub.add_parser("verify", help="verify every line and the chain; REPORT a truncated tail")
    common(p)
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("head", help="the anchor: seq + sha256 of the last line")
    common(p)
    p.set_defaults(fn=cmd_head)

    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        ap.print_help()
        return 2
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
