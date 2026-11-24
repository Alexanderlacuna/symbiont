"""THE EVALUATOR — the six-point source spec, in code, refusing by name
(req-P1-THE-EVALUATOR-six-checks-in-code).

WHY THIS FILE EXISTS. In the AMR discovery test (2026-11-11) the six checks were applied by a
HUMAN, in a report, by hand. The cycle's own evaluation could read a licence only from a code
host's API (`license.spdx_id`), so it refused THREE REAL DATA SOURCES as REFUSED[licence-unreadable]
— WHO GLASS, Africa CDC's MAAP country reports, an HF repackaging of GLASS — while the hand
judgement passed them. A spec applied by hand is not a spec; it is an opinion that cannot be
re-run. This module is the spec as code.

THE SIX CHECKS, each with its refusal — data, in one place (SIX_CHECKS below), so the report, the
run record and this docstring cannot drift:

    1 ROWS      are there rows at all?                      REFUSED[no-rows]
    2 COUNT     a count endpoint, or a file that counts itself? REFUSED[no-count-endpoint]
    3 RELEASE   a release id (not a moving alias)?           REFUSED[no-release]
    4 LICENCE   a readable licence — from an API, a page, or a release tag — RECORDED WITH ITS
                EVIDENCE (how · where · the publisher's own words · the date)  REFUSED[licence-unreadable]
    5 SCHEMA    a documented schema (columns, or an OBO/JSON schema)?  REFUSED[no-schema]
    6 STABLE    a stable URL with an archive or an immutable asset?    REFUSED[unstable-url]

PLUS THE DERIVED FLAG. A repackaged copy must NAME ITS AUTHORITY —
`derived: {authority: who-glass-amr, via: hf-electricsheepafrica}`, written as one line
`derived:who-glass-amr:via=hf-electricsheepafrica` — or it is REFUSED[derived-without-authority].
A copy of a copy that does not say what it is a copy OF is not a source; it is a rumour about one.

THE BOUNDARY, STATED HERE SO NOBODY HAS TO GUESS IT:

  · A PASS PRODUCES `PROPOSED`. Never `admitted`. This module has no verdict other than
    PROPOSED or REFUSED, `verdict["admitted"]` is the constant False, and the string "admitted"
    is not a value this file can return from `evaluate()`.
  · A MACHINE MAY JUDGE TRACTABILITY: rows · count · release · licence · schema · stability —
    the six questions a machine can answer from what a publisher declares and from bytes it can
    hash. Nothing else.
  · A MACHINE MAY NOT JUDGE SCIENCE. Is the dataset good? Is the study sound? Is the question
    worth asking? Not here, not ever here: a candidate's science/merit/quality claims are
    RECORDED in `not_judged` and pass through untouched.
  · A MACHINE MAY NOT GRANT PERMISSION. `ADMIT_BY_POLICY` is the constant False — it is not read
    from a config, a policy file or a candidate, so no configuration can make this module admit.
    `admit()` exists ONLY to refuse, by name (REFUSED[evaluator-may-not-admit]), a caller that
    asks this module to do the one thing it must not do.
  · IT DOES NOT WRITE. No diary event, no store, no file: this module imports nothing that can
    write (it imports only `re` and `hashlib`), opens nothing, and the station's own test scans
    its source to prove it (selftest_source_spec.py, S9 — including a PLANTED writer, so the
    scanner is shown to be able to fail).

IT DOES NOT FETCH EITHER (out of scope for this row, with the sandbox trial and admission
policy). Every field it reads is a DECLARATION or a RECORDED READING: an evidence record says how
the licence was read (`api` · `page` · `tag`), WHERE, the publisher's own words, and the date.
That is the difference between reading a licence and guessing one: a bare `licence: "CC-BY-4.0"`
with no address and no words is refused as licence-unreadable, because a name nobody can re-read
is not evidence.

USAGE
    from symbiont import source_spec
    v = source_spec.evaluate(candidate)          # pure: dict in, dict out, no I/O
    print("\\n".join(source_spec.lines(v)))       # the report lines, refusals first
    v["verdict"]                                 # "PROPOSED" | "REFUSED" — never "admitted"
"""
from __future__ import annotations

import hashlib
import re

