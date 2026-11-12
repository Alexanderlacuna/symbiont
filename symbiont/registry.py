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
    registry bootstrap --from-seed [--why "…"]
                                        FIRST RUN OF A CLONE (req-P0-A-NEW-USER-CANNOT-MINE-THE-
                                        PERMISS): the export ships INTENT (sources.yml) and no
                                        HISTORY (the store), so it writes one
                                        admitted{origin: backfill} per shipped source from
                                        symbiont/fixtures/source-events.seed.jsonl — naming the run
                                        record each came from, and SAYING how the clone started.
                                        It refuses to touch a store that already has history.
                                        The loop does this itself on a store with no diary and
                                        says NOTE[no-history]; this door is the explicit form.
    registry verify                     read the diary, verify every line and the hash chain,
                                        REPORT a truncated tail (exit 3), print the head
    registry head                       just the anchor: seq + sha256 of the last line
    registry show                       THE INDEX — a PROJECTION rebuilt every time from
                                        sources.yml (intent) + the diary (facts), one row per
                                        dataset, the two governance lines, and the sha256 of
                                        the rendered body in its own header. It READS the two
                                        sources and nothing else, so it is disposable: delete
                                        the artefact and the next build writes the same bytes.
                                        A hand-edited artefact is REFUSED[stale-index], reported
                                        and NOT overwritten.

WHY `allow`/`deny` WRITE sources.yml AND THE DIARY: flipping the switch is a DECISION, and a
decision with no history is a rumour. The switch file stays the operator's one hand-written file
(the intent), and `intent_changed{from,to,why}` records who flipped it, when, and why — so the
switch can always be REBUILT from the diary if it is lost or mangled.

Exit codes: 0 wrote it · 2 REFUSED, the reason named and nothing written (including
REFUSED[stale-index]: the index on disk is not the one this door wrote) · 3 `verify`/`show` found
damage (a finding: the diary is reported, never rewrapped or skipped).
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


def cmd_bootstrap(a) -> int:
    """`registry bootstrap --from-seed` — the EXPLICIT first step of a clone.

    (req-P0-A-NEW-USER-CANNOT-MINE-THE-PERMISS.) The loop does the same thing by itself when it
    finds a store with no diary (NOTE[no-history]); this door is the form an operator runs on
    purpose, before mining, so the admission history exists and can be read first. Both call the
    ONE bootstrap — `events.bootstrap_from_seed` — so there is no second definition of what a
    clone's initial history is.
    """
    if not a.from_seed:
        return _refuse("usage", "bootstrap needs --from-seed: the SHIPPED backfill is the only "
                                "thing this door writes admission history from (an admission with "
                                "no artefact behind it is what `admission admit` is for)")
    sources = _sources(a.sources_config)
    enabled_ids = {events.dataset_of(cfg)[0] for cfg in sources.values() if cfg.get("enabled")}
    if events.read(a.store).no_history:
        # docs/DESIGN.md §12.3: a seed naming a source this sources.yml does not declare means the
        # package and the config disagree about which sources exist — refuse, write NOTHING, and
        # name the disagreement (a half-bootstrap would be a history that hides its own gap).
        try:
            facts = events.read_seed()
        except events.DiaryRefused as e:
            say(str(e))
            return _refuse(e.code, "nothing was appended")
        missing = sorted({f["source_id"] for f in facts} - enabled_ids)
        if missing:
            return _refuse("seed-mismatch",
                           f"the shipped seed names {missing}, which "
                           f"{os.path.abspath(a.sources_config)} does not declare-and-enable — the "
                           f"package and the config disagree about which sources exist, so NOTHING "
                           f"was written. Declare it there, or admit it explicitly "
                           f"(symbiont.admission evaluate/admit, or registry override)")
    try:
        b = events.bootstrap_from_seed(a.store, enabled_ids, why=a.why)
    except events.DiaryRefused as e:
        say(str(e))
        return _refuse(e.code, f"nothing was appended to {events.diary_path(a.store)}")
    say(f"SEED     {b['seed']}  sha256={b['seed_sha256'][:16]}…")
    for s in b["skipped"]:
        say(f"SKIP     {s['source_id'] or '(store)'}: {s['why']}")
    for x in b["admitted"]:
        say(f"APPENDED seq {x['seq']}  admitted  {x['source_id']}  (operator, backfill)  "
            f"from {x['artefact']}")
    if not b["admitted"]:
        say("NOTE     nothing was written — the seed had nothing to add to this store")
        return 0
    h = events.head_of(a.store)
    say(f"BOOTSTRAP {len(b['admitted'])} admitted{{origin: backfill}} event(s) from the shipped "
        f"seed — why: {b['why']}")
    say(f"DIARY    {events.diary_path(a.store)}  head seq={h['seq']} "
        f"sha256={h['head_sha256'][:16]}…")
    say("NOTE     this is HISTORY, not permission: it restores decisions a shipped run record "
        "documents, every line says `origin: backfill`, and a store that already has history is "
        "never touched by it")
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


