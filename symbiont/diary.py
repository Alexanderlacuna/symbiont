"""THE DIARY — `<store>/source-events.jsonl`: THE ONE WRITER and the CLOSED TRANSITION TABLE.

WHY THIS FILE EXISTS (req-P1-THE-DIARY-WRITER-atomic-append-an, the writer half of the split row).
The lifecycle of a source is a FILE: append-only, one JSON line per transition, in the order the
transitions happened. This module is the only place that file is ever opened for writing, and it
is the only place its name is spelled. Everything else in the station (`events.py`, the loop, the
cycle, the admit command, the switch, the manual door) reaches the file through `append()` here.

THE LINE (fixed schema, one JSON object per line — FIELDS below is that schema, enforced):

    seq                1-based LINE NUMBER — the ONLY ordering authority. `ts` is evidence of when,
                       `seq` is what decides which came first; sorting by ts is forbidden (two
                       events in the same second are legal and indistinguishable by timestamp).
    ts                 ISO-8601 UTC, when the transition was recorded
    source_id          the DATASET identity (`<publisher>:<dataset>`), stable across releases
    release            the release the event is about (null when it has no release)
    class              one of CLASSES — the whole lifecycle, closed
    actor              machine | operator  (who caused it; the writer is always code)
    why                one sentence, in words, of what changed and on what evidence
    evidence           {...} whatever proves it: run_id, sha256s, counts, card path, refusal code
    origin             live | backfill   (backfilled lines say so; a reconstructed line is never
                       dressed up as one that was written at the time)
    prev_event_sha256  sha256 of the PREVIOUS line's bytes (GENESIS for the first line)

ATOMIC APPEND. One line is written with ONE `os.write()` on an O_APPEND handle, then fsync'd, so a
partial line cannot be produced by a crash mid-append (the alternative — seek, write, truncate — is
what leaves half a line behind). The read-modify-append is serialised by an flock() on
`<path>.lock`, so two writers cannot both claim seq n. A short write is NOT retried into the same
line: it is named, because a second write onto a partial line is how a corrupt history becomes a
mysterious one.

A TRUNCATED TAIL IS DETECTED, REPORTED, NEVER SKIPPED. `read()` walks the file line by line and
collects `problems`: a final line with no terminating newline, a line that is not JSON, a `seq`
that disagrees with its line number, a `prev_event_sha256` that disagrees with the hash of the line
before it. The events BEFORE the damage are returned as the history; the damaged bytes are REPORTED
with their line number and left on disk untouched; and `append()` REFUSES[truncated-diary] rather
than write a new line onto the damage (a new line written after a partial one merges into it and
the corruption becomes unattributable).

THE CLOSED TRANSITION TABLE, ENFORCED AT WRITE TIME. TRANSITIONS is data: state -> the classes that
may legally follow it. `append()` REFUSES[illegal-transition] an event whose class may not follow
the source's current state, naming the to-class, the from-class and the source, and appends
nothing — the caller's state change then does not happen. A log that can contain impossible
sequences cannot be replayed deterministically without heuristics, and heuristics are how "two
truths" come back.

THE ONE-WRITER RULE, MECHANICAL. `symbiont/selftest_diary_writer.py` walks the whole tree looking
for any other place that names this file or opens it for writing, and fails NAMING the offender. A
rule kept by good intentions is kept until the first busy night.

HONEST LIMIT — WHAT THE HASH CHAIN DOES NOT DO. The chain makes an ALTERATION of history visible:
change a byte on line 3 and every later `prev_event_sha256` breaks, and `verify()` names the line.
It does NOT stop someone who rewrites the WHOLE diary and recomputes every hash — a private,
self-consistent forgery is undetectable from inside the file. That is why the head hash is
ANCHORED where it costs something to forge: every run record carries the diary's head (seq +
sha256) at the moment the run finished, and the handover manifest repeats it and re-checks it
against the diary on disk.
"""
from __future__ import annotations

import datetime as dt
import errno
import fcntl
import hashlib
import json
import os

from . import contract

# the ONE literal in the tree: this module. selftest_diary_writer.py asserts exactly that.
DIARY_NAME = "source-events.jsonl"

# THE LINE'S SCHEMA — the nine fields a caller supplies, and the ten that land on disk.
EVENT_FIELDS = ("source_id", "release", "class", "actor", "why", "evidence", "origin")
FIELDS = ("seq", "ts") + EVENT_FIELDS + ("prev_event_sha256",)

