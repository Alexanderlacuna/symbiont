"""THE SENTINEL CYCLE — symbiont/cycle.py (req-P1-THE-SENTINEL-CYCLE-the-stations-own, 2026-11-11).

Design: reports/DESIGN-SENTINEL.md. Config: symbiont/cycle.yml (shipped `enabled: false`).

WHY THIS FILE EXISTS. Every organ of the station was built and tested — the four digests (41 KB),
health (26 KB), the admission policy (20/20 fixtures), the sandbox (17/17), the logged discovery
sweep (12 KB) — and NOTHING ran them as one bounded loop, so the station could not keep itself
current without a hand. This file is that loop and nothing else: it ORCHESTRATES the tested CLIs in
one order, on one budget, and writes one report. The steps are the tested code, called as the words
an operator would type (`python3 -m symbiont.admission …`), so the cycle and the tools can never
drift apart.

    python3 -m symbiont.cycle --mode watch|discover|both   one bounded run
    python3 -m symbiont.cycle status                        what a machine door's cycle_status reads
    python3 -m symbiont.cycle selftest                      the seven acceptance cases (DESIGN §8)

THE ORDER (DESIGN §3), each step's refusal class named:

    1 TELEMETRY   the digests under <store>/digest — a digest with no numbers is not a measurement
                  REFUSED[digest-no-numbers]
    2 DISCOVER    over the deployment's OWN surface registry, listed queries only, each logged with
                  its count. REFUSED[no-surfaces] with no registry (it never invents where to look),
                  REFUSED[surface-kind-unknown], REFUSED[budget] (stops the cycle),
                  REFUSED[network-down] / REFUSED[rate-too-low] per query (recorded, run continues)
    3 EVALUATE    per candidate: the licence read from the surface's API · a count endpoint · the
                  rate limit · archived · dead. REFUSED[licence-unreadable] · REFUSED[archived] ·
                  REFUSED[dead-repo] · REFUSED[no-count-endpoint]
    4 DRAFT       only when draft.provider is configured. None configured -> the candidate is
                  PROPOSED (reported, not built). REFUSED[draft-no-output] · REFUSED[licence-unreadable]
    5 SANDBOX     the draft runs in the tested throwaway store with the cycle's budget, and ITS COUNT
                  IS VERIFIED BY THE CYCLE against the source's own count — never against the draft's
                  self-report. REFUSED[count-mismatch] · REFUSED[count-unreadable] · REFUSED[budget]
    6 CARD        the admission card (licence · counts · sample rows · xref rate · cost · question),
                  which registers the source in sources.yml as enabled:false
    7 ADMIT       ONLY IF admit.by_policy AND every required card check is green AND the source is
                  stable for N cycles — and N is read from the owner's policy
                  (reports/symbiont-policy.yml), never from this file and never invented. The
                  admission record quotes the rule and N: "by policy, rule
                  stable_cycles_before_self_admit, N=<n>". by_policy:false (the shipped default)
                  means the cycle PROPOSES and a person admits.
    8 REPORT      found · refused (rule named) · drafted · sandboxed · admitted · cost — and
                  "I looked: <surfaces> <queries> 0 new" on a quiet week. A cycle that changes
                  nothing still prints, every time.

THE GOVERNANCE GATE (§7). The council question "May the SHIPPED station admit a data source by
policy with no human in the runtime loop?" is PENDING — asked 2026-11-11 01:45, the record was
DOWNGRADED TO PARKED (single-family, review failed). Until a ruling PERMITS it, `admit.by_policy:
true` is REFUSED BY NAME before any work, quoting the ruling file it read and why it does not count.
The cycle does not decide that question; it refuses to pretend it was answered.

WHAT IT MAY NEVER DO (§4): invent a surface · admit a source whose licence it could not read · admit
anything that failed its count check · spend past its budget · edit the law, fire lanes or the farm's
queue · call a provider the deployment did not configure.

EXIT CODES
    0  the run completed — a quiet week is a completion, and a candidate refused by its own rule is
       the cycle doing its job
    2  REFUSED before any work: cycle.yml missing/unreadable/malformed (the parse error is named),
       an unknown key, a bad mode/budget, admit.by_policy with no ruling on record, no surface
       registry, a surface kind this version does not read
    3  the run STOPPED, its reason named and recorded: budget spent mid-cycle, the digests do not
       hold, or the network was down for every query (the "I looked" line is still printed)
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

from .http import Fetcher, FetchError, HttpStatus
from . import admission_policy, contract

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CYCLE = os.path.join(FARM, "symbiont", "cycle.yml")
DEFAULT_CYCLE_STATE = os.path.join(FARM, "symbiont", "store", "cycle")

EXIT_OK, EXIT_REFUSED, EXIT_STOPPED = 0, 2, 3

CYCLE_KEYS = ("version", "enabled", "mode", "every", "budget", "rate_limit", "store", "report_dir",
              "state_dir", "admission", "surfaces", "draft", "admit")
BUDGET_KEYS = ("requests", "bytes", "wall_s")
RATE_KEYS = ("requests_per_minute", "min_interval_s")
SURFACE_KEYS = ("registry", "kind", "endpoint", "per_page", "dead_days", "seed_topics")
ADMISSION_KEYS = ("dir", "sources_config", "drafts_dir", "adapters_dir", "gate_file", "policy")
DRAFT_KEYS = ("provider",)
ADMIT_KEYS = ("by_policy", "ruling")
MODES = ("watch", "discover", "both")
KINDS = ("github_search", "listed")
DEAD_STATUS = (403, 429)                 # the surface says our rate budget is spent
RULING_FORBIDDEN = ("PARKED", "DOWNGRADED", "REJECTED", "UNREVIEWED", "SPLIT", "REFUSED")


class CycleRefused(RuntimeError):
    """The cycle refuses to go on, for a reason that has a name. `fatal` stops the whole run."""

    def __init__(self, code: str, detail: str, fatal: bool = True):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.fatal = fatal


def _need(cond: bool, msg: str, code: str = "cycle-schema") -> None:
    if not cond:
        raise CycleRefused(code, msg)


def _stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _sha_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _rel(path: str) -> str:
    try:
        return os.path.relpath(path, FARM)
    except ValueError:
        return path


# ═══════════════════════════════════════════════════════════ 0 · THE CONFIG (refuses, never guesses)
def load_cycle(path: str = DEFAULT_CYCLE) -> dict:
    """Parse + validate cycle.yml. Every way the file can be wrong is REFUSED BY NAME, and a
    malformed file names the parser's own error: a cycle that guesses at its budget or its admission
    default is an unbounded loop with good manners."""
    import yaml
    if not os.path.isfile(path):
        raise CycleRefused("cycle-missing",
                           f"the cycle config {path} is missing — the cycle refuses to start "
                           f"(it will not invent a budget, a surface or an admission default)")
    try:
        with open(path, "rb") as fh:
            raw_bytes = fh.read()
    except OSError as e:
        raise CycleRefused("cycle-unreadable",
                           f"the cycle config {path} could not be read "
                           f"({type(e).__name__}: {e}) — the cycle refuses to start") from e
    try:
        raw = yaml.safe_load(raw_bytes.decode("utf-8", "strict"))
    except (UnicodeDecodeError, yaml.YAMLError) as e:
        raise CycleRefused("cycle-malformed",
                           f"the cycle config {path} is MALFORMED — the cycle refuses to start, the "
                           f"parse error named: {type(e).__name__}: {e}") from e
    _need(isinstance(raw, dict), f"{path}: the top level must be a mapping with the keys "
                                 f"{list(CYCLE_KEYS)}")
    unknown = [k for k in raw if k not in CYCLE_KEYS]
    _need(not unknown, f"{path}: unknown top-level key(s) {sorted(unknown)} — the schema is "
                       f"{list(CYCLE_KEYS)}")
    missing = [k for k in CYCLE_KEYS if k not in raw]
    _need(not missing, f"{path}: missing key(s) {missing} — the cycle refuses to invent them")
    _need(raw["version"] == 1, f"{path}: unsupported version {raw['version']!r} (this code reads 1)")
    _need(isinstance(raw["enabled"], bool),
          f"{path}: `enabled` must be true or false, got {raw['enabled']!r}")
    _need(raw["mode"] in MODES, f"{path}: `mode` must be one of {list(MODES)}, got {raw['mode']!r}")
    _need(isinstance(raw["every"], str) and raw["every"],
          f"{path}: `every` must name the cadence (e.g. weekly)")

    b = raw["budget"]
    _need(isinstance(b, dict) and sorted(b) == sorted(BUDGET_KEYS),
          f"{path}: `budget` must be exactly {list(BUDGET_KEYS)}, got {b!r}")
    for k in BUDGET_KEYS:
        _need(isinstance(b[k], int) and not isinstance(b[k], bool) and b[k] >= 1,
              f"{path}: budget.{k} must be a positive integer, got {b[k]!r}")
    r = raw["rate_limit"]
    _need(isinstance(r, dict) and sorted(r) == sorted(RATE_KEYS),
          f"{path}: `rate_limit` must be exactly {list(RATE_KEYS)}, got {r!r}")

    s = raw["surfaces"]
    _need(isinstance(s, dict), f"{path}: `surfaces` must be a mapping")
    unknown = [k for k in s if k not in SURFACE_KEYS]
    _need(not unknown, f"{path}: surfaces carries unknown key(s) {sorted(unknown)} — the schema is "
                       f"{list(SURFACE_KEYS)}")
    _need(isinstance(s.get("registry"), str) and s["registry"],
          f"{path}: surfaces.registry must name the deployment's OWN registry — the cycle never "
          f"invents where to look")

    a = raw["admission"]
    _need(isinstance(a, dict) and sorted(a) == sorted(ADMISSION_KEYS),
          f"{path}: `admission` must be exactly {list(ADMISSION_KEYS)}, got {sorted(a)}")
    d = raw["draft"]
    _need(isinstance(d, dict) and sorted(d) == sorted(DRAFT_KEYS),
          f"{path}: `draft` must be exactly {list(DRAFT_KEYS)}, got {sorted(d)}")
    _need(isinstance(d["provider"], str), f"{path}: draft.provider must be a string (empty = no drafting)")
    ad = raw["admit"]
    _need(isinstance(ad, dict) and sorted(ad) == sorted(ADMIT_KEYS),
          f"{path}: `admit` must be exactly {list(ADMIT_KEYS)}, got {sorted(ad)}")
    _need(isinstance(ad["by_policy"], bool),
          f"{path}: admit.by_policy must be true or false, got {ad['by_policy']!r}")
    _need(isinstance(ad["ruling"], str), f"{path}: admit.ruling must be a path or an empty string")
    for k in ("store", "report_dir", "state_dir"):
        _need(isinstance(raw[k], str) and raw[k], f"{path}: {k} must be a path")
    raw["_path"] = os.path.abspath(path)
    raw["_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    return raw


def _path(cfg: dict, key: str, sub: str | None = None) -> str:
    """A path from the config, resolved against the farm (a deployment ships relative paths)."""
    p = cfg[key] if sub is None else cfg[key][sub]
    return p if os.path.isabs(p) else os.path.join(FARM, p)


def _ruling_path(cfg: dict) -> str:
    """The ruling path, or "" when none is on record (never the farm directory by accident)."""
    p = (cfg.get("admit") or {}).get("ruling") or ""
    return "" if not p else (p if os.path.isabs(p) else os.path.join(FARM, p))


# ═══════════════════════════════════════════════════════════ 1 · THE GOVERNANCE GATE (§7)
def read_ruling(path: str) -> tuple[bool, str]:
    """Is there a ruling ON RECORD that PERMITS admit-by-policy? -> (permits, why-not).

    The question the court was asked is a constitutional one, so the test is deliberately narrow:
    the file must exist, must carry a DECISION (or RULING) line that chooses option A — the option
    that ships admit-by-policy OFF BY DEFAULT — and must not carry a STATUS line that parks,
    downgrades, rejects or leaves it unreviewed. Anything else is NOT a permission, and the cycle
    says which of those it found, quoting the line it read."""
    if not path:
        return False, ("no ruling is on record (admit.ruling is empty) — the council question 'May "
                       "the SHIPPED station admit a data source by policy with no human in the "
                       "runtime loop?' is PENDING (reports/RULINGS/20261111-014502-*), so "
                       "admit.by_policy: true is REFUSED by name")
    if not os.path.isfile(path):
        return False, f"the ruling {path} is not on disk — a permission that cannot be read is not a permission"
    text = open(path, encoding="utf-8", errors="replace").read()
    status_line, decision_line = "", ""
    for line in text.splitlines():
        m = re.match(r"^#{0,3}\s*(STATUS|DECISION|RULING)\s*:\s*(.+?)\s*$", line)
        if not m:
            continue
        if m.group(1) == "STATUS" and not status_line:
            status_line = m.group(2)
        elif m.group(1) in ("DECISION", "RULING") and not decision_line:
            decision_line = m.group(2)
    if not decision_line:
        return False, (f"the ruling {path} carries no DECISION/RULING line — it is not a ruling "
                       f"this cycle can quote")
    bad = [w for w in RULING_FORBIDDEN if w in decision_line.upper() or w in status_line.upper()]
    if bad:
        return False, (f"the ruling {path} does not permit admit-by-policy: STATUS: {status_line or '(none)'} · "
                       f"DECISION: {decision_line} (found {', '.join(bad)}) — the ruling is not a "
                       f"permission this cycle may act on")
    if not re.match(r"^A\b", decision_line.strip(), re.I):
        return False, (f"the ruling {path} decided {decision_line!r}, not option A (ship "
                       f"admit-by-policy off by default) — admit.by_policy: true is REFUSED by name")
    return True, f"{_rel(path)}: DECISION: {decision_line} · STATUS: {status_line or '(none)'}"


# ═══════════════════════════════════════════════════════════ 2 · THE RUN
class Run:
    """One bounded run: its budget, its ledger, its report lines, its refusals."""

    def __init__(self, cfg: dict, mode: str):
        self.cfg = cfg
        self.mode = mode
        self.run_id = "cycle-" + _stamp()
        self.started_at = _utc()
        self.store = _path(cfg, "store")
        self.report_dir = _path(cfg, "report_dir")
        self.state_dir = _path(cfg, "state_dir")
        self.admission = {k: _path(cfg, "admission", k) for k in ADMISSION_KEYS}
        self.report_path = os.path.join(self.report_dir, f"CYCLE-REPORT-{_stamp()}.md")
        self.lines: list[str] = []
        self.found: list[dict] = []
        self.refused: list[dict] = []
        self.drafted: list[str] = []
        self.sandboxed: list[dict] = []
        self.carded: list[str] = []
        self.admitted: list[dict] = []
        self.proposed: list[dict] = []
        self.telemetry: list[dict] = []
        self.queries: list[dict] = []
        self.looked = dict(surfaces=0, queries=0, new=0, errors=0)
        self.steps_done: list[str] = []
        self.step_now = ""           # the step running when a stop happens (named in the record)
        self.stopped = ""            # the class that stopped the run ("" = completed)
        self.stop_detail = ""
        self.last_run_note = ""
        self.fetcher = Fetcher(budget=dict(cfg["budget"]), rate_limit=dict(cfg["rate_limit"]),
                               deadline_s=int(cfg["budget"]["wall_s"]))
        self._read_last_run()

    # -- ledger -----------------------------------------------------------------------------
    def say(self, line: str) -> None:
        print(line, flush=True)
        self.lines.append(line)

    def refuse(self, code: str, detail: str, fatal: bool = True) -> None:
        self.refused.append(dict(code=code, detail=detail, at=_utc()))
        if fatal:
            raise CycleRefused(code, detail, True)

    def note_refusal(self, code: str, detail: str) -> None:
        """A refusal that does NOT stop the run (one query, one candidate) — recorded, printed."""
        self.refused.append(dict(code=code, detail=detail, at=_utc()))
        self.say(f"REFUSED[{code}] {detail}")

    @property
    def cost(self) -> dict:
        return dict(requests=len(self.fetcher.log), bytes_in=self.fetcher.bytes_in,
                    wall_s=self.fetcher.wall_seconds, rate_limit_waits=self.fetcher.waits)

    @property
    def budget_left(self) -> dict:
        b = self.cfg["budget"]
        return dict(requests=b["requests"] - len(self.fetcher.log),
                    bytes=b["bytes"] - self.fetcher.bytes_in,
                    wall_s=round(b["wall_s"] - self.fetcher.wall_seconds, 1))

    # -- the previous run (A6: the next cycle says how much it did) ----------------------------
    def _read_last_run(self) -> None:
        p = os.path.join(self.state_dir, "last.json")
        if not os.path.isfile(p):
            return
        try:
            rec = json.load(open(p, encoding="utf-8"))
        except Exception:
            return
        self.last_run_note = (f"{rec.get('run_id', '?')} mode={rec.get('mode', '?')} "
                              f"outcome={rec.get('outcome', '?')} steps={','.join(rec.get('steps_done') or []) or 'none'} "
                              f"({rec.get('started_at', '?')})")

    # -- HTTP, charged against this run's ONE budget ------------------------------------------
    def get_text(self, url: str, note: str) -> str:
        try:
            return self.fetcher.get_text(url, note=note)
        except contract.BudgetExceeded as e:
            raise CycleRefused("budget", f"the cycle's own budget is spent mid-run ({e}) — the run "
                                         f"stops here and records why") from e
        except HttpStatus as e:
            code = "rate-too-low" if e.status in DEAD_STATUS else f"http-{e.status}"
            raise CycleRefused(code, str(e), fatal=False) from e
        except FetchError as e:
            raise CycleRefused("network-down", str(e), fatal=False) from e

    def get_json(self, url: str, note: str) -> dict:
        txt = self.get_text(url, note)
        try:
            return json.loads(txt)
        except Exception as e:
            raise CycleRefused("surface-unreadable",
                               f"{url} answered something that is not JSON ({type(e).__name__}); "
                               f"the surface's own words: {txt[:120]!r}", fatal=False) from e


# ═══════════════════════════════════════════════════════════ 3 · THE REGISTRY (never invented)
def load_registry(cfg: dict) -> list[dict]:
    """The deployment's OWN surface registry -> [{id, kind, queries|candidates, ...}].

    Two forms, both "listed only": a plain line-based registry (one query per line, `#` comments —
    the shape tools/discover-sweep.queries.txt already has) is a single github_search surface; a
    .yml registry names its surfaces explicitly, each with its kind and either its queries or its
    own listed candidates (with the count endpoint its counts must come from)."""
    import yaml
    path = _path(cfg, "surfaces", "registry")
    if not os.path.isfile(path):
        raise CycleRefused("no-surfaces",
                           f"the surface registry {path} is missing — the cycle does not invent "
                           f"where to look")
    s = cfg["surfaces"]
    if os.path.splitext(path)[1].lower() in (".yml", ".yaml"):
        try:
            raw = yaml.safe_load(open(path, encoding="utf-8"))
        except yaml.YAMLError as e:
            raise CycleRefused("registry-malformed",
                               f"the surface registry {path} is MALFORMED — the parse error named: {e}")
        _need(isinstance(raw, dict) and isinstance(raw.get("surfaces"), list) and raw["surfaces"],
              f"{path}: a structured registry needs a non-empty `surfaces` list", "no-surfaces")
        out = []
        for i, surf in enumerate(raw["surfaces"]):
            _need(isinstance(surf, dict) and surf.get("id"), f"{path}: surfaces[{i}] has no id",
                  "surface-schema")
            out.append(dict(surf))
        return out

    queries: list[str] = []
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line and not line.startswith("#"):
            queries.append(line)
    if not queries:
        raise CycleRefused("no-surfaces",
                           f"the surface registry {path} lists no query — an empty registry is not "
                           f"a place to look")
    for k in ("kind", "endpoint"):
        _need(isinstance(s.get(k), str) and s[k],
              f"{cfg['_path']}: a plain registry needs surfaces.{k} (it names how its queries "
              f"are read) — the cycle will not invent it", "no-surfaces")
    for k in ("per_page", "dead_days", "seed_topics"):
        _need(isinstance(s.get(k), int) and not isinstance(s[k], bool) and s[k] >= 1,
              f"{cfg['_path']}: a plain registry needs surfaces.{k} as a positive integer",
              "no-surfaces")
    return [dict(id=os.path.basename(path), kind=s["kind"], endpoint=s["endpoint"],
                 queries=queries, per_page=s["per_page"], dead_days=s["dead_days"],
                 seed_topics=s["seed_topics"])]


def _expand_seed(run: Run, surface: dict, line: str) -> list[str]:
    """`seed:owner/repo` -> the repo's first `seed_topics` topics as queries (the sweep's own form).
    An unreadable seed is a NAMED refusal, not a silent skip: it was listed, so it was looked for."""
    owner_repo = line.split(":", 1)[1].strip()
    url = "https://api.github.com/repos/" + owner_repo
    try:
        body = run.get_json(url, note=f"seed {owner_repo}")
    except CycleRefused as e:
        run.note_refusal(e.code, f"seed {owner_repo}: {e.detail}")
        run.looked["errors"] += 1
        return []
    topics = body.get("topics") or []
    return [f"topic:{t}" for t in topics[: int(surface.get("seed_topics") or 3)]]


def _seen_path(run: Run) -> str:
    return os.path.join(run.state_dir, "seen.jsonl")


def _seen_load(run: Run) -> set[str]:
    p = _seen_path(run)
    if not os.path.isfile(p):
        return set()
    out = set()
    for line in open(p, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("key"):
            out.add(rec["key"])
    return out


# ═══════════════════════════════════════════════════════════ 4 · THE STEPS
def step_telemetry(run: Run) -> None:
    """1 · TELEMETRY — the digests, READ ONLY (watch mode writes nothing but its own record).

    THE ONE DEFINITION SITE: `symbiont.digests.verify` decides whether a file is a digest at all —
    "a digest with no numbers is REFUSED (prose is not a digest)", "a count that cannot be
    reproduced by its own query is refused" — so the cycle does not re-invent that rule, it runs
    the tested one over every file in <store>/digest.

    A REFUSED DIGEST IS A NAMED REFUSAL, NOT A DEAD STATION. DESIGN §3 names digest-no-numbers as
    step 1's refusal; this cycle records it, names the file and the rule, and does NOT pretend the
    rest of the station is broken — discovery does not consume the digests, and a station whose
    watch says "this one file is prose" has done its job. (Quoted in the report either way.)"""
    from . import digests
    ddir = os.path.join(run.store, "digest")
    files = sorted(glob.glob(os.path.join(ddir, "*.md")))
    if not files:
        run.telemetry.append(dict(digest="", lines=0,
                                  note=f"no digest under {_rel(ddir)} — nothing measured yet (the "
                                       f"daily pass writes them); the cycle cannot report staleness "
                                       f"it has not measured"))
        run.say(f"TELEMETRY no digest under {_rel(ddir)} — nothing measured yet")
        run.steps_done.append("telemetry(0 digests)")
        return
    held = refused = 0
    for p in files:
        try:
            breaches = digests.verify(p)          # the tested rule, not a second copy of it
        except Exception as e:                    # a verifier that dies is a refusal, not a crash
            breaches = [f"REFUSED[digest-unreadable] {os.path.basename(p)}: "
                        f"{type(e).__name__}: {e}"]
        if breaches:
            refused += 1
            for b in breaches:
                code = _refusal_code(b, "digest-refused")
                run.note_refusal(code, re.sub(r"^REFUSED\[[a-z0-9-]+\]\s*", "", b))
            run.telemetry.append(dict(digest=_rel(p), lines=0,
                                      note="refused as a measurement (named above)"))
            continue
        held += 1
        text = open(p, encoding="utf-8", errors="replace").read()
        run.telemetry.append(dict(digest=_rel(p),
                                  lines=len(re.findall(r"^-\s+[^:\n]+:\s*\S", text, re.M)),
                                  note=""))
    run.say(f"TELEMETRY {held}/{len(files)} digest(s) hold (each count reproduced by its own query)"
            + (f" — {refused} refused by name" if refused else ""))
    run.steps_done.append(f"telemetry({held}/{len(files)} digests hold)")


def _api_candidate(run: Run, surface: dict, item: dict) -> dict:
    """EVALUATE one search result from what the surface's own API says about it."""
    full = item.get("full_name") or ""
    sid = re.sub(r"[^a-z0-9_]+", "_", full.lower().replace("/", "_"))[:40].strip("_") or "candidate"
    cand = dict(id=sid, key=f"github_search:{full}", full_name=full,
                url=item.get("html_url") or "", surface=surface["id"],
                licence=((item.get("license") or {}).get("spdx_id") or ""),
                archived=bool(item.get("archived")), pushed_at=item.get("pushed_at") or "",
                count_endpoint="", question="")
    return cand


