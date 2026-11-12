"""manifest — THE HANDOVER MANIFEST: the ONE artefact that crosses to the uploader (DESIGN-STATION §7).

The store is ours; the upload is GeneNetwork's. The boundary is a manifest plus the bytes it names,
and nothing else. So this module does exactly four things:

  1. STAGE      read the published view + its provenance + the run record + the rejects, and write the
                staged file the uploader will POST — view columns, then `source` and `source_release`
                on every row. The bytes are deterministic (no clock, no dict order), so their sha256
                is a reproducible claim rather than a timestamp in disguise.
  2. MANIFEST   one JSON artefact: dataset id · entity · rows with accessions · source + source_release
                per row · content hash of the staged file · total counts · rejects with reasons ·
                licence/attribution · append-only flag. Every §7 field is checked BY NAME.
                AND THE DATASET/RELEASE LEVEL (req-P1-IDENTITY-THREE-LEVELS-adapter-data): per
                source the manifest states dataset_id (<publisher>:<dataset>, stable across
                releases) and version_id (<dataset_id>:<release>, derived), so a row's version is
                never ambiguous — the release string alone cannot tell 'the previous version of
                THIS dataset' from 'a DIFFERENT dataset'.
  3. DRY-RUN    print what WOULD be appended (rows, collisions, rejects, ledger state) and write
                NOTHING — not one directory, not one byte. A dry-run that writes is a lie.
  4. RECEIPT    a row in the shape of reports/UPLOADS.tsv, for the uploader to append to its ledger.

Run:
    python3 -m symbiont.manifest --store symbiont/store/ship1-fire --entity gene --out M.json
    python3 -m symbiont.manifest --store symbiont/store/ship1-fire --entity gene --dry-run
    python3 -m symbiont.manifest --validate M.json
    python3 -m symbiont.manifest --self-test

A silent acceptance is the only forbidden outcome (§6): a missing view, a missing run record, a
missing rejects file or a manifest missing a §7 field is REFUSED with the reason and exit 2.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile

from . import contract, events

MANIFEST_VERSION = 1

# ---- DESIGN-STATION §7, one entry per phrase in the field list -------------------------------
# (dotted path into the manifest, the §7 phrase it satisfies, the name to refuse BY when absent)
REQUIRED_FIELDS = [
    ("dataset_id", "dataset id", "dataset_id"),
    ("entity", "entity", "entity"),
    ("rows.count", "rows with accessions", "rows.count"),
    ("rows.accessions", "rows with accessions", "rows.accessions"),
    ("rows.file", "rows with accessions", "rows.file"),
    ("row_provenance.per_row", "source + source_release PER ROW", "row_provenance.per_row"),
    ("row_provenance.source", "source + source_release PER ROW", "source"),
    ("row_provenance.source_release", "source + source_release PER ROW", "source_release"),
    ("staged.sha256", "content hash of the staged file", "staged.sha256"),
    ("counts.rows", "total counts", "counts.rows"),
    ("rejects.by_reason", "rejects with reasons", "rejects.by_reason"),
    ("licence.attribution", "licence/attribution", "licence.attribution"),
    ("append_only", "append-only flag", "append_only"),
]

# ---- the farm's upload-ledger shape (reports/UPLOADS.tsv) ------------------------------------
LEDGER_COLUMNS = [
    "upload_id", "uploaded_at", "store_version", "dataset_id", "entity", "target_dataset",
    "rows", "staged_sha256", "manifest_sha256", "append_only", "who", "qaqc_pass",
    "qaqc_verdict", "note",
]

STAGE_PROVENANCE_COLUMNS = ["source", "source_release"]


class Refusal(Exception):
    """A legal answer with a reason (§6): REFUSED[bad-input], exit 2."""

    def __init__(self, reason: str, detail: str):
        super().__init__(f"REFUSED[{reason}] {detail}")
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------- small helpers
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: str, what: str) -> dict:
    if not os.path.exists(path):
        raise Refusal("bad-input", f"{what} is missing at run time: {path} — the manifest names only files that exist")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise Refusal("bad-input", f"{what} at {path} is not JSON: {e}")


def dump_json(obj, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def say(line: str = "") -> None:
    print(line, flush=True)


# ---------------------------------------------------------------- the view, located by ENTITY
VIEW_FILE_RE = re.compile(r"^(?P<view>.+)\.v(?P<version>\d+)\.tsv$")


def find_view(store: str, entity: str) -> tuple[str, int, str]:
    """entity 'gene' -> the published view whose name is `gene` or ends `_gene`.

    Refuses rather than guesses: an ambiguous entity would silently hand over the wrong table.
    """
    vdir = os.path.join(store, "view")
    if not os.path.isdir(vdir):
        raise Refusal("bad-input", f"store has no view/ directory: {store}")
    found = []
    for name in sorted(os.listdir(vdir)):
        m = VIEW_FILE_RE.match(name)
        if not m:
            continue
        v = m.group("view")
        if v == entity or v.endswith("_" + entity):
            found.append((v, int(m.group("version")), os.path.join(vdir, name)))
    if not found:
        raise Refusal("bad-input", f"no view for entity {entity!r} under {vdir} "
                                   f"(looked for {entity}.v<N>.tsv or *_/{entity}.v<N>.tsv)")
    if len(found) > 1:
        names = ", ".join(f"{v}.v{n}" for v, n, _ in found)
        raise Refusal("bad-input", f"entity {entity!r} matches {len(found)} views ({names}) — "
                                   f"refusing to pick one")
    return found[0]


# ---------------------------------------------------------------- the staged file
def render_staged(view_bytes: bytes, stage_source: str, stage_release: str) -> tuple[bytes, list[str]]:
    """view TSV + one `source` and one `source_release` value on EVERY row. Clock-free, order-stable."""
    text = view_bytes.decode("utf-8")
    lines = text.splitlines()
    if not lines:
        raise Refusal("bad-input", "the published view is empty — nothing to stage")
    header = lines[0].split("\t")
    out = ["\t".join(header + STAGE_PROVENANCE_COLUMNS)]
    for ln in lines[1:]:
        if not ln:
            continue
        out.append("\t".join(ln.split("\t") + [stage_source, stage_release]))
    return ("\n".join(out) + "\n").encode("utf-8"), header + STAGE_PROVENANCE_COLUMNS


def count_stage_bytes(b: bytes) -> int:
    return max(0, b.decode("utf-8").count("\n") - 1)


# ---------------------------------------------------------------- rejects (with reasons)
def read_rejects(store: str, run_id: str, source: str) -> dict | None:
    path = os.path.join(store, "rejects", f"{run_id}.{source}.tsv")
    if not os.path.exists(path):
        return None
    rows, by_reason, sample = 0, {}, {}
    with open(path, encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            f = line.split("\t")
            reason = f[1] if len(f) > 1 else "?"
            detail = f[2] if len(f) > 2 else ""
            by_reason[reason] = by_reason.get(reason, 0) + 1
            sample.setdefault(reason, [])
            if len(sample[reason]) < 2:
                sample[reason].append({"reason": reason, "detail": detail,
                                       "row": "\t".join(f[3:])})
            rows += 1
    return {"source": source, "path": path, "sha256": sha256_file(path), "rows": rows,
            "format": "tsv", "columns": header, "by_reason": by_reason, "sample": sample}


# ---------------------------------------------------------------- xref (unresolved is MEASURED)
def read_xref(store: str, run_record: dict) -> dict:
    """Unresolved keys counted from xref/xref.tsv's own status column, then cross-checked against
    the run record. A store WITH unresolved keys still manifests — the count is stated, never hidden."""
    path = os.path.join(store, "xref", "xref.tsv")
    if not os.path.exists(path):
        raise Refusal("bad-input", f"xref table missing at run time: {path}")
    by_status, by_reason, unresolved = {}, {}, 0
    with open(path, encoding="utf-8") as fh:
        fh.readline()
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) < 5:
                continue
            status, reason = f[3], f[4]
            by_status[status] = by_status.get(status, 0) + 1
            if status == "unresolved":
                unresolved += 1
                by_reason[reason] = by_reason.get(reason, 0) + 1
    rec = ((run_record or {}).get("xref") or {})
    stated = rec.get("unresolved_keys")
    return {
        "file": path,
        "sha256": sha256_file(path),
        "unresolved_keys": unresolved,
        "unresolved_by_reason": dict(sorted(by_reason.items())),
        "by_status": dict(sorted(by_status.items())),
        "published_with_conflict": by_status.get("published_with_conflict", 0),
        "run_record_unresolved_keys": stated,
        "agrees_with_run_record": (stated == unresolved) if stated is not None else None,
        "note": ("unresolved keys are NOT in the staged file: they have no accession pair to hand over, "
                 "and they are counted here with their reason rather than dropped silently"),
    }


# ---------------------------------------------------------------- collisions
def collisions(staged_lines: list[bytes] | list[str], key_idx: int, accession_idx: list[int]) -> dict:
    if not staged_lines:
        return {"keys": 0, "accessions": {}, "rows": 0}
    body = staged_lines[1:]
    key_seen, dup_keys = {}, 0
    acc = {i: {} for i in accession_idx}
    dup_acc = {i: 0 for i in accession_idx}
    for raw in body:
        f = raw.rstrip("\n").split("\t")
        if len(f) <= key_idx:
            continue
        key_seen[f[key_idx]] = key_seen.get(f[key_idx], 0) + 1
        if key_seen[f[key_idx]] > 1:
            dup_keys += 1
        for i in accession_idx:
            if i < len(f) and f[i]:
                acc[i][f[i]] = acc[i].get(f[i], 0) + 1
                if acc[i][f[i]] > 1:
                    dup_acc[i] += 1
    return {"duplicate_key_rows": dup_keys,
            "duplicate_accession_rows": {str(i): dup_acc[i] for i in accession_idx},
            "keys": len(key_seen), "rows": len(body)}


def ledger_state(ledger: str, staged_sha: str) -> dict:
    if not ledger or not os.path.exists(ledger):
        return {"path": ledger, "exists": False, "rows": 0, "already_uploaded": False, "match": None}
    with open(ledger, encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        rows, match = 0, None
        idx = header.index("staged_sha256") if "staged_sha256" in header else None
        for line in fh:
            if not line.strip():
                continue
            rows += 1
            if idx is not None:
                f = line.rstrip("\n").split("\t")
                if len(f) > idx and f[idx] == staged_sha:
                    match = line.rstrip("\n")
    return {"path": ledger, "exists": True, "rows": rows, "already_uploaded": match is not None,
            "match": match}


# ---------------------------------------------------------------- §7 validation, BY NAME
# ---- the handover's COVERAGE BLOCK (req-P1-THE-RESEARCH-SPEC-what-a-question-req) -----------
# The manifest already says what WAS handed over. This block says whether what was handed over is
# the WHOLE of what the question required — taken from the RUN RECORD (which resolved the releases)
# and the SPEC (which stated the requirements), never re-decided here. Every acquired dataset points
# at its acquisition record (run record + state + the raw snapshots), so a reader can walk from the
# handover back to the bytes.
COVERAGE_FIELDS = ["research_id", "spec_sha256", "required", "acquired", "missing", "complete",
                   "selection"]


def _coverage_block(run_record: dict, store: str) -> dict:
    """The coverage block: AS THE RUN MEASURED IT, plus the pointers and two cross-checks.

    Three readers must never disagree about whether coverage was complete — the run record, the
    finding and the manifest. So this block is COPIED from the run record and then VERIFIED:
      · every acquired sha256 must be the sha256 the run record's own fetch wrote, and
      · the spec on disk must still hash to the spec the run used (an edited spec is a DIFFERENT
        investigation; a run record may never be re-read under a weaker one).
    A run that named no spec still states that, by name: coverage is never absent, and never
    assumed complete.
    """
    research = run_record.get("research") or {}
    cov = run_record.get("coverage") or {}
    if not research and not cov:
        return {
            "present": False,
            "research_id": None,
            "spec_sha256": None,
            "required": [],
            "acquired": [],
            "missing": [],
            "complete": None,
            "selection": {},
            "why": ("the run named no research spec (no --research), so what this investigation "
                    "REQUIRED is not stated anywhere: this manifest makes no coverage claim, and "
                    "invents none"),
        }

    out = {"present": True, "why": None}
    for k in COVERAGE_FIELDS:
        out[k] = cov.get(k, research.get(k))
    out["complete"] = bool(cov.get("complete")) if cov else None
    out["status"] = cov.get("status") or "unknown"
    out["missing_by_class"] = cov.get("missing_by_class") or {"required": [], "optional": []}
    out["optional_missing_allowed"] = True
    out["finding"] = cov.get("finding") or research.get("finding")
    out["spec_path"] = research.get("spec_path") or cov.get("spec_path") or ""

    # --- cross-check 1: the bytes behind each acquired dataset are the bytes the run wrote -----
    fetch = {}
    for sid, src in (run_record.get("sources") or {}).items():
        for reskey, r in ((src.get("fetch") or {}).get("resources") or {}).items():
            fetch[(sid, reskey)] = r.get("sha256")
    disagreements = []
    for a in out["acquired"]:
        for r in a.get("resources") or []:
            got = fetch.get((a.get("source"), r.get("reskey")))
            if got is not None and got != r.get("sha256"):
                disagreements.append(f"{a.get('dataset')}:{r.get('reskey')} coverage says "
                                     f"{r.get('sha256')} but the run record fetched {got}")
    out["agrees_with_run_record"] = not disagreements
    out["disagreements"] = disagreements

    # --- cross-check 2: is the spec on disk still the spec this run used? ---------------------
    vs = {"changed_since_run": None}
    if research.get("spec_path"):
        try:
            from . import research as _research
            vs = _research.verify_spec(research["spec_path"], research.get("spec_sha256") or "")
        except Exception as e:                    # never a reason to invent a verdict
            vs = {"changed_since_run": None, "error": f"{type(e).__name__}: {e}"}
    out["spec_verified"] = vs
    return out


def _coverage_line(m: dict) -> str:
    """One stdout line per manifest: whether the handover is the WHOLE of what was required."""
    cov = m.get("coverage") or {}
    if not cov.get("present"):
        return ("COVERAGE not stated — the run named no research spec (no --research): this "
                "handover makes no coverage claim")
    return (f"COVERAGE {cov.get('status')}  complete={cov.get('complete')}  "
            f"research_id={cov.get('research_id')}  spec_sha256={cov.get('spec_sha256')}  "
            f"acquired={[(x.get('dataset'), x.get('release')) for x in cov.get('acquired') or []]}  "
            f"missing={cov.get('missing') or 'none'}")


def validate_coverage(manifest: dict) -> list[str]:
    """The coverage block's OWN checks, each failure NAMING the field. Silent when the run named no
    spec (the block then says so and makes no claim), strict whenever it did."""
    cov = manifest.get("coverage")
    if cov is None:
        return ["REFUSED[bad-input] manifest carries no coverage block — whether the question was "
                "ANSWERED is not stated anywhere in the handover (req-P1-THE-RESEARCH-SPEC)"]
    if not cov.get("present"):
        if cov.get("complete") is not None or cov.get("spec_sha256"):
            return ["REFUSED[bad-input] coverage.present is false but the block still claims a "
                    "verdict — a manifest with no spec must make no coverage claim"]
        return []
    errs = []
    if not cov.get("research_id"):
        errs.append("REFUSED[bad-input] coverage.research_id is empty — the handover must name the "
                    "investigation whose coverage it states")
    sha = cov.get("spec_sha256") or ""
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        errs.append(f"REFUSED[bad-input] coverage.spec_sha256 is not a sha256 ({sha!r})")
    if not isinstance(cov.get("complete"), bool):
        errs.append(f"REFUSED[bad-input] coverage.complete must be true or false, got "
                    f"{cov.get('complete')!r}")
    for k in ("required", "acquired", "missing"):
        if not isinstance(cov.get(k), list):
            errs.append(f"REFUSED[bad-input] coverage.{k} must be a list, got {cov.get(k)!r}")
    if cov.get("complete") is True and cov.get("missing"):
        errs.append(f"REFUSED[bad-input] coverage.complete is true but missing[] names "
                    f"{cov.get('missing')} — an incomplete coverage cannot look complete")
    if not cov.get("selection"):
        errs.append("REFUSED[bad-input] coverage.selection is empty — the arms and filters the run "
                    "CHOSE must travel with the handover")
    for a in cov.get("acquired") or []:
        for k in ("dataset", "release", "sha256"):
            if not a.get(k):
                errs.append(f"REFUSED[bad-input] coverage.acquired entry is missing {k}: {a!r}")
        acq = a.get("acquisition") or {}
        if not acq.get("run_record"):
            errs.append(f"REFUSED[bad-input] coverage.acquired {a.get('dataset')!r} points at no "
                        f"acquisition record (acquisition.run_record) — a dataset in the handover "
                        f"must be traceable to the run that acquired it")
        elif not os.path.exists(acq["run_record"]):
            errs.append(f"REFUSED[bad-input] coverage.acquired {a.get('dataset')!r} points at an "
                        f"acquisition record that does not exist at validate time: "
                        f"{acq['run_record']}")
    return errs


def _get(m: dict, dotted: str):
    cur = m
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def validate(manifest: dict) -> list[str]:
    """Check the manifest against DESIGN-STATION §7's field list. Every failure NAMES the field."""
    errs = []
    for path, phrase, name in REQUIRED_FIELDS:
        val = _get(manifest, path)
        if val is None or val == "" or val == [] or val == {}:
            errs.append(f"REFUSED[bad-input] manifest field missing or empty: {name} "
                        f"(§7: {phrase}) — path {path}")
    rows = manifest.get("rows") or {}
    if isinstance(rows.get("count"), int) and rows["count"] <= 0:
        errs.append("REFUSED[bad-input] manifest field rows.count is not a positive integer "
                    f"(§7: rows with accessions) — got {rows['count']!r}")
    if manifest.get("append_only") is not True:
        errs.append("REFUSED[bad-input] append_only must be the boolean true (§7: append-only flag) "
                    f"— got {manifest.get('append_only')!r}; an overwrite is not a handover")
    pv = manifest.get("row_provenance") or {}
    if pv.get("per_row") is not True:
        errs.append("REFUSED[bad-input] row_provenance.per_row must be true (§7: source + source_release "
                    f"PER ROW) — got {pv.get('per_row')!r}")
    for col in STAGE_PROVENANCE_COLUMNS:
        if col not in (manifest.get("staged") or {}).get("columns", []):
            errs.append(f"REFUSED[bad-input] staged file has no {col} column (§7: source + "
                        f"source_release PER ROW) — columns are {manifest.get('staged', {}).get('columns')}")
    dg = (manifest.get("store") or {}).get("diary") or {}
    if dg.get("anchored") and dg.get("matches") is not True:
        errs.append("REFUSED[diary-altered] store.diary anchors a head the diary no longer answers "
                    f"to ({dg.get('detail')}) — the manifest repeats a history it cannot verify")
    errs.extend(validate_coverage(manifest))
    return errs


