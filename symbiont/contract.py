"""THE ADAPTER CONTRACT — a source is a plug-in file, the loop is unchanged.

An adapter is any module under adapters/ that exposes `ADAPTER` (an instance of a class
with the four methods below). Nothing else about the source is known to the loop:
no URLs, no column numbers, no licence text. If a new source needs a change to loop.py,
the contract has been violated — that is a bug in the adapter, not in the loop.

    discover(http, since)     -> Discovery
    fetch(http, since)        -> FetchPlan          (since = state dict from the last run)
    normalise(resources)      -> Normalised         (resources = {reskey: bytes})
    verify(discovery, normalised) -> Verify          (our count vs the source's own count)

Canonical record (what every adapter must emit; extra keys are allowed and ignored
by the join, but the join itself reads only these):

    {"key":    <string>  stable join key (symbol, case preserved)
     "id":     <string>  the id this source is the authority for, or "" if it is a xref provider
     "source": <string>  adapter name (set by the loop, not by the adapter)
     "xrefs":  {<namespace>: <id>}   ids this source asserts for the record
     "coords": {"chrom","start","end"} or {} when the source has none}

Normalised.rejects is a list of {"row": <verbatim raw line>, "reason": <code>, "detail": <str>}.
A row is NEVER dropped without appearing here: the loop refuses a run where
rows_in != len(records) + len(rejects).
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import re
from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE FOUR REFUSALS, as types. A refusal is not a crash: it carries a CODE that lands verbatim in
# the run record and on stdout ("REFUSED[budget] ..."), so "the source was skipped" and "the run
# broke" can never be mistaken for each other.
# ─────────────────────────────────────────────────────────────────────────────────────────────
class Refusal(RuntimeError):
    """A source (or the run) refuses to go on, for a reason that has a name."""
    code = "refused"


class BudgetExceeded(Refusal):        # T5: the per-source budget in sources.yml is spent
    code = "budget"


class LicenceUnknown(Refusal):        # T6: the terms could not be read, so no data may land
    code = "licence-unknown"


class SourceGone(Refusal):            # T9: the provider answers 404/410 — the source has moved
    code = "source-gone"            #      or been retired; the loop quarantines it by name


class ConfigError(RuntimeError):      # refused BEFORE the run: the config and the code disagree
    code = "config"


class DecisionError(ConfigError):      # the decision record is missing, stale or malformed
    code = "decision"              # (req-P1-THE-FRAMEWORK-GAINS-A-SOURCE-DISCOVE)


@dataclass
class Discovery:
    source: str
    release: str                             # human-meaningful: "ensembl-116", "mgi-2026-11-09"
    resources: list[dict]                    # [{reskey,url,etag,last_modified,content_length}]
    published_count: int | None = None        # the SOURCE's own count, not ours
    published_count_url: str = ""             # the URL that states it
    licence: dict = field(default_factory=dict)  # {name,url,quote,terms_sha256}
    notes: list[str] = field(default_factory=list)


@dataclass
class FetchPlan:
    new: bool                                 # False = the source has not moved since `since`
    reason: str                               # why, in words, for the run record
    resources: dict = field(default_factory=dict)  # {reskey: {"bytes": b, "filename": "x.rpt"}}
                                              # empty when new is False


@dataclass
class Normalised:
    rows_in: int
    records: list[dict]
    rejects: list[dict]
    published_count: int | None = None
    published_count_url: str = ""
    extra: dict = field(default_factory=dict)   # e.g. {"retired": [...]} for an xref provider


@dataclass
class Verify:
    ok: bool
    ours: int
    source_count: int | None
    url: str
    detail: str


def check_accounting(n: Normalised) -> list[str]:
    """The anti-silence rule, mechanically: for every resource the adapter says it read, the
    rows must be accounted for — as records, as rejects (with a reason), or as explicitly
    used for something the adapter names. Nothing may simply vanish."""
    errs: list[str] = []
    acc = (n.extra or {}).get("accounting") or []
    if not acc:
        return ["adapter reported no per-resource accounting"]
    total = 0
    for a in acc:
        total += a["rows_in"]
        owed = a["records"] + a["rejects"] + a.get("used", 0)
        if owed != a["rows_in"]:
            errs.append(f"{a['reskey']}: {a['rows_in']} rows in but {owed} accounted "
                        f"({a['records']} records + {a['rejects']} rejects + {a.get('used', 0)} used) "
                        f"→ {a['rows_in'] - owed} row(s) went missing")
    if total != n.rows_in:
        errs.append(f"rows_in={n.rows_in} disagrees with the sum over resources ({total})")
    return errs


def licence_terms(http, since: dict, release: str, url: str, name: str, pattern: str,
                  notes: list) -> dict:
    """Read (or reuse) the provider's own licence wording, and hash the page it came from.

    T6 (licence-unknown): if the page cannot be READ — unreachable, 404, empty, or worded in a way
    that no longer states the terms — this raises LicenceUnknown, and discovery dies there. Data
    never lands under terms we could not read: there is no fallback path to a stored licence for a
    release we have not recorded, and no "assume it is the same".

    Reuse rule: if the release has not moved, the terms we recorded on the run that ingested
    that release are the terms of that release, so we do not pay 30 s of latency to re-read a
    page that has not changed. The reuse is announced in `notes` — a silent reuse would be the
    same sin as a silent drop.
    """
    import hashlib
    import re
    old = since.get("licence") or {}
    if old.get("terms_sha256") and since.get("release") == release:
        notes.append(f"licence terms reused from run {since.get('run_id', '?')}: release {release} "
                     f"unchanged, page sha256 {old['terms_sha256']}")
        return dict(name=old.get("name", name), url=old.get("url", url), quote=old.get("quote", ""),
                    terms_sha256=old["terms_sha256"], reused_from=since.get("run_id", ""))
    try:
        page = http.get(url, f"licence {name}")
    except BudgetExceeded:
        raise            # a spent budget is not an unreadable licence: keep the code honest (T5 vs T6)
    except Exception as e:   # unreachable, 404, deadline, short read — all of them mean UNKNOWN
        raise LicenceUnknown(f"{name}: licence page {url} could not be read "
                             f"({type(e).__name__}: {e}) — refusing to ingest data under terms "
                             f"nobody read") from e
    text = page.decode("utf-8", "replace")
    m = re.search(pattern, text)
    if not m:
        raise LicenceUnknown(f"{name}: licence page {url} no longer states its terms in the expected "
                             f"words ({len(page)} bytes read) — refusing to record a licence we could "
                             f"not read")
    notes.append(f"licence page read at {url} ({len(page)} bytes)")
    return dict(name=name, url=url, quote=re.sub(r"\s+", " ", m.group(0)),
                terms_sha256=hashlib.sha256(page).hexdigest())


# ─────────────────────────────────────────────────────────────────────────────────────────────
# sources.yml — THE ONE DEFINITION SITE PER SOURCE
#
# One parser, one emitter, one validator. The emitter is TOTAL over the schema below (unknown
# keys are refused at load), which is what lets the loop write a quarantine back into sources.yml
# without reformatting the operator's file: load -> dump round-trips the shipped file byte for
# byte (proved by symbiont/selftest_sources.py).
# ─────────────────────────────────────────────────────────────────────────────────────────────
DEFAULT_SOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources.yml")

SOURCE_KEYS = ("id", "adapter", "enabled", "base_url", "endpoints", "storage", "rate_limit",
               "licence_url", "licence_text", "staleness", "budget")
SOURCE_OPTIONAL = ("note", "quarantined")
BUDGET_KEYS = ("requests", "bytes", "wall_s")
RATE_KEYS = ("requests_per_minute", "min_interval_s")
STALE_KEYS = ("policy_days",)
QUARANTINE_KEYS = ("reason", "at", "status")
STORAGE_CLASSES = ("store", "point-at")     # the STORE/POINT-AT tiers, req-P1-POINT-AT-ENFORCED-IN-CODE-the-tiers
STORAGE_ENTRY_KEYS = ("class", "reason")


def recipe_hash(recipe: dict) -> str:
    """sha256 of a fetch recipe's canonical JSON — ONE definition, three readers: the loop
    records it beside the accession, the resolve door re-computes it before fetching (a recipe
    that cannot say its own name on re-read is refused), and the tests recompute it from the
    record. Canonical form: json.dumps(recipe, sort_keys=True) — a recipe is a dict of
    primitives, so this is total and stable."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps(recipe, sort_keys=True).encode("utf-8")).hexdigest()