def _evaluate(run: Run, surface: dict, cand: dict) -> bool:
    """3 · EVALUATE — the rules, each named. Returns True when the candidate survives."""
    lic = (cand.get("licence") or "").strip()
    if not lic or lic.upper() in ("NOASSERTION", "NONE"):
        run.note_refusal("licence-unreadable",
                         f"{cand['full_name'] or cand['id']}: the API publishes no readable licence "
                         f"({lic or 'null'}) — terms unread = no data (POLICY-THIRD-PARTY §1)")
        return False
    if cand.get("archived"):
        run.note_refusal("archived",
                         f"{cand['full_name']}: archived upstream — a dependency we could not pin")
        return False
    pushed = cand.get("pushed_at") or ""
    if pushed:
        try:
            age = (dt.datetime.now(dt.timezone.utc)
                   - dt.datetime.fromisoformat(pushed.replace("Z", "+00:00"))).days
        except ValueError:
            age = -1
        dead = int(surface.get("dead_days") or 730)
        if age > dead:
            run.note_refusal("dead-repo",
                             f"{cand['full_name']}: no push for {age} day(s) (the surface's own "
                             f"dead_days={dead}) — a dependency that cannot be pinned is a risk")
            return False
    # RATE LIMIT FITS THIS RUN'S BUDGET (DESIGN §3 step 3): a source whose own politeness makes
    # even ONE read cost more wall clock than this run has left is refused NOW, with the
    # arithmetic, not discovered halfway through a sandbox run. The candidate's declared limit is
    # used when the registry names one; otherwise the deployment's own politeness (cycle.yml
    # rate_limit) governs, because it is the deployment doing the reading.
    rl = cand.get("rate_limit") or run.cfg["rate_limit"]
    rpm = float(rl.get("requests_per_minute") or 0)
    gap = float(rl.get("min_interval_s") or 0)
    wall_needed = (60.0 / rpm) if rpm else gap
    left = max(0.0, run.budget_left["wall_s"])
    if wall_needed > left:
        run.note_refusal("rate-too-low",
                         f"{cand['full_name'] or cand['id']}: at {rpm:g} request(s)/minute"
                         f"{f' with {gap:g}s between them' if gap else ''} one read needs "
                         f"~{wall_needed:.1f}s of wall clock and this run has {left:.1f}s left "
                         f"(budget wall_s={run.cfg['budget']['wall_s']}) — the cycle will not "
                         f"start a source it cannot read politely inside its own budget")
        return False
    if not cand.get("count_endpoint"):
        run.note_refusal("no-count-endpoint",
                         f"{cand['full_name'] or cand['id']}: a search result is a repository, not a "
                         f"countable data endpoint — the count that admits a source is the source's "
                         f"OWN row count and this surface publishes none, so the cycle may only "
                         f"propose it (list it in a structured registry with its count_endpoint)")
        return False
    return True