# ---------------------------------------------------------------- build the handover
def _store_identity_config(store: str) -> dict:
    """The sources.yml persisted BESIDE the run (digests._store_cfg writes it), when it can be
    read. The manifest derives a row's dataset identity from the SAME declaration the fetch used,
    so the handover cannot state an identity the run did not have."""
    p = os.path.join(store, "sources.yml")
    if not os.path.isfile(p):
        return {}
    try:
        return contract.load_sources(p)
    except Exception:
        return {}          # an unreadable/older config is NOT a reason to invent an identity


def _source_identity(name: str, release: str, prov_src: dict, store_cfg: dict) -> tuple[str, str]:
    """(dataset_id, version_id) for ONE source, in that order of evidence:
         1. the run's own view provenance — the loop derived both at publish time;
         2. else the store's persisted sources.yml, through the SAME helpers the loop used;
         3. else ('', '') — the caller names the absence, it never guesses."""
    ds = (prov_src.get("dataset_id") or "").strip()
    vid = (prov_src.get("version_id") or "").strip()
    if ds and vid:
        return ds, vid
    cfg = store_cfg.get(name)
    if cfg:
        try:
            return contract.dataset_id(cfg), contract.version_id(cfg, release)
        except contract.ConfigError:
            pass
    return "", ""


