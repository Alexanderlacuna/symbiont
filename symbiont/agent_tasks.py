"""THE TASK LEDGER + NO-PROGRESS DETECTOR + HARD CAPS for agent work
(req-P1-AGENT-BUDGET-AND-NO-PROGRESS-DETECTOR; SCOUT-AI-MINING-STATIONS.md §10 CONTROL 2).

The two numbers every mature agent station publishes and this one did not: what an agent task
SPENT (calls · tokens · bytes · wall-clock · rounds) and what it GAINED (new accessions, new
records) — plus the two stops that are NOT a clock kill. Tonight's shape, three times over: a
lane hit the 30-minute clock mid-report, a fire died silently, a scout died with no cause. The
documented failures elsewhere are precisely the uncapped cases (Sakana's self-relaunching
processes and terabyte checkpoints — the same paper's OWN answer is the hard-cap table: 5
experiments · 4 attempts · 7200 s · 20 citation rounds). Caps are DECLARED in the policy file
and ENFORCED here, by the station, never by the model's goodwill.

WHAT AN AGENT TASK IS HERE: work that proceeds in ROUNDS against the station — a round makes
CALLS (station doors, tools, requests), moves BYTES, may spend TOKENS (model calls), and either
GAINS something (a new accession in the store's id namespaces, a new record) or does not.
The station calls task.round(ctx) until the task says it is done — or until a stop fires.

THE THREE STOPS (each a NAMED refusal, never a clock kill, never a silent death):
    REFUSED[no-progress]   K consecutive rounds (policy agent_no_progress_rounds, default 3 —
                           the row's own default) with zero new accessions AND zero new records
    REFUSED[cap-rounds]    more rounds than the declared agent_max_rounds
    REFUSED[cap-wall]      more wall-clock than the declared agent_max_wall_s
    REFUSED[cap-calls]     more calls than the declared agent_max_calls

THE LEDGER (per task, written into the run record runs/<id>.json):
    calls · tokens · bytes · wall-clock · rounds · new accessions gained — per round AND summed,
    under `agent_tasks`, and as the run record's own `cost` line in the SAME shape the manifest
    already carries (requests · bytes_in · wall_seconds), extended for agent tasks with
    tokens · rounds · new_accessions · new_records — so any consumer of run-record cost reads an
    agent-task record without a second format.

THE POLICY (reports/symbiont-policy.yml, the ONE policy file the station reads — this module
adds no second reader: admission_policy.load_policy is the only parse): the four agent numbers
are declared there by the owner's row (agent_max_rounds 20 = Sakana's 20 citation rounds;
agent_max_wall_s 1800 = the 30-minute lane clock the row names; agent_max_calls 80 = 20 rounds
× the 4-call plugin contract; agent_no_progress_rounds 3 = the row's default K). A cap the
owner has not written (null) is no cap to enforce — the file's own doctrine — but the shipped
policy declares all four, so the station as shipped is capped.

Run (the python -m door; the console door `symbiont task` is deliberately NOT wired here —
cli.py belongs to the packaging lane in flight the night this landed):
    python3 -m symbiont.agent_tasks run --task <name> [--store DIR] [--run-id ID] [--policy PATH]
    python3 -m symbiont.agent_tasks selftest        (the three fixtures + the wall cap, assert-only)

TASK NAMES: the built-in fixtures and the real view-walk below, or `py:<path>` — a .py file
whose module-level TASK is the task object (an operator can drop a task in without touching
the station).

Exit codes follow the station's contract: 0 done · 2 refused BEFORE the work (unknown task,
unreadable/malformed policy — fail closed, the error named) · 3 the task was stopped BY NAME
(no-progress | cap-rounds | cap-wall | cap-calls).
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import sys
import time

from .admission_policy import load_policy, DEFAULT_POLICY
from .loop import DEFAULT_STORE, jdump, say, utc_stamp

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The policy's agent numbers -> what this module enforces. K defaults to 3 ONLY because the row
# says "default 3"; every other null cap means "no cap to enforce" (the policy file's doctrine).
NO_PROGRESS_DEFAULT = 3


class TaskRefused(RuntimeError):
    """A NAMED stop — the task's ledger is complete and written before this is raised."""
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def load_caps(policy_path: str = DEFAULT_POLICY) -> dict:
    """The four agent numbers from the owner's policy file (ONE reader: admission_policy).

    Fail closed like the rest of the station: a missing/malformed policy refuses the task
    BEFORE any work, with the parse error named — an uncapped run over an unread policy is the
    silent-inflation failure mode this row exists to end."""
    pol = load_policy(policy_path)      # raises PolicyError (code 'policy') — fail closed
    return {
        "max_rounds": pol.get("agent_max_rounds"),        # null = no cap (owner has not set one)
        "max_wall_s": pol.get("agent_max_wall_s"),
        "max_calls": pol.get("agent_max_calls"),
        "no_progress_rounds": pol.get("agent_no_progress_rounds") or NO_PROGRESS_DEFAULT,
    }