def _read_count(run: Run, cand: dict) -> int:
    """THE CYCLE'S OWN COUNT — never the draft's self-report (a check that trusts the checked
    thing is not a check)."""
    url = cand.get("count_endpoint") or ""
    if not url:
        raise CycleRefused("no-count-endpoint",
                           f"{cand['id']}: no count endpoint — the cycle cannot verify a count it "
                           f"cannot read")
    txt = run.get_text(url, note=f"count {cand['id']}")
    m = re.search(r"-?\d+", txt)
    if not m:
        raise CycleRefused("count-unreadable",
                           f"{cand['id']}: the count endpoint {url} answered no number "
                           f"({txt[:80]!r})")
    return int(m.group(0))


def _cli(run: Run, args: list[str]) -> subprocess.CompletedProcess:
    """Run the tested CLI that DEFINES the step — the cycle is an orchestrator, not a re-write."""
    return subprocess.run([sys.executable, "-m", *args], cwd=FARM, capture_output=True, text=True,
                          timeout=1800)


def _admission_args(run: Run) -> list[str]:
    a = run.admission
    return ["--admission-dir", a["dir"], "--sources-config", a["sources_config"],
            "--drafts-dir", a["drafts_dir"], "--adapters-dir", a["adapters_dir"],
            "--gate-file", a["gate_file"], "--policy", a["policy"], "--store", run.store]


