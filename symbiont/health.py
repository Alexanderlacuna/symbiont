"""SOURCE HEALTH — the structured record an agent reads before it acts.

The row (req-P1-SOURCE-HEALTH-AND-AGENT-ADDED-SOURCES): "i want to be able to check health of the
sources via agent, agent adds new sources etc, remove the human element from manually doing some
stuff". This module is `source_health` — one definition, used by BOTH the CLI
(`python3 -m symbiont.health`) and the MCP tool (`symbiont/mcpserver.py`, which runs this CLI).

Per source it returns a STRUCTURED record, read from the two places a fact can live:

    state          ok | degraded | down | quarantined | disabled
    last_run       age of the newest run record that names the source (and which run)
    last_success   age of the newest run where the source ran AND its count check agreed
    count_check    ours vs the source's own count, and the URL that states it
    rate_headroom  requests spent this cycle vs the budget cap, and the rate-limit waits slept
    licence        the terms on record (name, url, sha256) or "declared", never assumed
    staleness      OUR stored release vs the upstream release the last run saw; age vs policy
    reason         THE ONE reason the source is not ok — a non-ok source is never a bare red
    remediation    the last action an agent recorded (retry | quarantine | replace), with its note

An agent that reads a non-ok record can ACT, and the act is RECORDED:
    python3 -m symbiont.health --record-remediation <id> --action retry --note "why"
appends one line to the source's digest (store/digest/<id>.md) and one entry to
store/remediations.jsonl, then reports health with the remediation on the record.

THE NARROW AGENT RECORD (req-P1-SOURCE-HEALTH-AND-AGENT-ADDED-SOURCES): health(store) reads
sources.yml + the last run record + the xref/view counts and returns PER SOURCE one dict with
EXACTLY:
    state                ok | degraded | down | quarantined
    last_run_age_s       seconds since the newest run record naming the source (null: never)
    last_success_age_s   seconds since the newest run where it ran AND its count agreed
    count_check          {ours, theirs, url} from the last run's verify block (null: never verified)
    rate_used_this_cycle requests the source spent in the last run (null: never ran)
    licence              known (terms read + hashed in a run) | unknown (declared or nothing)
    staleness            {our_release, upstream_release|null} — ours stored, theirs last seen
    reason               ONE string, ALWAYS set — an ok source says WHY it is ok
and `python3 -m symbiont.health --store <dir>` prints it as JSON, one object per source
(one {"<id>": {...}} per line). Health NEVER FETCHES: a quarantined source is read off the disk
(its block in sources.yml), never probed — zero requests, by construction and by test F3.

States and their ONE reason (precedence, first match wins):
    quarantined  sources.yml carries the quarantine block   -> its reason, verbatim
    disabled     sources.yml enabled: false                 -> "switched off by the operator"
    down         the last run REFUSED the source, or no run record names it at all
    degraded     the last run completed but the count check disagreed, or the stored release
                 is past staleness policy (SOURCE-STALE)
    ok           ran, count agreed, release within policy

Run:  python3 -m symbiont.health [--sources-config P] [--store S] [--json]
                                 [--record-remediation <id> --action retry|quarantine|replace
                                  --note <text>]
      --store S            the narrow record as JSON, one object per source (the agent surface)
      no --store           the human lines (the operator surface; selftest_sources F12 asserts them)
      --json               the full structured record (the MCP tool surface)

Exit codes: 0 reported · 2 REFUSED[config|action] (config could not be loaded, or a remediation
was malformed / named a source that is not defined).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys

from . import contract

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORE = os.path.join(ROOT, "store")
ACTIONS = ("retry", "quarantine", "replace")   # the row: retry, quarantine, or propose a replacement


# ------------------------------------------------------------------ disk, read only
def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_iso(s: str):
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


def _age(rec: dict, key: str, now: dt.datetime):
    """Seconds since the record's <key> timestamp, or None when absent/unparsable."""
    at = _parse_iso(rec.get(key) or "")
    if not at:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=dt.timezone.utc)
    return round((now - at).total_seconds())


def _fmt_age(sec) -> str:
    if sec is None:
        return "never"
    if sec < 90:
        return f"{sec}s"
    if sec < 5400:
        return f"{round(sec / 60)}m"
    if sec < 172800:
        return f"{round(sec / 3600)}h"
    return f"{round(sec / 86400, 1)}d"


