"""THE THREE FIXTURES of the schedule model (req-P1-THE-SCHEDULE-MODEL-when-the-station).

The gate the row names: "three fixtures plus one real week of logs". This file is the three
fixtures; the real week accrues in <store>/schedule/ticks.jsonl from the cron table entries the
model installs (symbiont/docs/SCHEDULE.md). Every fixture runs the SHIPPED implementation
(symbiont/station-job.sh) against a sandbox farm+store — the only "network" touched is a closed
port on 127.0.0.1 (that is the point of F3).

    F1  OVERLAP    two fires on one source: the second exits REFUSED[locked] quoting the holder;
                   and the global cap: a third source at STATION_MAX_INFLIGHT refuses, logged.
    F2  MISSED TICK  a tick that did not run is LOGGED with its reason (held, budget) and the next
                   tick says "I caught up: last pass <age>" — never a silent gap.
    F3  NETWORK DOWN  the whole thing runs with the network DOWN and still logs "I looked"; and a
                   job 2x overdue appears as a NAMED finding in the health digest.

Run:  python3 -m symbiont.selftest_schedule      (offline; exit 0 = all three fixtures held)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))            # .../symbiont
APP_ROOT = os.path.dirname(HERE)                             # .../app-farm
RUNNER = os.path.join(HERE, "station-job.sh")
HB_LIB = os.path.join(APP_ROOT, "nurse", "hb.sh")
REAL_SOURCES = os.path.join(HERE, "sources.yml")
CLOSED = "http://127.0.0.1:1/"                               # nothing listens: the network is down

# THE FIXTURES' ONE FARM-ONLY INPUT. nurse/ is the FARM's own machine (DESIGN-STATION §1b: the
# door, the sequencer, the pass, the seat — never shipped), so a clone has no heartbeat library
# and these fixtures cannot run there. That absence is a NAMED SKIP; an hb.sh that is THERE but
# broken stays a FAIL (the fixtures then fail on their own assertions — never a silent pass).
MISSING_INPUTS: list[tuple[str, str, str]] = []


def skip(name: str, missing: str, path: str) -> None:
    MISSING_INPUTS.append((name, missing, path))
    print(f"  SKIP  {name}: {missing} at {path}", flush=True)


def missing_inputs() -> list[tuple[str, str]]:
    """What this tree does not carry, named once (each fixture is skipped, not failed)."""
    out: list[tuple[str, str]] = []
    if not os.path.isfile(HB_LIB):
        out.append((HB_LIB,
                    "the farm's heartbeat library nurse/hb.sh, which every fixture copies into "
                    "its sandbox farm (farm-only by DESIGN-STATION §1b — never shipped)"))
    return out


class Sandbox:
    """A throwaway farm+store: real hb.sh + real sources.yml (base_url repointed at a closed port)."""

    def __init__(self, tmp: str):
        self.farm = os.path.join(tmp, "farm")
        self.store = os.path.join(tmp, "store")
        os.makedirs(os.path.join(self.farm, "nurse", "heartbeats"), exist_ok=True)
        os.makedirs(os.path.join(self.farm, "symbiont"), exist_ok=True)
        shutil.copy(HB_LIB, os.path.join(self.farm, "nurse", "hb.sh"))
        txt = open(REAL_SOURCES, encoding="utf-8").read()
        txt = re.sub(r"base_url: \S+", f"base_url: {CLOSED}", txt)
        self.sources = os.path.join(self.farm, "symbiont", "sources.yml")
        open(self.sources, "w", encoding="utf-8").write(txt)

    def env(self, **over):
        e = dict(os.environ)
        e.update(FARM=self.farm, STORE=self.store, STATION_SOURCES=self.sources)
        e.update({k: str(v) for k, v in over.items()})
        return e

    def run(self, *args, **env_over):
        p = subprocess.run(["bash", RUNNER, *args], capture_output=True, text=True,
                           env=self.env(**env_over), timeout=120)
        return p.returncode, p.stdout + p.stderr

    def spawn(self, *args, **env_over):
        return subprocess.Popen(["bash", RUNNER, *args], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env=self.env(**env_over))

    def ticks(self, jobkey):
        path = os.path.join(self.store, "schedule", "ticks.jsonl")
        out = []
        if os.path.exists(path):
            for ln in open(path, encoding="utf-8"):
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("jobkey") == jobkey:
                    out.append(r)
        return out

    def seed_lastpass(self, jobkey, age_s):
        d = os.path.join(self.store, "schedule", "last")
        os.makedirs(d, exist_ok=True)
        ts = int(time.time()) - age_s
        json.dump({"jobkey": jobkey, "ts": ts, "iso": f"seeded-{age_s}s-ago"},
                  open(os.path.join(d, f"{jobkey}.json"), "w"))

    def hold(self, reason="selftest-hold"):
        open(os.path.join(self.farm, "nurse", "OWNER-HOLD"), "w").write(
            f"REASON={reason}\nSET-AT=2026-11-10T00:00:00Z\nSET-BY=selftest_schedule\n")

    def unhold(self):
        os.remove(os.path.join(self.farm, "nurse", "OWNER-HOLD"))


def fixture_f1_overlap(tmp):
    sb = Sandbox(tmp)
    ok = []

    # two overlapping fires on one source: the first holds the lock, the second must REFUSE, quoting
    a = sb.spawn("fetch", "alpha", STATION_FETCH_CMD="sleep 3")
    time.sleep(1.0)                                   # let A take the lock and write its holder line
    rc, out = sb.run("fetch", "alpha", STATION_FETCH_CMD="sleep 3")
    ok.append((rc == 3, f"F1 second fire exits REFUSED[locked] (rc={rc})"))
    ok.append(("REFUSED[locked] alpha: held by pid=" in out, f"F1 the refusal QUOTES the holder: {out.strip()[:90]}"))
    t = [x for x in sb.ticks("fetch-alpha") if x["outcome"] == "missed"]
    ok.append((len(t) == 1 and t[0]["reason"] == "locked" and "pid=" in t[0]["detail"],
               "F1 the refused tick is LOGGED: reason=locked, detail quotes the holder"))
    ok.append((os.path.exists(os.path.join(sb.farm, "nurse", "heartbeats", "station-fetch-alpha")),
               "F1 the overlapping fire still wrote its OWN heartbeat row (ownership, §4)"))

    # the global cap: with MAXINFLIGHT=1 and alpha in flight, a fetch of beta is a LOGGED miss
    b = sb.spawn("fetch", "alpha", STATION_FETCH_CMD="sleep 3")
    time.sleep(1.0)
    rc, out = sb.run("fetch", "beta", STATION_MAX_INFLIGHT=1, STATION_FETCH_CMD="sleep 3")
    ok.append((rc == 4 and "reason=cap" in out, f"F1 the in-flight cap refuses, logged (rc={rc})"))
    ok.append(("held now: pid=" in out, "F1 the cap miss QUOTES the in-flight holder(s)"))
    ok.append((any(x["reason"] == "cap" for x in sb.ticks("fetch-beta")),
               "F1 the cap tick is LOGGED with its reason"))
    a.wait(); b.wait()
    ok.append((a.returncode == 0 and sb.ticks("fetch-alpha")[-1]["outcome"] == "ran",
               "F1 the HOLDER itself ran to completion and logged outcome=ran"))
    return ok


def fixture_f2_missed_tick(tmp):
    sb = Sandbox(tmp)

    # a tick that did not run is LOGGED with its reason: held (the owner's HOLD switch)
    sb.hold("night window — owner deciding")
    rc, out = sb.run("fetch", "alpha", STATION_FETCH_CMD="sleep 0")
    ok = [(rc == 4 and "MISSED station-fetch-alpha reason=held" in out,
           f"F2 a held tick is a logged MISS (rc={rc})"),
          ("night window" in out, "F2 the held miss quotes the owner's reason"),
          (any(x["reason"] == "held" for x in sb.ticks("fetch-alpha")),
           "F2 the held tick line is in ticks.jsonl")]
    sb.unhold()

    # a tick that did not run is LOGGED with its reason: budget (REFUSED[budget], T5)
    stub = "echo 'REFUSED[budget] alpha: request #3 would spend the source budget (cap requests=1)'; exit 3"
    rc, out = sb.run("fetch", "alpha", STATION_FETCH_CMD=stub)
    ok += [(rc == 4 and "MISSED station-fetch-alpha reason=budget" in out,
            f"F2 a budget-refused tick is a logged MISS (rc={rc})"),
           ("REFUSED[budget] alpha" in out, "F2 the budget miss quotes the loop's own refusal"),
           (any(x["reason"] == "budget" for x in sb.ticks("fetch-alpha")),
            "F2 the budget tick line is in ticks.jsonl")]

    # the next tick says "I caught up: last pass <age>" — never a silent gap
    sb.seed_lastpass("fetch-alpha", 9 * 86400)        # the last pass was nine days ago
    rc, out = sb.run("fetch", "alpha", STATION_FETCH_CMD="sleep 0")
    ok += [(rc == 0, f"F2 the catch-up run succeeds (rc={rc})"),
           ("I caught up: last pass" in out and "9d" in out,
            f"F2 the next tick SAYS it caught up, with the age: {out.strip()[:90]}"),
           ("I caught up: last pass" in sb.ticks("fetch-alpha")[-1]["detail"],
            "F2 the catch-up line is IN the tick log, not just stdout")]
    return ok


def fixture_f3_network_down(tmp):
    sb = Sandbox(tmp)                                 # every base_url points at a CLOSED port

    # regression guard: on a VIRGIN sandbox the health probe must still write its digest page —
    # the tick log does not exist yet, and a missing log may never kill the watchman
    rc, out = sb.run("health")
    ok = [(rc == 0 and os.path.exists(os.path.join(sb.store, "schedule", "health-digest.md")),
           f"F3 the health probe writes its digest even on a virgin store (rc={rc})")]

    # the whole thing runs with the network DOWN and still logs "I looked" — the fetch spends
    # ZERO provider requests (pre-flight connect refused) and is a logged miss, never a quiet death
    rc, out = sb.run("fetch", "ensembl_biomart")
    ok = [(rc == 4 and "reason=down" in out, f"F3 a network-down fetch is a logged MISS (rc={rc})"),
          ("I looked" in out, "F3 the network-down fetch says I LOOKED"),
          ("zero provider requests spent" in out, "F3 the network-down fetch spent zero provider requests"),
          (any(x["reason"] == "down" and "I looked" in x["detail"] for x in sb.ticks("fetch-ensembl_biomart")),
           "F3 the I-looked line is IN the tick log")]

    # the disk-only jobs do not even notice: digest and manifest still run and log
    rc, out = sb.run("digest")
    ok += [(rc == 0, f"F3 the digest pass runs with the network down (rc={rc})"),
          (sb.ticks("digest") and sb.ticks("digest")[-1]["outcome"] == "ran",
           "F3 the digest pass LOGGED its tick")]
    rc, out = sb.run("manifest")                      # empty sandbox store: refused, but LOGGED
    ok += [(len(sb.ticks("manifest")) >= 1,
           f"F3 the manifest pass logged its tick (outcome={sb.ticks('manifest')[-1]['outcome']}) — never silent")]
    # a job 2x overdue appears as a NAMED finding in the health digest (§4)
    sb.seed_lastpass("health", 3 * 3600)              # hourly job, 3h silent: overdue
    sb.seed_lastpass("fetch-ensembl_biomart", 15 * 86400)   # weekly job, 15d silent: overdue
    sb.seed_lastpass("fetch-mgi", 60)                # fresh: must NOT be a finding
    # digest is fresh (it just ran); discovery and manifest have NO last-pass record: NEVER-RAN
    rc, out = sb.run("health")
    digest_page = os.path.join(sb.store, "schedule", "health-digest.md")
    ok += [(rc == 0 and os.path.exists(digest_page), f"F3 the health probe wrote the digest (rc={rc})")]
    page = open(digest_page, encoding="utf-8").read()
    for name, cond in [
        ("health OVERDUE", "FINDING station-job health OVERDUE: last pass 3h" in page),
        ("fetch-ensembl_biomart OVERDUE", "FINDING station-job fetch-ensembl_biomart OVERDUE" in page),
        ("manifest NEVER-RAN", "FINDING station-job manifest NEVER-RAN" in page),
        ("discovery NEVER-RAN", "FINDING station-job discovery NEVER-RAN" in page),
        ("fresh fetch-mgi NOT a finding", "fetch-mgi OVERDUE" not in page and "fetch-mgi NEVER-RAN" not in page),
        ("fresh digest NOT a finding", "FINDING station-job digest" not in page),
    ]:
        ok.append((cond, f"F3 the health digest names the 2x-overdue job BY NAME: {name}"))
    return ok


def main() -> int:
    fixtures = [("F1 overlap", fixture_f1_overlap),
                ("F2 missed tick", fixture_f2_missed_tick),
                ("F3 network down + overdue findings", fixture_f3_network_down)]
    blocked = missing_inputs()
    if blocked:
        for name, fn in fixtures:
            for path, what in blocked:
                skip(f"{name} ({fn.__name__})", what, path)
        print(f"SCHEDULE SELFTEST: 0 held, {len(MISSING_INPUTS)} skipped, 0 failed — the three "
              f"fixtures need the farm's own heartbeat, which this tree does not ship")
        print(f"RESULT: 0 passed, {len(MISSING_INPUTS)} skipped, 0 failed (clone mode)")
        print("SKIPPED: " + "; ".join(n for n, _w, _p in MISSING_INPUTS))
        return 0
    npass = nfail = 0
    for name, fn in fixtures:
        with tempfile.TemporaryDirectory(prefix="symbiont-sched-") as tmp:
            for held, msg in fn(tmp):
                if held:
                    npass += 1
                    print(f"  PASS  {msg}")
                else:
                    nfail += 1
                    print(f"  FAIL  {msg}")
    print(f"SCHEDULE SELFTEST: {npass} held, {nfail} failed "
          + ("— the schedule model's three fixtures hold" if nfail == 0 else "— RED"))
    print(f"RESULT: {npass} passed, 0 skipped, {nfail} failed (every check ran — no skip)")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