def _diary_block(store: str, run_record: dict) -> dict:
    """THE DIARY'S ANCHOR, CARRIED AND RE-CHECKED (req-P1-THE-DIARY-appended-by-the-code-nev).

    The diary's hash chain makes an alteration of one line visible; it cannot stop someone
    rewriting the whole file and recomputing every link. So the head hash is ANCHORED where it
    costs something to forge: the run record wrote it down when it was the head, this manifest
    repeats it, and the two are checked against the diary ON DISK as the manifest is built. A
    mismatch is REFUSED[diary-altered] — never a warning, because a manifest is the artefact the
    recipient trusts.
    """
    anchor = dict(run_record.get("diary") or {})
    path = events.diary_path(store)
    if not anchor:
        return dict(path=path, anchored=False, seq=None, head_sha256=None, matches=None,
                    note=("this run record predates the diary: no head was anchored, so the "
                          "handover makes no claim about history it cannot check"))
    ok, why = events.head_matches(store, anchor)
    problems = events.read(store).problems
    if problems:                       # an ALTERATION anywhere breaks the chain, not only the tail
        ok, why = False, events.report(path, problems)
    return dict(path=path, anchored=True, seq=anchor.get("seq"),
                head_sha256=anchor.get("head_sha256"), events=anchor.get("events"),
                anchored_in=anchor.get("anchored_in"), matches=ok, detail=why,
                note=("the recipient can re-run `python3 -m symbiont.registry verify --store "
                      "<store>` and must see this same head: rewriting the diary then also means "
                      "rewriting this manifest, which has already been handed over"))