def resolve_url(base: str, endpoint: str) -> str:
    """An endpoint is a URL, or a path relative to base_url. THIS is the mirror lever: with every
    data endpoint relative, repointing a mirror is an edit to base_url and to nothing else."""
    e = (endpoint or "").strip()
    if e in ("", "."):
        return base
    if e.startswith(("http://", "https://")):
        return e
    return base.rstrip("/") + "/" + e.lstrip("/")


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def load_sources(path: str = DEFAULT_SOURCES) -> dict:
    """Parse + validate sources.yml -> {id: cfg}. Refuses (by name) a missing file, a malformed
    entry, a missing field, an unknown field, or a budget so large it is not a budget."""
    import yaml
    _need(os.path.isfile(path), f"sources config missing: {path}")
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    _need(isinstance(raw, dict), f"{path}: top level must be a mapping with `version` and `sources`")
    _need(raw.get("version") == 1, f"{path}: unsupported version {raw.get('version')!r} (this code reads 1)")
    _need(isinstance(raw.get("sources"), list) and raw["sources"], f"{path}: `sources` must be a non-empty list")
    out: dict[str, dict] = {}
    for i, cfg in enumerate(raw["sources"]):
        where = f"{path}: sources[{i}]"
        _need(isinstance(cfg, dict), f"{where} is not a mapping")
        sid = cfg.get("id")
        _need(isinstance(sid, str) and sid, f"{where} has no `id`")
        where = f"{path}: source {sid!r}"
        unknown = [k for k in cfg if k not in SOURCE_KEYS + SOURCE_OPTIONAL]
        _need(not unknown, f"{where} carries unknown key(s) {sorted(unknown)} — the schema is {list(SOURCE_KEYS)}")
        missing = [k for k in SOURCE_KEYS if k not in cfg]
        _need(not missing, f"{where} is missing required key(s) {missing}")
        _need(sid not in out, f"{where} is defined twice")
        _need(isinstance(cfg["adapter"], str) and cfg["adapter"], f"{where}: `adapter` must name an adapter module")
        _need(isinstance(cfg["enabled"], bool), f"{where}: `enabled` must be true or false")
        _need(isinstance(cfg["base_url"], str) and cfg["base_url"].startswith(("http://", "https://")),
              f"{where}: `base_url` must be an http(s) URL, got {cfg['base_url']!r}")
        _need(isinstance(cfg["endpoints"], dict) and cfg["endpoints"], f"{where}: `endpoints` must be a non-empty mapping")
        # the STORE/POINT-AT tier is ENFORCED here (req-P1-POINT-AT-ENFORCED-IN-CODE-the-tiers):
        # every endpoint carries a storage class, a source whose class is missing is REFUSED BY
        # NAME — never defaulted to store — and a point-at class carries its REASON (the licence
        # forbids storage, or the bulk is above a declared size).
        st = cfg["storage"]
        _need(isinstance(st, dict) and st, f"{where}: `storage` must be a non-empty mapping "
              f"(one class per endpoint: store | point-at)")
        no_class = sorted(k for k in cfg["endpoints"] if k not in st)
        _need(not no_class, f"{where}: storage class missing for endpoint(s) {no_class} — a source "
              f"whose class is missing is REFUSED BY NAME, never defaulted to store")
        not_an_endpoint = sorted(k for k in st if k not in cfg["endpoints"])
        _need(not not_an_endpoint, f"{where}: `storage` names endpoint(s) {not_an_endpoint} that "
              f"`endpoints` does not define — the config and the code disagree about what exists")
        for ep, sc in st.items():
            _need(isinstance(sc, dict), f"{where}: storage.{ep} must be a mapping "
                  f"(class: store | point-at, reason: …)")
            bad = sorted(k for k in sc if k not in STORAGE_ENTRY_KEYS)
            _need(not bad, f"{where}: storage.{ep} carries unknown key(s) {bad} — "
                  f"the schema is {list(STORAGE_ENTRY_KEYS)}")
            _need(sc.get("class") in STORAGE_CLASSES, f"{where}: storage.{ep}.class must be one "
                  f"of {list(STORAGE_CLASSES)}, got {sc.get('class')!r}")
            if sc["class"] == "point-at":
                _need(isinstance(sc.get("reason"), str) and sc["reason"].strip(),
                      f"{where}: storage.{ep} is point-at but carries no reason — the reason (the "
                      f"licence forbids storage, or the bulk is above a declared size) is the "
                      f"policy, not decoration")
        for k in BUDGET_KEYS:
            v = (cfg["budget"] or {}).get(k)
            _need(isinstance(v, int) and v > 0, f"{where}: budget.{k} must be a positive integer, got {v!r}")
        for k in RATE_KEYS:
            v = (cfg["rate_limit"] or {}).get(k)
            _need(isinstance(v, (int, float)) and v >= 0, f"{where}: rate_limit.{k} must be a number >= 0, got {v!r}")
        _need(isinstance((cfg["staleness"] or {}).get("policy_days"), int),
              f"{where}: staleness.policy_days must be an integer")
        for k in ("licence_url", "licence_text"):
            _need(isinstance(cfg[k], str) and cfg[k], f"{where}: `{k}` must be a non-empty string")
        q = cfg.get("quarantined")
        if q is not None:
            _need(isinstance(q, dict), f"{where}: `quarantined` must be null or a mapping")
            miss = [k for k in QUARANTINE_KEYS if not q.get(k)]
            _need(not miss, f"{where}: `quarantined` is missing {miss}")
        out[sid] = cfg
    return out