# ── THE INDEX: A PROJECTION, NOT A SECOND AUTHORITY ─────────────────────────────────────────
# req-P1-THE-INDEX-IS-A-PROJECTION-NOT-A-S. The index is DERIVED, every time, from exactly two
# inputs — sources.yml (the INTENT) and the diary (`events.DIARY_NAME`, the FACTS) — and from
# nothing else. No state/ file, no run record, no previous index: that is what makes it disposable,
# and what a rebuild proves. (The diary's FILE NAME is never spelled here: diary.py owns the one
# literal in the tree, and selftest_diary_writer D10 fails naming any module that re-spells it.)
#
# THE PRECEDENCE IS WRITTEN HERE, ONCE, AND NOWHERE ELSE. There is no other rule below this
# comment — no per-class table, no special case, no second place to be argued with:
#
#   status        <- THE LATEST FACT       the class of the last NON-INTENT event (diary.py's
#                                          Read.state_of, quoted not re-derived): an
#                                          `intent_changed` moves permission, never status.
#   may fetch     <- THE INTENT            `enabled:` in sources.yml. A count_mismatch is a fact
#                                          about the bytes; it never switches a source off.
#   release       <- THE LATEST FACT that names one.
#   last_checked  <- THE LATEST DIGEST     the latest event whose evidence carries a content
#                                          digest (DIGEST_KEYS) — this is what the abandonment
#                                          alarm measures, so 'checked' means 'a digest was
#                                          recorded', not 'somebody said so'.
#   last event    <- THE LATEST EVENT      any class, in FILE ORDER (seq is the only ordering
#                                          authority; ts is evidence of when, never the order).
#
# A reader who wants a different status must put a new FACT in the diary. This file will not grow
# a second opinion.
INDEX_NAME = "source-index.txt"
INDEX_WINDOW_DAYS = 90
RENDER_LINE = "# render sha256="
# the evidence keys that carry a CONTENT DIGEST of what was seen. Named once: `acquired` carries a
# resources map (endpoint -> sha256), the counts and the schema facts carry their own sha256s, and
# an adapter may name its digest plainly. A key whose value is empty is not a digest.
DIGEST_KEYS = ("resources", "sha256", "payload_sha256", "content_sha256", "schema_sha256",
               "to_schema_sha256", "digest")
OVERRIDE_CLASS = "manual_override"


def index_path(store: str) -> str:
    """`<store>/source-index.txt` — the artefact, beside the diary it is derived from."""
    return os.path.join(os.path.abspath(store), INDEX_NAME)


def _day(ts) -> "object":
    """The DATE of an event's ts, or None when it cannot be read (a date is never guessed)."""
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(str(ts)).date()
    except (TypeError, ValueError):
        return None