# ── the whole lifecycle, closed ───────────────────────────────────────────────────────────────
# The row named seventeen of these; the six marked (emitted by a path already in the tree) are
# here because the CYCLE and the ADMIT path already write them — a class the tree emits but the
# table rejects would make the diary refuse the station's own transitions.
CLASSES = frozenset({
    # the row's seventeen
    "discovered", "evaluated", "sandboxed", "admitted", "intent_changed", "acquired",
    "release_moved", "count_ok", "count_mismatch", "schema_changed", "stale", "unreadable",
    "vanished", "retired", "manual_add", "manual_override", "override_expired",
    # emitted by paths in this tree (cycle discover · evaluation · quarantine)
    "refused", "reconsidered", "carded", "first_harvest", "quarantined", "unquarantined",
})

# A permission change is a FACT ABOUT PERMISSION, not a lifecycle state: it never moves the
# source's state, so it may follow any state (and the state machine reads the last NON-intent
# class). Without this, recording "the operator flipped the switch" between two acquisitions
# would make the next acquisition's predecessor a class the table does not carry.
INTENT_CLASSES = frozenset({"intent_changed"})

ACTORS = ("machine", "operator")
ORIGINS = ("live", "backfill")

GENESIS = "0" * 64

# Statuses a source carries AFTER it has been acquired at least once: the classes a dataset can
# be re-observed in. Named once, because four rows of the table spell the same list.
_OBSERVED = frozenset({"acquired", "count_ok", "count_mismatch", "release_moved", "schema_changed",
                       "stale", "unreadable", "vanished", "quarantined", "retired"})

# state (the last non-intent class) -> the classes that may legally follow it.
# `None` is "no history for this dataset yet".
TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"discovered", "evaluated", "refused", "admitted", "manual_add",
                     "manual_override", "retired", "sandboxed", "carded", "quarantined"}),
    "discovered": frozenset({"evaluated", "refused", "carded", "sandboxed", "retired",
                             "manual_override", "quarantined", "vanished"}),
    "evaluated": frozenset({"admitted", "refused", "carded", "sandboxed", "retired",
                            "manual_override"}),
    "sandboxed": frozenset({"carded", "admitted", "refused", "retired", "manual_override"}),
    "carded": frozenset({"admitted", "refused", "retired", "manual_override"}),
    # THE NAMED RULE: a refused source does not become admitted; it returns through reconsidered.
    "refused": frozenset({"reconsidered", "retired", "manual_override"}),
    "reconsidered": frozenset({"evaluated", "refused", "admitted", "retired"}),
    # admitted may be followed by a first acquisition (or a count that arrives without one, when a
    # run re-uses a release already on disk) and by every observation of the dataset.
    "admitted": frozenset({"acquired", "first_harvest", "count_ok", "count_mismatch",
                           "release_moved", "schema_changed", "stale", "unreadable", "vanished",
                           "quarantined", "retired", "reconsidered", "manual_override",
                           "override_expired"}),
    "manual_add": frozenset({"evaluated", "refused", "carded", "sandboxed", "manual_override",
                             "retired"}),
    "manual_override": frozenset({"acquired", "count_ok", "count_mismatch", "evaluated",
                                  "refused", "override_expired", "retired", "manual_override"}),
    "override_expired": frozenset({"evaluated", "refused", "retired", "manual_override",
                                   "acquired", "count_ok", "count_mismatch"}),
    "first_harvest": _OBSERVED | {"override_expired"},
    "acquired": _OBSERVED | {"override_expired"},
    "count_ok": _OBSERVED | {"override_expired"},
    "count_mismatch": frozenset({"count_ok", "count_mismatch", "acquired", "schema_changed",
                                 "quarantined", "unquarantined", "unreadable", "vanished",
                                 "retired"}),
    # a schema change is a FACT ABOUT THE BYTES, not a permission: the source stays admitted and
    # its adapter owes an evaluation, so the state may be observed again or retired, and the run
    # may record the same change twice (a re-run sees the same new columns).
    "schema_changed": _OBSERVED | {"override_expired"},
    "release_moved": frozenset({"acquired", "count_ok", "count_mismatch", "schema_changed",
                                "stale", "vanished", "retired"}),
    "stale": frozenset({"stale", "acquired", "count_ok", "count_mismatch", "release_moved",
                        "schema_changed", "vanished", "retired"}),
    "unreadable": frozenset({"unreadable", "acquired", "count_ok", "count_mismatch",
                             "schema_changed", "quarantined", "vanished", "retired"}),
    "quarantined": frozenset({"unquarantined", "retired", "admitted"}),
    "unquarantined": frozenset({"admitted", "evaluated", "refused", "retired"}),
    "vanished": frozenset({"reconsidered", "count_ok", "retired"}),
    "retired": frozenset({"reconsidered", "retired"}),
}