# ---------------------------------------------------------------- the task protocol
class RoundReport(dict):
    """What ONE round did. `calls` is required (a round that made no calls gained nothing by
    definition); everything else defaults to 0. `new_accessions` may be a count or a list —
    the ledger stores the COUNT and (when a list) the ids, because an accession is an id in the
    store's namespaces and 'we gained 12' must be checkable against WHICH 12."""

ALLOWED = {"calls", "tokens", "bytes", "new_accessions", "new_records", "note", "new_accession_ids"}


def _norm_round(raw: dict, rnd: int) -> RoundReport:
    if not isinstance(raw, dict):
        raise TaskRefused("bad-round", f"round {rnd} did not return a mapping of its spending")
    unknown = [k for k in raw if k not in ALLOWED]
    if unknown:
        raise TaskRefused("bad-round", f"round {rnd} reported unknown field(s) {sorted(unknown)}")
    calls = raw.get("calls")
    if not isinstance(calls, int) or isinstance(calls, bool) or calls < 0:
        raise TaskRefused("bad-round", f"round {rnd} did not report a non-negative integer `calls`")
    acc = raw.get("new_accessions", 0)
    ids = raw.get("new_accession_ids")
    if isinstance(acc, list):
        ids = ids or acc
        acc = len(acc)
    if not isinstance(acc, int) or isinstance(acc, bool) or acc < 0:
        raise TaskRefused("bad-round", f"round {rnd} reported a non-integer new_accessions")
    if ids is not None and not isinstance(ids, list):
        raise TaskRefused("bad-round", f"round {rnd} reported new_accession_ids that is not a list")
    rep = RoundReport(calls=calls, tokens=int(raw.get("tokens") or 0), bytes=int(raw.get("bytes") or 0),
                      new_accessions=acc, new_records=int(raw.get("new_records") or 0),
                      note=str(raw.get("note") or ""))
    if ids is not None:
        rep["new_accession_ids"] = [str(i) for i in ids]
    return rep