def dump_sources(sources: dict) -> str:
    """Emit sources.yml in canonical order. Deterministic, and total over the schema — so the loop
    can rewrite ONE field (a quarantine) without touching anything else in the operator's file."""
    def val(v) -> str:
        if v is None:
            return "null"
        if v is True:
            return "true"
        if v is False:
            return "false"
        if isinstance(v, (int, float)):
            return repr(v)
        return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = ["version: 1", "", "sources:"]
    for sid in sorted(sources):
        c = sources[sid]
        lines.append(f"  - id: {c['id']}")
        lines.append(f"    adapter: {c['adapter']}")
        lines.append(f"    enabled: {val(c['enabled'])}")
        if c.get("note"):
            lines.append(f"    note: {val(c['note'])}")
        lines.append(f"    base_url: {c['base_url']}")
        lines.append("    endpoints:")
        for k in sorted(c["endpoints"]):
            lines.append(f"      {k}: {val(c['endpoints'][k])}")
        lines.append("    storage:")
        for k in sorted(c["storage"]):
            sc = c["storage"][k]
            lines.append(f"      {k}:")
            lines.append(f"        class: {sc['class']}")   # a closed vocabulary: store | point-at, emitted bare
            if sc.get("reason"):
                lines.append(f"        reason: {val(sc['reason'])}")
        lines.append("    rate_limit:")
        for k in RATE_KEYS:
            lines.append(f"      {k}: {val(c['rate_limit'][k])}")
        lines.append(f"    licence_url: {c['licence_url']}")
        lines.append(f"    licence_text: {val(c['licence_text'])}")
        lines.append("    staleness:")
        for k in STALE_KEYS:
            lines.append(f"      {k}: {val(c['staleness'][k])}")
        lines.append("    budget:")
        for k in BUDGET_KEYS:
            lines.append(f"      {k}: {val(c['budget'][k])}")
        q = c.get("quarantined")
        if q is None:
            lines.append("    quarantined: null")
        else:
            lines.append("    quarantined:")
            for k in QUARANTINE_KEYS:
                lines.append(f"      {k}: {val(q[k])}")
    return "\n".join(lines) + "\n"