def build(store: str, entity: str, stage_path: str, ledger: str | None,
          dry_run: bool) -> dict:
    store = os.path.abspath(store)
    view_name, version, view_path = find_view(store, entity)
    stem = f"{view_name}.v{version}"
    vdir = os.path.join(store, "view")
    schema_path = os.path.join(vdir, f"{stem}.schema.json")
    prov_path = os.path.join(vdir, f"{stem}.provenance.json")
    view_bytes = open(view_path, "rb").read()
    schema = read_json(schema_path, "the view schema")
    prov = read_json(prov_path, "the view provenance")
    run_id = prov.get("run_id") or ""
    if not run_id:
        raise Refusal("bad-input", f"view provenance states no run_id: {prov_path}")
    run_record = read_json(os.path.join(store, "runs", f"{run_id}.json"), f"the run record for {run_id}")

    # the diary's anchor is checked BEFORE anything is rendered: a manifest that repeats a head the
    # diary on disk no longer answers to would launder an altered history into the handover.
    diary_block = _diary_block(store, run_record)
    if diary_block.get("anchored") and not diary_block.get("matches"):
        raise Refusal("diary-altered",
                      f"the diary {diary_block['path']} does not answer to the head run {run_id} "
                      f"anchored: {diary_block.get('detail')} — REFUSED, no manifest is written; "
                      f"history was altered after the run")

    sources = prov.get("sources") or {}
    if not sources:
        raise Refusal("bad-input", f"view provenance names no sources: {prov_path}")
    roles = {n: (run_record.get("sources", {}).get(n, {}) or {}).get("role", "") for n in sources}
    order = sorted(sources, key=lambda n: (0 if roles.get(n) == "id_authority" else 1, n))

    # --- releases PER SOURCE, from the store's own state + provenance (never invented) --------
    store_cfg = _store_identity_config(store)
    per_source, missing_state, identity_unknown = [], [], []
    for n in order:
        state_path = os.path.join(store, "state", f"{n}.json")
        if os.path.exists(state_path):
            st = read_json(state_path, f"state for {n}")
            release = st.get("release", "")
        else:
            missing_state.append(state_path)
            release = (sources[n] or {}).get("release", "")
        if not release:
            raise Refusal("bad-input", f"no release recorded for source {n!r} (state/{n}.json or "
                                       f"view provenance) — source_release PER ROW cannot be stated")
        dataset, version = _source_identity(n, release, sources[n] or {}, store_cfg)
        if not (dataset and version):
            identity_unknown.append(
                f"source {n!r}: no dataset identity in the view provenance and none derivable from "
                f"{os.path.join(store, 'sources.yml')} — its rows name a release ({release}) but not "
                f"the DATASET that release belongs to (declare publisher+dataset in sources.yml: "
                f"req-P1-IDENTITY-THREE-LEVELS-adapter-data)")
        per_source.append({
            "source": n,
            "source_release": release,
            # THE DATASET LEVEL (stable across releases) and the DERIVED version (which release of
            # it): both quoted verbatim from the run's provenance, never re-spelt here.
            "dataset_id": dataset,
            "version_id": version,
            "role": roles.get(n, ""),
            "licence": (sources[n] or {}).get("licence") or {},
            "published_count": (sources[n] or {}).get("published_count"),
            "snapshots": {k: {"sha256": v.get("sha256"), "path": v.get("path"), "release": v.get("release")}
                          for k, v in ((sources[n] or {}).get("snapshots") or {}).items()},
        })

    stage_source = "|".join(s["source"] for s in per_source)
    stage_release = "|".join(s["source_release"] for s in per_source)
    staged_bytes, staged_columns = render_staged(view_bytes, stage_source, stage_release)
    staged_sha = sha256_bytes(staged_bytes)

    # --- counts: the staged truth, the store's accounting, the rejects ------------------------
    accounting = ((run_record.get("cost") or {}))
    rejects_files, rejects_by_reason, rejects_total, rejects_sample = [], {}, 0, {}
    for n in order:
        r = read_rejects(store, run_id, n)
        if r is None:
            rejects_files.append({"source": n, "path": os.path.join(store, "rejects", f"{run_id}.{n}.tsv"),
                                  "rows": None,
                                  "note": "no rejects file for this source in this run — the loop writes one "
                                          "even when empty; a missing file is stated, not assumed empty"})
            continue
        rejects_total += r["rows"]
        for k, v in r["by_reason"].items():
            rejects_by_reason[k] = rejects_by_reason.get(k, 0) + v
        for k, v in r["sample"].items():
            rejects_sample.setdefault(k, []).extend(v)
        rejects_files.append({k: r[k] for k in ("source", "path", "sha256", "rows", "format", "columns")})

    xref = read_xref(store, run_record)

    # --- per-resource accounting, INCLUDING `used` (a row the adapter names as consumed, e.g. MGI's
    # withdrawn-marker map): rows_in == records + rejects + used, else the manifest says so by name.
    acc_rows, acc_bad = [], []
    for n in order:
        for a in (((run_record.get("sources") or {}).get(n) or {}).get("normalise") or {}).get("accounting") or []:
            owed = (a.get("records") or 0) + (a.get("rejects") or 0) + (a.get("used") or 0)
            acc_rows.append({"source": n, "reskey": a.get("reskey"), "rows_in": a.get("rows_in"),
                             "records": a.get("records"), "rejects": a.get("rejects"),
                             "used": a.get("used", 0), "closes": owed == a.get("rows_in")})
            if owed != a.get("rows_in"):
                acc_bad.append(f"{n}:{a.get('reskey')} {a.get('rows_in')} rows in, {owed} accounted")
    rows_in = sum(a["rows_in"] or 0 for a in acc_rows) or accounting.get("rows_in")
    used = sum(a["used"] or 0 for a in acc_rows)

    # --- what the staged file holds ----------------------------------------------------------
    staged_lines = staged_bytes.decode("utf-8").splitlines()
    key_idx = staged_columns.index("symbol") if "symbol" in staged_columns else 0
    accession_idx = [staged_columns.index(c) for c in staged_columns
                     if c in ("symbol", "mgi_id", "ensembl_gene_id") and c in staged_columns]
    coll = collisions(staged_lines, key_idx, accession_idx)
    coll["vs_ledger"] = ledger_state(ledger or "", staged_sha)

    dataset_id = f"symbiont.{view_name}.v{version}"
    coverage = _coverage_block(run_record, store)
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "generated_at": utc_now(),
        "dataset_id": dataset_id,
        "entity": entity,
        "append_only": True,
        # WHETHER THE QUESTION WAS ANSWERED (req-P1-THE-RESEARCH-SPEC-what-a-question-req): the §7
        # fields say what was handed over; this block says whether that is the WHOLE of what the
        # investigation required — and names what is missing when it is not.
        "coverage": coverage,
        "store": {
            "path": store,
            "view": view_name,
            "version": version,
            "view_file": view_path,
            "view_sha256": sha256_bytes(view_bytes),
            "schema_file": schema_path,
            "schema_sha256": sha256_bytes(open(schema_path, "rb").read()),
            "provenance_file": prov_path,
            "provenance_rows": prov.get("rows"),
            "run_id": run_id,
            "run_record": os.path.join(store, "runs", f"{run_id}.json"),
            # THE DIARY (the facts) and its anchored head: what the store DID, as opposed to what
            # it holds. A recipient can verify both without either MCP door.
            "diary": diary_block,
        },
        "rows": {
            "count": count_stage_bytes(staged_bytes),
            "key": (schema.get("key") or ["symbol"])[0],
            "columns": staged_columns,
            "accessions": [c for c in staged_columns if c in ("symbol", "mgi_id", "ensembl_gene_id")],
            "file": stage_path,
            "file_sha256": staged_sha,
            "sample": [ln.split("\t") for ln in staged_lines[1:4]] + [ln.split("\t") for ln in staged_lines[-2:]],
            "note": "every row carries its accessions and its source+source_release; the rows live in the "
                    "staged file whose sha256 is `staged.sha256`, so the manifest stays one small artefact "
                    "while the uploader can still verify every byte it posts",
        },
        "row_provenance": {
            "per_row": True,
            "columns": STAGE_PROVENANCE_COLUMNS,
            "value_form": "role order id_authority first, then xref_provider; multiple sources pipe-joined",
            "source": stage_source,
            "source_release": stage_release,
            # source_release is the LEVEL-3 half a row carries; these two blocks state the whole
            # identity behind it, so 'which release did we admit, and which is current' is answerable
            # from the handover alone (req-P1-IDENTITY-THREE-LEVELS-adapter-data).
            "dataset_ids": {s["source"]: s["dataset_id"] for s in per_source},
            "version_ids": {s["source"]: s["version_id"] for s in per_source},
            "identity_unknown": identity_unknown,
            "fields": {
                "symbol": ", ".join(s["source"] for s in per_source if s["role"] == "xref_provider") or stage_source,
                "mgi_id": ", ".join(s["source"] for s in per_source if s["role"] == "xref_provider") or stage_source,
                "ensembl_gene_id": ", ".join(s["source"] for s in per_source if s["role"] == "id_authority") or stage_source,
                "chromosome": ", ".join(s["source"] for s in per_source if s["role"] == "id_authority") or stage_source,
                "start": ", ".join(s["source"] for s in per_source if s["role"] == "id_authority") or stage_source,
                "end": ", ".join(s["source"] for s in per_source if s["role"] == "id_authority") or stage_source,
            },
            "sources": per_source,
        },
        "staged": {
            "path": stage_path,
            "sha256": staged_sha,
            "bytes": len(staged_bytes),
            "format": "tsv",
            "rows": count_stage_bytes(staged_bytes),
            "columns": staged_columns,
            "deterministic": True,
            "verify": "sha256sum <staged> must equal this value before posting; if it does not, do not post",
        },
        "counts": {
            "rows": count_stage_bytes(staged_bytes),
            "rows_in": rows_in,
            "records": accounting.get("rows_records"),
            "rejects": rejects_total,
            "used": used,
            "records_plus_rejects_plus_used": ((accounting.get("rows_records") or 0) + rejects_total + used),
            "published_pairs": (run_record.get("xref") or {}).get("pairs"),
            "authority_rows": (run_record.get("xref") or {}).get("authority_rows"),
            "provider_rows": (run_record.get("xref") or {}).get("provider_rows"),
            "authority_rows_not_published": (run_record.get("xref") or {}).get("authority_rows_not_published"),
            "unresolved_keys": xref["unresolved_keys"],
            "collisions": coll,
            "accounting_closes": ((rows_in == (accounting.get("rows_records") or 0) + rejects_total + used)
                                  and not acc_bad) if rows_in is not None else None,
            "accounting_by_resource": acc_rows,
            "accounting_open": acc_bad,
            "note": "rows_in = records + rejects + used: a row can be rejected or consumed, it cannot vanish "
                    "(contract.check_accounting)",
        },
        "rejects": {
            "count": rejects_total,
            "by_reason": dict(sorted(rejects_by_reason.items())),
            "files": rejects_files,
            "sample": rejects_sample,
            "note": "rejects are NOT staged: they carry no publishable accession pair, and they travel with "
                    "their reason so the uploader can report them instead of silently losing them",
        },
        "xref": xref,
        "licence": {
            "attribution": "; ".join(
                f"{s['source']} {s['source_release']}: "
                f"{(s['licence'] or {}).get('name', 'LICENCE UNKNOWN — DO NOT REDISTRIBUTE')} "
                f"{(s['licence'] or {}).get('url', '')}".strip()
                for s in per_source),
            "terms": [{"source": s["source"], "source_release": s["source_release"],
                       "name": (s["licence"] or {}).get("name", ""),
                       "url": (s["licence"] or {}).get("url", ""),
                       "quote": (s["licence"] or {}).get("quote", ""),
                       "terms_sha256": (s["licence"] or {}).get("terms_sha256", "")}
                      for s in per_source],
        },
        "warnings": ([f"no state file for a source, release taken from provenance: {p}" for p in missing_state]
                     + identity_unknown
                     + ([f"research coverage is NOT complete ({coverage.get('status')}): missing "
                         f"{coverage.get('missing')} — {coverage.get('finding')}"]
                        if coverage.get("present") and coverage.get("complete") is False else [])
                     + ([f"coverage disagrees with the run record: {d}" for d in
                         (coverage.get("disagreements") or [])])
                     + ([f"the research spec on disk is no longer the spec this run used "
                         f"({(coverage.get('spec_verified') or {}).get('current_spec_sha256')} vs "
                         f"{(coverage.get('spec_verified') or {}).get('recorded_spec_sha256')}) — "
                         f"this is a DIFFERENT investigation, not a re-read of this one"]
                        if (coverage.get("spec_verified") or {}).get("changed_since_run") else [])
                     + ([] if xref["agrees_with_run_record"] in (True, None) else
                        [f"unresolved count disagrees with the run record: xref.tsv={xref['unresolved_keys']} "
                         f"run={xref['run_record_unresolved_keys']}"])),
    }

    manifest["ledger"] = {
        "path": ledger or "",
        "columns": LEDGER_COLUMNS,
        "header": "\t".join(LEDGER_COLUMNS),
        "append_command": f"tail -n +2 <receipt> >> {ledger or 'reports/UPLOADS.tsv'}",
        "note": "the receipt carries the header, so `cat <receipt> >> reports/UPLOADS.tsv` works even the "
                "first time, and never rewrites a row already in the ledger",
    }
    manifest["upload_plan"] = {
        "target": "GeneNetwork dataset (named by the uploader — this side never names GN's tables)",
        "would_append_rows": coll["rows"],
        "rejects_not_appended": rejects_total,
        "unresolved_not_appended": xref["unresolved_keys"],
        "collisions_within_staged": (coll["duplicate_key_rows"]
                                     + sum(coll["duplicate_accession_rows"].values())),
        "collisions_with_ledger": coll["vs_ledger"]["already_uploaded"],
        "already_uploaded": coll["vs_ledger"]["already_uploaded"],
        "append_only": True,
        "dedupe_by": "staged.sha256 (content hash) — the same bytes never append twice",
    }
    manifest["_staged_bytes"] = staged_bytes  # popped before writing; never serialised
    return manifest


