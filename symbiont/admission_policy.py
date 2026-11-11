"""THE ADMISSION POLICY, AS A FILE THE STATION READS (req-P1-ADMISSION-POLICY-the-N-stable-cycles).

Owner decision D3 (2026-11-10): "maybe policy — a source that passes every card check and has been
stable for N cycles may activate itself. NOT A BLOCKER: our team is not in the path of adding a
source. The card is the safety, not a person." Before this module, that sentence was prose:
nothing in the station read a policy, so admission was manual or absent. Now the policy is a FILE
the owner owns (reports/symbiont-policy.yml) and the station only READS it — it never invents,
defaults or rounds a number that is not written there.

THE ONE RULE: a source may SELF-ADMIT when
    (a) every check the policy names in card_checks_required reads `ok` on its card, AND
    (b) it has been stable for stable_cycles_before_self_admit (N) consecutive cycles,
where a CYCLE is one harvest run recorded in the store in which the source ran and its count check
agreed (a run where it was skipped does not count either way; a refusal, a failed count check or a
re-admission breaks the chain). On self-admission the record says, verbatim:
    "by policy, rule stable_cycles_before_self_admit, N=<n>"
Anything else still needs the operator's word: the station refuses BY NAME and never writes a
record. The admission record is the decision artefact (<store>/admissions/<source>.json) plus a
line in the source's digest; sources.yml is NOT edited here — joining the registry stays an
operator act, and a quarantined source is never self-admitted (its way back is the card through
`python3 -m symbiont.loop --readmit <id> --card <file>`).

FAIL CLOSED: a policy that is missing, unreadable or malformed — or an N the owner has not set —
makes the station REFUSE to self-admit ANYTHING, and say so, with the parse error named. A number
the owner has not set is not a permission.

THE CARD (a text file, the same shape as the --readmit card, extended with check lines):
    source: <id>            must match --source
    changed: <what changed> the card names what changed, always
    check licence: ok           ┐
    check count_match: ok       │ one line per required check; `ok` passes, anything
    check sample_rows: ok       │ else fails it; a missing line fails it too
    check xref_rate: ok         │
    check cost: ok              ┘

Run (the station command; the admission pipeline's gated step may quote the same rule):
    python3 -m symbiont.admission_policy source_admit --source <id> --card <card file> \
        [--store DIR] [--policy PATH] [--sources-config PATH]

Exit codes: 0 self-admitted (record written, the rule and N quoted)
            2 REFUSED — [policy] fail closed (missing/unreadable/malformed policy, N unset)
                       or [admit] the operator's word is needed (a failed card check, an unstable
                       source, a quarantined source, or a cap in the policy says no).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys

from . import contract
from .loop import DEFAULT_STORE, digest_line, jdump, say, utc_stamp

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_POLICY = os.path.join(FARM, "reports", "symbiont-policy.yml")

# The policy schema, total: an unknown key is refused by name (the config and the code must not
# disagree about what exists — the same doctrine sources.yml loads under).
POLICY_KEYS = (
    "stable_cycles_before_self_admit",   # N — may be null (owner has not set it yet)
    "max_sources_active",                # may be null (no cap to enforce)
    "max_new_sources_per_cycle",         # may be null (no cap to enforce)
    "agent_max_rounds",                  # agent-task caps (agent_tasks.py): may be null (no cap)
    "agent_max_wall_s",
    "agent_max_calls",
    "agent_no_progress_rounds",           # K: consecutive no-gain rounds ⇒ REFUSED[no-progress]
    "retention_releases_per_source",       # retention (retention.py): N releases kept per source;
                                          # may be null (fail closed — the sweep drops nothing)
    "retention_disk_consequence_mb",       # the disk consequence of N, stated by the owner in MB
    "licence_must_be_read",              # required: bool
    "card_checks_required",              # required: non-empty list of check names
    "quarantine_after_count_mismatches",  # required: int >= 1
    "quarantine_after_rate_breach",      # required: bool
)
NUMBER_KEYS = ("stable_cycles_before_self_admit", "max_sources_active", "max_new_sources_per_cycle",
               "agent_max_rounds", "agent_max_wall_s", "agent_max_calls", "agent_no_progress_rounds",
               "retention_releases_per_source", "retention_disk_consequence_mb")
FIXED_KEYS = ("licence_must_be_read", "card_checks_required",
              "quarantine_after_count_mismatches", "quarantine_after_rate_breach")
RULE = "stable_cycles_before_self_admit"      # THE rule the admission record quotes, by name


class PolicyError(RuntimeError):
    """The policy could not be READ faithfully — the station fails closed and says so."""
    code = "policy"


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise PolicyError(msg)


def load_policy(path: str = DEFAULT_POLICY) -> dict:
    """Parse + validate the owner's policy file -> dict with every POLICY_KEYS key present.

    Fail closed on every way a policy can be missing, unreadable or malformed — with the reason
    (and for a malformed file, the parser's own error) NAMED in the message. A number key the
    owner has not written is returned as None: the station never invents a number.
    """
    import yaml
    if not os.path.isfile(path):
        raise PolicyError(f"the policy file {path} is missing — fail closed: the station refuses "
                          f"to self-admit anything until the owner writes one")
    try:
        with open(path, "rb") as fh:
            raw_bytes = fh.read()
    except OSError as e:
        raise PolicyError(f"the policy file {path} could not be read ({type(e).__name__}: {e}) — "
                          f"fail closed: the station refuses to self-admit anything") from e
    try:
        raw = yaml.safe_load(raw_bytes.decode("utf-8", "strict"))
    except (UnicodeDecodeError, yaml.YAMLError) as e:
        raise PolicyError(f"the policy file {path} is malformed — fail closed, nothing "
                          f"self-admits, the parse error named: {e}") from e
    _need(isinstance(raw, dict), f"{path}: top level must be a mapping with `version` and `policy`")
    _need(raw.get("version") == 1, f"{path}: unsupported version {raw.get('version')!r} (this code reads 1)")
    pol = raw.get("policy")
    _need(isinstance(pol, dict), f"{path}: `policy` must be a mapping with the keys {list(POLICY_KEYS)}")
    unknown = [k for k in pol if k not in POLICY_KEYS]
    _need(not unknown, f"{path}: policy carries unknown key(s) {sorted(unknown)} — the schema is "
                        f"{list(POLICY_KEYS)}")
    missing = [k for k in FIXED_KEYS if k not in pol]
    _need(not missing, f"{path}: policy is missing key(s) {missing} that the station must read "
                       f"— fail closed: the station refuses to self-admit anything")
    out = {k: pol.get(k) for k in NUMBER_KEYS}     # a number the owner has not set is None
    for k in NUMBER_KEYS:
        v = out[k]
        _need(v is None or (isinstance(v, int) and not isinstance(v, bool) and v >= 1),
              f"{path}: policy.{k} must be null or a positive integer, got {v!r}")
    _need(isinstance(pol["licence_must_be_read"], bool),
          f"{path}: policy.licence_must_be_read must be true or false, got {pol['licence_must_be_read']!r}")
    checks = pol["card_checks_required"]
    _need(isinstance(checks, list) and checks and all(isinstance(c, str) for c in checks),
          f"{path}: policy.card_checks_required must be a non-empty list of check names")
    _need(len(set(checks)) == len(checks), f"{path}: policy.card_checks_required names a check twice: {checks}")
    _need(all(re.fullmatch(r"[a-z0-9_]+", c) for c in checks),
          f"{path}: policy.card_checks_required names an unparseable check: {checks}")
    qn = pol["quarantine_after_count_mismatches"]
    _need(isinstance(qn, int) and not isinstance(qn, bool) and qn >= 1,
          f"{path}: policy.quarantine_after_count_mismatches must be an integer >= 1, got {qn!r}")
    _need(isinstance(pol["quarantine_after_rate_breach"], bool),
          f"{path}: policy.quarantine_after_rate_breach must be true or false, "
          f"got {pol['quarantine_after_rate_breach']!r}")
    out["licence_must_be_read"] = pol["licence_must_be_read"]
    out["card_checks_required"] = list(checks)
    out["quarantine_after_count_mismatches"] = qn
    out["quarantine_after_rate_breach"] = pol["quarantine_after_rate_breach"]
    return out


def required_checks(pol: dict) -> list[str]:
    """The checks a card must pass. licence_must_be_read: true means the licence check is
    required even if card_checks_required forgot it — the terms unread = no data doctrine (T6)
    is not something a list may silently drop."""
    req = list(pol["card_checks_required"])
    if pol["licence_must_be_read"] and "licence" not in req:
        req = ["licence"] + req
    return req


def read_card(path: str, sid: str, required: list[str]) -> tuple[dict, list[str]]:
    """Parse the card -> ({changed, checks}, problems). problems is empty iff the card names this
    source, names what changed, and every required check line reads `ok`."""
    text = open(path, encoding="utf-8").read()
    problems: list[str] = []
    if f"source: {sid}" not in text:
        problems.append(f"the card does not name this source (it needs a `source: {sid}` line)")
    m = re.search(r"^changed:[ \t]*(\S.*)$", text, re.M)
    changed = m.group(1).strip() if m else ""
    if not changed:
        problems.append("the card names no change (it needs a `changed:` line)")
    checks: dict[str, str] = {}
    for cm in re.finditer(r"^check[ \t]+([a-z0-9_]+):[ \t]*(\S.*)$", text, re.M):
        checks[cm.group(1)] = cm.group(2).strip()
    for name in required:
        if name not in checks:
            problems.append(f"the card is missing the `check {name}: ok` line")
        elif checks[name].lower() != "ok":
            problems.append(f"check {name} failed: {checks[name]}")
    return dict(changed=changed, checks=checks), problems


def stable_cycles(store: str, sid: str) -> tuple[int, list[str]]:
    """The trailing chain of consecutive STABLE cycles for <sid>, newest run first.

    Evidence is read from the run records on disk (the same source of truth the loop's own
    quarantine chain reads). A cycle is STABLE when the source ran and its count check agreed
    (verify.ok is true). A run where the source was SKIPPED — or a run that does not mention it —
    is not a cycle of this source and does not touch the chain; a refusal, a failed count check
    or a re-admission BREAKS it (a re-admission starts a fresh chain, exactly as in loop.py)."""
    rdir = os.path.join(store, "runs")
    if not os.path.isdir(rdir):
        return 0, []
    recs = []
    for name in os.listdir(rdir):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(rdir, name), encoding="utf-8") as fh:
                recs.append(json.load(fh))
        except Exception:
            continue
    recs.sort(key=lambda r: r.get("started_at") or "")
    ids: list[str] = []
    for r in reversed(recs):
        s = (r.get("sources") or {}).get(sid)
        if s is None:
            continue                       # not a cycle of this source
        if s.get("readmitted"):
            break                          # a re-admission starts a fresh chain
        if s.get("skipped"):
            continue                       # did not run: the chain is untouched
        if s.get("refused"):
            break                          # a refused cycle is not a stable one
        if (s.get("verify") or {}).get("ok") is True:
            ids.append(r.get("run_id", ""))
            continue
        break                              # ran and the count check disagreed: not stable
    ids.reverse()
    return len(ids), ids


def cap_refusal(pol: dict, sid: str, sources: dict, store: str) -> str | None:
    """The two caps, read from the policy and enforced ONLY when the owner has set them (null =
    no cap: a cap the owner has not written does not exist to enforce). Returns the refusal
    reason, or None. ONE definition: both the station's `source_admit` here and the gated step
    `symbiont.admission admit` call this, so the numbers cannot mean two things."""
    m_active = pol["max_sources_active"]
    if m_active is not None:
        active = [s for s, c in sources.items() if c["enabled"] and not c.get("quarantined")]
        prospective = len(active) + (0 if sid in active else 1)
        if prospective > m_active:
            return (f"max_sources_active={m_active} and {len(active)} active — this "
                    f"admission would make {prospective}")
    m_new = pol["max_new_sources_per_cycle"]
    if m_new is not None:
        adir = os.path.join(store, "admissions")
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        others = mine = 0
        if os.path.isdir(adir):
            for name in os.listdir(adir):
                if not name.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(adir, name), encoding="utf-8") as fh:
                        rec = json.load(fh)
                except Exception:
                    continue
                if not (rec.get("at") or "").startswith(today):
                    continue
                if rec.get("source") == sid:
                    mine = 1
                else:
                    others += 1
        if not mine and others >= m_new:
            return (f"max_new_sources_per_cycle={m_new} and {others} source(s) already "
                    f"self-admitted this cycle (UTC day {today})")
    return None


def consult(sid: str, card_path: str, store: str, policy_path: str, sources: dict) -> dict:
    """THE ONE DECISION: consult the owner's policy and say whether <sid> may SELF-admit.

    Both the station's `source_admit` CLI (this module) and the gated step
    `symbiont.admission admit` call THIS, so the rule has one definition and the numbers cannot
    mean two things. The station READS the owner's policy; it never sets, defaults or rounds a
    number that is not written there.

    Returns dict(ok, code, reason, line, rule, N, numbers, required, policy_path, policy_sha,
                 cycles, runs, checks, card_changed):
        ok       True  = the source may self-admit (the record quotes `line`)
        code     "policy" (fail closed: missing/unreadable/malformed) · "n-unset" (the owner has
                 not set N — refused BY NAME) · "card" (a required check is not ok) ·
                 "quarantine" (its way back is the re-admit card) · "cap" (a policy cap says no) ·
                 "cycles" (stable for fewer than N) · "" when ok
        reason   the refusal, named ("" when ok); it carries "the operator's word is needed"
                 wherever that is the way forward
    Fail closed is absolute: a policy that is missing, unreadable or malformed makes the station
    refuse to SELF-admit ANYTHING, the parse error named — and nothing else about it changes.
    """
    out = dict(ok=False, code="", reason="", line="", rule=RULE, N=None, numbers=None,
               required=[], policy_path=os.path.abspath(policy_path), policy_sha="",
               cycles=0, runs=[], checks={}, card_changed="")
    try:
        pol = load_policy(policy_path)
    except PolicyError as e:
        out.update(code="policy", reason=str(e))
        return out
    with open(policy_path, "rb") as fh:
        out["policy_sha"] = hashlib.sha256(fh.read()).hexdigest()
    out["N"] = n = pol[RULE]
    out["numbers"] = {k: pol[k] for k in POLICY_KEYS}
    req = out["required"] = required_checks(pol)

    # N unset is the named failure of the row: the station refuses self-admission BY NAME, fail
    # closed, because a number the owner has not set is not a permission.
    if n is None:
        out.update(code="n-unset",
                   reason=(f"the policy sets no {RULE} (N) — the station refuses self-admission "
                           f"BY NAME, fail closed: the owner sets the number, the station only "
                           f"reads it; the operator's word is needed"))
        return out
    if not os.path.isfile(card_path):
        out.update(code="card", reason=f"the card {card_path!r} does not exist — the operator's "
                                       f"word is needed")
        return out
    card, problems = read_card(card_path, sid, req)
    out["card_changed"] = card["changed"]
    q = (sources.get(sid) or {}).get("quarantined")
    if q:
        out.update(code="quarantine",
                   reason=(f"quarantined since {q['at']} ({q['reason']}) — the way back is the "
                           f"re-admission card (python3 -m symbiont.loop --readmit {sid} "
                           f"--card <file>), not the admission policy; the operator's word is "
                           f"needed"))
        return out
    if problems:
        out.update(code="card",
                   reason=("the card does not pass every required check (" + "; ".join(problems) +
                           ") — a source failing one card check never self-admits regardless of "
                           "N — the operator's word is needed"))
        return out
    out["checks"] = {k: "ok" for k in req}
    cap = cap_refusal(pol, sid, sources, store)
    if cap:
        out.update(code="cap", reason=cap + " — the operator's word is needed")
        return out
    cycles, runs = stable_cycles(store, sid)
    out.update(cycles=cycles, runs=runs)
    if cycles < n:
        out.update(code="cycles",
                   reason=(f"stable for {cycles} consecutive cycle(s); the policy requires "
                           f"{RULE} N={n} — the operator's word is needed"))
        return out
    out.update(ok=True, line=f"self-admitted by policy, rule {RULE}, N={n}")
    return out


def source_admit(sid: str, card_path: str, store: str, policy_path: str,
                sources_config: str) -> int:
    """THE station command: consult the policy and either self-admit <sid> (record written,
    exit 0) or refuse BY NAME (exit 2). The station READS the owner's numbers, it never sets one."""
    try:
        sources = contract.load_sources(sources_config)
    except contract.ConfigError as e:
        say(f"REFUSED[admit] {sid}: the sources config could not be read ({e}) — the operator's "
            f"word is needed")
        return 2
    v = consult(sid, card_path, store, policy_path, sources)
    if v["code"] != "policy":
        say(f"POLICY   {v['policy_path']}  sha256={v['policy_sha'][:16]}…  "
            f"{RULE}={'UNSET' if v['N'] is None else v['N']}  "
            f"max_sources_active={v['numbers']['max_sources_active']}  "
            f"max_new_sources_per_cycle={v['numbers']['max_new_sources_per_cycle']}  "
            f"checks={','.join(v['required'])}")
    if not v["ok"]:
        tag = "admit" if v["code"] == "n-unset" else v["code"]
        say(f"REFUSED[{tag}] {sid}: {v['reason']}")
        return 2
    n = v["N"]
    say(f"CARD     {sid}  {len(v['required'])}/{len(v['required'])} checks ok  "
        f"changed: {v['card_changed']}")
    say(f"CYCLES   {sid}  stable for {v['cycles']} consecutive cycle(s) "
        f"({', '.join(v['runs']) or 'no runs'}) — the policy requires N={n}")

    when = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    rec = dict(
        admission_id=f"admit-{utc_stamp()}", source=sid, decision="self-admitted", by="policy",
        rule=RULE, N=n, stable_cycles=v["cycles"], stable_cycle_runs=v["runs"], line=v["line"],
        card=os.path.abspath(card_path), card_changed=v["card_changed"],
        checks=v["checks"],
        policy=dict(path=v["policy_path"], sha256=v["policy_sha"], numbers=v["numbers"]),
        at=when,
    )
    rec_path = jdump(rec, os.path.join(store, "admissions", f"{sid}.json"))
    dpath = digest_line(store, sid,
                        f"self-admitted {when} by policy, rule {RULE}, N={n} "
                        f"({v['cycles']} stable cycles, card {os.path.abspath(card_path)})")
    say(f"ADMITTED {sid}  {v['line']}  ({v['cycles']} stable cycles)")
    say(f"RECORD   {rec_path}")
    say(f"DIGEST   {dpath}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="symbiont.admission_policy",
                                 description="the admission policy reader: the station READS "
                                             "reports/symbiont-policy.yml and self-admits a "
                                             "source only when the policy says so")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sa = sub.add_parser("source_admit", help="consult the policy: self-admit a source only when "
                                             "every card check reads ok AND it has been stable "
                                             "for N cycles; anything else needs the operator's word")
    sa.add_argument("--source", required=True, metavar="SOURCE",
                    help="the candidate source id (its stable cycles are read from --store's run records)")
    sa.add_argument("--card", required=True, metavar="PATH",
                    help="the admission card: names this source, what changed, and one "
                         "`check <name>: ok` line per required check")
    sa.add_argument("--store", default=DEFAULT_STORE,
                    help="the store whose runs/ records are the source's cycles")
    sa.add_argument("--policy", default=DEFAULT_POLICY,
                    help="path to the owner's policy file (default: reports/symbiont-policy.yml)")
    sa.add_argument("--sources-config", default=contract.DEFAULT_SOURCES,
                    help="path to sources.yml, for the quarantine and active-cap checks")
    a = ap.parse_args(argv)
    return source_admit(a.source, a.card, a.store, a.policy, a.sources_config)


if __name__ == "__main__":
    sys.exit(main())
