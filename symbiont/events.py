"""THE DIARY, as the STATION uses it — permission, identity, and the one call the paths make.

The FILE layer is `symbiont/diary.py`: the one writer (`diary.append`), the closed transition
table, the truncation detector and the hash chain. This module adds the two things the station
knows that the file does not:

    PERMISSION   `permission()` — may this dataset be acquired? The diary answers, not sources.yml.
                 `enabled: true` says MAY WE FETCH; an `admitted` (or a scoped `manual_override`)
                 says SOMEBODY DECIDED WE MAY HAVE IT. Design §1: permission is a decision.
    IDENTITY     `dataset_of()` — the diary's key is the DATASET (`<publisher>:<dataset>`), derived
                 from the config by `contract.identity`, never invented; when a config declares no
                 publisher+dataset the fallback key (`adapter:<name>`) is RECORDED in the event's
                 evidence, so a weaker key announces itself.

NOTHING HERE WRITES THE FILE. `append()`/`emit()`/`seed_admitted()` all end in `diary.append()`,
and this module does not spell the file's name — `symbiont/selftest_diary_writer.py` fails, naming
the offender, if any module but `diary.py` names it or opens it for writing.

WHY THE SPLIT EXISTS (req-P1-THE-DIARY-WRITER-atomic-append-an): a station-facing helper that can
also append is a second writer with a friendly name. One writer, one name, one atomic write.
"""
from __future__ import annotations

# re-exported so every existing caller keeps its import (`from . import events`) and there is
# exactly ONE definition of each: the file layer is diary.py and nowhere else.
from .diary import (ACTORS, CLASSES, DIARY_NAME, EVENT_FIELDS, FIELDS, GENESIS, INTENT_CLASSES,
                    ORIGINS, PERMITTING, REVOKING, SEED_NAME, SEED_WHY, TRANSITIONS, DiaryRefused,
                    DiaryUnreadable, IllegalTransition, Read, SeedRefused, TruncatedDiary, append,
                    append_event, bootstrap_from_seed, diary_path, head_matches, head_of, line_bytes,
                    read, read_seed, report, seed_path, sha, transitions_from, verify)
from . import contract

__all__ = [
    "ACTORS", "CLASSES", "DIARY_NAME", "EVENT_FIELDS", "FIELDS", "GENESIS", "INTENT_CLASSES",
    "ORIGINS", "PERMITTING", "REVOKING", "SEED_NAME", "SEED_WHY", "TRANSITIONS", "DiaryRefused",
    "DiaryUnreadable", "IllegalTransition", "Read", "SeedRefused", "TruncatedDiary", "append",
    "append_event", "bootstrap_from_seed", "diary_path", "head_matches", "head_of", "line_bytes",
    "read", "read_seed", "report", "seed_path", "sha", "transitions_from", "verify",
    "permission", "dataset_of", "emit", "seed_admitted",
]


# ── permission (the door reads THIS, not sources.yml alone) ─────────────────────────────────
def permission(r: Read, source_id: str, today: str | None = None) -> tuple[bool, str, dict]:
    """May this dataset be acquired? -> (ok, why-it-is-not, the permitting event).

    PERMISSION IS A DECISION, ENABLED IS A PERMISSION (DESIGN §1): `enabled: true` in sources.yml
    says MAY WE FETCH; this says SOMEBODY ADMITTED IT (or authorised one scoped exception). The
    latest permitting or revoking event wins; a later count_mismatch (or schema_changed) changes
    the STATUS and never revokes the admission.
    """
    import datetime as _dt
    today = today or _dt.date.today().isoformat()
    latest: dict | None = None
    for ev in r.events_of(source_id):
        if ev.get("class") in PERMITTING or ev.get("class") in REVOKING:
            latest = ev                    # the LATEST permission fact wins, in file order
    if latest is None:
        return False, "the diary holds no 'admitted' event for this dataset", {}
    if latest.get("class") in REVOKING:
        return False, (f"its latest permission fact is '{latest.get('class')}' at seq "
                       f"{latest.get('seq')} ({str(latest.get('why', ''))[:120]})"), {}
    latest_permit = latest
    if latest_permit.get("class") == "manual_override":
        scope = str((latest_permit.get("evidence") or {}).get("scope") or "")
        if not scope:
            return False, (f"the manual_override at seq {latest_permit.get('seq')} records no scope "
                           f"(one-run or until:<YYYY-MM-DD>), so it authorises nothing"), {}
        if scope.startswith("until:"):
            end = scope.split(":", 1)[1]
            if end < today:
                return False, (f"the manual_override at seq {latest_permit.get('seq')} expired on "
                               f"{end} (today {today})"), {}
    return True, "", latest_permit


def dataset_of(cfg: dict) -> tuple[str, str]:
    """The diary's key for a sources.yml entry, and HOW it was derived.

    The dataset identity is `contract.dataset_id` (declared publisher+dataset) — never invented.
    A config that declares neither (a trial adapter, the sandbox's throwaway entry) is keyed by
    `adapter:<name>` and the fallback is RECORDED in the event's evidence, so a diary keyed on a
    weaker identity says so instead of pretending.
    """
    ident = contract.identity(cfg)
    if ident.get("dataset_id"):
        return ident["dataset_id"], "declared:publisher+dataset"
    return f"adapter:{(cfg or {}).get('adapter') or (cfg or {}).get('id') or '?'}", "adapter-fallback"


def emit(store: str, cfg: dict, event_class: str, *, why: str, actor: str = "machine",
         release: str | None = None, evidence: dict | None = None,
         origin: str = "live") -> dict:
    """Emit for a sources.yml ENTRY: derives the dataset identity and records how it was derived."""
    source_id, how = dataset_of(cfg)
    ev = dict(evidence or {})
    ev.setdefault("source_id_source", how)
    return append(store, event_class, source_id=source_id, why=why, actor=actor, release=release,
                  evidence=ev, origin=origin)


def seed_admitted(store: str, cfg: dict, *, why: str, evidence: dict | None = None,
                  release: str | None = None) -> dict | None:
    """Backfill one `admitted` line for a store that predates the diary (used by the station's
    fixtures and by `registry add-event --origin backfill`). Idempotent: an already-admitted
    dataset is left alone and None is returned."""
    source_id, how = dataset_of(cfg)
    r = read(store)
    ok, _why, _ev = permission(r, source_id)
    if ok:
        return None
    ev = dict(evidence or {})
    ev.setdefault("source_id_source", how)
    return append(store, "admitted", source_id=source_id, why=why, actor="operator",
                  release=release, evidence=ev, origin="backfill")