# ---------------------------------------------------------------- the runner
def run_task(task, store: str, run_id: str | None = None, policy_path: str = DEFAULT_POLICY,
             question: str = "") -> tuple[dict, int]:
    """Run ONE agent task under the declared caps; return (run record, exit code).

    The record is written to <store>/runs/<run_id>.json — the same run-record directory the
    harvest loop uses — with `kind: agent-task`, so every reader of runs/ can tell an agent
    task from a harvest run by name (loop._prior_run_failures skips them; health reads sources)."""
    caps = load_caps(policy_path)
    run_id = run_id or f"agent-{utc_stamp()}"
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    rounds: list[dict] = []
    totals = dict(calls=0, tokens=0, bytes=0, wall_seconds=0.0, new_accessions=0, new_records=0)
    ctx = dict(store=store, run_id=run_id, round=0)
    begin = getattr(task, "begin", None)
    if callable(begin):
        begin(ctx)
    exit_word, refused = "done", None
    code = 0
    try:
        streak = 0
        rnd = 0
        while True:
            rnd += 1
            if caps["max_rounds"] is not None and rnd > caps["max_rounds"]:
                raise TaskRefused("cap-rounds",
                                  f"round {rnd} would exceed the declared cap agent_max_rounds="
                                  f"{caps['max_rounds']} (policy {os.path.basename(policy_path)})")
            t0 = time.monotonic()
            rep = _norm_round(task.round(ctx), rnd)
            wall = time.monotonic() - t0
            ctx["round"] = rnd
            for k in ("calls", "tokens", "bytes", "new_accessions", "new_records"):
                totals[k] += rep[k]
            totals["wall_seconds"] = round(totals["wall_seconds"] + wall, 3)
            entry = dict(round=rnd, calls=rep["calls"], tokens=rep["tokens"], bytes=rep["bytes"],
                         wall_seconds=round(wall, 3), new_accessions=rep["new_accessions"],
                         new_records=rep["new_records"])
            if rep.get("note"):
                entry["note"] = rep["note"]
            if rep.get("new_accession_ids"):
                entry["new_accession_ids"] = rep["new_accession_ids"]
            rounds.append(entry)
            # hard caps — enforced by the station AFTER the round's spending is in the ledger,
            # so a refused task still shows exactly what it spent before it was stopped.
            if caps["max_calls"] is not None and totals["calls"] > caps["max_calls"]:
                raise TaskRefused("cap-calls",
                                  f"calls {totals['calls']} exceed the declared cap agent_max_calls="
                                  f"{caps['max_calls']} at round {rnd}")
            if caps["max_wall_s"] is not None and totals["wall_seconds"] > caps["max_wall_s"]:
                raise TaskRefused("cap-wall",
                                  f"wall {totals['wall_seconds']}s exceeds the declared cap "
                                  f"agent_max_wall_s={caps['max_wall_s']} at round {rnd}")
            # no-progress — the loop-shape sibling of the loop's own NO_NEW: a round that gained
            # neither a new accession nor a new record moves nothing, and K of those in a row
            # is a refusal BY NAME, not a clock kill and not an infinite loop.
            if rep["new_accessions"] or rep["new_records"]:
                streak = 0
            else:
                streak += 1
                if streak >= caps["no_progress_rounds"]:
                    raise TaskRefused("no-progress",
                                      f"{caps['no_progress_rounds']} consecutive round(s) with no new "
                                      f"accession and no new record (rounds "
                                      f"{rnd - caps['no_progress_rounds'] + 1}-{rnd})")
            if task.done(ctx):
                break
    except TaskRefused as e:
        exit_word, refused, code = "refused", dict(code=e.code, reason=e.reason), 3
    ledger = {
        "task": task.name,
        "rounds": rounds,
        "totals": dict(totals, rounds=len(rounds)),
        "exit": exit_word,
        "refused": refused,
        "caps": {k: v for k, v in caps.items()},
        "policy": dict(path=policy_path,
                       sha256=__import__("hashlib").sha256(open(policy_path, "rb").read()).hexdigest()),
    }
    record = {
        "run_id": run_id,
        "kind": "agent-task",
        "task": task.name,
        "question": question,
        "started_at": started,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "agent_tasks": [ledger],
        # THE COST LINE the manifest already carries (requests · bytes_in · wall_seconds),
        # extended to agent tasks with tokens · rounds · new_accessions · new_records.
        "cost": {
            "requests": totals["calls"],
            "bytes_in": totals["bytes"],
            "wall_seconds": totals["wall_seconds"],
            "tokens": totals["tokens"],
            "rounds": len(rounds),
            "new_accessions": totals["new_accessions"],
            "new_records": totals["new_records"],
            "note": "the manifest's own cost shape (requests/bytes_in/wall_seconds), extended to "
                    "agent tasks with tokens/rounds/new_accessions/new_records",
        },
        "refusals": [refused] if refused else [],
    }
    jdump(record, os.path.join(store, "runs", f"{run_id}.json"))
    return record, code