def _header(text: str) -> str:
    """Everything above the data: the operator's comment block, preserved verbatim by a rewrite."""
    out: list[str] = []
    for line in text.split("\n"):
        if line.startswith("#") or line.strip() == "":
            out.append(line)
        else:
            break
    return ("\n".join(out) + "\n") if out else ""


def set_quarantine(path: str, source_id: str, reason: str, status: str, when: str) -> dict:
    """T9: move a 404/gone source to `quarantined` IN sources.yml, atomically, verified by re-reading.

    Only that source's `quarantined:` block changes: the comment header above `version:` is carried
    over verbatim, and every key is emitted in the same fixed order, so the rewrite is a one-hunk
    diff. An operator clears it by setting `quarantined:` back to null.
    """
    with open(path, encoding="utf-8") as fh:
        header = _header(fh.read())
    sources = load_sources(path)
    _need(source_id in sources, f"{path}: no source {source_id!r} to quarantine")
    sources[source_id]["quarantined"] = dict(reason=reason, at=when, status=status)
    text = header + dump_sources(sources)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    back = load_sources(path)
    _need(back[source_id].get("quarantined", {}).get("status") == status,
          f"{path}: quarantine of {source_id!r} did not survive a re-read")
    return back[source_id]


def check_registry(sources: dict, adapters: dict, decisions_dir: str | None = None,
                   now=None, max_age_days: int | None = None) -> None:
    """The load-time refusals of the row, BY NAME:
         a source enabled in config with no adapter, and an adapter with no config entry.
    Both mean config and code disagree about what exists; a run over that disagreement would
    silently publish half a claim, so it is refused before a single request leaves the box.

    AND THE SOURCE-DISCOVERY REFUSAL (req-P1-THE-FRAMEWORK-GAINS-A-SOURCE-DISCOVE): every
    SHIPPED adapter must carry reports/adapters/<adapter>.decision.md, fresh within 90 days.
    `check_decisions` is the refusal itself and is TOTAL over the dict it is given; here it is
    applied to the adapters that are shipped files (those whose module lives under
    symbiont/adapters/), because a decision record governs the station's own adapters. A
    hand-built or trial-loaded adapter object (no module provenance) is not a shipped file and
    is left to the caller that built it — the admission fixture must be able to try a draft."""
    by_adapter: dict[str, str] = {}
    for sid in sorted(sources):
        cfg = sources[sid]
        ad = cfg["adapter"]
        if ad in by_adapter:
            raise ConfigError(f"adapter {ad!r} is configured twice (sources {by_adapter[ad]!r} and {sid!r}) — "
                              f"one adapter, one entry")
        by_adapter[ad] = sid
        if cfg["enabled"] and ad not in adapters:
            raise ConfigError(f"source {sid!r} is enabled in sources.yml but no adapter module provides "
                              f"adapter {ad!r} — refusing (repoint `adapter`, or set enabled: false)")
    orphan = sorted(a for a in adapters if a not in by_adapter)
    if orphan:
        raise ConfigError(f"adapter(s) {orphan} exist under symbiont/adapters/ with no entry in "
                          f"sources.yml — refusing: an adapter without config has no endpoint, licence "
                          f"or budget, so it may not be run")
    shipped = {name: ad for name, ad in adapters.items() if _is_shipped(ad)}
    check_decisions(shipped, decisions_dir=decisions_dir, now=now, max_age_days=max_age_days)


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE DECISION RECORD — the scout phase, as a refusal (req-P1-THE-FRAMEWORK-GAINS-A-SOURCE-DISCOVE)
#
# `python3 -m harvester.discover <source>` writes reports/adapters/<source>.decision.md with a
# verdict (USE-MCP | WRAP-MCP | WRITE-ADAPTER | NO-SOURCE), the URLs it read and the date. That
# file is the ONLY place "which way do we fetch" lives. The contract reads it here and REFUSES an
# adapter whose record is missing, malformed, or older than 90 days — because a source can lose
# its MCP, and a direction nobody re-checked is a guess wearing a record's clothes.
# ─────────────────────────────────────────────────────────────────────────────────────────────
VERDICTS = ("USE-MCP", "WRAP-MCP", "WRITE-ADAPTER", "NO-SOURCE")
DECISION_MAX_AGE_DAYS = 90
DECISIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "reports", "adapters")