# the classes that GRANT permission to acquire, and the ones that take it back. Status is not
# permission: a `count_mismatch` or a `schema_changed` after an admission leaves the source
# admitted and its status changed, which is exactly the disagreement the diary exists to make
# legible.
PERMITTING = frozenset({"admitted", "manual_override"})
REVOKING = frozenset({"refused", "retired", "override_expired"})


# ── refusals (named, as everywhere else in the station) ──────────────────────────────────────
class DiaryRefused(contract.Refusal):
    """The diary refuses to write, or to be read past damage. `code` names the reason."""
    code = "diary"


class IllegalTransition(DiaryRefused):
    """The event would make replay impossible: the class may not follow the previous state."""
    code = "illegal-transition"

    def __init__(self, event_class: str, previous_class: str | None, source_id: str,
                 allowed: frozenset[str]):
        self.event_class = event_class
        self.previous_class = previous_class
        self.source_id = source_id
        self.allowed = allowed
        prev = previous_class or "(no prior event)"
        super().__init__(
            f"'{event_class}' may not follow '{prev}' for {source_id} — the diary's closed "
            f"transition table allows only {sorted(allowed or ())}; a refused source returns "
            f"through 'reconsidered', and nothing was appended")


class TruncatedDiary(DiaryRefused):
    """The file's tail (or a line inside it) is damaged. REPORTED, never skipped."""
    code = "truncated-diary"

    def __init__(self, path: str, problems: list[dict]):
        self.path = path
        self.problems = problems
        self.report = report(path, problems)
        super().__init__(self.report)


class DiaryUnreadable(DiaryRefused):
    code = "diary-unreadable"


# ── the file ─────────────────────────────────────────────────────────────────────────────────
def diary_path(store: str) -> str:
    """`<store>/source-events.jsonl` — the store is the unit of history, so it is beside it."""
    return os.path.join(os.path.abspath(store), DIARY_NAME)