def _digest_event(ev: dict) -> bool:
    evidence = ev.get("evidence") or {}
    return any(evidence.get(k) for k in DIGEST_KEYS)


def index_rows(sources: dict, r) -> list[dict]:
    """ONE ROW PER DATASET, over the UNION of the two inputs. Nothing else is consulted.

    The union matters: a dataset that has been REMOVED from sources.yml still has facts in the
    diary, and an index that dropped it would agree with the config by forgetting the history. Such
    a row reads `may fetch: no` (no intent in the config grants it any more) while its status still
    comes from the diary — the disagreement stays legible instead of disappearing.
    """
    rows: dict[str, dict] = {}
    for sid, cfg in sources.items():
        try:
            did, how = events.dataset_of(cfg)
        except contract.ConfigError:
            did, how = f"adapter:{sid}", "adapter-fallback"
        rows[did] = dict(dataset_id=did, declared=True, may_fetch=bool(cfg.get("enabled")),
                         identity_source=how)
    for ev in r.events:
        did = str(ev.get("source_id") or "?")
        rows.setdefault(did, dict(
            dataset_id=did, declared=False, may_fetch=False,
            identity_source=str((ev.get("evidence") or {}).get("source_id_source") or "?")))
    for did, row in rows.items():
        row["status"] = r.state_of(did)            # THE LATEST FACT — diary.py's definition
        row["release"] = None
        row["last_event"] = None
        row["last_checked"] = None
        for ev in r.events_of(did):                # file order == seq order == the order
            if ev.get("class") not in events.INTENT_CLASSES and ev.get("release"):
                row["release"] = ev["release"]    # THE LATEST FACT that names a release
            if _digest_event(ev):
                row["last_checked"] = ev           # THE LATEST DIGEST
            row["last_event"] = ev                 # THE LATEST EVENT, any class
    return [rows[k] for k in sorted(rows)]


def governance_lines(rows: list[dict], r, today) -> list[str]:
    """The two lines the governance needs: who was let through, and who has been left alone.

    An exception granted and forgotten is indistinguishable from a policy change, and a source
    switched on and never looked at again is the failure that has no error message — it just quietly
    stops being true. Both are named here, from the diary alone.
    """
    import datetime as dt
    since = today - dt.timedelta(days=INDEX_WINDOW_DAYS)
    granted = []
    for ev in r.events:
        if ev.get("class") != OVERRIDE_CLASS:
            continue
        d = _day(ev.get("ts"))
        if d is None or d < since:
            continue
        scope = str((ev.get("evidence") or {}).get("scope") or "(no scope recorded)")
        granted.append(f"{ev.get('source_id')}@seq{ev.get('seq')} scope={scope} "
                       f"{str(ev.get('ts'))[:10]} by {ev.get('actor')}")
    overrides = (f"OVERRIDES  granted in the last {INDEX_WINDOW_DAYS} days (since {since}): " +
                 (" · ".join(granted) if granted else "none"))
    abandoned = []
    for row in rows:
        if not row["may_fetch"]:
            continue
        seen = row["last_checked"]
        d = _day(seen.get("ts")) if seen else None
        if d is None:
            # NOT 'never fetched' — 'no digest in the diary': the line states what it can see. A
            # store whose facts carry no digest alarms every enabled source, and that IS the
            # finding: nothing in it can be shown to have been checked.
            abandoned.append(f"{row['dataset_id']} (no digest recorded)")
        elif (today - d).days > INDEX_WINDOW_DAYS:
            abandoned.append(f"{row['dataset_id']} (last digest {str(seen.get('ts'))[:10]}, "
                             f"{(today - d).days} days ago)")
    alarm = (f"ABANDONED  enabled but unchecked for {INDEX_WINDOW_DAYS} days: " +
             (" · ".join(abandoned) if abandoned else
              "none — every enabled source carries a digest inside the window"))
    return [overrides, alarm]