def _is_shipped(adapter) -> bool:
    """True only for an adapter loaded from a file under symbiont/adapters/ (the station's own
    plug-ins). `load_adapters` stamps `_module_file`; a draft, a trial-loaded fixture or a
    hand-built object has none and is not a shipped file."""
    f = getattr(adapter, "_module_file", None)
    if not f:
        return False
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapters") + os.sep
    return os.path.abspath(f).startswith(root)


def read_decision(path: str) -> dict:
    """Parse a decision record -> {source, adapter, verdict, decided_at, max_age_days, probe}.
    Raises DecisionError with the reason named when the record cannot say its verdict or date.
    The canonical lines are the FIRST unindented `key: value` block; the prose below is free."""
    if not os.path.isfile(path):
        raise DecisionError(f"no decision record at {path}")
    fields: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^([a-z_]+):[ \t]*(\S.*?)[ \t]*$", line)
                if m and m.group(1) in ("source", "adapter", "verdict", "decided_at",
                                        "max_age_days", "probe", "need"):
                    fields.setdefault(m.group(1), m.group(2).strip())
    except OSError as e:
        raise DecisionError(f"the decision record {path} could not be read ({type(e).__name__}: {e})") from e
    verdict = fields.get("verdict", "")
    if not verdict:
        raise DecisionError(f"{path} carries no `verdict:` line — the record is the only place "
                            f"'which way do we fetch' lives, and it does not say")
    if verdict not in VERDICTS:
        raise DecisionError(f"{path} carries verdict {verdict!r}, which is not one of {list(VERDICTS)}")
    stamp = fields.get("decided_at", "")
    if not stamp:
        raise DecisionError(f"{path} carries no `decided_at:` line — a decision with no date "
                            f"cannot be shown to be current")
    if _parse_stamp(stamp) is None:
        raise DecisionError(f"{path} carries decided_at {stamp!r}, which is not an ISO-8601 timestamp")
    return dict(source=fields.get("source", ""), adapter=fields.get("adapter", ""),
                verdict=verdict, decided_at=stamp,
                max_age_days=fields.get("max_age_days", ""), probe=fields.get("probe", ""))