def line_bytes(ev: dict) -> bytes:
    """The canonical bytes of one event (no trailing newline): sorted keys, no spaces."""
    return json.dumps(ev, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def report(path: str, problems: list[dict]) -> str:
    """The one-line-per-problem report an operator reads. Names the line and what is wrong."""
    out = [f"TRUNCATED {os.path.basename(path)}: {len(problems)} damaged point(s) — history is "
           f"read ONLY up to the last complete line, and nothing will be appended onto it:"]
    for p in problems:
        out.append(f"  {p['kind']}: {p['detail']}")
    out.append("  the partial/damaged bytes are REPORTED here and left on disk untouched — a "
               "truncated line is never silently skipped, and never doubled over")
    return "\n".join(out)


class Read:
    """One read of the diary: the complete events, the head, and every problem found."""

    def __init__(self, path: str):
        self.path = path
        self.events: list[dict] = []
        self.lines: list[bytes] = []          # the exact bytes of every complete line
        self.seq = 0
        self.head = GENESIS
        self.exists = False
        self.bytes = 0
        self.problems: list[dict] = []

    @property
    def truncated(self) -> bool:
        return bool(self.problems)

    def report(self) -> str:
        return report(self.path, self.problems)

    def state_of(self, source_id: str) -> str | None:
        """The last NON-intent class of one dataset — the state the transition table reads."""
        state = None
        for ev in self.events:
            if ev.get("source_id") == source_id and ev.get("class") not in INTENT_CLASSES:
                state = ev.get("class")
        return state

    def events_of(self, source_id: str) -> list[dict]:
        return [ev for ev in self.events if ev.get("source_id") == source_id]


def read(store: str) -> Read:
    """Read the diary in FILE ORDER, verifying every line. Damage becomes `problems`, not an
    exception: the caller decides whether to report (a status command) or refuse (a write)."""
    path = diary_path(store)
    r = Read(path)
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
    except FileNotFoundError:
        return r
    except OSError as e:
        raise DiaryUnreadable(f"{path} cannot be read ({e}) — history cannot be stated") from e
    r.exists, r.bytes = True, len(blob)
    if not blob:
        return r
    chunks = blob.split(b"\n")
    tail = chunks.pop()                      # b"" when the file ends with a newline
    if tail:
        r.problems.append(dict(kind="truncated",
                               detail=f"line {len(chunks) + 1} has no terminating newline "
                                      f"({len(tail)} byte(s): {tail[:60]!r}) — the last write "
                                      f"did not complete"))
    prev_sha = GENESIS
    for i, raw in enumerate(chunks, start=1):
        try:
            ev = json.loads(raw.decode("utf-8"))
        except Exception as e:
            r.problems.append(dict(kind="unparseable",
                                   detail=f"line {i} is not JSON ({type(e).__name__}: {e}) — "
                                          f"seq {i - 1} is the last usable fact"))
            break
        if not isinstance(ev, dict) or "class" not in ev or "seq" not in ev:
            r.problems.append(dict(kind="unparseable",
                                   detail=f"line {i} is not an event (no seq/class): {raw[:60]!r}"))
            break
        if ev.get("seq") != i:
            r.problems.append(dict(kind="seq",
                                   detail=f"line {i} carries seq {ev.get('seq')!r} — seq is the "
                                          f"line order and the only ordering authority, so a "
                                          f"diary whose seq disagrees with its lines is damaged"))
            break
        if ev.get("prev_event_sha256") != prev_sha:
            r.problems.append(dict(kind="chain",
                                   detail=f"line {i} (seq {i}, class {ev.get('class')!r}) names "
                                          f"prev_event_sha256={str(ev.get('prev_event_sha256'))[:12]}… "
                                          f"but line {i - 1} hashes to {prev_sha[:12]}… — the chain "
                                          f"is broken: an earlier line was altered or removed"))
            break
        prev_sha = sha(raw)
        r.events.append(ev)
        r.lines.append(raw)
        r.seq = i
        r.head = prev_sha
    return r


def verify(store: str) -> tuple[Read, list[dict]]:
    """The full check: the read's problems. (Whether history was truncated AT THE END — a valid
    chain that simply stops earlier — cannot be seen from the file alone: that is what the anchor
    written by a run record and repeated by the manifest is for, `head_matches()`.)"""
    r = read(store)
    return r, list(r.problems)


# ── the append (THE ONE WRITER) ──────────────────────────────────────────────────────────────
def append(store: str, event_class: str, *, source_id: str, why: str, actor: str = "machine",
           release: str | None = None, evidence: dict | None = None, origin: str = "live",
           clock=_utc) -> dict:
    """Append ONE event. THE ONLY function in the tree that writes the diary.

    Refuses (nothing appended, the caller's state change does not happen) when:
      · the class is not in CLASSES                      REFUSED[diary]
      · the class may not follow the source's state      REFUSED[illegal-transition] (both named)
      · the last line is truncated / the chain is broken REFUSED[truncated-diary] (reported)
      · actor is not machine|operator, origin not live|backfill     REFUSED[diary]
    """
    if event_class not in CLASSES:
        raise DiaryRefused(f"unknown event class {event_class!r} — the lifecycle is closed: "
                           f"{sorted(CLASSES)}")
    if actor not in ACTORS:
        raise DiaryRefused(f"actor must be one of {list(ACTORS)}, got {actor!r}")
    if origin not in ORIGINS:
        raise DiaryRefused(f"origin must be one of {list(ORIGINS)}, got {origin!r}")
    if not str(source_id or "").strip():
        raise DiaryRefused("an event needs a source_id (the dataset identity, "
                           "<publisher>:<dataset>) — an event about nothing is not a fact")
    path = diary_path(store)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = path + ".lock"
    fd_lock = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd_lock, fcntl.LOCK_EX)          # serialise read-modify-append
        r = read(store)
        if r.problems:
            raise TruncatedDiary(path, r.problems)
        state = r.state_of(source_id)
        if event_class not in INTENT_CLASSES:
            allowed = TRANSITIONS.get(state, frozenset())
            if event_class not in allowed:
                raise IllegalTransition(event_class, state, source_id, allowed)
        ev = dict(seq=r.seq + 1, ts=clock(), source_id=source_id, release=release,
                  **{"class": event_class}, actor=actor, why=why, evidence=evidence or {},
                  origin=origin, prev_event_sha256=r.head)
        if set(ev) != set(FIELDS):                   # the schema is checked, not hoped for
            raise DiaryRefused(f"the event's fields are {sorted(ev)} but the diary's schema is "
                               f"{sorted(FIELDS)} — a line with other fields is not readable "
                               f"history")
        payload = line_bytes(ev) + b"\n"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            n = os.write(fd, payload)                # ONE write: no partial line from a crash
            if n != len(payload):
                raise DiaryUnreadable(
                    f"{path}: wrote {n} of {len(payload)} bytes in one append — the store is out "
                    f"of space or the filesystem is failing; the line is short and `registry "
                    f"verify` will REPORT it as truncated, never skip it")
            os.fsync(fd)
        finally:
            os.close(fd)
        return ev
    finally:
        try:
            fcntl.flock(fd_lock, fcntl.LOCK_UN)
        except OSError as e:                          # never mask the real refusal
            if e.errno not in (errno.EBADF, errno.EINVAL):
                raise
        os.close(fd_lock)