def _step_pipeline(run: Run, cand: dict) -> None:
    """4 DRAFT · 5 SANDBOX · 6 CARD · 7 ADMIT for one candidate that survived evaluation."""
    sid = cand["id"]
    a = run.admission
    # -- 4 DRAFT (probe + evaluate are the tested steps that make a candidate real) -------------
    if not run.cfg["draft"]["provider"]:
        run.proposed.append(dict(id=sid, why="draft.provider is empty — no drafting configured, so "
                                              "nothing was built; a person admits or discards"))
        run.say(f"PROPOSED  {sid}  vetted and PROPOSED (draft.provider empty: nothing drafted, "
                f"nothing admitted)")
        return
    for step, extra in (("probe", ["--id", sid, "--licence-url", cand.get("licence_url", "")]),
                        ("evaluate", []), ("draft", [])):
        args = [step]
        if step == "probe":
            args += [cand["url"]]
        else:
            args += [sid]
        p = _cli(run, ["symbiont.admission", *args, *extra, *_admission_args(run)])
        if p.returncode != 0:
            code = _refusal_code(p.stdout + p.stderr, "draft-refused")
            run.note_refusal(code, f"{sid}: source_{step} exited {p.returncode}: "
                                   f"{(p.stdout or p.stderr).strip().splitlines()[-1][:200]}")
            return
    # the deployment's own provider writes the adapter (it is run with the candidate and the path)
    draft_path = os.path.join(a["drafts_dir"], f"{sid}.py")
    cand_path = os.path.join(a["dir"], sid, "candidate.json")
    before = os.path.getmtime(draft_path) if os.path.isfile(draft_path) else 0.0
    prov = run.cfg["draft"]["provider"]
    p = subprocess.run([prov, cand_path, draft_path], cwd=FARM, capture_output=True, text=True,
                       timeout=1800)
    wrote = os.path.isfile(draft_path) and os.path.getmtime(draft_path) > before
    if p.returncode != 0 or not wrote:
        run.note_refusal("draft-no-output",
                         f"{sid}: the configured provider {prov!r} exited {p.returncode} and "
                         f"{'wrote' if wrote else 'never wrote'} {_rel(draft_path)} — a candidate "
                         f"without a draft is not drafted")
        return
    run.drafted.append(sid)
    run.say(f"DRAFTED   {sid}  provider={prov} -> {_rel(draft_path)}")

    # -- 5 SANDBOX (the tested throwaway store, the cycle's own budget) -------------------------
    budget = run.cfg["budget"]
    p = _cli(run, ["symbiont.admission", "sandbox", sid,
                   "--budget-requests", str(budget["requests"]),
                   "--budget-bytes", str(budget["bytes"]),
                   "--budget-wall-s", str(budget["wall_s"]), *_admission_args(run)])
    if p.returncode != 0:
        code = _refusal_code(p.stdout + p.stderr, "sandbox-refused")
        run.note_refusal(code, f"{sid}: source_sandbox exited {p.returncode}: "
                               f"{(p.stdout or p.stderr).strip().splitlines()[-1][:200]}")
        return
    receipts = sorted(glob.glob(os.path.join(a["dir"], sid, "sandbox-*.receipt.json")))
    if not receipts:
        run.note_refusal("sandbox-no-receipt", f"{sid}: the sandbox wrote no receipt under "
                                                f"{_rel(os.path.join(a['dir'], sid))}")
        return
    receipt = json.load(open(receipts[-1], encoding="utf-8"))
    try:
        theirs = _read_count(run, cand)
    except CycleRefused as e:
        if e.code == "budget":
            raise                       # the cycle's own budget STOPPED it: never a per-candidate note
        run.note_refusal(e.code, f"{sid}: {e.detail}")
        return
    ours = receipt.get("records")
    if ours != theirs:
        run.note_refusal("count-mismatch",
                         f"{sid}: the sandbox produced {ours} record(s) and the source's own count "
                         f"endpoint {cand['count_endpoint']} says {theirs} — nothing carded, "
                         f"nothing admitted (a source that fails its count check never admits, "
                         f"regardless of policy)")
        return
    run.sandboxed.append(dict(id=sid, ours=ours, theirs=theirs, receipt=_rel(receipts[-1]),
                              cost=receipt.get("cost") or {}))
    run.say(f"SANDBOXED {sid}  count VERIFIED ours={ours} = source={theirs} "
            f"({_rel(receipts[-1])})")

    # -- 6 CARD ---------------------------------------------------------------------------------
    p = _cli(run, ["symbiont.admission", "card", sid, "--question", cand.get("question") or "",
                   *_admission_args(run)])
    card = os.path.join(a["dir"], sid, "card.md")
    if p.returncode != 0 or not os.path.isfile(card):
        code = _refusal_code(p.stdout + p.stderr, "card-refused")
        run.note_refusal(code, f"{sid}: source_card exited {p.returncode}: "
                               f"{(p.stdout or p.stderr).strip().splitlines()[-1][:200]}")
        return
    run.carded.append(_rel(card))
    run.say(f"CARDED    {sid}  {_rel(card)}")

    # -- 7 ADMIT — the one gated step -----------------------------------------------------------
    if not run.cfg["admit"]["by_policy"]:
        run.proposed.append(dict(id=sid, why="admit.by_policy is false — the cycle PROPOSES; a "
                                              "person admits (the shipped default)"))
        run.say(f"PROPOSED  {sid}  card is green, admit.by_policy=false: the cycle proposes, a "
                f"person admits")
        return
    checks = _card_checks(card)
    required = _required_checks(run)
    failed = [k for k in required if checks.get(k, "").lower() != "ok"]
    if failed:
        run.note_refusal("card-check",
                         f"{sid}: required card check(s) not green ({', '.join(failed)}) — a source "
                         f"failing one required check never self-admits, regardless of N")
        return
    p = _cli(run, ["symbiont.admission", "admit", sid, *_admission_args(run)])
    if p.returncode != 0:
        code, why = _admit_refusal(p.stdout + p.stderr)
        run.note_refusal(code, f"{sid}: source_admit exited {p.returncode}: {why}")
        return
    rec = os.path.join(a["dir"], sid, "admitted.json")
    line = ""
    if os.path.isfile(rec):
        try:
            line = ((json.load(open(rec, encoding="utf-8")).get("policy") or {}).get("line") or "")
        except Exception:
            line = ""
    run.admitted.append(dict(id=sid, record=_rel(rec), line=line,
                             checks=checks, required=required))
    run.say(f"ADMITTED  {sid}  {line or '(the receipt quotes no rule — FINDING)'} "
            f"record={_rel(rec)}")


def _card_checks(card_path: str) -> dict:
    checks: dict[str, str] = {}
    for m in re.finditer(r"^check[ \t]+([a-z0-9_]+):[ \t]*(\S.*)$",
                         open(card_path, encoding="utf-8", errors="replace").read(), re.M):
        checks[m.group(1)] = m.group(2).strip()
    return checks


def _required_checks(run: Run) -> list[str]:
    """The checks the owner's policy requires — ONE definition site, the policy file."""
    try:
        return admission_policy.required_checks(
            admission_policy.load_policy(run.admission["policy"]))
    except admission_policy.PolicyError as e:
        raise CycleRefused("policy", f"the admission policy could not be read, so the required "
                                     f"card checks are unknown: {e}") from e


def _refusal_code(text: str, default: str) -> str:
    m = re.search(r"REFUSED\[([a-z0-9-]+)\]", text)
    return m.group(1) if m else default