# ── THE SPEC AS DATA — one definition site ───────────────────────────────────────────────────
# (field, refusal class, the question) in the row's own order. A check added here is a check the
# evaluator runs; a class spelled here is the class the refusal prints.
SIX_CHECKS = (
    ("rows", "no-rows", "are there rows at all?"),
    ("count", "no-count-endpoint", "a count endpoint, or a file that counts itself?"),
    ("release", "no-release", "a release id — not a moving alias?"),
    ("licence", "licence-unreadable",
     "a licence read from an api, a page or a tag, recorded with where and the words?"),
    ("schema", "no-schema", "a documented schema — columns, or an OBO/JSON schema?"),
    ("stable", "unstable-url", "a stable URL with an archive or an immutable asset?"),
)
DERIVED_REFUSAL = "derived-without-authority"
ADMIT_REFUSAL = "evaluator-may-not-admit"

PROPOSED, REFUSED = "PROPOSED", "REFUSED"

# THE CONSTANTS THAT MAKE THE BOUNDARY CODE, NOT PROSE.
MACHINE_MAY_JUDGE = tuple(f for f, _c, _q in SIX_CHECKS)          # tractability, nothing else
MACHINE_MAY_NOT_JUDGE = ("science", "permission")
ADMIT_BY_POLICY = False        # never read from a config: the evaluator may not grant permission
ADMITTED = False               # the only value `verdict["admitted"]` can ever have

# A release id identifies BYTES. These identify "whatever is there today", so they are not ids.
MOVING_ALIASES = frozenset({"latest", "head", "main", "master", "current", "stable", "nightly",
                            "tip", "trunk", "dev", "rolling", "n/a", "na", "none", "null", "-"})
# How a COUNT can be established by a machine. A number PRINTED IN A DOCUMENT is neither: it is a
# claim, and the same doctrine the station already applies to a dump's count ("a count published
# with no way to reproduce it is a claim, not a measurement") refuses it here.
COUNT_KINDS = ("endpoint", "file")
# How a licence may be READ (the row's three ways) — and no fourth way, so "we asked the model" or
# "the README said MIT" is not on the list.
LICENCE_HOWS = ("api", "page", "tag")
SCHEMA_KINDS = ("columns", "obo", "json", "csvw", "frictionless")
STABILITY_KINDS = ("archive", "immutable-asset", "dated-release", "doi")
# Path segments that name a MOVING branch rather than a fixed asset. A URL that points at one of
# these is not stable unless the candidate also names the pin (a revision, a DOI, a snapshot id).
MOVING_SEGMENTS = ("main", "master", "head", "latest", "trunk", "dev")
NOT_A_LICENCE = frozenset({"", "none", "null", "noassertion", "unknown", "unclear", "tbd", "?",
                           "n/a", "na"})
SCIENCE_KEYS = ("science", "merit", "quality", "significance", "novelty", "importance",
                "scientific_merit", "validity")
ADMIT_KEYS = ("admitted", "admit", "admit_by_policy", "verdict", "state", "status")
DERIVED_KEYS = ("derived", "derived_from", "repackaged_from", "authority", "via")