def _run_records(store: str) -> list[dict]:
    """Every run record on disk, oldest first. A record that cannot be parsed is skipped —
    health reports what is readable, and a corrupt record is the nurse's problem, not a lie here."""
    rdir = os.path.join(store, "runs")
    out = []
    if not os.path.isdir(rdir):
        return out
    for name in sorted(os.listdir(rdir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(rdir, name), encoding="utf-8") as fh:
                r = json.load(fh)
            if isinstance(r, dict):
                out.append(r)
        except Exception:
            continue
    out.sort(key=lambda r: r.get("started_at") or "")
    return out


def _outcome(recs: list[dict], sid: str):
    """The newest run record in which <sid> has a real outcome — it ran, it was refused, or it
    was skipped for a reason of its own (disabled/quarantined). A `not-selected` skip is not an
    outcome: --sources merely narrowed that run."""
    for r in reversed(recs):
        s = (r.get("sources") or {}).get(sid)
        if not s:
            continue
        if (s.get("skipped") or {}).get("reason") == "not-selected":
            continue
        return r, s
    return None, None


def _last_naming(recs: list[dict], sid: str):
    for r in reversed(recs):
        if sid in (r.get("sources") or {}):
            return r
    return None


def _state_file(store: str, sid: str) -> dict:
    p = os.path.join(store, "state", f"{sid}.json")
    try:
        with open(p, encoding="utf-8") as fh:
            s = json.load(fh)
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def _remediations(store: str) -> list[dict]:
    p = os.path.join(store, "remediations.jsonl")
    out = []
    if not os.path.isfile(p):
        return out
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def record_remediation(store: str, sid: str, action: str, note: str) -> dict:
    """Append the agent's act to the digest AND the remediation log, and return the entry.
    Both are facts on disk: the digest line is what an operator reads per source, the jsonl is
    what a tool reads back. A remediation nobody recorded is an act nobody took."""
    when = _now().replace(microsecond=0).isoformat()
    entry = dict(at=when, source=sid, action=action, note=note)
    ddir = os.path.join(store, "digest")
    os.makedirs(ddir, exist_ok=True)
    dpath = os.path.join(ddir, f"{sid}.md")
    with open(dpath, "a", encoding="utf-8") as fh:
        fh.write(f"remediation {when} ({action}): {note}\n")
    with open(os.path.join(store, "remediations.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    entry["digest"] = dpath
    return entry


# ------------------------------------------------------------------ the record
def health_record(sources_config: str = contract.DEFAULT_SOURCES,
                  store: str = DEFAULT_STORE) -> dict:
    """The structured record for every source — the one definition of source_health."""
    now = _now()
    sources = contract.load_sources(sources_config)          # refuses by name, ConfigError
    recs = _run_records(store)
    remeds = _remediations(store)
    out = []
    for sid in sorted(sources):
        cfg = sources[sid]
        st = _state_file(store, sid)
        last_run = _last_naming(recs, sid)
        r, s = _outcome(recs, sid)
        refused = (s or {}).get("refused") or {}
        skipped = (s or {}).get("skipped") or {}
        verify = (s or {}).get("verify") or {}
        cost = (s or {}).get("cost") or {}

        # -- count_check: ours vs theirs, and the URL that states theirs --------------------
        count_check = None
        if verify:
            count_check = dict(ok=verify.get("ok"), ours=verify.get("ours"),
                               source_count=verify.get("source_count"),
                               url=verify.get("url"), detail=verify.get("detail"))

        # -- rate-limit headroom used this cycle ----------------------------------------------
        budget_requests = int((cfg.get("budget") or {}).get("requests") or 0)
        waits = cost.get("rate_limit_waits") or []
        rate_headroom = None
        if cost:
            used = int(cost.get("requests") or 0)
            rate_headroom = dict(requests_used=used, requests_cap=budget_requests or None,
                                 remaining=(budget_requests - used) if budget_requests else None,
                                 waits_slept=len(waits),
                                 wait_s=round(float(sum(waits)), 3) if waits else 0.0)

        # -- licence: the terms on record, or what sources.yml declares, never an assumption --
        lic_state = (st.get("licence") or {})
        licence = dict(name=cfg.get("licence_text", ""), url=cfg.get("licence_url", ""),
                       terms_sha256=lic_state.get("terms_sha256", ""),
                       status="recorded" if lic_state.get("terms_sha256") else "declared",
                       note="" if lic_state.get("terms_sha256")
                            else "terms declared in sources.yml but never read in a run")

        # -- staleness: OUR stored release vs the upstream the last run saw ------------------
        policy = int((cfg.get("staleness") or {}).get("policy_days") or 0)
        release_at = _parse_iso(st.get("release_at") or st.get("updated_at") or "")
        age_days = round((now - release_at).total_seconds() / 86400, 1) if release_at else None
        upstream = ((s or {}).get("stale") or {}).get("upstream_release") \
            or ((s or {}).get("discover") or {}).get("release") or ""
        stale_now = bool(policy and age_days is not None and age_days > policy)
        staleness = dict(our_release=st.get("release", ""), upstream_release=upstream,
                         release_at=(st.get("release_at") or st.get("updated_at") or None),
                         age_days=age_days, policy_days=policy, stale=stale_now,
                         stale_since=(release_at.date().isoformat() if stale_now and release_at else None))

        # -- the state, and THE ONE reason ----------------------------------------------------
        q = cfg.get("quarantined")
        if q:
            state = "quarantined"
            reason = (f"quarantined since {q['at']} ({q['status']}): {q['reason']}")
        elif not cfg["enabled"]:
            state = "disabled"
            reason = "sources.yml: enabled false — switched off by the operator"
        elif r is None:
            state = "down"
            reason = (f"never run: no run record under {os.path.join(store, 'runs')} "
                      f"names this source")
        elif refused:
            state = "down"
            reason = f"last run {r.get('run_id', '?')} REFUSED[{refused.get('code')}]: {refused.get('detail')}"
        elif skipped:
            state = "down"
            reason = f"last run {r.get('run_id', '?')} skipped it: {skipped.get('detail') or skipped.get('reason')}"
        elif verify.get("ok") is False:
            state = "degraded"
            reason = (f"count check disagreed in run {r.get('run_id', '?')}: "
                      f"ours={verify.get('ours')} vs source={verify.get('source_count')} "
                      f"at {verify.get('url')}")
        elif stale_now:
            state = "degraded"
            reason = (f"SOURCE-STALE: stored release {staleness['our_release']} is "
                      f"{staleness['age_days']}d old (policy {policy}d), first observed "
                      f"{staleness['stale_since']}; upstream at last run: {upstream or '?'}")
        else:
            state = "ok"
            reason = ""

        # -- last success: ran AND the count agreed -------------------------------------------
        last_ok = None
        for rr in reversed(recs):
            ss = (rr.get("sources") or {}).get(sid) or {}
            if ss.get("skipped") or ss.get("refused"):
                continue
            if (ss.get("verify") or {}).get("ok") is True:
                last_ok = rr
                break

        rem = next((x for x in reversed(remeds) if x.get("source") == sid), None)

        out.append(dict(
            id=sid, state=state, reason=reason,
            last_run=dict(run_id=(last_run or {}).get("run_id"),
                          at=(last_run or {}).get("started_at"),
                          age_s=_age(last_run or {}, "started_at", now)) if last_run else None,
            last_success=dict(run_id=(last_ok or {}).get("run_id"),
                              at=(last_ok or {}).get("started_at"),
                              age_s=_age(last_ok or {}, "started_at", now)) if last_ok else None,
            count_check=count_check, rate_headroom=rate_headroom,
            licence=licence, staleness=staleness, remediation=rem,
        ))
    return dict(sources_config=os.path.abspath(sources_config), store=os.path.abspath(store),
                generated_at=now.isoformat(), sources=out)


# ------------------------------------------------------------------ the narrow agent record
def _store_view_xref(store: str):
    """The counts the store itself grounds the claim in: the view's provenance (view name, rows)
    and the xref pairs on disk. Read from the store, never fetched."""
    view_name = view_rows = xref_pairs = None
    vdir = os.path.join(store, "view")
    if os.path.isdir(vdir):
        for name in sorted(os.listdir(vdir)):
            if not name.endswith(".provenance.json"):
                continue
            try:
                with open(os.path.join(vdir, name), encoding="utf-8") as fh:
                    p = json.load(fh)
                if isinstance(p, dict):
                    view_name, view_rows = p.get("view"), p.get("rows")
            except Exception:
                continue
    xp = os.path.join(store, "xref", "xref.tsv")
    if os.path.isfile(xp):
        with open(xp, encoding="utf-8") as fh:
            xref_pairs = max(0, sum(1 for _ in fh) - 1)   # minus the header row
    return view_name, view_rows, xref_pairs


def health(store: str = DEFAULT_STORE, sources_config: str = contract.DEFAULT_SOURCES) -> dict:
    """The narrow agent record — one dict PER SOURCE, with exactly the eight keys (see the module
docstring). Reads sources.yml + the last run record + the xref/view counts; fetches NOTHING, so a
quarantined source costs zero requests (the quarantine block in sources.yml IS its state)."""
    now = _now()
    sources = contract.load_sources(sources_config)          # refuses by name, ConfigError
    recs = _run_records(store)
    view_name, view_rows, xref_pairs = _store_view_xref(store)
    out: dict[str, dict] = {}
    for sid in sorted(sources):
        cfg = sources[sid]
        st = _state_file(store, sid)
        last_run = _last_naming(recs, sid)
        r, s = _outcome(recs, sid)
        refused = (s or {}).get("refused") or {}
        skipped = (s or {}).get("skipped") or {}
        verify = (s or {}).get("verify") or {}
        cost = (s or {}).get("cost") or {}

        count_check = None
        if verify:
            count_check = dict(ours=verify.get("ours"), theirs=verify.get("source_count"),
                               url=verify.get("url"))

        rate_used = int(cost["requests"]) if cost.get("requests") is not None else None

        lic_sha = (st.get("licence") or {}).get("terms_sha256") \
            or ((s or {}).get("discover") or {}).get("licence", {}).get("terms_sha256")
        licence = "known" if lic_sha else "unknown"

        policy = int((cfg.get("staleness") or {}).get("policy_days") or 0)
        release_at = _parse_iso(st.get("release_at") or st.get("updated_at") or "")
        age_days = round((now - release_at).total_seconds() / 86400, 1) if release_at else None
        upstream = ((s or {}).get("stale") or {}).get("upstream_release") or None
        our_release = st.get("release") or ((s or {}).get("stale") or {}).get("stored_release") or ""
        stale_now = bool(policy and age_days is not None and age_days > policy)
        staleness = dict(our_release=our_release, upstream_release=upstream)

        last_ok = None
        for rr in reversed(recs):
            ss = (rr.get("sources") or {}).get(sid) or {}
            if ss.get("skipped") or ss.get("refused"):
                continue
            if (ss.get("verify") or {}).get("ok") is True:
                last_ok = rr
                break

        # -- the state, and THE ONE reason (always set, even for ok) ---------------------------
        q = cfg.get("quarantined")
        if q:
            state = "quarantined"
            reason = (f"quarantined since {q.get('at')} ({q.get('status')}): {q.get('reason')}")
        elif not cfg["enabled"]:
            state = "down"
            reason = "switched off by the operator (sources.yml enabled: false) — not fetched"
        elif r is None:
            state = "down"
            reason = (f"never run: no run record under {os.path.join(store, 'runs')} "
                      f"names this source")
        elif refused:
            state = "down"
            reason = (f"last run {r.get('run_id', '?')} "
                      f"REFUSED[{refused.get('code')}]: {refused.get('detail')}")
        elif skipped:
            state = "down"
            reason = (f"last run {r.get('run_id', '?')} skipped it: "
                      f"{skipped.get('detail') or skipped.get('reason')}")
        elif verify.get("ok") is False:
            state = "degraded"
            reason = (f"count check disagreed in run {r.get('run_id', '?')}: "
                      f"ours={verify.get('ours')} vs theirs={verify.get('source_count')} "
                      f"at {verify.get('url')}")
        elif stale_now:
            state = "degraded"
            reason = (f"SOURCE-STALE: stored release {our_release or '?'} is {age_days}d old "
                      f"(policy {policy}d); upstream at last run: {upstream or '?'}")
        else:
            state = "ok"
            why = []
            if r is not None:
                why.append(f"ran in {r.get('run_id', '?')} "
                           f"{_fmt_age(_age(r, 'started_at', now))} ago")
            if verify:
                why.append(f"count check agreed (ours={verify.get('ours')} "
                           f"== theirs={verify.get('source_count')})")
            why.append(f"licence {licence}")
            if our_release:
                rel = f"our release {our_release}"
                rel += (f" matches upstream {upstream}" if upstream == our_release
                        else (f"; upstream at last run {upstream}" if upstream else ""))
                if age_days is not None:
                    rel += f", {age_days}d old within the {policy}d policy"
                why.append(rel)
            if view_rows is not None:
                why.append(f"the store grounds it: view {view_name} holds {view_rows} rows "
                           f"over {xref_pairs} xref pairs")
            reason = "ok: " + "; ".join(why)

        out[sid] = dict(
            state=state,
            last_run_age_s=_age(last_run or {}, "started_at", now),
            last_success_age_s=_age(last_ok or {}, "started_at", now),
            count_check=count_check,
            rate_used_this_cycle=rate_used,
            licence=licence,
            staleness=staleness,
            reason=reason,
        )
    return out


# ------------------------------------------------------------------ the CLI
def _line(r: dict) -> str:
    """One human line per source. The quarantined and ok formats are the T9 formats
    (selftest_sources F12 asserts them); a non-ok source always carries its ONE reason."""
    sid, state, reason = r["id"], r["state"], r["reason"]
    if state == "quarantined":
        # kept byte-compatible with the T9 line: quarantined_since, status and reason verbatim
        q = r.get("_q") or {}
        return (f"{sid}  state=quarantined  quarantined_since={q.get('at', '?')}  "
                f"status={q.get('status', '?')}  reason={q.get('reason', '?')}")
    bits = [f"{sid}  state={state}"]
    if state == "ok":
        if r.get("last_run"):
            bits.append(f"last_run={_fmt_age(r['last_run']['age_s'])} ago (run {r['last_run']['run_id']})")
        if r.get("last_success"):
            bits.append(f"last_success={_fmt_age(r['last_success']['age_s'])} ago")
        cc = r.get("count_check")
        if cc:
            bits.append(f"count_check {'ok' if cc.get('ok') else 'DISAGREE'} "
                        f"(ours={cc.get('ours')} vs source={cc.get('source_count')})")
        rh = r.get("rate_headroom")
        if rh:
            bits.append(f"rate {rh['requests_used']}/{rh['requests_cap'] or '?'} requests, "
                        f"{rh['waits_slept']} waits ({rh['wait_s']}s)")
        bits.append(f"licence {r['licence']['status']}")
        st = r["staleness"]
        bits.append(f"release {st['our_release'] or '?'} (age {st['age_days']}d, "
                    f"policy {st['policy_days']}d)")
    else:
        bits.append(f"reason={reason}")
    if r.get("remediation"):
        m = r["remediation"]
        bits.append(f"remediation {m.get('action')} at {m.get('at')}: {m.get('note')}")
    return "  ".join(bits)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="symbiont.health",
        description="source_health: per-source structured health, from sources.yml + the store")
    ap.add_argument("--sources-config", default=contract.DEFAULT_SOURCES,
                    help="path to sources.yml (the one definition site per source)")
    ap.add_argument("--store", default=None,
                    help="the symbiont store to read; GIVEN, the narrow record is printed as "
                         "JSON, one object per source (the agent surface)")
    ap.add_argument("--json", action="store_true", help="the structured record as JSON")
    ap.add_argument("--record-remediation", metavar="ID",
                    help="record an agent's act for source ID before reporting")
    ap.add_argument("--action", choices=list(ACTIONS), help="with --record-remediation")
    ap.add_argument("--note", default="", help="with --record-remediation: why, in words")
    a = ap.parse_args(argv)
    store = a.store or DEFAULT_STORE

    try:
        sources = contract.load_sources(a.sources_config)
    except contract.ConfigError as e:
        print(f"REFUSED[{e.code}] {e}")
        return 2

    if a.record_remediation:
        if a.record_remediation not in sources:
            print(f"REFUSED[action] {a.record_remediation}: not a source in "
                  f"{os.path.abspath(a.sources_config)} — a remediation is recorded for a source "
                  f"that exists, by name")
            return 2
        if not a.action or not a.note:
            print(f"REFUSED[action] --record-remediation needs --action (one of {', '.join(ACTIONS)}) "
                  f"AND --note (what was done and why) — an unexplained act is not a record")
            return 2
        m = record_remediation(store, a.record_remediation, a.action, a.note)
        print(f"REMEDIATION {a.record_remediation} recorded ({a.action}): {a.note}")
        print(f"REMEDIATION digest -> {m['digest']}")

    if a.store is not None and not a.json:
        # the agent surface (this row): the narrow record, one JSON object per source, one per line
        for sid, rec in health(store=store, sources_config=a.sources_config).items():
            print(json.dumps({sid: rec}))
        return 0

    rec = health_record(a.sources_config, store)
    # the raw quarantine block rides along for the T9-format line
    for r in rec["sources"]:
        r["_q"] = sources[r["id"]].get("quarantined") or {}
    if a.json:
        for r in rec["sources"]:
            r.pop("_q", None)
        print(json.dumps(rec, indent=2, sort_keys=True))
    else:
        print(f"source_health  {len(rec['sources'])} source(s) from {rec['sources_config']}  "
              f"store={rec['store']}")
        for r in rec["sources"]:
            print(_line(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