# ---------------------------------------------------------------- the built-in tasks
class _Fixture:
    """A deterministic task: rounds() returns one RoundReport per call until it runs dry."""
    name = "fixture"
    plan: list[dict] = []

    def begin(self, ctx):
        self.i = 0

    def round(self, ctx):
        if self.i >= len(self.plan):
            raise TaskRefused("bad-round", "fixture plan exhausted — the station never invented a round")
        rep = dict(self.plan[self.i])
        self.i += 1
        return rep

    def done(self, ctx):
        return self.i >= len(self.plan)


class LoopNoGain(_Fixture):
    """Loops forever, never gains: the documented failure shape (a caller's loop that keeps
    spending at our door). The station must stop it at round K with REFUSED[no-progress]."""
    name = "fixture:loop-no-gain"
    plan = [dict(calls=2, bytes=100, tokens=0, note="spends, gains nothing") for _ in range(200)]

    def done(self, ctx):
        return False          # it NEVER finishes — only a named stop may end it


class OnePerRound(_Fixture):
    """Gains exactly one accession per round for 5 rounds, then finishes: progress every round,
    so the no-progress detector must NOT stop it."""
    name = "fixture:one-per-round"
    plan = [dict(calls=1, bytes=50, tokens=10, new_accessions=1, new_accession_ids=[f"acc-{i}"],
                 new_records=0, note="one new accession") for i in range(5)]

    def done(self, ctx):
        return self.i >= len(self.plan)


class CapBreachCalls(_Fixture):
    """Gains an accession every round (never no-progress) but spends 30 calls a round against
    the declared agent_max_calls=80: the station must refuse it BY NAME at the calls cap."""
    name = "fixture:cap-breach-calls"
    plan = [dict(calls=30, bytes=500, tokens=100, new_accessions=1,
                 new_accession_ids=[f"acc-{i}"], note="progress, but spendy") for i in range(200)]

    def done(self, ctx):
        return False


class CapBreachWall(_Fixture):
    """Gains an accession every round but sleeps 0.4 s a round; used with a fixture policy
    whose agent_max_wall_s is 1, so the wall cap is exercised without a 30-minute wait."""
    name = "fixture:cap-breach-wall"
    plan = [dict(calls=1, bytes=10, tokens=0, new_accessions=1, new_accession_ids=[f"acc-{i}"],
                 note="progress, but slow") for i in range(200)]

    def done(self, ctx):
        return False

    def round(self, ctx):
        time.sleep(0.4)
        return _Fixture.round(self, ctx)


class ViewAccessionWalk:
    """THE REAL TASK (not a fixture): walk the station's published view
    (<store>/view/mouse_gene.v1.tsv) in chunks, one round per chunk, banking the MGI accessions
    newly seen this round. Calls are real reads at the station's own door, bytes are real bytes;
    tokens are 0 because this task spends no model (the ledger carries the field so a task that
    does spend tokens records them). Every row's mgi_id is unique, so every round gains."""
    name = "view-accession-walk"

    def begin(self, ctx):
        path = os.path.join(ctx["store"], "view", "mouse_gene.v1.tsv")
        if not os.path.isfile(path):
            raise TaskRefused("bad-input", f"no published view at {path}")
        self.lines = open(path, encoding="utf-8").read().splitlines()
        self.header = self.lines[0].split("\t")
        self.mgi_col = self.header.index("mgi_id")
        self.seen: set[str] = set()
        self.i = 0
        self.chunk = 20000
        self.total = len(self.lines) - 1

    def round(self, ctx):
        chunk = self.lines[1 + self.i:self.chunk * (self.i + 1) + 1]
        self.i += 1
        new_ids = [ln.split("\t")[self.mgi_col] for ln in chunk]
        new_ids = [a for a in new_ids if a and a not in self.seen]
        self.seen.update(new_ids)
        return dict(calls=1, bytes=sum(len(ln) + 1 for ln in chunk), tokens=0,
                    new_accessions=len(new_ids), new_accession_ids=new_ids[:5],
                    new_records=0, note=f"view lines {self.i * self.chunk - self.chunk + 1}-"
                                        f"{min(self.i * self.chunk, self.total)}")

    def done(self, ctx):
        return self.i * self.chunk >= self.total