class Refused(Exception):
    """A refusal with a NAME. `code` is the class the line prints; `detail` says what was missing."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"REFUSED[{code}] {detail}")
        self.code = code
        self.detail = detail

    def __str__(self) -> str:            # the greppable line every caller prints
        return f"REFUSED[{self.code}] {self.detail}"


# ── small honest helpers ────────────────────────────────────────────────────────────────────
def _text(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _dig(v) -> dict:
    return v if isinstance(v, dict) else {}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ref(code: str, sid: str, detail: str) -> dict:
    return dict(code=code, line=f"REFUSED[{code}] {sid}: {detail}", detail=detail)


def _ok(evidence, note: str = "") -> tuple:
    return True, evidence, note


def _no(problem: str, evidence=None) -> tuple:
    return False, evidence or {}, problem


# ── 1 · ROWS ────────────────────────────────────────────────────────────────────────────────
def _check_rows(c: dict) -> tuple:
    """ROWS PRESENT? A repository is a PROGRAM, not rows — and a claim of rows needs words and an
    address, or the machine is repeating an assertion nobody can check."""
    shape = _text(c.get("shape") or c.get("kind") or "").lower()
    if shape in ("repository", "repo", "program", "tool", "library"):
        return _no(f"a {shape} is a program, not rows — a code repository publishes no dataset, so "
                   f"there is nothing here to count, map or acquire (the AMR test found nine "
                   f"admissible TOOLS and zero sources on that surface)")
    r = c.get("rows")
    if isinstance(r, bool):
        return _no("rows is a boolean; a boolean is not evidence of rows")
    if isinstance(r, int):
        return (_ok({"declared": r}), "") if r > 0 else _no("zero rows is an empty source")
    if isinstance(r, dict):
        ev = {k: r.get(k) for k in ("quote", "where", "read_at", "how", "sha256") if r.get(k)}
        if not _text(r.get("quote")) or not _text(r.get("where")):
            return _no("the candidate says rows exist but records neither the words nor the "
                       "address they were read at — a claim with no evidence is not a row count",
                       ev)
        return _ok(ev)
    return _no("no rows: the candidate declares no dataset at all (rows, or the words and the "
               "address that show them)")


# ── 2 · COUNT ───────────────────────────────────────────────────────────────────────────────
def _check_count(c: dict) -> tuple:
    """A COUNT ENDPOINT, OR A SELF-COUNTING FILE. The count that admits a source must be one the
    machine can REPRODUCE: an endpoint that answers, or bytes the machine can count."""
    r = _dig(c.get("count"))
    if not r:
        return _no("no count: nothing tells the machine what this source's own number is (a count "
                   "ENDPOINT, or a file that counts itself)")
    kind = _text(r.get("kind")).lower()
    if kind not in COUNT_KINDS:
        return _no(f"the count is declared kind {kind or '(absent)'!r}; this spec reads "
                   f"{list(COUNT_KINDS)} — a number printed in a document is a claim, not a "
                   f"count the machine can reproduce")
    where = _text(r.get("where") or r.get("url") or r.get("endpoint"))
    if not where:
        return _no(f"the count is declared kind {kind!r} with no address: an endpoint needs its "
                   f"URL, a self-counting file needs where the file is")
    if kind == "file" and not (_text(r.get("member")) or _text(r.get("quote"))):
        return _no("the count is kind 'file' but names neither the member that counts itself nor "
                   "the publisher's words that say the file is machine-readable")
    return _ok({k: r.get(k) for k in ("kind", "where", "url", "endpoint", "member", "quote",
                                      "read_at", "sha256") if r.get(k)})


# ── 3 · RELEASE ─────────────────────────────────────────────────────────────────────────────
def _check_release(c: dict, derived: dict | None = None) -> tuple:
    """A RELEASE ID. A derived copy may INHERIT its authority's release (it has no stream of its
    own), but it must then name the authority it inherited from."""
    r = c.get("release")
    note = ""
    if isinstance(r, dict):
        rid = _text(r.get("id"))
        note = _text(r.get("note"))
        inherited = _text(r.get("inherited_from"))
        if not rid:
            return _no(f"no release: the candidate's own words are {note!r} — a source with no "
                       f"release id is a moving target")
        if inherited and not (derived or {}).get("authority"):
            return _no(f"the release is inherited from {inherited!r} but the candidate is not "
                       f"marked derived with that authority")
        return _ok({k: r.get(k) for k in ("id", "inherited_from", "where", "quote", "read_at",
                                          "note") if r.get(k)})
    rid = _text(r)
    if not rid:
        return _no("no release: nothing here identifies WHICH bytes are meant (a release id, or a "
                   "tag)")
    if rid.lower() in MOVING_ALIASES:
        return _no(f"the release id is {rid!r} — a moving alias is not a release id: it names "
                   f"whatever is there today, not the bytes that were evaluated")
    return _ok({"id": rid})


# ── 4 · LICENCE ─────────────────────────────────────────────────────────────────────────────
def _check_licence(c: dict) -> tuple:
    """A READABLE LICENCE, RECORDED WITH ITS EVIDENCE. The three ways a licence may be read (api ·
    page · tag) and the four facts that make the reading an EVIDENCE record: how, where, the
    publisher's own words, and the date. A bare name is a guess and is refused as one."""
    r = c.get("licence")
    if isinstance(r, str):
        return _no(f"the licence is the bare name {r.strip()!r} with no address and no words: a "
                   f"name nobody can re-read is a GUESS, not a reading — record how it was read "
                   f"(api · page · tag), WHERE, and the publisher's own words")
    d = _dig(r)
    if not d:
        return _no("no licence: the candidate declares none, so its terms are unknown")
    how = _text(d.get("how")).lower()
    lic = _text(d.get("licence") or d.get("name"))
    where = _text(d.get("where") or d.get("url"))
    quote = _text(d.get("quote"))
    read_at = _text(d.get("read_at") or d.get("read"))
    missing = []
    if how not in LICENCE_HOWS:
        missing.append(f"how={how or '(absent)'} (this spec reads {list(LICENCE_HOWS)})")
    if lic.lower() in NOT_A_LICENCE:
        missing.append(f"licence={lic or '(absent)'}")
    if not where:
        missing.append("where=(absent)")
    if not quote:
        missing.append("quote=(absent)")
    if not read_at:
        missing.append("read_at=(absent)")
    ev = {k: d.get(k) for k in ("how", "licence", "name", "where", "url", "quote", "read_at",
                                "sha256", "actor", "note") if d.get(k)}
    if missing:
        return _no("the licence is not RECORDED WITH ITS EVIDENCE — " + " · ".join(missing) +
                   " — a licence read by guesswork is not a licence read", ev)
    if not d.get("sha256"):
        ev["sha256"] = _sha(f"{lic}\n{where}\n{quote}")   # a receipt for the reading itself
        ev["sha256_of"] = "the declared reading (licence · where · quote)"
    return _ok(ev)