def _admit_refusal(out: str) -> tuple[str, str]:
    """The refusal class `source_admit` actually gave. Under admit.by_policy the decision that
    matters is the POLICY's own verdict — `does NOT self-admit [cycles] … N=<n>` — not the gate
    line it falls back to (the operator's word is demanded only because the policy said no), so the
    cycle quotes the policy's class and its reason, N included."""
    m = re.search(r"does NOT self-admit \[([a-z0-9-]+)\]\s*(.+)", out)
    if m:
        code, reason = m.group(1), m.group(2).strip().splitlines()[0]
        gate = re.search(r"REFUSED\[(?:gate|admit)\][^\n]*", out)
        return code, reason + (" · then " + gate.group(0)[:160] if gate else "")
    return _refusal_code(out, "admit-refused"), (out.strip().splitlines()[-1][:220]
                                                  if out.strip() else "(no output)")


def step_discover(run: Run, surfaces: list[dict]) -> None:
    """2 · DISCOVER over the registry, then EVALUATE and the pipeline, per candidate."""
    seen = _seen_load(run)
    seen_out = open(_seen_path(run), "a", encoding="utf-8") if _ensure_dir(run.state_dir) else None
    candidates: list[dict] = []
    for surface in surfaces:
        kind = surface.get("kind")
        if kind not in KINDS:
            raise CycleRefused("surface-kind-unknown",
                               f"surface {surface.get('id')!r} names kind {kind!r}; this version "
                               f"reads {list(KINDS)} — it will not guess how to read a surface")
        run.looked["surfaces"] += 1
        if kind == "listed":
            for c in surface.get("candidates") or []:
                _need(isinstance(c, dict) and c.get("id") and c.get("url"),
                      f"surface {surface['id']!r}: every listed candidate needs an id and a url",
                      "surface-schema")
                run.looked["queries"] += 1
                key = f"listed:{c['id']}"
                candidates.append(dict(id=c["id"], key=key, full_name=c["id"], url=c["url"],
                                       licence_url=c.get("licence_url", ""),
                                       count_endpoint=c.get("count_endpoint", ""),
                                       rate_limit=c.get("rate_limit") or {},
                                       question=c.get("question", ""),
                                       licence=c.get("licence", ""), archived=False,
                                       pushed_at="", surface=surface["id"]))
                if seen_out and key not in seen:
                    seen_out.write(json.dumps(dict(key=key, at=_utc())) + "\n")
            continue

        # github_search: every listed query is read, and LOGGED with its own count
        queries: list[str] = []
        for q in surface.get("queries") or []:
            queries.extend(_expand_seed(run, surface, q) if q.startswith("seed:") else [q])
        for q in queries:
            run.looked["queries"] += 1
            url = f"{surface['endpoint']}?q={_quote(q)}&per_page={int(surface['per_page'])}"
            try:
                body = run.get_json(url, note=f"search {q}")
            except CycleRefused as e:
                if e.fatal:
                    raise                    # budget: the run STOPS mid-cycle (A6), never a note
                run.queries.append(dict(surface=surface["id"], query=q, url=url, count=None,
                                        new=0, error=e.code))
                run.note_refusal(e.code, f"surface {surface['id']} query {q!r}: {e.detail}")
                run.looked["errors"] += 1
                continue
            total = body.get("total_count")
            items = body.get("items") or []
            new = 0
            for item in items:
                cand = _api_candidate(run, surface, item)
                if cand["key"] in seen:
                    continue
                new += 1
                run.looked["new"] += 1
                candidates.append(cand)
                if seen_out:
                    seen_out.write(json.dumps(dict(key=cand["key"], at=_utc())) + "\n")
            run.queries.append(dict(surface=surface["id"], query=q, url=url, count=total, new=new,
                                    error=""))
            run.say(f"QUERY     {surface['id']} {q!r} -> {total} result(s), {new} new")
    if seen_out:
        seen_out.close()

    run.say(f"I looked: {run.looked['surfaces']} surface(s) · {run.looked['queries']} "
            f"quer{'y' if run.looked['queries'] == 1 else 'ies'} · {run.looked['new']} new"
            + (f" · {run.looked['errors']} error(s), each named above" if run.looked["errors"] else ""))
    run.steps_done.append(f"discovery({run.looked['queries']} queries, {run.looked['new']} new)")

    for cand in candidates:
        run.found.append({k: cand.get(k) for k in ("id", "key", "full_name", "url", "licence",
                                                   "surface", "count_endpoint")})
        if not _evaluate(run, _surface_of(surfaces, cand["surface"]), cand):
            continue
        _step_pipeline(run, cand)
    run.steps_done.append(f"pipeline({len(run.drafted)} drafted, {len(run.sandboxed)} sandboxed, "
                          f"{len(run.carded)} carded, {len(run.admitted)} admitted, "
                          f"{len(run.proposed)} proposed)")


def _surface_of(surfaces: list[dict], sid: str) -> dict:
    for s in surfaces:
        if s.get("id") == sid:
            return s
    return {}


def _quote(q: str) -> str:
    import urllib.parse
    return urllib.parse.quote(q, safe="")