def render_index(sources: dict, r, *, now: str, sources_config: str, path: str) -> str:
    """The rendered index: a six-line header (with the sha256 of the body it heads), a blank
    line, then the body — one row per dataset, then the two governance lines.

    DETERMINISTIC BY CONSTRUCTION: no wall clock, no hash of a directory, no previous index. The
    only inputs are the two files' bytes plus `now` (the day the two 90-day windows are measured
    back from), and the header says so.
    """
    import datetime as dt
    today = dt.date.fromisoformat(now)
    rows = index_rows(sources, r)
    cols = ["DATASET_ID", "MAY-FETCH", "STATUS", "RELEASE", "LAST-EVENT"]
    table = [[row["dataset_id"],
              "yes" if row["may_fetch"] else "no",
              row["status"] or "(no fact)",
              row["release"] or "(none)",
              (f"seq {row['last_event'].get('seq')} {row['last_event'].get('ts')}"
               if row["last_event"] else "(no event)")] for row in rows]
    widths = [max([len(cols[i])] + [len(t[i]) for t in table]) for i in range(len(cols))]
    body_lines = ["  ".join(c.ljust(widths[i]) for i, c in enumerate(cols)).rstrip()]
    body_lines += ["  ".join(c.ljust(widths[i]) for i, c in enumerate(t)).rstrip() for t in table]
    body_lines.append("")
    body_lines += governance_lines(rows, r, today)
    body = "\n".join(body_lines) + "\n"

    try:
        with open(sources_config, "rb") as fh:
            cfg_sha = hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        cfg_sha = "unreadable"
    declared = sum(1 for row in rows if row["declared"])
    header = [
        f"# INDEX  {path} — a PROJECTION of sources.yml (intent) + {events.DIARY_NAME} (facts), "
        f"and of nothing else. Delete it: `symbiont.registry show` writes the same bytes again.",
        f"{RENDER_LINE}{hashlib.sha256(body.encode('utf-8')).hexdigest()}  <- sha256 of EVERY "
        f"byte below the blank line (a header cannot carry the hash of itself)",
        f"# built  sources.yml={os.path.abspath(sources_config)} sha256={cfg_sha}  ·  "
        f"{events.DIARY_NAME}={r.path} "
        f"events={len(r.events)} seq={r.seq} head_sha256={r.head}",
        "# rule   status <- THE LATEST FACT (last non-intent event) · may fetch <- THE INTENT "
        "(sources.yml enabled) · release <- the latest fact naming one · last_checked <- THE "
        "LATEST DIGEST · last-event <- the latest event, in seq order. Written once, in "
        "symbiont/registry.py.",
        f"# rows   {len(rows)} dataset(s) = {declared} declared in sources.yml ∪ "
        f"{len(rows) - declared} named only in the diary",
        f"# window {INDEX_WINDOW_DAYS} days measured back from {today} — the render DATE is the "
        f"only input that is neither of the two sources (pin it with --now).",
    ]
    return "\n".join(header) + "\n\n" + body