# ── 5 · SCHEMA ──────────────────────────────────────────────────────────────────────────────
def _check_schema(c: dict) -> tuple:
    """A DOCUMENTED SCHEMA: columns, or an OBO/JSON schema. A PDF has no schema a machine can
    bind to — that is not a fault of the data, it is the extraction work, and the refusal says so."""
    d = _dig(c.get("schema"))
    if not d:
        return _no("no schema: the candidate documents no columns and no OBO/JSON schema, so a "
                   "machine cannot know what its fields mean")
    kind = _text(d.get("kind")).lower()
    if kind not in SCHEMA_KINDS:
        return _no(f"the schema is declared kind {kind or '(absent)'!r}; this spec reads "
                   f"{list(SCHEMA_KINDS)} — a human-readable table in a PDF is not a machine "
                   f"schema", {k: d.get(k) for k in ("kind", "note") if d.get(k)})
    cols = d.get("columns")
    if kind == "columns" and not (_text(d.get("where")) and
                                  ((isinstance(cols, list) and cols) or _text(d.get("quote")))):
        return _no("the schema is kind 'columns' but names no address and no columns (or the "
                   "words that document them)")
    if kind in ("obo", "json", "csvw", "frictionless") and not _text(d.get("where")):
        return _no(f"the schema is kind {kind!r} but names no address for the schema file")
    return _ok({k: d.get(k) for k in ("kind", "where", "columns", "quote", "read_at", "sha256")
                if d.get(k) is not None})


# ── 6 · STABILITY ───────────────────────────────────────────────────────────────────────────
def _check_stability(c: dict) -> tuple:
    """A STABLE URL WITH AN ARCHIVE OR AN IMMUTABLE ASSET. A URL on a moving branch, or a link
    into somebody's personal spreadsheet, is a URL that will not be the same bytes tomorrow."""
    d = _dig(c.get("stable") or c.get("url_of_record"))
    url = _text(d.get("url") if isinstance(d, dict) else d) or _text(c.get("url"))
    if not url:
        return _no("no URL of record: nothing names where the bytes live")
    if not re.match(r"^https?://[^/\s]+", url):
        return _no(f"the URL of record {url!r} is not an http(s) address with a host")
    d = _dig(d) or {"url": url}
    how = _text(d.get("how")).lower()
    if how not in STABILITY_KINDS:
        return _no(f"the URL {url!r} names no archive, no immutable asset, no dated release and "
                   f"no DOI (how={how or '(absent)'}; this spec reads {list(STABILITY_KINDS)}) — "
                   f"a bare link is not a stable URL", {"url": url, "note": d.get("note")})
    pinned = _text(d.get("pinned") or d.get("revision") or d.get("doi"))
    segs = [s.lower() for s in url.split("://", 1)[-1].split("/")[1:]]
    if not pinned and any(s in MOVING_SEGMENTS for s in segs):
        return _no(f"the URL {url!r} points at a MOVING branch and names no pin (revision · DOI · "
                   f"snapshot): the bytes behind it will change", {"url": url})
    return _ok({k: d.get(k) for k in ("url", "how", "pinned", "revision", "doi", "quote",
                                      "read_at", "sha256", "note") if d.get(k)})


# ── the derived flag ───────────────────────────────────────────────────────────────────────
def derived_line(authority: str, via: str) -> str:
    """The one line a repackaged copy writes on every row: derived:<authority>:via=<via>."""
    return f"derived:{authority}:via={via}"