def append_event(event: dict, *, store: str | None = None, clock=_utc) -> dict:
    """`append(event)` — the row's shape: ONE event DICT in, the ten-field line on disk out.

    The dict carries the seven fields the caller knows (`EVENT_FIELDS`) and may carry seq, ts and
    prev_event_sha256 to be RECORDED as stated (a backfill does). Whatever it does not carry, the
    writer assigns: seq and prev_event_sha256 from the file under the lock (the caller cannot know
    them, which is why they are the writer's to assign), ts from the clock. Unknown or missing
    fields are REFUSED[diary] — the schema is one schema.
    """
    fields = dict(event)
    target = store or fields.pop("store", None)
    if not target:
        raise DiaryRefused("append(event) needs a store: pass store=, or a 'store' key in the "
                           "event — history lives beside a store, so an event without one has "
                           "nowhere to be true")
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise DiaryRefused(f"unknown field(s) {sorted(unknown)} — the diary's schema is "
                           f"{list(FIELDS)}")
    missing = set(EVENT_FIELDS) - set(fields)
    if missing:
        raise DiaryRefused(f"missing field(s) {sorted(missing)} — every event says "
                           f"{list(EVENT_FIELDS)}")
    recorded = {k: fields.pop(k) for k in ("seq", "ts", "prev_event_sha256") if k in fields}
    if recorded:
        # A backfill records history that was written elsewhere: the recorded seq must be the
        # NEXT line, and the recorded prev must be the head this file really has.
        r = read(target)
        if "seq" in recorded and recorded["seq"] != r.seq + 1:
            raise DiaryRefused(f"the event says seq {recorded['seq']} but line {r.seq + 1} is the "
                               f"next line — seq is the line order, not a claim")
        if "prev_event_sha256" in recorded and recorded["prev_event_sha256"] != r.head:
            raise DiaryRefused(f"the event names prev_event_sha256 {str(recorded['prev_event_sha256'])[:12]}… "
                               f"but the diary's head is {r.head[:12]}… — a recorded link that "
                               f"does not match the file is not a link")
    return append(target, fields.pop("class"), source_id=fields.pop("source_id"),
                  why=fields.pop("why"), actor=fields.pop("actor", "machine"),
                  release=fields.pop("release", None), evidence=fields.pop("evidence", None),
                  origin=fields.pop("origin", "live"), clock=clock)


def head_of(store: str) -> dict:
    """The anchor: {path, seq, events, head_sha256, truncated}. Printed in the run record and
    repeated (and re-checked) in the manifest."""
    r = read(store)
    return dict(path=diary_path(store), seq=r.seq, events=len(r.events), head_sha256=r.head,
                truncated=r.truncated, problems=[p["detail"] for p in r.problems])


def head_matches(store: str, anchor: dict) -> tuple[bool, str]:
    """Does the diary on disk still answer to the head a run record / manifest wrote down?"""
    r = read(store)
    want_seq = int(anchor.get("seq") or 0)
    want_sha = str(anchor.get("head_sha256") or GENESIS)
    if want_seq == 0:
        return (not r.events), f"anchor seq 0 but the diary holds {len(r.events)} event(s)"
    if r.seq < want_seq:
        return False, (f"the diary holds {r.seq} line(s) but the anchor names seq {want_seq} — "
                       f"lines were REMOVED from the end of the history")
    got = sha(r.lines[want_seq - 1])
    if got != want_sha:
        return False, (f"seq {want_seq} hashes to {got[:16]}… but the anchor (written when it was "
                       f"the head) says {want_sha[:16]}… — the diary was ALTERED after the run")
    return True, f"seq {want_seq} still hashes to {got[:16]}… as the anchor wrote it"


def transitions_from(state: str | None) -> frozenset[str]:
    """The table is DATA: this is the one accessor, so a test can walk it without guessing."""
    return TRANSITIONS.get(state, frozenset())