def split_index(text: str):
    """(header, body) — the body is everything after the FIRST blank line, which is exactly the
    region the header's sha256 covers. None when there is no such line (not one of ours)."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line == "":
            return "\n".join(lines[:i]), "\n".join(lines[i + 1:])
    return None


def index_state(path: str) -> dict:
    """Is the artefact on disk still the bytes this door wrote?

    exists=False          nothing there: a first build, nothing to disagree with.
    stale=False           the body still hashes to the sha256 its own header claims.
    stale=True            HAND-EDITED or FOREIGN: the body does not hash to the header's sha256,
                          or the header carries none at all. The `reason` names which.
    """
    if not os.path.exists(path):
        return dict(exists=False, stale=False, said="", actual="", reason="")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        return dict(exists=True, stale=True, said="", actual="",
                    reason=f"cannot be read as text ({type(e).__name__}: {e})")
    parts = split_index(text)
    if parts is None:
        return dict(exists=True, stale=True, said="", actual="",
                    reason="carries no blank line, so it has no header and no body to hash")
    header, body = parts
    said = ""
    for line in header.split("\n"):
        if line.startswith(RENDER_LINE):
            words = line[len(RENDER_LINE):].split()
            said = words[0] if words else ""
    actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if said == actual:
        return dict(exists=True, stale=False, said=said, actual=actual, reason="")
    reason = (f"its header claims render sha256={said[:16]}… but its body hashes to "
              f"{actual[:16]}…" if said else
              f"its header carries no '{RENDER_LINE.strip()}' line, so it is not an index this "
              f"door wrote")
    return dict(exists=True, stale=True, said=said, actual=actual, reason=reason)


def cmd_show(a) -> int:
    """`registry show` — rebuild the index from the two sources, print it, write the artefact."""
    import datetime as dt
    now = a.now or dt.date.today().isoformat()
    try:
        dt.date.fromisoformat(now)
    except ValueError:
        return _refuse("usage", f"--now {now!r} is not a date (YYYY-MM-DD)")
    sources = _sources(a.sources_config)
    r = events.read(a.store)
    if r.problems:
        # AN INDEX RENDERED FROM DAMAGED HISTORY IS THE SECOND AUTHORITY THIS ROW FORBIDS: the
        # damaged bytes are REPORTED (as `verify` reports them) and nothing is written.
        print(events.report(events.diary_path(a.store), r.problems), file=sys.stderr)
        return 3
    path = os.path.abspath(a.index or index_path(a.store))
    state = index_state(path)
    if state["stale"]:
        return _refuse(
            "stale-index",
            f"{path}: {state['reason']}. The index is a PROJECTION and this file is not one — it "
            f"was edited by hand (or written by something else). It is REPORTED here and NOT "
            f"overwritten: delete it and `registry show` again, which rebuilds every byte from "
            f"{os.path.abspath(a.sources_config)} + {events.diary_path(a.store)}")
    text = render_index(sources, r, now=now, sources_config=a.sources_config, path=path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    back = index_state(path)          # the write is PROVED by a re-read, not by the write
    if back["stale"] or not back["exists"]:
        return _refuse("index", f"{path}: the bytes written did not survive a re-read "
                                f"({back['reason'] or 'absent'}) — nothing here may be trusted")
    sys.stdout.write(text)
    return 0


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

    p = sub.add_parser("bootstrap", help="FIRST RUN OF A CLONE: write the shipped backfill "
                                          "(admitted{origin: backfill}) into a store that has no "
                                          "admission history at all")
    common(p)
    p.add_argument("--from-seed", action="store_true",
                   help=f"use the shipped seed ({events.seed_path()})")
    p.add_argument("--why", default=events.SEED_WHY,
                   help=f"one sentence recorded on every line (default: {events.SEED_WHY!r})")
    p.add_argument("--sources-config", default=contract.DEFAULT_SOURCES,
                   help="the INTENT whose enabled datasets this backfill covers")
    p.set_defaults(fn=cmd_bootstrap)

    p = sub.add_parser("head", help="the anchor: seq + sha256 of the last line")
    common(p)
    p.set_defaults(fn=cmd_head)

    p = sub.add_parser("show", help="THE INDEX — rebuilt from sources.yml + the diary, every "
                                     "time; the artefact is disposable and a hand-edited one is "
                                     "REFUSED[stale-index]")
    common(p)
    p.add_argument("--sources-config", default=contract.DEFAULT_SOURCES,
                   help="the INTENT: the operator's one hand-written file")
    p.add_argument("--index", default="", help="the artefact to write (default: <store>/"
                                               f"{INDEX_NAME})")
    p.add_argument("--now", default="", help="the day the 90-day windows are measured back "
                                              "from (YYYY-MM-DD; default today)")
    p.set_defaults(fn=cmd_show)

    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        ap.print_help()
        return 2
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