def _check_derived(c: dict) -> tuple:
    """A REPACKAGED COPY MUST NAME ITS AUTHORITY. Silence about being a copy is the failure mode
    the AMR test named: "it is a copy of a copy — the authority remains WHO".

    THREE WAYS A CANDIDATE SAYS IT IS A COPY, and all three are hints this check reads: a
    `derived` block, a top-level `derived_from`/`authority`/`via` key, or a RELEASE that says it is
    inherited from somebody else (a copy has no release stream of its own). Any of them without a
    named authority AND a named `via` is REFUSED[derived-without-authority]."""
    rel = _dig(c.get("release"))
    inherited = _text(rel.get("inherited_from"))
    hint = any(k in c for k in DERIVED_KEYS) or bool(inherited)
    d = c.get("derived")
    if isinstance(d, str):
        d = {"authority": d}
    d = _dig(d)
    authority = _text(d.get("authority") or d.get("of") or inherited)
    via = _text(d.get("via") or d.get("by") or d.get("through"))
    line = _text(c.get("authority_line")) or (derived_line(authority, via)
                                              if authority and via else "")
    if authority and via:
        return True, {"authority": authority, "via": via, "line": line,
                      "quote": _text(c.get("authority_quote")) or ""}, ""
    if hint or d:
        why = (f"its release is inherited from {inherited!r} and it names no `via`" if inherited
               and not via else
               f"authority={authority or '(absent)'} via={via or '(absent)'}")
        return False, {"authority": authority, "via": via, "line": line}, (
            "the candidate is a REPACKAGED COPY that names no authority — "
            f"{why}; a copy of a copy that does not say what it is a copy OF is not a source "
            "(the line it owes is 'derived:<authority>:via=<via>')")
    return True, {}, ""


# ── the question this module must never answer ────────────────────────────────────────────
def admit(*_a, **_k):
    """THE ONE THING THIS MODULE MAY NOT DO. Admission is a policy step (`admit.by_policy` is
    False) taken by a person, never by an evaluator: a tractability pass is PROPOSED, full stop.

    This function exists ONLY to refuse, by name, any caller that tries to make the evaluator
    admit — including a test that wants to prove the path does not exist. It writes nothing,
    grants nothing, and never returns."""
    raise Refused(ADMIT_REFUSAL,
                  "the evaluator may not admit: a pass is PROPOSED, and admission is a policy step "
                  "(admit.by_policy is False — a person decides). There is no code path from "
                  "evaluate() to an 'admitted' event, and this module has no writer")


def _asks_admission(c: dict) -> str:
    """A candidate that ARRIVES admitted, or asks to be — the other way to try to admit by machine."""
    for k in ADMIT_KEYS:
        if k not in c:
            continue
        v = c[k]
        if k == "verdict" and _text(v).upper() == PROPOSED:
            continue
        if isinstance(v, bool):
            if v:
                return f"{k}={v!r}"
        elif _text(v).lower() in ("admitted", "admit", "true", "yes", "approved"):
            return f"{k}={v!r}"
    return ""


# ── THE EVALUATOR ─────────────────────────────────────────────────────────────────────────
_CHECKS = {"rows": _check_rows, "count": _check_count, "release": _check_release,
           "licence": _check_licence, "schema": _check_schema, "stable": _check_stability}