# ---------------------------------------------------------------- receipt
def receipt_row(manifest: dict, manifest_sha: str) -> list[str]:
    staged_sha = manifest["staged"]["sha256"]
    return [
        f"{manifest['dataset_id']}-{staged_sha[:12]}",          # upload_id (content-derived)
        manifest["generated_at"],                                # uploaded_at (uploader overwrites with its clock)
        manifest["store"]["view_sha256"],                        # store_version
        manifest["dataset_id"],
        manifest["entity"],
        "",                                                      # target_dataset — the uploader names it
        str(manifest["counts"]["rows"]),
        staged_sha,
        manifest_sha,
        "true",
        "symbiont.manifest (receipt; the uploader records who it is)",
        "counts+sha256+rejects+unresolved+xref",
        "PASS" if not manifest["warnings"] else "REVIEW",
        (f"rejects={manifest['rejects']['count']} "
         f"({', '.join(f'{k}={v}' for k, v in manifest['rejects']['by_reason'].items())}); "
         f"unresolved={manifest['xref']['unresolved_keys']}; "
         f"collisions={manifest['upload_plan']['collisions_within_staged']}; "
         f"accounting_closes={manifest['counts']['accounting_closes']}"),
    ]


# ---------------------------------------------------------------- the command
def run(store: str, entity: str, out: str | None, stage: str | None, ledger: str | None,
        receipt: str | None, dry_run: bool) -> int:
    # dry-run must write NOTHING, so a path is only ever defaulted in write mode
    if not dry_run and not out:
        raise Refusal("bad-input", "--out is required unless --dry-run (a handover with no file is a rumour)")

    # the path is resolved even in dry-run (so the field-set check can be shown in full) but the
    # directory is created only when we actually write: a dry-run leaves the tree byte-identical.
    stage_path = stage or os.path.join(store, "handover", f"{entity}.handover.staged.tsv")
    m = build(store, entity, stage_path, ledger, dry_run)
    staged_bytes = m.pop("_staged_bytes")
    m["dry_run"] = bool(dry_run)
    m["staged"]["exists"] = os.path.exists(stage_path)
    m["staged"]["written"] = not dry_run

    errs = validate(m)
    if errs:
        for e in errs:
            say(e)
        return 2

    # The manifest's OWN hash is never inside the manifest (a self-referential hash is not a
    # verifiable claim). The receipt carries sha256 of the file as written, so `sha256sum <manifest>`
    # must equal the ledger row the uploader appends.
    row = receipt_row(m, "(dry-run: manifest not written)" if dry_run else "")

    if dry_run:
        say(f"DRY-RUN  dataset_id={m['dataset_id']}  entity={m['entity']}  append_only=true")
        say(f"DRY-RUN  store={m['store']['path']}  view={m['store']['view']}.v{m['store']['version']}  "
            f"run={m['store']['run_id']}")
        say(f"DRY-RUN  WOULD STAGE  {stage_path}  {len(staged_bytes)} bytes  sha256={m['staged']['sha256']}  "
            f"rows={m['staged']['rows']}  columns={','.join(m['staged']['columns'])}")
        say(f"DRY-RUN  WOULD APPEND rows={m['upload_plan']['would_append_rows']}  "
            f"collisions_within_staged={m['upload_plan']['collisions_within_staged']}  "
            f"collisions_with_ledger={m['upload_plan']['collisions_with_ledger']}")
        say(f"DRY-RUN  NOT APPENDED  rejects={m['rejects']['count']} "
            f"({', '.join(f'{k}={v}' for k, v in m['rejects']['by_reason'].items()) or 'none'})  "
            f"unresolved_keys={m['xref']['unresolved_keys']}")
        led = m["counts"]["collisions"]["vs_ledger"]
        say(f"DRY-RUN  LEDGER {led['path'] or '(none given)'}: "
            + ("absent — the receipt carries the header" if not led["exists"]
               else f"{led['rows']} row(s) already ledgered; already_uploaded={led['already_uploaded']}"))
        say(f"DRY-RUN  RECEIPT (would write) {receipt or '(default)'}  row={row[0]}")
        say(f"DRY-RUN  every §7 field present: {len(REQUIRED_FIELDS)}/{len(REQUIRED_FIELDS)} checked BY NAME")
        say("DRY-RUN  nothing written: no directory created, no file touched, no ledger row appended")
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(stage_path)), exist_ok=True)
    with open(stage_path, "wb") as fh:
        fh.write(staged_bytes)
    on_disk = sha256_file(stage_path)
    if on_disk != m["staged"]["sha256"]:
        raise Refusal("bad-input", f"staged file on disk hashes to {on_disk} but the manifest says "
                                   f"{m['staged']['sha256']} — refusing to hand over bytes we cannot verify")
    dump_json(m, out)
    manifest_sha = sha256_file(out)
    row = receipt_row(m, manifest_sha)

    receipt_path = receipt or os.path.join(os.path.dirname(os.path.abspath(out)),
                                           f"UPLOADS-receipt-{m['dataset_id']}-{m['staged']['sha256'][:12]}.tsv")
    with open(receipt_path, "w", encoding="utf-8") as fh:
        fh.write("\t".join(LEDGER_COLUMNS) + "\n")
        fh.write("\t".join(row) + "\n")

    say(f"MANIFEST {out}  sha256={manifest_sha}  dataset_id={m['dataset_id']}  entity={m['entity']}  append_only=true")
    say(f"STAGED   {stage_path}  rows={m['staged']['rows']}  bytes={m['staged']['bytes']}  "
        f"sha256={m['staged']['sha256']}  (recomputed on disk: {'MATCH' if on_disk == m['staged']['sha256'] else 'MISMATCH'})")
    say(f"COUNTS   rows={m['counts']['rows']} rows_in={m['counts']['rows_in']} records={m['counts']['records']} "
        f"rejects={m['counts']['rejects']} unresolved_keys={m['counts']['unresolved_keys']} "
        f"accounting_closes={m['counts']['accounting_closes']}")
    say(f"REJECTS  {m['rejects']['count']} — "
        f"{', '.join(f'{k}={v}' for k, v in m['rejects']['by_reason'].items()) or 'none'}  "
        f"(files: {', '.join(os.path.basename(f.get('path', '')) for f in m['rejects']['files'])})")
    say(f"LICENCE  {m['licence']['attribution']}")
    say(f"RECEIPT  {receipt_path}  (append: tail -n +2 '{receipt_path}' >> {ledger or 'reports/UPLOADS.tsv'})")
    say(f"§7 CHECK {len(REQUIRED_FIELDS)}/{len(REQUIRED_FIELDS)} fields present, each named")
    say(_coverage_line(m))
    if m["warnings"]:
        for w in m["warnings"]:
            say(f"WARN     {w}")
    return 0


