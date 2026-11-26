"""research — THE RESEARCH SPEC: what a question REQUIRES, hashed, and refused when coverage is short.

WHY (owner + reviewer, 2026-11-11): the station can acquire data successfully and still fail to
fulfil the question — and produce a plausible result that does not reveal the shortfall. The
registry (sources.yml) is domain-neutral: it says which datasets exist, trusted, at which release.
It does NOT say what THIS investigation requires. This module is the second half of that sentence,
and it makes the intersection between the two provable instead of assumed:

    registry  (sources.yml)          which datasets exist, admitted, trusted, at which release
    SPEC      (research/<id>.yml)    what THIS investigation requires — and what is only optional
    RUN RECORD (runs/<id>.json)      the release pins RESOLVED at run time
    MANIFEST  (the handover)         the coverage block: what was acquired, what was missing

THE BINARY SPLIT (deliverable 3) that makes reruns honest: the SPEC carries REQUIREMENTS, never a
release. The release is resolved at run time and lives in the run record. So the same spec re-run
against a newer release is the SAME investigation (spec_sha256 unchanged, run record shows the new
release), and a changed spec is a NEW one (its sha changes). A spec that tries to bake a release in
is REFUSED BY NAME — see `_check_dataset_name`.

THE CANONICALISER (deliverable 2): `canonical_bytes()` builds the spec's identity from the PARSED
fields — sorted keys, whitespace-collapsed strings, set-like lists sorted and de-duplicated — and
`spec_sha256()` hashes exactly those bytes. Comments, key order and spacing are GONE after the YAML
parse, so reformatting the file cannot change its identity. The canonical form is JSON with
`sort_keys=True` and no insignificant whitespace: two specs that mean the same thing hash the same.

COVERAGE REFUSAL (deliverable 4) is `coverage()`: it compares every declared dataset against the
station's admitted+enabled set BEFORE anything is spent. A missing REQUIRED dataset is a refusal
the loop acts on (`REFUSED[missing-dataset]`, acquisition NOT STARTED); a missing OPTIONAL dataset
is allowed but is named in `missing[]` and downgrades the coverage to `complete: false`,
`status: "partial"` — silent downgrade is forbidden in both directions.

THE PER-DATASET STATUS (req-P1-RESEARCH-CHECK-a-read-door-that-says). `coverage()` also answers
WHY a dataset is not there — one word per dataset, from a FIXED vocabulary, so a reader is never
shown one blank where five different facts live:

    present       the store holds it, and this is its RELEASE (quoted, never assumed)
    not-admitted  sources.yml has no source answering to the name
    not-enabled   declared, but `enabled: false` (or quarantined, or not selected by --sources)
    not-acquired  admitted and enabled, and the store holds NO snapshot for it (never fetched)
    stale         present, but the release the store holds is BEHIND the upstream release the last
                  run that reached the metadata endpoint observed

THE ONE-DEFINITION RULE, and it is a rule about disagreement rather than taste: THIS function is
the only coverage evaluator in the farm. The MCP reading door `research_check` calls it
(annot/core.py) and composes no verdict of its own — a second evaluator is exactly how the CLI and
the MCP door start disagreeing about whether a spec is executable. The status layer reads
`state/<id>.json` and `runs/*.json` OFF DISK and makes ZERO requests: presence is a fact about the
corpus, and the reading side of this house must never acquire.

THE SCHEMA (an unknown key, a missing key, or a release pin is REFUSED BY NAME):

    research_id        <id>            the spec's own name: [A-Za-z0-9][A-Za-z0-9._-]*
    question           one sentence    what this investigation asks (NOT which release answers it)
    arms               mapping         ALL THREE declared — gene_identity, phenotype_ontology,
                                       literature — each a non-empty string, or null for "not part
                                       of this investigation". An arm is never OMITTED: what the
                                       investigation does not do is declared, not implied.
    filters            mapping         species · term_sets{<arm>: [terms]} · date_cutoff
                                       (date_cutoff: null means "no cutoff", which is declared)
    required_datasets  non-empty list  <publisher>:<dataset> — or a sources.yml id — this
                                       investigation CANNOT be answered without. Missing -> REFUSED
    optional_datasets  list            wanted, not required. Missing -> allowed, named in missing[],
                                       coverage complete:false / status "partial".
                                       OPTIONALITY IS DECLARED BEFORE THE RUN: it is inside the
                                       hash, so flipping a dataset from required to optional after
                                       seeing the results changes spec_sha256, and the run record's
                                       spec no longer matches the file — a NEW investigation, not a
                                       quiet edit of this one (see verify_spec()).

Run:
    python3 -m symbiont.research --hash      symbiont/research/mouse-gene-identity.yml
    python3 -m symbiont.research --canonical symbiont/research/mouse-gene-identity.yml
    python3 -m symbiont.research --check     symbiont/research/mouse-gene-identity.yml
    python3 -m symbiont.research --self-test        (offline; the four NSC claims, no network)
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

RESEARCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research")
SPEC_VERSION = 1

# the three arms, as the row names them. ALL THREE are declared in every spec — an arm that is not
# part of this investigation is `null`, never an omission.
ARMS = ("gene_identity", "phenotype_ontology", "literature")

FILTER_KEYS = ("species", "term_sets", "date_cutoff")
SPEC_KEYS = ("research_id", "question", "arms", "filters", "required_datasets", "optional_datasets")

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# a dataset FREEDOM FROM RELEASE PINS: these are the shapes an operator reaches for when they try
# to pin a release inside a question, and each one would make the spec expire.
RELEASE_PIN_CHARS = ("@", "=", "~", "#")


class SpecError(contract.ConfigError):
    """A spec that cannot be honoured as written — REFUSED BY NAME before the run (exit 2)."""
    code = "spec"


class MissingDataset(contract.Refusal):
    """A required dataset is not admitted+enabled: REFUSED[missing-dataset] (acquisition NOT STARTED)."""
    code = "missing-dataset"


# ---------------------------------------------------------------- small helpers
def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise SpecError(msg)


def _collapse(s: str) -> str:
    """The ONE string normalisation the identity uses: every run of whitespace becomes one space.
    A spec re-indented, or a `question:` re-wrapped across lines, means the same thing and hashes
    the same."""
    return " ".join(str(s).split())


def _str_list(v, where: str, what: str) -> list[str]:
    _need(isinstance(v, list), f"{where}: {what} must be a list, got {type(v).__name__}")
    out = []
    for i, x in enumerate(v):
        _need(isinstance(x, str) and x.strip(), f"{where}: {what}[{i}] must be a non-empty string, got {x!r}")
        out.append(_collapse(x))
    return out


def _check_dataset_name(name: str, where: str) -> str:
    """A dataset NAME, never a release. `<publisher>:<dataset>` (or a sources.yml id).

    WHY THIS REFUSES: deliverable 3 is the binary split — the spec carries requirements, the run
    record carries the release pins. `mgi:MRK_ENSEMBL@2026-11-09` in a spec would bake one release
    into the question, so a re-run against a newer release would be refused forever (or, worse,
    silently answered from the old one). The refusal names the offending string.
    """
    _need(isinstance(name, str) and name.strip(), f"{where}: a dataset name must be a non-empty string, got {name!r}")
    n = _collapse(name)
    _need(" " not in n, f"{where}: dataset name {n!r} carries whitespace — the name is "
                        f"<publisher>:<dataset> or a sources.yml id")
    for ch in RELEASE_PIN_CHARS:
        _need(ch not in n,
              f"{where}: dataset name {n!r} carries {ch!r} — a spec states WHAT this investigation "
              f"requires, never WHICH RELEASE: the release is resolved at run time and lives in the "
              f"run record (a spec pinned to a release would make the same investigation a "
              f"different one tomorrow)")
    _need(not n.endswith(":"), f"{where}: dataset name {n!r} ends with ':' — an unset dataset half")
    return n


def _default_sources_cfg() -> dict:
    return contract.load_sources(contract.DEFAULT_SOURCES)


# ---------------------------------------------------------------- load + validate (BY NAME)
def load_spec(path: str) -> dict:
    """Parse + validate a spec FILE. Refuses (by name) a missing file, a malformed mapping, an
    unknown key, a missing key, an unset arm, a release pin, or a dataset declared both required and
    optional. Returns the RAW parsed mapping — the identity is derived by canonical_bytes()."""
    import yaml
    _need(os.path.isfile(path), f"research spec missing at run time: {path} — the coverage gate "
                                f"refuses to run a question it cannot read")
    with open(path, encoding="utf-8") as fh:
        try:
            raw = yaml.safe_load(fh)
        except yaml.YAMLError as e:
            raise SpecError(f"{path}: research spec is not YAML: {e}") from e
    return validate_spec(raw, path)


def validate_spec(raw, path: str) -> dict:
    """THE spec schema, stated ONCE. `raw` is an already-parsed mapping and `path` is only the
    LABEL every refusal names — a file path for load_spec(), or the label the reading door uses for
    a requirement set that was handed to it as arguments (there is no second schema: a door that
    validated its own arguments would accept a spec the loop refuses, or refuse one it accepts)."""
    _need(isinstance(raw, dict), f"{path}: top level must be a mapping with {list(SPEC_KEYS)}")
    unknown = [k for k in raw if k not in SPEC_KEYS + ("version",)]
    _need(not unknown,
          f"{path}: carries unknown key(s) {sorted(unknown)} — the schema is {list(SPEC_KEYS)}; a "
          f"release pin (`release:`/`version:` of a dataset) is not a spec field, because the run "
          f"record carries the releases resolved at run time")
    if "version" in raw:
        _need(raw["version"] == SPEC_VERSION,
              f"{path}: unsupported version {raw['version']!r} (this code reads {SPEC_VERSION})")
    missing = [k for k in SPEC_KEYS if k not in raw]
    _need(not missing, f"{path}: research spec is missing required key(s) {missing}")

    rid = raw["research_id"]
    _need(isinstance(rid, str) and ID_RE.match(rid),
          f"{path}: research_id {rid!r} must match {ID_RE.pattern} (it names the artefact and the "
          f"run records that used it)")

    q = raw["question"]
    _need(isinstance(q, str) and q.strip(), f"{path}: `question` must be a non-empty string")
    qc = _collapse(q)
    _need(qc[-1] in ".?!", f"{path}: `question` must be ONE SENTENCE ending in . ? or ! — got {qc[-1]!r}")

    arms = raw["arms"]
    _need(isinstance(arms, dict), f"{path}: `arms` must be a mapping of the three arms {list(ARMS)}")
    unknown_arms = sorted(k for k in arms if k not in ARMS)
    _need(not unknown_arms, f"{path}: `arms` carries unknown arm(s) {unknown_arms} — the three arms "
                            f"are {list(ARMS)}")
    unset = [a for a in ARMS if a not in arms]
    _need(not unset, f"{path}: `arms` does not DECLARE {unset} — an arm that is not part of this "
                     f"investigation is `null`, never omitted: what the investigation does not do "
                     f"is declared, not implied")
    for a in ARMS:
        v = arms[a]
        _need(v is None or (isinstance(v, str) and v.strip()),
              f"{path}: arms.{a} must be a non-empty string or null (not part of this "
              f"investigation), got {v!r}")

    f = raw["filters"]
    _need(isinstance(f, dict), f"{path}: `filters` must be a mapping with {list(FILTER_KEYS)}")
    unknown_f = sorted(k for k in f if k not in FILTER_KEYS)
    _need(not unknown_f, f"{path}: `filters` carries unknown key(s) {unknown_f} — the schema is "
                         f"{list(FILTER_KEYS)}")
    for k in FILTER_KEYS:
        _need(k in f, f"{path}: `filters` is missing {k} (date_cutoff: null DECLARES 'no cutoff')")
    _need(isinstance(f["species"], str) and f["species"].strip(),
          f"{path}: filters.species must be a non-empty string, got {f['species']!r}")
    # an UNQUOTED YAML date parses as a datetime.date — that is the same value as its ISO string, so
    # it is normalised rather than refused (the identity must not depend on quoting style)
    if isinstance(f["date_cutoff"], (dt.date, dt.datetime)):
        raw["filters"]["date_cutoff"] = f["date_cutoff"].isoformat()[:10]
        f = raw["filters"]
    _need(f["date_cutoff"] is None or (isinstance(f["date_cutoff"], str) and DATE_RE.match(str(f["date_cutoff"]))),
          f"{path}: filters.date_cutoff must be null or YYYY-MM-DD, got {f['date_cutoff']!r}")
    ts = f["term_sets"]
    _need(ts is None or isinstance(ts, dict),
          f"{path}: filters.term_sets must be a mapping of arm -> [terms] (or empty)")
    for a, terms in (ts or {}).items():
        _need(a in ARMS, f"{path}: filters.term_sets names {a!r}, which is not one of the arms {list(ARMS)}")
        _str_list(terms, f"{path}: filters.term_sets.{a}", "terms")

    req = _str_list(raw["required_datasets"], f"{path}: required_datasets", "dataset")
    opt = _str_list(raw["optional_datasets"], f"{path}: optional_datasets", "dataset")
    _need(bool(req), f"{path}: required_datasets is empty — a spec that requires nothing cannot "
                     f"refuse anything, and the coverage gate would pass every shortfall silently")
    req = [_check_dataset_name(x, f"{path}: required_datasets") for x in req]
    opt = [_check_dataset_name(x, f"{path}: optional_datasets") for x in opt]
    both = sorted(set(req) & set(opt))
    _need(not both, f"{path}: dataset(s) {both} are declared BOTH required and optional — "
                    f"optionality is a decision made BEFORE the run, and a dataset that is both is "
                    f"the decision left unmade")
    _need(len(set(req)) == len(req), f"{path}: required_datasets names a dataset twice")
    _need(len(set(opt)) == len(opt), f"{path}: optional_datasets names a dataset twice")
    return raw


def spec_from_requirements(research_id: str = "", question: str = "", arms: dict | None = None,
                           filters: dict | None = None, required=None, optional=None) -> tuple[dict, list]:
    """A spec built from ARGUMENTS, validated by the SAME schema and hashed by the SAME canonical
    form — for a caller that has no spec file yet (an agent asking 'could the corpus answer this?'
    before writing one). Returns (spec, defaults_applied).

    The defaults are DECLARED, not hidden: every one of them is listed in `defaults_applied` and
    travels in the answer, because an unstated default that changes a verdict is a lie by omission
    (`filters.species: "*"` means NO SPECIES WAS DECLARED — this is a requirement set, not a
    question). The point of the arguments is the DATASETS; everything else exists so that the same
    validator, and so the same identity rule, apply to what the door was handed.
    """
    defaults: list[str] = []
    rid = _collapse(research_id) if research_id else ""
    if not rid:
        rid = "inline-research-check"
        defaults.append(f"research_id = {rid!r} (none declared)")
    q = _collapse(question) if question else ""
    if not q:
        q = "No question was declared with this requirement set."
        defaults.append("question = 'No question was declared with this requirement set.'")

    given_arms = dict(arms or {})
    arm_block = {a: given_arms.get(a) for a in ARMS}
    absent = [a for a in ARMS if a not in given_arms]
    if absent:
        defaults.append(f"arms {absent} declared null (not part of this investigation)")

    given_filters = dict(filters or {})
    flt = {"species": given_filters.get("species"),
           "term_sets": given_filters.get("term_sets") or {},
           "date_cutoff": given_filters.get("date_cutoff")}
    if not flt["species"]:
        flt["species"] = "*"
        defaults.append("filters.species = '*' (no species was declared — this is a requirement "
                        "set, not a question)")
    if "date_cutoff" not in given_filters:
        defaults.append("filters.date_cutoff = null (no cutoff declared)")

    raw = {"research_id": rid, "question": q, "version": SPEC_VERSION, "arms": arm_block,
           "filters": flt,
           "required_datasets": list(required or []),
           "optional_datasets": list(optional or [])}
    return validate_spec(raw, "research_check (inline requirement set)"), defaults


# ---------------------------------------------------------------- the canonicaliser + its sha256
def canonical(spec: dict) -> dict:
    """The spec's IDENTITY as data: only the declared fields, keys sorted, strings
    whitespace-collapsed, set-like lists (datasets, term sets) sorted and de-duplicated.

    Deterministic by construction, and blind to the file's cosmetics: YAML comments never reach
    the parsed mapping, and key order / spacing / indentation / flow-vs-block style are gone by
    the time this runs. That is what makes `reformatting cannot change an identity` true rather
    than hoped for.
    """
    f = spec["filters"]
    ts = {}
    for a in ARMS:                                   # arms named in a FIXED order, for the reader
        terms = (f.get("term_sets") or {}).get(a) or []
        ts[a] = sorted({_collapse(t) for t in terms})
    return {
        "research_id": spec["research_id"],
        "question": _collapse(spec["question"]),
        "arms": {a: (None if spec["arms"][a] is None else _collapse(spec["arms"][a])) for a in ARMS},
        "filters": {
            "species": _collapse(f["species"]),
            "term_sets": ts,
            "date_cutoff": f["date_cutoff"],
        },
        # datasets are SETS by declaration: reordering them in the file is not a new investigation
        "required_datasets": sorted({_collapse(x) for x in spec["required_datasets"]}),
        "optional_datasets": sorted({_collapse(x) for x in spec["optional_datasets"]}),
    }


def canonical_bytes(spec: dict) -> bytes:
    """The bytes the identity is the sha256 OF. Sorted keys, no insignificant whitespace, one
    trailing newline, ASCII — so two runs on any host produce the same 64 hex characters."""
    return (json.dumps(canonical(spec), sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("utf-8")


def spec_sha256(spec: dict) -> str:
    """THE identity. It changes when the REQUIREMENTS change, and only then — not when the file is
    reformatted, not when the data behind a dataset is released again."""
    return hashlib.sha256(canonical_bytes(spec)).hexdigest()


def selection(spec: dict) -> dict:
    """What the run CHOSE (arms + filters) — the manifest's `selection` block, taken from the same
    canonical form so the handover cannot spell the selection differently from the hash."""
    c = canonical(spec)
    return {"arms": c["arms"], "filters": c["filters"]}


def verify_spec(path: str, recorded_sha: str) -> dict:
    """Is the spec on disk still the spec this run used? An edited spec is a DIFFERENT investigation
    (its sha changed), so a run record may never be re-read as if it belonged to the new one."""
    out = {"spec_path": os.path.abspath(path) if path else "", "recorded_spec_sha256": recorded_sha,
           "current_spec_sha256": None, "changed_since_run": None}
    if not path or not os.path.isfile(path):
        out["changed_since_run"] = None          # nothing to compare against; never guessed
        return out
    try:
        cur = spec_sha256(load_spec(path))
    except SpecError as e:
        out["current_spec_sha256"] = None
        out["changed_since_run"] = True
        out["error"] = str(e)
        return out
    out["current_spec_sha256"] = cur
    out["changed_since_run"] = (cur != recorded_sha) if recorded_sha else None
    return out


# ---------------------------------------------------------------- the registry side of the intersection
def dataset_names(sid: str, cfg: dict) -> set[str]:
    """Every name this source ANSWERS TO: its sources.yml id, and — when the entry declares them —
    its dataset identity `<publisher>:<dataset>`. A spec may name either; an entry that declares no
    publisher/dataset simply answers to its id (nothing is invented)."""
    names = {sid}
    d = (contract.identity(cfg) or {}).get("dataset_id")
    if d:
        names.add(d)
    return names


def registry_view(sources: dict, only: list[str] | None = None) -> dict:
    """What the station can serve RIGHT NOW, per source: present (admitted — it is in sources.yml),
    enabled, quarantined, selected. Admission IS the registry entry: contract.check_registry has
    already refused a config/code disagreement before the loop reaches this point."""
    out = {}
    for sid, cfg in sorted(sources.items()):
        out[sid] = {
            "source": sid,
            "dataset": (contract.identity(cfg) or {}).get("dataset_id"),
            "names": sorted(dataset_names(sid, cfg)),
            "admitted": True,
            "enabled": bool(cfg.get("enabled")),
            "quarantined": bool(cfg.get("quarantined")),
            "selected": (only is None) or (sid in only),
        }
    return out


def _usable(entry: dict) -> bool:
    return entry["admitted"] and entry["enabled"] and not entry["quarantined"] and entry["selected"]


def _why_not(entry: dict) -> str:
    if not entry["admitted"]:
        return "not admitted (no sources.yml entry)"
    if not entry["enabled"]:
        return "in sources.yml but not enabled (enabled: false)"
    if entry["quarantined"]:
        return "quarantined in sources.yml (its way back is the re-admission card)"
    if not entry["selected"]:
        return "enabled but not selected by --sources for this run"
    return "usable"


def _dataset_digest(snaps: dict) -> tuple[str, list[dict]]:
    """A DATASET-level content hash: the sha256 of its resources' (reskey, sha256) lines, sorted.
    Per-resource hashes travel beside it, so a reader can check the bytes and not just the summary."""
    rows = []
    for reskey in sorted(snaps or {}):
        s = snaps[reskey] or {}
        rows.append({"reskey": reskey, "sha256": s.get("sha256") or s.get("payload_sha256") or "",
                     "path": s.get("path") or s.get("accession") or "",
                     "release": s.get("release") or "",
                     "storage_class": "point-at" if s.get("pointat") else "store"})
    blob = "\n".join(f"{r['reskey']}:{r['sha256']}" for r in rows).encode("utf-8")
    return (hashlib.sha256(blob).hexdigest() if rows else ""), rows


# ---------------------------------------------------------------- the COVERAGE STATUS vocabulary
# FIVE words, FIXED. A reading door that answered "missing" for all of them would be telling the
# reader one thing where the station knows five — and an empty phenotype section can mean any of
# them (no biology · no dataset admitted · admitted but disabled · admitted but never fetched ·
# fetched but stale). Each word below is a DIFFERENT fact, and each reason says which file says so.
STATUS_PRESENT = "present"
STATUS_NOT_ADMITTED = "not-admitted"
STATUS_NOT_ENABLED = "not-enabled"
STATUS_NOT_ACQUIRED = "not-acquired"
STATUS_STALE = "stale"
STATUS_VOCABULARY = (STATUS_PRESENT, STATUS_NOT_ADMITTED, STATUS_NOT_ENABLED,
                     STATUS_NOT_ACQUIRED, STATUS_STALE)
# the three a REQUIRED dataset may not be in: present and stale both mean the corpus HAS it
MISSING_STATUSES = (STATUS_NOT_ADMITTED, STATUS_NOT_ENABLED, STATUS_NOT_ACQUIRED)


def _state_path(store: str, sid: str) -> str:
    return os.path.join(store, "state", f"{sid}.json")


def _state_of(store: str, sid: str) -> dict:
    """The store's CURRENT-release pointer for one source: {release, release_at, snapshots, …}, or
    {} when it is absent or unreadable. READ ONLY — this function never creates, touches or
    repairs a state file; an unreadable one is reported as absent by name, not guessed at."""
    try:
        with open(_state_path(store, sid), encoding="utf-8") as fh:
            s = json.load(fh)
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def _run_records(store: str) -> list[dict]:
    """Every run record on disk, oldest first. Read through health.py, which is the ONE definition
    of 'the run records of this store' (update.py and health.py both read it there): a second reader
    here would be a second answer to 'what did the station last see'."""
    if not store or not os.path.isdir(os.path.join(store, "runs")):
        return []
    from . import health
    return health._run_records(store)


def _upstream_seen(recs: list[dict], sid: str) -> tuple[str | None, str | None, str | None]:
    """The upstream release the station last OBSERVED for this source, and which run observed it:
    the newest run record that reached the source's metadata endpoint (`discover.release`, or the
    `stale.upstream_release` that same discovery wrote). ZERO REQUESTS — this is read off disk,
    which is what lets the reading side answer 'stale' without touching the network."""
    best: tuple[str, str, str] | None = None
    for r in recs or []:
        if not isinstance(r, dict):
            continue
        s = (r.get("sources") or {}).get(sid) or {}
        release = (((s.get("discover") or {}).get("release")
                    or (s.get("stale") or {}).get("upstream_release")) or "")
        if not release:
            continue
        at = r.get("started_at") or ""
        if best is None or at >= best[2]:
            best = (release, r.get("run_id") or "", at)
    return best or (None, None, None)


def _behind(stored: str, upstream: str) -> bool | None:
    """Is OUR stored release BEHIND the upstream one? The comparison rule is update.py's own
    trailing-token rule (`_token`), imported rather than re-written: two rules for 'which release is
    newer' is how a station starts disagreeing with itself about what is out of date. None means
    the two ids cannot be ordered at all — which is said, never guessed."""
    from .update import _token
    a, b = _token(stored), _token(upstream)
    if not a or not b:
        return None
    if a.isdigit() and b.isdigit():
        return int(a) < int(b)
    return a < b


def coverage(spec: dict, sources: dict, only: list[str] | None = None,
             record: dict | None = None, store: str = "", spec_path: str = "") -> dict:
    """THE INTERSECTION, as a block: what the spec requires, what the station had, what was
    acquired (with the release pins the RUN resolved), what is missing, and whether that is fatal.

    Before the run (`record=None`) this is the GATE: `missing_by_class["required"]` non-empty means
    the loop must refuse and spend nothing. After the run it is the MANIFEST's coverage block.

    `complete` is true only when EVERY declared dataset (required and optional) is present; a
    missing OPTIONAL dataset is allowed, but it is not allowed to look complete — it yields
    `complete: false`, `status: "partial"` and names itself in `missing[]`.

    AND, per dataset, WHY: `datasets[]` carries one of the five STATUS_VOCABULARY words with a
    reason and the file that says so, `dataset_status` is the same thing as a mapping, and
    `verdict` is the one line a reader pastes ("OK" | "REFUSED[missing-dataset] <name> (<status>)").
    THIS is the only coverage evaluator in the farm: the MCP door `research_check` calls it.
    """
    reg = registry_view(sources, only)
    req = [_collapse(x) for x in spec["required_datasets"]]
    opt = [_collapse(x) for x in spec["optional_datasets"]]
    ordered = [(n, True) for n in req] + [(n, False) for n in opt]

    def resolve(name: str) -> tuple[str | None, dict | None, str]:
        hits = [e for e in reg.values() if name in e["names"]]
        if not hits:
            return None, None, "not in the registry (sources.yml has no source answering to this name)"
        if len(hits) > 1:
            ids = sorted(e["source"] for e in hits)
            return None, None, (f"AMBIGUOUS: {len(hits)} sources answer to this name ({ids}) — "
                                f"refusing to pick one")
        e = hits[0]
        if not _usable(e):
            return None, e, _why_not(e)
        return e["source"], e, "usable"

    acquired, missing_req, missing_opt, detail = [], [], [], {}
    satisfied = []
    facts: dict[str, dict] = {}
    # CAN PRESENCE BE SEEN AT ALL? Only with a run record (what this run acquired) or a store (what
    # the corpus holds). Without either this call is the pre-flight GATE and answers the registry
    # question only — it does not say a dataset was never fetched when it never looked.
    recs = _run_records(store) if store else []
    presence_eyed = (record is not None) or bool(store and os.path.isdir(store))
    for name, is_required in ordered:
        sid, entry, why = resolve(name)
        if sid is None:
            (missing_req if is_required else missing_opt).append(name)
            detail[name] = {"required": is_required, "reason": why,
                            "candidates": sorted(e["source"] for e in reg.values() if name in e["names"])}
            facts[name] = {"source": (entry or {}).get("source"), "why": why,
                           "status": (STATUS_NOT_ENABLED if entry is not None
                                      else STATUS_NOT_ADMITTED)}
            continue
        facts[name] = {"source": sid, "why": why}
        satisfied.append((name, sid, is_required))

    if record is not None:
        for name, sid, is_required in satisfied:
            src = (record.get("sources") or {}).get(sid) or {}
            release = ((src.get("discover") or {}).get("release")) or None
            snaps = src.get("snapshots") or {}
            if not snaps:
                # the source was admitted+enabled but yielded nothing this run: the dataset did not
                # arrive, whatever the registry said. Required -> missing; optional -> named here.
                (missing_req if is_required else missing_opt).append(name)
                why = ("admitted and enabled, but this run acquired nothing "
                       f"for it (status {record.get('status')!r})")
                detail[name] = {"required": is_required, "source": sid, "reason": why,
                                "candidates": [sid]}
                facts[name].update(present=False, release=None, why=why,
                                   origin=f"run record {record.get('run_id')!r}")
                continue
            facts[name].update(present=True, release=release,
                               origin=f"run record {record.get('run_id')!r}")
            dsha, rows = _dataset_digest(snaps)
            identity = contract.identity(sources.get(sid) or {}, release)
            acquired.append({
                "dataset": name,
                "source": sid,
                "release": release,
                "version_id": identity.get("version_id"),
                # WHERE THIS CAME FROM, by name: the run record that resolved the release, the
                # state the release is stamped in, and the bytes themselves.
                "acquisition": {
                    "run_id": record.get("run_id"),
                    "run_record": (os.path.join(store, "runs", f"{record.get('run_id')}.json")
                                   if store else ""),
                    "state": (os.path.join(store, "state", f"{sid}.json") if store else ""),
                    "raw": [{"reskey": r["reskey"], "sha256": r["sha256"], "path": r["path"]}
                            for r in rows],
                },
                "sha256": dsha,
                "resources": rows,
            })

    # ── THE CORPUS SIDE, when nothing was handed in as a record ──────────────────────────────────
    # This is the READING door's half: the store's own state/<id>.json says whether the dataset was
    # ever fetched, and which release the station holds — the two facts an empty answer section was
    # previously silent about. Zero requests, zero writes: the file is opened for reading only.
    if record is None and presence_eyed:
        for name, sid, is_required in satisfied:
            st = _state_of(store, sid)
            snaps = st.get("snapshots") or {}
            where = _state_path(store, sid)
            if not snaps:
                (missing_req if is_required else missing_opt).append(name)
                why = ("admitted and enabled, but the store holds no snapshot for it: "
                       f"{where} " + ("records no snapshots" if st else "is absent") +
                       " — it has never been fetched")
                detail[name] = {"required": is_required, "source": sid, "reason": why,
                                "candidates": [sid]}
                facts[name].update(present=False, release=None, why=why, origin=where)
                continue
            facts[name].update(present=True, release=(st.get("release") or None), origin=where)

    # ── THE PER-DATASET VERDICT: one word, one reason, one file that says so ─────────────────────
    dataset_rows, stated = [], {}
    for name, is_required in ordered:
        fact = facts[name]
        source = fact.get("source")
        upstream, up_run, up_at = (None, None, None)
        release = None
        if "status" in fact:                      # settled by the registry: admitted/enabled
            status, reason = fact["status"], fact["why"]
        elif fact.get("present"):
            stored = fact.get("release") or ""
            upstream, up_run, up_at = _upstream_seen(recs, source)
            release = stored or None
            behind = _behind(stored, upstream) if (stored and upstream) else None
            if behind:
                status = STATUS_STALE
                reason = (f"present but STALE: the store holds {stored}, and the last run that "
                          f"reached the metadata endpoint observed the upstream release {upstream} "
                          f"(run {up_run} at {up_at}) — a reading door may not fetch the newer one; "
                          f"bringing it up is the loop's job")
            else:
                status = STATUS_PRESENT
                reason = (f"present: release {stored or '(nameless)'} is what the store holds for "
                          f"{source} ({fact.get('origin')})")
                if stored and upstream and stored != upstream and behind is None:
                    reason += (f"; the newest observed upstream release is {upstream} (run {up_run}) — "
                               f"not orderable against ours, so it is NOT called stale")
        elif presence_eyed:
            status, reason = STATUS_NOT_ACQUIRED, fact["why"]
        else:
            status = None
            reason = ("presence NOT evaluated: no store and no run record were supplied, so this "
                      "call answers the registry question only and claims nothing about the corpus")
        stated[name] = status
        dataset_rows.append({
            "dataset": name, "required": is_required, "source": source, "status": status,
            "release": release, "upstream_release": upstream,
            "upstream_observed_by": (dict(run_id=up_run, started_at=up_at) if up_run else None),
            "reason": reason,
            "evidence": {"state": (_state_path(store, source) if (store and source) else ""),
                         "runs": (os.path.join(store, "runs") if store else "")},
        })

    missing = missing_req + missing_opt
    verdict = ("OK" if not missing_req else
               "REFUSED[missing-dataset] " +
               ", ".join(f"{n} ({stated.get(n)})" for n in missing_req))
    status = ("refused-missing-required" if missing_req
              else ("partial" if missing_opt else "complete"))
    return {
        "research_id": spec["research_id"],
        "spec_sha256": spec_sha256(spec),
        "spec_path": os.path.abspath(spec_path) if spec_path else "",
        "required": req,
        "optional": opt,
        "acquired": acquired,
        "missing": missing,
        "missing_by_class": {"required": missing_req, "optional": missing_opt},
        "missing_detail": detail,
        "complete": (not missing_req) and (not missing_opt),
        "status": status,
        "optional_missing_allowed": True,
        "available": list(reg.values()),
        "selection": selection(spec),
        # ── THE REASON PER DATASET (the five-word vocabulary) and the ONE verdict line ──────────
        "datasets": dataset_rows,
        "dataset_status": stated,
        "status_vocabulary": list(STATUS_VOCABULARY),
        "presence_evaluated": presence_eyed,
        "verdict": verdict,
        "verdict_ok": not missing_req,
        "status_note": ("each dataset carries ONE of " + " · ".join(STATUS_VOCABULARY) +
                        "; present and stale both mean the corpus HAS it, the other three say why "
                        "it does not. status: null means presence was not evaluated (no store and "
                        "no run record), which is a different thing from not-acquired"),
        "note": ("complete is true only when every REQUIRED and every OPTIONAL dataset arrived; a "
                 "missing optional dataset is allowed and still visibly downgrades this block "
                 "(complete: false, status: partial, named in missing[]) — a silent downgrade to "
                 "fewer datasets is the failure this block exists to make impossible"),
    }


def coverage_finding(cov: dict) -> str:
    """The finding a reader cannot miss: it CARRIES the coverage block's decisive fields inline, so
    the run record, the digest and the manifest can never disagree about whether coverage was
    complete."""
    block = {k: cov.get(k) for k in ("research_id", "spec_sha256", "required", "optional",
                                     "missing", "complete", "status", "selection",
                                     "dataset_status", "verdict")}
    block["acquired"] = [{"dataset": a["dataset"], "release": a["release"], "sha256": a["sha256"]}
                         for a in (cov.get("acquired") or [])]
    return (f"COVERAGE-{str(cov.get('status', 'unknown')).upper()} "
            f"research_id={cov.get('research_id')} spec_sha256={cov.get('spec_sha256')} "
            "coverage=" + json.dumps(block, sort_keys=True, separators=(",", ":")))


# ---------------------------------------------------------------- the command
def _say(line: str = "") -> None:
    print(line, flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the research spec: hash it, canonicalise it, check coverage")
    ap.add_argument("--hash", default="", metavar="SPEC.yml", help="print spec_sha256")
    ap.add_argument("--canonical", default="", metavar="SPEC.yml", help="print the canonical bytes")
    ap.add_argument("--check", default="", metavar="SPEC.yml",
                    help="print the coverage block against sources.yml, and exit 4 if a REQUIRED "
                         "dataset is missing (nothing is fetched: this is the gate, not a run)")
    ap.add_argument("--sources-config", default=contract.DEFAULT_SOURCES)
    ap.add_argument("--store", default="", metavar="DIR",
                    help="also read state/<id>.json and runs/ so the check can say present / "
                         "not-acquired / stale (read-only: nothing is fetched, nothing is written)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        from . import selftest_research
        return selftest_research.main([])
    try:
        if a.hash:
            spec = load_spec(a.hash)
            _say(f"spec_sha256 {spec_sha256(spec)}  research_id={spec['research_id']}  {a.hash}")
            return 0
        if a.canonical:
            sys.stdout.write(canonical_bytes(load_spec(a.canonical)).decode())
            return 0
        if a.check:
            spec = load_spec(a.check)
            sources = contract.load_sources(a.sources_config)
            cov = coverage(spec, sources, store=a.store, spec_path=a.check)
            _say(json.dumps(cov, indent=2, sort_keys=True))
            _say(f"VERDICT  {cov['verdict']}")
            _say(coverage_finding(cov))
            if cov["missing_by_class"]["required"]:
                _say(f"REFUSED[missing-dataset] {spec['research_id']}: required "
                     f"{cov['missing_by_class']['required']} — ACQUISITION NOT STARTED")
                return 4
            return 0
    except contract.ConfigError as e:
        _say(f"REFUSED[{'spec' if isinstance(e, SpecError) else getattr(e, 'code', 'config')}] {e}")
        return 2
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