def evaluate(candidate: dict, *, evaluate_now: str = "") -> dict:
    """Run the six checks over one candidate and return a VERDICT RECORD. Pure: no I/O, no network,
    no writes, deterministic.

    The verdict is PROPOSED when every check holds, REFUSED otherwise — and `admitted` is the
    constant False in both cases, because the machine may judge tractability and nothing else.
    EVERY failure is named (not just the first), so the answer to "why was this refused" is the
    list itself, greppable one line per class."""
    cand = dict(candidate or {})
    sid = _text(cand.get("id") or cand.get("source_id")) or "(unnamed candidate)"
    refusals: list[dict] = []
    checks: list[dict] = []

    # 0 · A candidate that arrives pre-admitted (or asks to be) is refused by name.
    asked = _asks_admission(cand)
    if asked:
        refusals.append(_ref(ADMIT_REFUSAL, sid,
                             f"the candidate declares {asked}: a candidate cannot arrive with "
                             f"permission — the evaluator proposes, a person admits"))

    # 0b · THE DERIVED FLAG (checked once; the release check reads it).
    d_ok, derived, d_problem = _check_derived(cand)
    if not d_ok:
        refusals.append(_ref(DERIVED_REFUSAL, sid, d_problem))
    if derived:
        checks.append(dict(check="derived", question="does a repackaged copy name its authority?",
                           ok=d_ok, evidence=derived, problem=d_problem))

    # 1-6 · THE SIX, in the row's order.
    for field, code, question in SIX_CHECKS:
        fn = _CHECKS[field]
        ok, ev, problem = fn(cand, derived) if field == "release" else fn(cand)
        checks.append(dict(check=field, question=question, ok=ok, evidence=ev, problem=problem))
        if not ok:
            refusals.append(_ref(code, sid, problem))

    declared_science = {k: cand[k] for k in SCIENCE_KEYS if cand.get(k)}
    return dict(
        id=sid,
        url=_text(cand.get("url")),
        verdict=PROPOSED if not refusals else REFUSED,
        refusals=refusals,
        checks=checks,
        licence=next((c["evidence"] for c in checks if c["check"] == "licence"), {}),
        derived=derived or None,
        not_judged=dict(
            science=declared_science,
            note=("SCIENCE IS NOT JUDGED HERE. No field of this record says whether the data are "
                  "good, the study is sound or the question is worth asking; those are a person's "
                  "to answer" if declared_science else
                  "SCIENCE IS NOT JUDGED HERE — and this candidate made no scientific claim to "
                  "pass through")),
        machine_may_judge=list(MACHINE_MAY_JUDGE),
        machine_may_not_judge=list(MACHINE_MAY_NOT_JUDGE),
        admit_by_policy=ADMIT_BY_POLICY,
        admitted=ADMITTED,
        evaluate_now=_text(evaluate_now),
    )


def lines(verdict: dict) -> list[str]:
    """The verdict as printable lines: the verdict line, then EVERY refusal by name, then the
    licence reading (where and what), then what was not judged."""
    v = verdict or {}
    sid = _text(v.get("id")) or "(unnamed candidate)"
    out = [f"{_text(v.get('verdict')) or REFUSED}  {sid}"
           + (f"  ({v['url']})" if v.get("url") else "")]
    for r in v.get("refusals") or []:
        out.append("  " + r["line"])
    lic = v.get("licence") or {}
    if lic and (lic.get("how") or lic.get("licence")):
        out.append(f"  licence read {lic.get('how')}: {lic.get('licence')} — where "
                   f"{lic.get('where')} — quote { (lic.get('quote') or '')[:160]!r}"
                   + (f" — read {lic['read_at']}" if lic.get("read_at") else ""))
    if v.get("derived"):
        dv = v["derived"]
        line = dv.get("line") or derived_line(dv.get("authority", ""), dv.get("via", ""))
        out.append(f"  {line}  (a copy names the authority it copies)")
    nj = (v.get("not_judged") or {}).get("science") or {}
    if nj:
        out.append("  NOT JUDGED (science): " + "; ".join(f"{k}={str(x)[:80]!r}"
                                                          for k, x in nj.items()))
    out.append(f"  admitted={v.get('admitted')} · admit.by_policy={v.get('admit_by_policy')} · "
               f"a machine judges {', '.join(v.get('machine_may_judge') or [])} and not "
               f"{', '.join(v.get('machine_may_not_judge') or [])}")
    return out


def evaluate_all(candidates, *, evaluate_now: str = "") -> list[dict]:
    """Every candidate, one verdict each — the doorway a caller loops over. Ordered as given."""
    return [evaluate(c, evaluate_now=evaluate_now) for c in (candidates or [])]


def summary(verdicts) -> dict:
    """Counts for a run record: proposed · refused · by class. Never an 'admitted' count — there
    is nothing in this module that could raise one."""
    refused: dict[str, int] = {}
    proposed = 0
    for v in verdicts or []:
        if v.get("verdict") == PROPOSED:
            proposed += 1
        for r in v.get("refusals") or []:
            refused[r["code"]] = refused.get(r["code"], 0) + 1
    return dict(evaluated=len(verdicts or []), proposed=proposed,
                refused=sum(1 for v in (verdicts or []) if v.get("verdict") == REFUSED),
                by_class=dict(sorted(refused.items())),
                admitted=0, admit_by_policy=ADMIT_BY_POLICY)