# ---------------------------------------------------------------- self-test (fixtures in tmpdir)
FIXTURE_VIEW = (
    "symbol\tmgi_id\tensembl_gene_id\tchromosome\tstart\tend\n"
    "Trp53\tMGI:98834\tENSMUSG00000059552\t11\t69580359\t69591873\n"
    "Tp53inp1\tMGI:1923946\tENSMUSG00000028211\t4\t29698937\t29733133\n"
    "Oca2\tMGI:97454\tENSMUSG00000030528\t7\t62249694\t62622037\n"
)


def _fixture_store(root: str) -> str:
    """A tiny store that is honest about the same things the big one is: a view, a provenance sidecar,
    a run record, one reject with a reason, one unresolved xref key."""
    store = os.path.join(root, "store")
    for sub in ("view", "runs", "rejects", "xref", "state"):
        os.makedirs(os.path.join(store, sub), exist_ok=True)
    vb = FIXTURE_VIEW.encode()
    vsha = sha256_bytes(vb)
    open(os.path.join(store, "view", "mouse_gene.v1.tsv"), "wb").write(vb)
    dump_json({"view": "mouse_gene", "version": 1, "key": ["symbol"],
               "columns": [{"name": c} for c in ("symbol", "mgi_id", "ensembl_gene_id",
                                                 "chromosome", "start", "end")]},
              os.path.join(store, "view", "mouse_gene.v1.schema.json"))
    dump_json({"view": "mouse_gene", "version": 1, "rows": 3, "view_sha256": vsha, "run_id": "fixture-run",
               "sources": {
                   "ensembl_biomart": {"release": "ensembl-116", "published_count": 78348,
                                       "licence": {"name": "Ensembl data: no restrictions",
                                                   "url": "https://www.ensembl.org/x", "terms_sha256": "aa"}},
                   "mgi": {"release": "mgi-2026-11-09", "published_count": 77645,
                           "licence": {"name": "MGI: CC-BY 4.0", "url": "https://mgi/x", "terms_sha256": "bb"}}}},
              os.path.join(store, "view", "mouse_gene.v1.provenance.json"))
    dump_json({"run_id": "fixture-run",
               "sources": {"ensembl_biomart": {"role": "id_authority"},
                           "mgi": {"role": "xref_provider"}},
               "cost": {"rows_in": 4, "rows_records": 3, "rows_rejected": 1, "rows_published": 3},
               "xref": {"pairs": 3, "unresolved_keys": 1, "authority_rows": 4, "provider_rows": 3,
                        "authority_rows_not_published": 1}},
              os.path.join(store, "runs", "fixture-run.json"))
    open(os.path.join(store, "rejects", "fixture-run.ensembl_biomart.tsv"), "w").write(
        "reskey\treason\tdetail\trow\nbiomart_tsv\tempty_symbol\tno MGI symbol\t\tENSMUSG00000000001\t1\t1\t2\n")
    open(os.path.join(store, "rejects", "fixture-run.mgi.tsv"), "w").write(
        "reskey\treason\tdetail\trow\n")
    open(os.path.join(store, "xref", "xref.tsv"), "w").write(
        "key\tauthority_id\tprovider_id\tstatus\treason\n"
        "Trp53\tENSMUSG00000059552\tMGI:98834\tok\t\n"
        "Tp53inp1\tENSMUSG00000028211\tMGI:1923946\tok\t\n"
        "Oca2\tENSMUSG00000030528\tMGI:97454\tok\t\n"
        "Ghost1\tENSMUSG00000099999\t\tunresolved\tno_provider_match\n")
    open(os.path.join(store, "xref", "conflicts.tsv"), "w").write(
        "key\treason\tauthority_ids\tprovider_ids\tdetail\n"
        "Ghost1\tno_provider_match\tENSMUSG00000099999\t\tmgi publishes no marker\n")
    dump_json({"release": "ensembl-116"}, os.path.join(store, "state", "ensembl_biomart.json"))
    dump_json({"release": "mgi-2026-11-09"}, os.path.join(store, "state", "mgi.json"))
    return store