def _parse_stamp(stamp: str):
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def decision_age_days(decided_at: str, now=None) -> int:
    """Whole days between the record's date and `now` (UTC). Floor, so 'older than 90' is
    honest: a record is refused only once 91 whole days have passed."""
    import datetime as dt
    t = _parse_stamp(decided_at)
    if t is None:
        raise DecisionError(f"decided_at {decided_at!r} is not an ISO-8601 timestamp")
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return max(0, int((now - t).total_seconds() // 86400))


def check_decisions(adapters: dict, decisions_dir: str | None = None, now=None,
                    max_age_days: int | None = None) -> None:
    """THE REFUSAL: every adapter given must have a fresh decision record, or it is REFUSED BY
    NAME. Total over `adapters` — the caller decides WHICH adapters are in scope (check_registry
    passes the shipped ones). A missing record names the adapter and the path; a stale record
    quotes its age and the limit; a malformed record names what it could not say."""
    root = decisions_dir or DECISIONS_DIR
    limit = DECISION_MAX_AGE_DAYS if max_age_days is None else int(max_age_days)
    for name in sorted(adapters):
        path = os.path.join(root, f"{name}.decision.md")
        if not os.path.isfile(path):
            raise DecisionError(
                f"adapter {name!r} has NO decision record at {path} — REFUSED BY NAME: which "
                f"way we fetch is not written down. Run `python3 -m harvester.discover {name}` "
                f"and re-run; reports/adapters/ is the only place the direction lives")
        rec = read_decision(path)
        age = decision_age_days(rec["decided_at"], now)
        if age > limit:
            raise DecisionError(
                f"adapter {name!r} has a decision record at {path} dated {rec['decided_at']} — "
                f"{age} days old, the limit is {limit} — REFUSED BY NAME: a source can lose its "
                f"MCP, so the direction is re-probed, never assumed. Run "
                f"`python3 -m harvester.discover {name}` and re-run")



def load_adapters(package: str = "symbiont.adapters") -> dict:
    """Import every module under adapters/ and return {name: adapter_object}."""
    pkg = importlib.import_module(package)
    found = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{package}.{info.name}")
        ad = getattr(mod, "ADAPTER", None)
        if ad is None:
            # A module may DECLARE itself the plug-in of ANOTHER ARM instead of a collector plug-in:
            # module-level `OTHER_ARM = "<arm>"`. Today that is symbiont/adapters/mcp_source.py, the
            # generic MCP source adapter (req-P1-A-GENERIC-MCP-ADAPTER-any-external-M): it answers
            # the same four calls in its OWN shape, is driven by its own runner, and is neither an
            # id_authority nor an xref_provider — so filing it under a gene-arm role would be a lie
            # in the registry. The declaration is a NAME the module states: an UNDECLARED
            # non-plug-in still refuses here, by name, exactly as before.
            other_arm = getattr(mod, "OTHER_ARM", "")
            if isinstance(other_arm, str) and other_arm:
                continue
            raise RuntimeError(f"{mod.__name__} has no module-level ADAPTER — not a plug-in")
        for attr in ("name", "role", "configure", "discover", "fetch", "normalise", "verify"):
            if not hasattr(ad, attr):
                raise RuntimeError(f"{mod.__name__} ADAPTER missing {attr}")
        if ad.role not in ("id_authority", "xref_provider"):
            raise RuntimeError(f"{mod.__name__} role {ad.role!r} not in (id_authority, xref_provider)")
        ad._module_file = os.path.abspath(getattr(mod, "__file__", ""))   # provenance: a SHIPPED file
        found[ad.name] = ad
    if not found:
        raise RuntimeError("no adapters found — an empty symbiont is a lie, not a harvest")
    return found