BUILTINS = {c.name: c for c in (LoopNoGain, OnePerRound, CapBreachCalls, CapBreachWall, ViewAccessionWalk)}


def _load_task(name: str):
    if name.startswith("py:"):
        path = name[3:]
        spec = importlib.util.spec_from_file_location("agent_task_" + os.path.basename(path).replace(".", "_"), path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.TASK
    if name in BUILTINS:
        return BUILTINS[name]()
    raise TaskRefused("bad-input", f"unknown task {name!r} — built-ins: {sorted(BUILTINS)}, or py:<path>")


# ---------------------------------------------------------------- selftest (fixtures + caps)
def selftest(fixdir: str) -> int:
    """The three fixtures of the row's gate + the wall cap, assert-only, against throwaway
    stores under symbiont/fixtures/agent-budget/ — never the live store."""
    from .loop import jdump as _j
    import shutil
    root = os.path.join(fixdir, "agent-budget")
    shutil.rmtree(root, ignore_errors=True)
    here = os.path.dirname(os.path.abspath(__file__))
    shipped = os.path.normpath(os.path.join(FARM, "reports", "symbiont-policy.yml"))
    wall_policy = os.path.join(root, "wall-policy.yml")
    os.makedirs(os.path.join(root, "wall-store", "runs"), exist_ok=True)
    with open(wall_policy, "w", encoding="utf-8") as fh:
        fh.write("# FIXTURE — same schema as the shipped policy; only agent_max_wall_s differs (1s)\n"
                 "version: 1\npolicy:\n"
                 "  stable_cycles_before_self_admit: null\n"
                 "  max_sources_active: null\n"
                 "  max_new_sources_per_cycle: null\n"
                 "  agent_max_rounds: 20\n"
                 "  agent_max_wall_s: 1\n"
                 "  agent_max_calls: 80\n"
                 "  agent_no_progress_rounds: 3\n"
                 "  licence_must_be_read: true\n"
                 "  card_checks_required: [licence, count_match, sample_rows, xref_rate, cost]\n"
                 "  quarantine_after_count_mismatches: 2\n"
                 "  quarantine_after_rate_breach: true\n")
    ok = [0]

    def check(name, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))
        if not cond:
            ok[0] += 1

    def run(name, store, policy=shipped, run_id=None):
        task = _load_task(name)
        s = os.path.join(root, store)
        os.makedirs(os.path.join(s, "runs"), exist_ok=True)
        rec, code = run_task(task, s, run_id=run_id, policy_path=policy,
                            question="agent-budget fixture")
        return rec, code

    # 1 — the loop that never gains stops at round K=3, REFUSED[no-progress] naming K
    rec, code = run("fixture:loop-no-gain", "store-no-gain", run_id="fixture-no-gain")
    led = rec["agent_tasks"][0]
    check("loop-no-gain stops at round 3 (K) with REFUSED[no-progress]",
          code == 3 and led["refused"]["code"] == "no-progress"
          and "3 consecutive round(s)" in led["refused"]["reason"] and len(led["rounds"]) == 3)
    check("  its ledger is written (3 rounds, 6 calls, 0 accessions)",
          led["totals"]["rounds"] == 3 and led["totals"]["calls"] == 6
          and led["totals"]["new_accessions"] == 0
          and os.path.isfile(os.path.join(root, "store-no-gain", "runs", "fixture-no-gain.json")))
    # 2 — the task that gains one accession per round is NOT stopped
    rec, code = run("fixture:one-per-round", "store-one-per", run_id="fixture-one-per-round")
    led = rec["agent_tasks"][0]
    check("one-per-round completes (5 rounds, 5 accessions, not stopped)",
          code == 0 and led["exit"] == "done" and len(led["rounds"]) == 5
          and led["totals"]["new_accessions"] == 5 and led["refused"] is None)
    # 3 — exceeding the declared calls cap refuses BY NAME
    rec, code = run("fixture:cap-breach-calls", "store-cap-calls", run_id="fixture-cap-calls")
    led = rec["agent_tasks"][0]
    check("cap-breach-calls refuses by name REFUSED[cap-calls] (90 > 80, at round 3)",
          code == 3 and led["refused"]["code"] == "cap-calls"
          and "90 exceed" in led["refused"]["reason"] and "agent_max_calls=80" in led["refused"]["reason"]
          and len(led["rounds"]) == 3)
    # 4 — the wall cap refuses by name too (fixture policy: 1 s)
    rec, code = run("fixture:cap-breach-wall", "wall-store", policy=wall_policy, run_id="fixture-cap-wall")
    led = rec["agent_tasks"][0]
    check("cap-breach-wall refuses by name REFUSED[cap-wall]",
          code == 3 and led["refused"]["code"] == "cap-wall"
          and "agent_max_wall_s=1" in led["refused"]["reason"])
    # 5 — the cost line carries the manifest's own keys, extended for agent tasks
    rec, code = run("fixture:one-per-round", "store-cost-line", run_id="fixture-cost-line")
    cost = rec["cost"]
    check("cost line: manifest shape (requests/bytes_in/wall_seconds) + tokens/rounds/accessions",
          cost["requests"] == 5 and cost["bytes_in"] == 250 and cost["tokens"] == 50
          and cost["rounds"] == 5 and cost["new_accessions"] == 5
          and rec["kind"] == "agent-task")
    # 6 — the shipped policy declares all four caps and the loader reads them
    caps = load_caps(shipped)
    check("shipped policy declares 20 rounds · 1800 s · 80 calls · K=3",
          caps == {"max_rounds": 20, "max_wall_s": 1800, "max_calls": 80, "no_progress_rounds": 3})
    print(f"RESULT: agent-budget selftest {'GREEN' if ok[0] == 0 else f'RED — {ok[0]} failure(s)'}")
    return 1 if ok[0] else 0


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="python3 -m symbiont.agent_tasks", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="run ONE agent task under the declared caps; ledger -> runs/<id>.json")
    r.add_argument("--task", required=True, help=f"{' | '.join(sorted(BUILTINS))} | py:<path>")
    r.add_argument("--store", default=DEFAULT_STORE)
    r.add_argument("--run-id", default=None)
    r.add_argument("--policy", default=DEFAULT_POLICY)
    r.add_argument("--question", default="")
    sub.add_parser("selftest", help="the three fixtures + the wall cap, assert-only, throwaway stores")
    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        return selftest(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"))
    if a.cmd != "run":
        ap.print_help()
        return 2
    try:
        task = _load_task(a.task)
    except TaskRefused as e:
        say(f"REFUSED[{e.code}]  {e.reason}")
        return 2
    os.makedirs(os.path.join(a.store, "runs"), exist_ok=True)
    rec, code = run_task(task, a.store, run_id=a.run_id, policy_path=a.policy, question=a.question)
    led = rec["agent_tasks"][0]
    t = led["totals"]
    say(f"TASK {rec['run_id']}  {rec['task']}  rounds={t['rounds']}  calls={t['calls']}  "
        f"tokens={t['tokens']}  bytes={t['bytes']}  wall={t['wall_seconds']}s  "
        f"new_accessions={t['new_accessions']}  new_records={t['new_records']}  exit={led['exit']}")
    if led["refused"]:
        say(f"REFUSED[{led['refused']['code']}]  {led['refused']['reason']}")
    say(f"LEDGER  {os.path.join(a.store, 'runs', rec['run_id'] + '.json')}")
    return code


if __name__ == "__main__":
    sys.exit(main())