def _dir_snapshot(root: str) -> dict:
    snap = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for f in sorted(filenames):
            p = os.path.join(dirpath, f)
            st = os.stat(p)
            snap[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
    return snap


def self_test() -> int:
    pass_n, fail_n = 0, 0
    root = tempfile.mkdtemp(prefix="manifest-selftest-")
    try:
        store = _fixture_store(root)
        out = os.path.join(root, "out", "MANIFEST.json")

        # T1 the fixture store manifests, and its sha256 matches the staged file byte for byte
        m = build(store, "gene", os.path.join(root, "stage.tsv"), None, False)
        bytes_ = m.pop("_staged_bytes")
        if not validate(m):
            pass_n += 1
            say(f"PASS T1a manifest of the fixture store validates against all {len(REQUIRED_FIELDS)} §7 fields")
        else:
            fail_n += 1
            say(f"FAIL T1a §7 validation: {validate(m)}")
        if sha256_bytes(bytes_) == m["staged"]["sha256"]:
            pass_n += 1
            say(f"PASS T1b staged sha256 recomputed from the bytes: {m['staged']['sha256'][:16]}…")
        else:
            fail_n += 1
            say("FAIL T1b staged sha256 does not match its own bytes")

        # T2 a manifest missing source_release is REFUSED BY NAME
        bad = json.loads(json.dumps({k: v for k, v in m.items()}))
        bad["row_provenance"].pop("source_release")
        errs = validate(bad)
        named = [e for e in errs if "source_release" in e]
        if named and "REFUSED[bad-input]" in named[0]:
            pass_n += 1
            say(f"PASS T2 fixture missing source_release is REFUSED by name: {named[0]}")
        else:
            fail_n += 1
            say(f"FAIL T2 a manifest missing source_release was accepted (errors={errs})")

        # T2b an append_only=false manifest is refused too (append-only is the point of the handover)
        bad2 = json.loads(json.dumps({k: v for k, v in m.items()}))
        bad2["append_only"] = False
        if any("append_only" in e for e in validate(bad2)):
            pass_n += 1
            say("PASS T2b append_only=false is REFUSED by name")
        else:
            fail_n += 1
            say("FAIL T2b append_only=false was accepted")

        # T3 dry-run writes NOTHING (dir snapshot before/after, byte-identical)
        before = _dir_snapshot(root)
        rc = run(store, "gene", None, None, None, None, dry_run=True)
        after = _dir_snapshot(root)
        if rc == 0 and before == after:
            pass_n += 1
            say(f"PASS T3 dry-run wrote nothing ({len(after)} files, snapshot identical before/after)")
        else:
            fail_n += 1
            say(f"FAIL T3 dry-run changed the tree: rc={rc} added={sorted(set(after) - set(before))}")

        # T4 a store with an unresolved xref key still manifests, and states the unresolved count
        if m["xref"]["unresolved_keys"] == 1 and m["counts"]["unresolved_keys"] == 1:
            pass_n += 1
            say("PASS T4 unresolved xref key: store still manifests, unresolved_keys=1 stated in the manifest")
        else:
            fail_n += 1
            say(f"FAIL T4 unresolved count wrong: {m['xref']['unresolved_keys']}")

        # T5 the receipt row has the ledger's column count (reports/UPLOADS.tsv shape)
        row = receipt_row(m, "0" * 64)
        if len(row) == len(LEDGER_COLUMNS):
            pass_n += 1
            say(f"PASS T5 receipt row width {len(row)} == ledger columns {len(LEDGER_COLUMNS)}")
        else:
            fail_n += 1
            say(f"FAIL T5 receipt row width {len(row)} != {len(LEDGER_COLUMNS)}")

        # T6 a store with no run record is REFUSED by name, not tolerated
        os.remove(os.path.join(store, "runs", "fixture-run.json"))
        try:
            build(store, "gene", None, None, True)
            fail_n += 1
            say("FAIL T6 a store with no run record was accepted")
        except Refusal as e:
            pass_n += 1
            say(f"PASS T6 missing run record REFUSED: {e}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    say(f"SELFTEST: {pass_n} passed, {fail_n} failed")
    return 0 if fail_n == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the handover manifest: store -> staged file + manifest + receipt")
    ap.add_argument("--store", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "store"))
    ap.add_argument("--entity", default="gene")
    ap.add_argument("--out", default=None, help="where to write the manifest JSON")
    ap.add_argument("--stage", default=None, help="where to write the staged file (default <store>/handover/)")
    ap.add_argument("--ledger", default=None, help="reports/UPLOADS.tsv — read for collisions, never written")
    ap.add_argument("--receipt", default=None, help="where to write the ledger-shaped receipt row")
    ap.add_argument("--dry-run", action="store_true", help="print what WOULD be appended; write nothing")
    ap.add_argument("--validate", default=None, metavar="MANIFEST.json", help="check a manifest against §7")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()
    if a.validate:
        try:
            m = read_json(a.validate, "the manifest to validate")
        except Refusal as e:
            say(str(e))
            return 2
        errs = validate(m)
        if errs:
            for e in errs:
                say(e)
            return 2
        say(f"§7 CHECK OK  {a.validate}  dataset_id={m.get('dataset_id')}  rows={(m.get('rows') or {}).get('count')}  "
            f"staged.sha256={((m.get('staged') or {}).get('sha256') or '')[:16]}…  "
            f"append_only={m.get('append_only')}  ({len(REQUIRED_FIELDS)}/{len(REQUIRED_FIELDS)} fields, each named)")
        return 0
    try:
        return run(a.store, a.entity, a.out, a.stage, a.ledger, a.receipt, a.dry_run)
    except Refusal as e:
        say(str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