def _ensure_dir(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return False
    return True


# ═══════════════════════════════════════════════════════════ 5 · THE REPORT AND THE RECORD
def _bullets(items: list[str]) -> list[str]:
    return items or ["- none"]


def write_report(run: Run) -> str:
    """The CYCLE-REPORT: found · refused (rule named) · drafted · sandboxed · admitted · cost, and
    the "I looked" line — printed on a quiet week too, because a cycle that changes nothing is
    still a cycle that LOOKED (DESIGN §3 step 8)."""
    _ensure_dir(run.report_dir)
    b = run.cfg["budget"]
    cost = run.cost
    looked = (f"I looked: {run.looked['surfaces']} surface(s) · {run.looked['queries']} "
              f"quer{'y' if run.looked['queries'] == 1 else 'ies'} · {run.looked['new']} new")
    if run.looked["errors"]:
        looked += f" · {run.looked['errors']} error(s), each named below"
    L = [f"# CYCLE-REPORT — {run.run_id}",
         "",
         f"mode `{run.mode}` · config `{_rel(run.cfg['_path'])}` (sha256 "
         f"{run.cfg['_sha256'][:16]}…) · surfaces `{_rel(_path(run.cfg, 'surfaces', 'registry'))}`",
         f"started {run.started_at} · outcome **{run.stopped or 'completed'}**"
         + (f" — {run.stop_detail}" if run.stop_detail else ""),
         f"budget requests={b['requests']} bytes={b['bytes']} wall_s={b['wall_s']} · spent "
         f"requests={cost['requests']} bytes={cost['bytes_in']} wall={cost['wall_s']}s",
         f"last run: {run.last_run_note or 'none on record'}",
         "",
         f"## found — {len(run.found)} candidate(s)"]
    L += _bullets([f"- `{c['id']}` — {c.get('full_name') or c.get('url')} (licence "
                   f"{c.get('licence') or 'n/a'}, surface `{c.get('surface')}`)"
                   for c in run.found])
    L += ["", f"## refused — {len(run.refused)} (the rule is NAMED, always)"]
    L += _bullets([f"- REFUSED[{r['code']}] {r['detail']}" for r in run.refused])
    L += ["", f"## drafted — {len(run.drafted)}"]
    L += _bullets([f"- {s}" for s in run.drafted])
    L += ["", f"## sandboxed — {len(run.sandboxed)}"]
    L += _bullets([f"- `{s['id']}` count VERIFIED ours={s['ours']} = source={s['theirs']} · "
                   f"receipt `{s['receipt']}`" for s in run.sandboxed])
    L += ["", f"## carded — {len(run.carded)}"]
    L += _bullets([f"- `{c}`" for c in run.carded])
    L += ["", f"## admitted — {len(run.admitted)}"]
    L += _bullets([f"- `{a['id']}` — {a['line'] or 'NO RULE QUOTED — FINDING'} · record `{a['record']}`"
                   for a in run.admitted])
    L += ["", f"## proposed (a person decides) — {len(run.proposed)}"]
    L += _bullets([f"- `{p['id']}` — {p['why']}" for p in run.proposed])
    L += ["", "## telemetry"]
    L += _bullets([f"- `{t['digest'] or '(none)'}`: {t['lines']} measured line(s)"
                   + (f" — {t['note']}" if t.get("note") else "") for t in run.telemetry])
    L += ["", "## queries (each logged with its own count)"]
    L += _bullets([f"- `{q['query']}` -> {q['count']} result(s) · {q['new']} new"
                   + (f" · REFUSED[{q['error']}]" if q.get("error") else "") for q in run.queries]
                  or ([] if run.mode != "watch" else ["- none (mode watch: nothing was looked for)"]))
    L += ["", f"**{looked}**", "",
          "## cost",
          f"requests={cost['requests']} · bytes_in={cost['bytes_in']} · wall_s={cost['wall_s']} · "
          f"rate_limit_waits={cost['rate_limit_waits']}",
          "", f"steps: {', '.join(run.steps_done) or 'none'}", ""]
    with open(run.report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    return run.report_path


def write_record(run: Run, outcome: str) -> str:
    _ensure_dir(run.state_dir)
    rec = dict(run_id=run.run_id, mode=run.mode, started_at=run.started_at, at=_utc(),
               outcome=outcome, stopped=run.stopped, stop_detail=run.stop_detail,
               steps_done=run.steps_done, config=_rel(run.cfg["_path"]),
               config_sha256=run.cfg["_sha256"], report=_rel(run.report_path),
               report_sha256=_sha_file(run.report_path) if os.path.isfile(run.report_path) else "",
               looked=run.looked, cost=run.cost, budget=run.cfg["budget"],
               budget_left=run.budget_left,
               found=run.found, refused=run.refused, drafted=run.drafted,
               sandboxed=run.sandboxed, carded=run.carded, admitted=run.admitted,
               proposed=run.proposed, queries=run.queries, telemetry=run.telemetry)
    p = os.path.join(run.state_dir, "runs")
    _ensure_dir(p)
    path = os.path.join(p, f"{run.run_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    with open(os.path.join(run.state_dir, "last.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    return path


# ═══════════════════════════════════════════════════════════ 6 · THE RUN ITSELF
def run_cycle(cfg: dict, mode: str | None = None) -> int:
    """One bounded run. The preflight refusals all happen BEFORE a request leaves the box."""
    mode = mode or cfg["mode"]
    try:
        _need(mode in MODES, f"mode must be one of {list(MODES)}, got {mode!r}", "mode")
    except CycleRefused as e:
        print(f"REFUSED[{e.code}] {e.detail}", flush=True)
        return EXIT_REFUSED
    if not cfg["enabled"]:
        # THE SHIPPED DEFAULT: nothing at all. No request, no report, no state, no store touch.
        print(f"CYCLE     disabled — `{_rel(cfg['_path'])}` says enabled: false; the cycle does "
              f"nothing at all (no request, no report, nothing spent)", flush=True)
        return EXIT_OK
    permits, why = (True, "")
    if cfg["admit"]["by_policy"]:
        permits, why = read_ruling(_ruling_path(cfg))
        if not permits:
            print(f"REFUSED[governance] admit.by_policy: true is REFUSED — {why}", flush=True)
            return EXIT_REFUSED
    surfaces: list[dict] = []
    if mode in ("discover", "both"):
        try:
            surfaces = load_registry(cfg)
        except CycleRefused as e:
            print(f"REFUSED[{e.code}] {e.detail}", flush=True)
            return EXIT_REFUSED

    run = Run(cfg, mode)
    run.say(f"CYCLE     {run.run_id} mode={mode} config={_rel(cfg['_path'])} "
            f"budget={json.dumps(cfg['budget'])}")
    if run.last_run_note:
        run.say(f"LAST RUN  {run.last_run_note}")
    if cfg["admit"]["by_policy"]:
        run.say(f"GOVERNANCE admit.by_policy=true — permitted on record: {why}")
    elif mode != "watch":
        run.say(f"GOVERNANCE admit.by_policy=false — the cycle PROPOSES; a person admits")
    outcome = "completed"
    try:
        if mode in ("watch", "both"):
            run.step_now = "telemetry"
            step_telemetry(run)
        if mode in ("discover", "both"):
            run.step_now = "discovery"
            step_discover(run, surfaces)
        if run.looked["errors"] and run.looked["errors"] >= run.looked["queries"]:
            codes = sorted({q["error"] for q in run.queries if q.get("error")})
            run.stopped = "network-down" if codes == ["network-down"] else "surfaces-unreadable"
            run.stop_detail = (f"every one of the {run.looked['queries']} listed quer"
                               f"{'y' if run.looked['queries'] == 1 else 'ies'} died before it could "
                               f"be read ({', '.join(codes)}; each refusal is named above and in the "
                               f"report) — the cycle looked, and says so, but it saw nothing")
    except CycleRefused as e:
        run.stopped = e.code
        run.stop_detail = e.detail
        run.steps_done.append(f"{run.step_now or 'start'}(stopped: {e.code})")
        run.refused.append(dict(code=e.code, detail=e.detail, at=_utc()))
        run.say(f"REFUSED[{e.code}] {e.detail}")
    outcome = run.stopped or "completed"
    rp = write_report(run)
    rec = write_record(run, outcome)
    run.say(f"COST      requests={run.cost['requests']} bytes={run.cost['bytes_in']} "
            f"wall={run.cost['wall_s']}s · budget left {json.dumps(run.budget_left)}")
    run.say(f"REPORT    {_rel(rp)}  sha256={_sha_file(rp)[:16]}…")
    run.say(f"RECORD    {_rel(rec)}")
    return EXIT_OK if not run.stopped else EXIT_STOPPED


# ═══════════════════════════════════════════════════════════ 7 · THE MACHINE DOOR READS THIS
def status_text(config: str = DEFAULT_CYCLE) -> str:
    """`cycle_status` — enabled? · mode · last run · last report · budget left · next due.
    READ-ONLY: it reads cycle.yml and the last run record and writes nothing at all."""
    try:
        cfg = load_cycle(config)
    except CycleRefused as e:
        return (f"REFUSED[{e.code}] {e.detail}\nCYCLE  unreadable: {config} — status cannot be "
                f"reported from a config the cycle itself refuses")
    permits, why = read_ruling(_ruling_path(cfg))
    state = _path(cfg, "state_dir")
    rec: dict = {}
    p = os.path.join(state, "last.json")
    if os.path.isfile(p):
        try:
            rec = json.load(open(p, encoding="utf-8"))
        except Exception:
            rec = {}
    b = cfg["budget"]
    left = rec.get("budget_left") or dict(b)
    due = "unknown"
    if rec.get("started_at"):
        try:
            t = dt.datetime.fromisoformat(rec["started_at"])
            days = {"daily": 1, "weekly": 7, "monthly": 30}.get(cfg["every"], 7)
            due = (t + dt.timedelta(days=days)).replace(microsecond=0).isoformat()
        except ValueError:
            due = "unknown"
    out = [
        f"CYCLE     enabled={str(cfg['enabled']).lower()} · mode={cfg['mode']} · every={cfg['every']}"
        + ("" if cfg["enabled"] else " · SHIPPED OFF: it does nothing at all"),
        f"CONFIG    {_rel(cfg['_path'])}  sha256={cfg['_sha256'][:16]}…",
        f"SURFACES  {_rel(_path(cfg, 'surfaces', 'registry'))} (kind {cfg['surfaces']['kind']})",
        f"LAST RUN  " + (f"{rec.get('run_id')} mode={rec.get('mode')} outcome={rec.get('outcome')} "
                         f"at {rec.get('started_at')}" if rec else "none on record"),
        f"LAST REPORT " + (f"{rec.get('report')}  sha256={str(rec.get('report_sha256'))[:16]}…"
                           if rec.get("report") else "none"),
        f"BUDGET    requests {left.get('requests')}/{b['requests']} left · bytes "
        f"{left.get('bytes')}/{b['bytes']} left · wall {left.get('wall_s')}/{b['wall_s']}s left",
        f"NEXT DUE  {due}",
        f"ADMIT     by_policy={str(cfg['admit']['by_policy']).lower()} — "
        + (f"PERMITTED: {why}" if permits and cfg["admit"]["by_policy"] else why),
        "DOOR      cycle_status is READ-ONLY and cycle_run_once is the only write; both live on "
        "the MACHINE door only — never on the read doors (DESIGN-SENTINEL §5)",
    ]
    if rec.get("stopped"):
        out.append(f"LAST STOPPED ON {rec['stopped']} after {', '.join(rec.get('steps_done') or [])} "
                   f"— {rec.get('stop_detail', '')}")
    return "\n".join(out)


# ═══════════════════════════════════════════════════════════ 8 · THE SEVEN ACCEPTANCE CASES
def selftest() -> int:
    """DESIGN-SENTINEL §8, all seven, offline, each quoted with its exit code.

    Nothing here touches the internet: every surface is a local HTTP server on 127.0.0.1, every
    path lives under one temp directory, and each case runs the REAL `python3 -m symbiont.cycle` in
    a subprocess so the exit code quoted is the cycle's own."""
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import yaml

    GENES = ("symbol\tMGI id\tchrom\tstart\tend\n"
             "Trp53\tMGI:98834\t11\t69580359\t69581786\n"
             "Brca1\tMGI:104537\t11\t101471872\t101543608\n"
             "Epcam\tMGI:106653\t17\t87652551\t87659529\n")
    LICENCE = ("<html><body>CC0 1.0 Universal — public domain dedication. You may store and "
               "redistribute this data.</body></html>")
    SEARCH = dict(total_count=0, items=[])

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _s(self, body: str, ct: str, code: int = 200):
            b = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(b)

        def do_HEAD(self):
            self._s(GENES, "text/tab-separated-values")

        def do_GET(self):
            if self.path.startswith("/genes"):
                self._s(GENES, "text/tab-separated-values")
            elif self.path.startswith("/licence"):
                self._s(LICENCE, "text/html")
            elif self.path.startswith("/count-bad"):
                self._s("4", "text/plain")
            elif self.path.startswith("/count"):
                self._s("3", "text/plain")
            elif self.path.startswith("/search"):
                self._s(json.dumps(SEARCH), "application/json")
            elif self.path.startswith("/nothing"):
                self._s("gone", "text/plain", 404)
            else:
                self._s("not found", "text/plain", 404)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    results: list[tuple[str, bool]] = []

    def check(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good))
        print(f"  {'PASS' if good else 'FAIL'} {name}" + (f"  {detail}" if detail else ""),
              flush=True)

    def tree(tmp: str, *, surf_yaml: str = "", queries: list[str] | None = None,
             budget=(8, 4000000, 120), provider: str = "", by_policy: bool = False,
             ruling: str = "", enabled: bool = True, digest: bool = True, n: int = 1,
             cycles: int = 1, mode: str = "discover") -> dict:
        """One fixture deployment: config + registry + store + policy + sources, all under tmp."""
        os.makedirs(os.path.join(tmp, "store", "digest"), exist_ok=True)
        if digest:
            with open(os.path.join(tmp, "store", "digest", "gaps.md"), "w") as fh:
                fh.write("# GAPS\n\n- gaps measured: 0\n")
        os.makedirs(os.path.join(tmp, "store", "runs"), exist_ok=True)
        for i in range(cycles):
            with open(os.path.join(tmp, "store", "runs", f"c{i}.json"), "w") as fh:
                json.dump(dict(run_id=f"c{i}", started_at=f"2026-11-0{i + 1}T00:00:00+00:00",
                               sources={"fix_genes": dict(verify=dict(ok=True), skipped=False)}),
                          fh)
        dummy = yaml.safe_load(open(os.path.join(FARM, "symbiont", "sources.yml")))
        one = dict(dummy["sources"][0])
        one["enabled"] = False
        with open(os.path.join(tmp, "sources.yml"), "w") as fh:
            fh.write("# the fixture deployment's registry\n")
            fh.write(yaml.safe_dump(dict(version=1, sources=[one]), sort_keys=False))
        pol = open(os.path.join(FARM, "reports", "symbiont-policy.yml")).read()
        pol = re.sub(r"^(\s*stable_cycles_before_self_admit:)\s*null.*$", rf"\g<1> {n}", pol, flags=re.M)
        with open(os.path.join(tmp, "policy.yml"), "w") as fh:
            fh.write(pol)
        if surf_yaml:
            reg = os.path.join(tmp, "surfaces.yml")
            open(reg, "w").write(surf_yaml)
        else:
            reg = os.path.join(tmp, "queries.txt")
            open(reg, "w").write("# the fixture registry\n" + "\n".join(queries or []) + "\n")
        cfg = dict(version=1, enabled=enabled, mode=mode, every="weekly",
                   budget=dict(requests=budget[0], bytes=budget[1], wall_s=budget[2]),
                   rate_limit=dict(requests_per_minute=120, min_interval_s=0.0),
                   store=os.path.join(tmp, "store"),
                   report_dir=os.path.join(tmp, "reports"),
                   state_dir=os.path.join(tmp, "store", "cycle"),
                   admission=dict(dir=os.path.join(tmp, "adm"),
                                  sources_config=os.path.join(tmp, "sources.yml"),
                                  drafts_dir=os.path.join(tmp, "drafts"),
                                  adapters_dir=os.path.join(tmp, "adapters"),
                                  gate_file=os.path.join(FARM, "symbiont", "admission-gate.txt"),
                                  policy=os.path.join(tmp, "policy.yml")),
                   surfaces=dict(registry=reg, kind="github_search",
                                 endpoint=f"{base}/search", per_page=5, dead_days=730,
                                 seed_topics=3),
                   draft=dict(provider=provider),
                   admit=dict(by_policy=by_policy, ruling=ruling))
        p = os.path.join(tmp, "cycle.yml")
        open(p, "w").write(yaml.safe_dump(cfg, sort_keys=False))
        return cfg

    def cycle(tmp: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "symbiont.cycle",
                               "--config", os.path.join(tmp, "cycle.yml"), *args],
                              cwd=FARM, capture_output=True, text=True, timeout=900)

    def listed(tmp: str, **over) -> str:
        """A structured registry naming ONE candidate with its own count endpoint."""
        cand = dict(id="fix_genes", url=f"{base}/genes.tsv", licence_url=f"{base}/licence.html",
                    count_endpoint=over.pop("count_endpoint", f"{base}/count"),
                    licence="CC0-1.0", question="does the fixture answer a question")
        return yaml.safe_dump(dict(version=1, surfaces=[
            dict(id="fixture-data", kind="listed", candidates=[cand])]), sort_keys=False)

    PROV = os.path.join(tempfile.mkdtemp(prefix="cyc-prov-"), "provider.sh")
    with open(PROV, "w") as fh:                     # a provider that DRAFTS the tested template
        fh.write("#!/bin/bash\n"
                 "# the deployment's OWN drafting provider: argv = <candidate.json> <draft.py>\n"
                 "set -e\n"
                 "ADM=\"$(dirname \"$(dirname \"$1\")\")\"\n"
                 "ID=\"$(basename \"$2\" .py)\"\n"
                 f"cd {FARM}\n"
                 "python3 -m symbiont.admission draft \"$ID\" --admission-dir \"$ADM\" "
                 "--drafts-dir \"$(dirname \"$2\")\" >/dev/null\n"
                 "test -f \"$2\"\n")
    os.chmod(PROV, 0o755)
    print(f"== selftest_cycle: the seven acceptance cases of DESIGN-SENTINEL §8 (offline, "
          f"surface at {base}) ==", flush=True)
    tmpdir = tempfile.mkdtemp(prefix="symbiont-cycle-test-")
    try:
        # ══ A1 · A QUIET WEEK ══════════════════════════════════════════════════════════════════
        print("\nA1 a quiet week — it looks, it prints 'I looked … 0 new', it exits 0", flush=True)
        tmp = os.path.join(tmpdir, "a1")
        tree(tmp, queries=["topic:mcp-server biomedical", "BioASQ"], mode="both")
        SEARCH.update(total_count=0, items=[])
        p = cycle(tmp)
        check("A1 quiet week exits 0", p.returncode == 0, f"exit={p.returncode}")
        check("A1 'I looked: 1 surface · 2 queries · 0 new' is printed",
              "I looked: 1 surface(s) · 2 queries · 0 new" in p.stdout,
              next((l for l in p.stdout.splitlines() if l.startswith("I looked")), "(no line)"))
        check("A1 the report exists and quotes the same line",
              any("I looked: 1 surface(s) · 2 queries · 0 new" in open(f, encoding="utf-8").read()
                  for f in glob.glob(os.path.join(tmp, "reports", "*.md"))))

        # ══ A2 · ONE REFUSED CANDIDATE ═════════════════════════════════════════════════════════
        print("\nA2 one new candidate — found, refused by its OWN rule, the rule in the report",
              flush=True)
        tmp = os.path.join(tmpdir, "a2")
        tree(tmp, queries=["topic:biomedical-nlp"])
        SEARCH.update(total_count=2, items=[
            dict(full_name="someone/unlicensed", html_url=f"{base}/x", license=None,
                 archived=False, pushed_at="2026-11-01T00:00:00Z"),
            dict(full_name="someone/archived", html_url=f"{base}/y",
                 license=dict(spdx_id="MIT"), archived=True, pushed_at="2026-11-01T00:00:00Z")])
        p = cycle(tmp)
        check("A2 exits 0 (a refused candidate is the cycle working)", p.returncode == 0,
              f"exit={p.returncode}")
        check("A2 REFUSED[licence-unreadable] is named", "REFUSED[licence-unreadable]" in p.stdout)
        check("A2 REFUSED[archived] is named", "REFUSED[archived]" in p.stdout)
        rep = "\n".join(open(f, encoding="utf-8").read()
                        for f in glob.glob(os.path.join(tmp, "reports", "*.md")))
        check("A2 both refusals are IN the report with the rule named",
              "REFUSED[licence-unreadable]" in rep and "REFUSED[archived]" in rep)

        # ══ A3 · COUNT-MISMATCH DRAFT REFUSED ═════════════════════════════════════════════════
        print("\nA3 one draft — the sandbox count disagrees: REFUSED[count-mismatch], nothing "
              "carded, nothing admitted", flush=True)
        tmp = os.path.join(tmpdir, "a3")
        tree(tmp, surf_yaml=listed(tmp, count_endpoint=f"{base}/count-bad"), provider=PROV)
        p = cycle(tmp)
        check("A3 REFUSED[count-mismatch] is named", "REFUSED[count-mismatch]" in p.stdout,
              next((l for l in p.stdout.splitlines() if "count-mismatch" in l), "(none)"))
        check("A3 nothing was carded", not glob.glob(os.path.join(tmp, "adm", "*", "card.md")))
        check("A3 nothing was admitted (no admitted.json)",
              not glob.glob(os.path.join(tmp, "adm", "*", "admitted.json")))

        # ══ A4 · by_policy FALSE ⇒ PROPOSED, NOT ADMITTED ═════════════════════════════════════
        print("\nA4 admit.by_policy false — a fully green card is PROPOSED and sources.yml gains "
              "no enabled:true", flush=True)
        tmp = os.path.join(tmpdir, "a4")
        tree(tmp, surf_yaml=listed(tmp), provider=PROV, by_policy=False)
        p = cycle(tmp)
        check("A4 exits 0", p.returncode == 0, f"exit={p.returncode}")
        check("A4 the card is green (5/5 checks ok)",
              bool(glob.glob(os.path.join(tmp, "adm", "fix_genes", "card.md")))
              and "check cost: ok" in open(os.path.join(tmp, "adm", "fix_genes", "card.md")).read(),
              "card.md written")
        check("A4 PROPOSED is said and ADMITTED is not",
              "PROPOSED  fix_genes" in p.stdout and "ADMITTED" not in p.stdout)
        up = yaml.safe_load(open(os.path.join(tmp, "sources.yml")))
        enabled = [s["id"] for s in up["sources"] if s["enabled"]]
        check("A4 sources.yml holds no enabled:true for the candidate", "fix_genes" not in enabled,
              f"enabled={enabled}")

        # ══ A5 · N UNMET vs N MET ═════════════════════════════════════════════════════════════
        print("\nA5 admit.by_policy true — N unmet is refused naming N; N met is admitted and the "
              "record quotes rule + N", flush=True)
        RULING = os.path.join(tmpdir, "ruling-A.md")
        open(RULING, "w").write("# COUNCIL RULING — fixture\nQuestion: may the shipped station "
                                "admit by policy?\nRULING: A\n"
                                "## DECISION: A — admit-by-policy permitted, off by default\n"
                                "## STATUS: RATIFIED (fixture)\n")
        tmp = os.path.join(tmpdir, "a5-unmet")
        tree(tmp, surf_yaml=listed(tmp), provider=PROV, by_policy=True, ruling=RULING, n=3,
             cycles=1)
        p = cycle(tmp)
        check("A5 (N unmet) REFUSED[cycles] names N=3",
              "REFUSED[cycles]" in p.stdout and "N=3" in p.stdout,
              next((l for l in p.stdout.splitlines() if "stable_cycles" in l or "cycles" in l), ""))
        check("A5 (N unmet) nothing admitted",
              not glob.glob(os.path.join(tmp, "adm", "*", "admitted.json")))
        tmp = os.path.join(tmpdir, "a5-met")
        tree(tmp, surf_yaml=listed(tmp), provider=PROV, by_policy=True, ruling=RULING, n=1,
             cycles=1)
        p = cycle(tmp)
        adm = glob.glob(os.path.join(tmp, "adm", "*", "admitted.json"))
        line = ""
        if adm:
            line = ((json.load(open(adm[0])).get("policy") or {}).get("line") or "")
        check("A5 (N met) ADMITTED and the record quotes the rule and N",
              "ADMITTED  fix_genes" in p.stdout
              and "rule stable_cycles_before_self_admit, N=1" in line, f"record line={line!r}")

        # ══ A6 · BUDGET EXHAUSTED MID-CYCLE ═══════════════════════════════════════════════════
        print("\nA6 the budget is spent mid-cycle — the cycle STOPS, records why, exits 3, and the "
              "next cycle says how much it did", flush=True)
        tmp = os.path.join(tmpdir, "a6")
        tree(tmp, queries=["q-one", "q-two", "q-three"], budget=(1, 4000000, 120))
        SEARCH.update(total_count=0, items=[])
        p = cycle(tmp)
        check("A6 exits 3 (stopped, not a crash)", p.returncode == 3, f"exit={p.returncode}")
        check("A6 REFUSED[budget] is named", "REFUSED[budget]" in p.stdout)
        p2 = cycle(tmp)
        check("A6 the NEXT run says how much the last one did",
              "LAST RUN" in p2.stdout and "outcome=budget" in p2.stdout
              and "discovery(stopped: budget)" in p2.stdout,
              next((l for l in p2.stdout.splitlines() if "LAST RUN" in l), ""))
        check("A6 the report of the next run quotes it too",
              any("outcome=budget" in open(f, encoding="utf-8").read()
                  for f in sorted(glob.glob(os.path.join(tmp, "reports", "*.md")))[-1:]))

        # ══ A7 · THE NETWORK IS DOWN ══════════════════════════════════════════════════════════
        print("\nA7 the network is down — named refusals, no hang, 'I looked' still printed, exit 3",
              flush=True)
        tmp = os.path.join(tmpdir, "a7")
        tree(tmp, queries=["topic:x", "topic:y"])
        cfgp = os.path.join(tmp, "cycle.yml")
        txt = open(cfgp).read().replace(f"{base}/search", "http://127.0.0.1:9/search")
        open(cfgp, "w").write(txt)
        p = cycle(tmp)
        check("A7 exits 3", p.returncode == 3, f"exit={p.returncode}")
        check("A7 REFUSED[network-down] is named for the queries",
              p.stdout.count("REFUSED[network-down]") >= 2)
        check("A7 'I looked' is still printed", "I looked: 1 surface(s) · 2" in p.stdout,
              next((l for l in p.stdout.splitlines() if l.startswith("I looked")), "(none)"))
        check("A7 nothing hung and a report was written",
              bool(glob.glob(os.path.join(tmp, "reports", "*.md"))))

        # ══ G4 · THE RATE LIMIT DOES NOT FIT THE BUDGET (DESIGN §3 step 3) ═══════════════════
        print("\nG4 a source whose own rate needs more wall clock than the run has left — "
              "REFUSED[rate-too-low] with the arithmetic, before any read", flush=True)
        tmp = os.path.join(tmpdir, "g4")
        tree(tmp, surf_yaml=listed(tmp).replace(
            "count_endpoint:", "rate_limit: {requests_per_minute: 1, min_interval_s: 60}\n"
                               "    count_endpoint:"), budget=(8, 4000000, 5), provider=PROV)
        p = cycle(tmp)
        check("G4 REFUSED[rate-too-low] is named with the wall arithmetic",
              "REFUSED[rate-too-low]" in p.stdout and "wall clock" in p.stdout,
              next((l for l in p.stdout.splitlines() if "rate-too-low" in l), "(none)"))
        check("G4 nothing was drafted", not glob.glob(os.path.join(tmp, "drafts", "*.py")))

        # ══ THE DISABLED DEFAULT + THE GOVERNANCE REFUSAL (the row's own GATE) ════════════════
        print("\nG1 the shipped default — enabled: false does NOTHING AT ALL", flush=True)
        tmp = os.path.join(tmpdir, "g1")
        tree(tmp, queries=["topic:x"], enabled=False)
        p = cycle(tmp)
        check("G1 exits 0 and says it did nothing",
              p.returncode == 0 and "does nothing at all" in p.stdout, f"exit={p.returncode}")
        check("G1 no report, no state, no store write",
              not os.path.exists(os.path.join(tmp, "reports"))
              and not os.path.exists(os.path.join(tmp, "store", "cycle")),
              f"reports={os.path.exists(os.path.join(tmp, 'reports'))}")
        print("\nG2 the governance gate — admit.by_policy: true with no ruling is REFUSED BY NAME",
              flush=True)
        tmp = os.path.join(tmpdir, "g2")
        tree(tmp, surf_yaml=listed(tmp), provider=PROV, by_policy=True, ruling="")
        p = cycle(tmp)
        check("G2 exits 2 and REFUSED[governance] names the pending ruling",
              p.returncode == 2 and "REFUSED[governance]" in p.stdout
              and "PENDING" in p.stdout, f"exit={p.returncode}")
        check("G2 nothing looked at, nothing spent, no report",
              "I looked" not in p.stdout
              and not os.path.exists(os.path.join(tmp, "reports")))
        tmp = os.path.join(tmpdir, "g3")
        tree(tmp, surf_yaml=listed(tmp), provider=PROV, by_policy=True,
             ruling=os.path.join(FARM, "reports", "RULINGS",
                                 "20261111-014502-2839278-May-the-SHIPPED-station-admit-a-data-sou.md"))
        p = cycle(tmp)
        check("G3 the REAL parked ruling does not count as a permission (REFUSED[governance])",
              p.returncode == 2 and "REFUSED[governance]" in p.stdout
              and "DOWNGRADED" in p.stdout, f"exit={p.returncode}")
    finally:
        srv.shutdown()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        shutil.rmtree(os.path.dirname(PROV), ignore_errors=True)
    ok = sum(1 for _, good in results if good)
    print(f"\nRESULT: {ok} passed, {len(results) - ok} failed", flush=True)
    return 0 if ok == len(results) else 1


# ═══════════════════════════════════════════════════════════ the CLI
def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "selftest":
        return selftest()
    ap = argparse.ArgumentParser(
        prog="symbiont.cycle",
        description="the sentinel cycle: one bounded run of watch (telemetry) and/or discover "
                    "(candidates -> evaluate -> draft -> sandbox -> card -> admit-by-policy), on its "
                    "own budget, writing one CYCLE-REPORT per run")
    ap.add_argument("--mode", choices=list(MODES), default=None,
                    help="watch | discover | both (default: the mode cycle.yml ships)")
    ap.add_argument("--config", default=DEFAULT_CYCLE, help="the cycle config (default: cycle.yml)")
    ap.add_argument("--status", action="store_true",
                    help="print what the machine door's cycle_status reads, and exit")
    a = ap.parse_args(argv)
    if a.status:
        print(status_text(a.config))
        return EXIT_OK
    try:
        cfg = load_cycle(a.config)
    except CycleRefused as e:
        print(f"REFUSED[{e.code}] {e.detail}", flush=True)
        return EXIT_REFUSED
    return run_cycle(cfg, a.mode)


if __name__ == "__main__":
    sys.exit(main())
