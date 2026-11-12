"""THE ADMISSION PIPELINE — an agent adds a source, the gate decides if it goes live.

The row (req-P1-SOURCE-HEALTH-AND-AGENT-ADDED-SOURCES): the admission pipeline as TOOLS, so a
source can go from a URL to an active source in sources.yml without a human doing the manual
steps — except the ONE gated step, which stays with the owner (at first the owner's line on
record, later policy: N stable cycles and every card check green).

    source_probe(url)          read the URL under a hard probe budget; a usable body becomes a
                               CANDIDATE (candidate.json); nothing usable -> NO-SOURCE with the
                               URLs it read (statuses included — a refusal is never a bare red)
    source_evaluate(id)        licence (read and hashed), accessions (the id namespaces in the
                               sample), rate limit (headers or defaults), size, cadence
    source_draft(id)           an adapter SKELETON from the template, written to
                               symbiont/adapters_draft/<id>.py — OUTSIDE symbiont/adapters/, so
                               the shipped loop's load-time refusal (an adapter with no config
                               entry) is never tripped by a draft — then the CONTRACT'S LOAD
                               CHECK: the drafted module must load with name/role/methods intact
    source_sandbox(id)        run the drafted adapter through the REAL harvest machinery
                               (symbiont.loop.harvest_source) into a THROWAWAY store under a
                               budget; over budget -> REFUSED[budget]; the raw snapshot, state
                               and run record land in the throwaway store, never the live one
    source_card(id)            the admission card: licence · counts matched · sample rows ·
                               xref rate · cost · the question it helps answer; AND it registers
                               the candidate in sources.yml as enabled: false (the one definition
                               site carries the definition, inactive)
    source_admit(id)           the ONE gated step: flip enabled: true in sources.yml, move the
                               adapter into symbiont/adapters/, re-run the registry check.
                               It CONSULTS THE OWNER'S POLICY FIRST (req-P1-ADMISSION-POLICY,
                               2026-11-10): a source whose card passes every check the policy
                               names AND that has been stable for N consecutive cycles
                               self-admits, and its receipt says "by policy, rule
                               stable_cycles_before_self_admit, N=<n>". ANYTHING ELSE needs the
                               operator's word: REFUSED[gate] without it, quoting the owner's
                               line on record. A policy that is missing, unreadable, malformed,
                               or whose N is unset makes the station refuse to SELF-admit and
                               say so — it never becomes a second blocker on the operator's word.

One CLI, one definition per tool: `python3 -m symbiont.admission <cmd> …`. The MCP server
(symbiont/mcpserver.py) runs THIS CLI — the tools an agent calls and the CLI an operator can
run by hand are the same words.

Where things land (default --admission-dir <store>/admission):
    <admission-dir>/<id>/candidate.json     the probe's record
    <admission-dir>/<id>/evaluation.json    the evaluation
    <admission-dir>/<id>/sandbox/<ts>/      the throwaway store of that sandbox run
    <admission-dir>/<id>/sandbox/<ts>.receipt.json
    <admission-dir>/<id>/card.md            the admission card
    <admission-dir>/<id>/admitted.json      the receipt, once the gate has said yes

Exit codes follow the loop's: 0 done · 2 REFUSED before the work (config, candidate, card, gate)
· 3 a named finding from the work (NO-SOURCE, budget, licence-unknown, source-gone,
source-unreachable).
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import glob
import hashlib
import json
import os
import re
import shutil
import sys

from . import admission_policy, contract, events, sandbox
from .http import Fetcher, FetchError
from . import loop as loop_mod

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORE = os.path.join(ROOT, "store")
DEFAULT_ADMISSION_DIR = os.path.join(DEFAULT_STORE, "admission")
DEFAULT_DRAFTS_DIR = os.path.join(ROOT, "adapters_draft")
DEFAULT_ADAPTERS_DIR = os.path.join(ROOT, "adapters")
DEFAULT_GATE_FILE = os.path.join(ROOT, "admission-gate.txt")

PROBE_BUDGET = {"requests": 8, "bytes": 8000000, "wall_s": 120}
EVAL_BUDGET = {"requests": 3, "bytes": 8000000, "wall_s": 120}
SANDBOX_DEFAULT_BUDGET = {"requests": 6, "bytes": 20000000, "wall_s": 600}

ACCESSION_PATTERNS = {           # the id namespaces an evaluation looks for in the sample
    "mgi": re.compile(r"MGI:\d+"),
    "ensembl_gene": re.compile(r"ENSMUSG\d{11}"),
    "ensembl_transcript": re.compile(r"ENSMUST\d{11}"),
    "refseq": re.compile(r"[A-Z]{2}_[A-Z]\d+\.\d+"),
}


def _utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _stamp() -> str:
    return _utc().strftime("%Y%m%dT%H%M%SZ")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _jdump(obj, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _refuse(code: str, msg: str) -> int:
    print(f"REFUSED[{code}] {msg}")
    return 2


def _slug(url: str) -> str:
    """A stable id for a candidate: host+path squeezed to a PYTHON IDENTIFIER (the id
    becomes an adapter module name), plus a short sha."""
    m = re.match(r"https?://([^/]+)(.*)", url)
    host_path = (m.group(1) + m.group(2)) if m else url
    base = re.sub(r"[^a-z0-9]+", "_", host_path.lower()).strip("_")[:40] or "source"
    return f"{base}_{_sha(url.encode())[:6]}"


def _admit_dir(a, sid: str) -> str:
    return os.path.join(a.admission_dir, sid)


def _need_candidate(a, sid: str) -> dict:
    p = os.path.join(_admit_dir(a, sid), "candidate.json")
    if not os.path.isfile(p):
        raise SystemExit(_refuse("no-candidate",
                                 f"{sid}: no candidate on disk at {p} — source_probe <url> first"))
    return _load(p)


def _need_evaluation(a, sid: str) -> dict:
    p = os.path.join(_admit_dir(a, sid), "evaluation.json")
    if not os.path.isfile(p):
        raise SystemExit(_refuse("no-evaluation",
                                 f"{sid}: no evaluation on disk at {p} — source_evaluate {sid} first"))
    return _load(p)


def _draft_path(a, sid: str) -> str:
    return os.path.join(a.drafts_dir, f"{sid}.py")


# ═════════════════════════════════════════════════════════════════ 1 · source_probe
def cmd_probe(a) -> int:
    """Read the URL under a hard budget. Usable body -> candidate; nothing usable -> NO-SOURCE,
    with the URLs it read (and their statuses) — never a bare red."""
    if a.id and not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", a.id):
        return _refuse("id", f"{a.id!r}: a candidate id becomes an adapter module name -- it "
                              f"must match [a-z][a-z0-9_]* (lowercase, so file names, sources.yml "
                              f"keys and module names cannot disagree)")
    sid = a.id or _slug(a.url)
    sdir = _admit_dir(a, sid)
    fetcher = Fetcher(budget=dict(PROBE_BUDGET), rate_limit={"requests_per_minute": 0,
                                                             "min_interval_s": 0.0})
    meta: dict = {}
    try:
        h = fetcher.head(a.url, note="probe metadata")
        meta = dict(last_modified=h.get("last_modified", ""),
                    content_length=h.get("content_length", 0), etag=h.get("etag", ""))
    except Exception as e:
        meta = dict(head_refused=f"{type(e).__name__}: {e}")
    body = b""
    err = ""
    try:
        body = fetcher.get(a.url, note=f"probe {sid}")
    except contract.Refusal as e:
        err = f"REFUSED[{e.code}] {e}"
    except FetchError as e:
        err = str(e)
    except Exception as e:                                   # never a crash where a refusal will do
        err = f"{type(e).__name__}: {e}"
    urls_read = [dict(n=e["n"], url=e["url"], effective_url=e["effective_url"],
                      status=e["status"], bytes=e["bytes"], note=e["note"])
                 for e in fetcher.log]

    if err or not body.strip():
        print(f"NO-SOURCE {a.url}")
        print(f"probe {sid} read {len(urls_read)} url(s):")
        for e in urls_read:
            print(f"  {e['n']}. {e['url']} -> status={e['status']} bytes={e['bytes']} ({e['note']})")
        if err:
            print(f"reason: {err}")
        else:
            print("reason: the URL answered nothing a source could be built from (empty body)")
        return 3

    text = body.decode("utf-8", "replace")
    sample = text.splitlines()[:20]
    head = {k: v for k, v in (fetcher.log[-1] or {}).items()}
    cand = dict(
        id=sid, url=a.url, effective_url=fetcher.last_effective_url() or a.url,
        probed_at=_utc().isoformat(), bytes=len(body), sha256=_sha(body),
        sample=sample, sample_is_text=bool(sample and text.strip()),
        last_modified=meta.get("last_modified", ""), content_length=meta.get("content_length", 0),
        etag=meta.get("etag", ""), head_refused=meta.get("head_refused", ""),
        licence_url=a.licence_url or "", urls_read=urls_read,
        probe_budget=dict(PROBE_BUDGET),
    )
    p = _jdump(cand, os.path.join(sdir, "candidate.json"))
    print(f"PROBE    {sid}  {a.url}")
    print(f"  read {len(urls_read)} request(s), {len(body)} bytes, sha256={cand['sha256'][:16]}…")
    print(f"  sample: {' | '.join(sample[:2])[:100]}")
    if cand["licence_url"]:
        print(f"  licence page (declared): {cand['licence_url']}")
    print(f"CANDIDATE {p}")
    return 0


# ═══════════════════════════════════════════════════════════════ 2 · source_evaluate
def cmd_evaluate(a) -> int:
    sid = a.id
    cand = _need_candidate(a, sid)
    fetcher = Fetcher(budget=dict(EVAL_BUDGET), rate_limit={"requests_per_minute": 0,
                                                            "min_interval_s": 0.0})
    # -- licence: read the page and hash the terms; never assume --------------------------------
    licence = dict(url=cand.get("licence_url", ""), status="unknown", sha256="",
                   quote="", note="")
    if licence["url"]:
        try:
            txt = fetcher.get_text(licence["url"], note=f"licence page for {sid}")
            licence.update(status="read", sha256=_sha(txt.encode("utf-8", "replace")),
                           quote=re.sub(r"<[^>]+>", " ", txt)[:300].strip())
        except Exception as e:
            licence["note"] = f"licence page unreadable: {type(e).__name__}: {e}"
    else:
        licence["note"] = "the probe was given no licence page — evaluate cannot read terms it " \
                          "was never pointed at"

    # -- accessions: which id namespaces live in the sample --------------------------------------
    joined = "\n".join(cand.get("sample") or [])
    accessions = {ns: len(pat.findall(joined)) for ns, pat in ACCESSION_PATTERNS.items()}
    accessions = {ns: n for ns, n in accessions.items() if n}

    # -- rate limit: the polite default until the provider declares its own (a later admission
    #    policy may learn it from the provider's headers); never a guess above 20 rpm -----------
    rate_limit = dict(requests_per_minute=20, min_interval_s=3.0, source="default")
    # -- size and cadence: from the probe's own measurements -------------------------------------
    size = dict(bytes=cand.get("bytes"), declared=cand.get("content_length"))
    lm = cand.get("last_modified") or ""
    cadence = dict(last_modified=lm,
                   age_days=(round((_utc() - email.utils.parsedate_to_datetime(lm)
                                    ).total_seconds() / 86400, 1)
                             if lm else None)) if lm else dict(last_modified="", age_days=None)

    checks = [
        dict(check="licence", status=licence["status"], note=licence["note"] or licence["quote"][:80]),
        dict(check="accessions", status="found" if accessions else "none",
             note=f"{len(accessions)} namespace(s): {', '.join(sorted(accessions)) or '—'}"),
        dict(check="size", status="measured", note=f"{size['bytes']} bytes read at probe"),
        dict(check="cadence", status="known" if lm else "unknown",
             note=f"last-modified {lm or 'not stated by the provider'}"),
    ]
    ev = dict(id=sid, evaluated_at=_utc().isoformat(), url=cand["url"],
              licence=licence, accessions=accessions, rate_limit=rate_limit,
              size=size, cadence=cadence, checks=checks)
    p = _jdump(ev, os.path.join(_admit_dir(a, sid), "evaluation.json"))
    print(f"EVALUATE {sid}  from {os.path.join(a.admission_dir, sid, 'candidate.json')}")
    for c in checks:
        print(f"  {c['check']:12} {c['status']:8} {c['note'][:88]}")
    print(f"EVALUATION {p}")
    return 0


# ═══════════════════════════════════════════════════════════════════ 3 · source_draft
TEMPLATE = '''"""DRAFT adapter — __SOURCE_ID__ — generated by source_draft, from the template.

This is a SKELETON: the agent that drafted it owns the column mapping. Parsing and normalisation
live here; the endpoint, the licence, the rate limit and the budget come from sources.yml by way
of configure() (the sandbox builds that cfg from the probe and the evaluation). Every reject
carries a reason, and every row is accounted for — the same contract any shipped adapter holds.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re

from symbiont import contract
from symbiont.contract import Discovery, FetchPlan, Normalised, Verify

ID_PATTERNS = __ID_PATTERNS__
COORD_HEADERS = {"chrom": ("chrom",), "start": ("start",), "end": ("end",)}
SYMBOL_HEADERS = ("symbol", "name", "gene")


class DraftAdapter:
    name = "__SOURCE_ID__"
    role = "xref_provider"

    def configure(self, cfg: dict) -> None:
        self.cfg = cfg
        eps = cfg.get("endpoints") or {}
        self.data_url = contract.resolve_url(cfg["base_url"], eps.get("data", "."))
        self.metadata_url = contract.resolve_url(cfg["base_url"], eps.get("metadata", "."))
        self.licence_url = cfg["licence_url"]
        self.licence_name = cfg["licence_text"]
        self.count_url = (contract.resolve_url(cfg["base_url"], eps["count"])
                          if eps.get("count") else "")

    def discover(self, http, since: dict) -> Discovery:
        notes = [f"draft adapter: data={self.data_url} metadata={self.metadata_url}"]
        release = "__SOURCE_ID__-unknown"
        try:
            head = http.head(self.metadata_url, note="draft metadata")
            if head.get("last_modified"):
                release = re.sub(r"[^A-Za-z0-9._-]+", "-",
                                 "__SOURCE_ID__-" + head["last_modified"][:16])
        except Exception as e:
            notes.append(f"metadata HEAD refused ({type(e).__name__}); release stays {release}")
        try:
            txt = http.get_text(self.licence_url, note="licence")
        except contract.Refusal:
            raise                       # a budget refusal is a budget refusal, not unreadable terms
        except Exception as e:
            raise contract.LicenceUnknown(
                f"__SOURCE_ID__: licence page {self.licence_url} unreadable: "
                f"{type(e).__name__}: {e} — no data may land under terms we did not read") from e
        licence = dict(name=self.licence_name, url=self.licence_url,
                       quote=re.sub(r"<[^>]+>", " ", txt)[:300].strip(),
                       terms_sha256=hashlib.sha256(
                           txt.encode("utf-8", "replace")).hexdigest())
        published = None
        if self.count_url:
            try:
                published = int(http.get_text(self.count_url, note="draft count").strip())
            except Exception as e:
                notes.append(f"count endpoint unreadable ({type(e).__name__}); verify falls back "
                             f"to row accounting")
        d = Discovery(source=self.name, release=release,
                      resources=[dict(reskey="data", url=self.data_url, etag="",
                                      last_modified="", content_length=0, ext="tsv")],
                      published_count=published,
                      published_count_url=(self.count_url if published is not None else ""),
                      licence=licence, notes=notes)
        return d

    def fetch(self, http, since: dict) -> FetchPlan:
        body = http.get(self.data_url, note="draft data")
        return FetchPlan(new=True, reason="draft adapter: the sandbox always fetches",
                         resources={"data": {"bytes": body, "filename": "__SOURCE_ID__.tsv"}})

    def normalise(self, resources) -> Normalised:
        text = resources["data"].decode("utf-8", "replace")
        sep = "\\t" if "\\t" in text.splitlines()[0] else ","
        rows = [r for r in csv.reader(io.StringIO(text), delimiter=sep) if r]
        header, data = rows[0], rows[1:]
        looks_like_header = all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_ ./()#-]*", c or "x")
                                for c in header)
        if not looks_like_header:
            data = rows
        idx = {c.lower(): i for i, c in enumerate(header)} if looks_like_header else {}

        def col_of(*names):
            for n in names:
                for k, i in idx.items():
                    if n in k:
                        return i
            return None

        key_col = col_of(*SYMBOL_HEADERS) if looks_like_header else 0
        records, rejects = [], []
        for row in data:
            if not row or key_col >= len(row) or not (row[key_col] or "").strip():
                rejects.append(dict(reskey="data", row="\\t".join(row),
                                    reason="no-key", detail="empty key column"))
                continue
            xrefs = {}
            for i, v in enumerate(row):
                if i == key_col or not v:
                    continue
                col = (header[i].lower() if looks_like_header and i < len(header) else "")
                for ns, pat in ID_PATTERNS.items():
                    if (ns in col or pat.fullmatch(v.strip())) and pat.search(v):
                        xrefs[ns] = pat.search(v).group(0)
                        break
            coords = {}
            for key, names in COORD_HEADERS.items():
                c = col_of(*names) if looks_like_header else None
                if c is not None and c < len(row) and row[c].strip().isdigit():
                    coords[key] = int(row[c].strip())
            records.append(dict(key=row[key_col].strip(), id="", source=self.name,
                                xrefs=xrefs, coords=coords))
        accounting = [dict(reskey="data", rows_in=len(data), records=len(records),
                           rejects=len(rejects), used=0)]
        return Normalised(rows_in=len(data), records=records, rejects=rejects,
                          extra=dict(accounting=accounting))

    def verify(self, d, n) -> Verify:
        ours = len(n.records)
        if d.published_count is None:
            return Verify(ok=True, ours=ours, source_count=None, url=self.data_url,
                          detail="the source publishes no count of its own; rows_in is accounted "
                                 "for by records+rejects (the contract enforced it before here)")
        return Verify(ok=(ours == d.published_count), ours=ours,
                      source_count=d.published_count, url=self.count_url,
                      detail="draft adapter: our records vs the source's own count")


ADAPTER = DraftAdapter()
'''


def _template_for(sid: str) -> str:
    pats = "{\n    " + ",\n    ".join(
        f'"{ns}": re.compile(r"{pat.pattern}")' for ns, pat in ACCESSION_PATTERNS.items()) + ",\n}"
    return (TEMPLATE
            .replace("__SOURCE_ID__", sid)
            .replace("__ID_PATTERNS__", pats)
            .replace("\\t", "\\t"))


def _load_adapter_modules(dirs: list[str], allow_empty: bool = False) -> dict:
    """Load every adapter module under the given DIRECTORIES (file-based, so a demo dir outside
    the package works) with the contract's own checks: ADAPTER present, name/role/methods intact,
    role in (id_authority, xref_provider). This mirrors contract.load_adapters exactly. An empty
    DIRECTORY is allowed (the caller checks the UNION is not empty: an empty symbiont is a lie)."""
    import importlib.util
    found = {}
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".py") or name.startswith("_"):
                continue
            path = os.path.join(d, name)
            spec = importlib.util.spec_from_file_location(f"_admission_draft_{name[:-3]}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            ad = getattr(mod, "ADAPTER", None)
            if ad is None:
                raise RuntimeError(f"{path} has no module-level ADAPTER — not a plug-in")
            for attr in ("name", "role", "configure", "discover", "fetch", "normalise", "verify"):
                if not hasattr(ad, attr):
                    raise RuntimeError(f"{path} ADAPTER missing {attr}")
            if ad.role not in ("id_authority", "xref_provider"):
                raise RuntimeError(f"{path} role {ad.role!r} not in (id_authority, xref_provider)")
            found[ad.name] = ad
    if not found and not allow_empty:
        raise RuntimeError("no adapters found — an empty symbiont is a lie, not a harvest")
    return found


def _load_for_check(dirs: list[str]) -> dict:
    """The contract's load check. The SHIPPED dirs load through the contract itself (the real
    package import — shipped adapters use relative imports, which only a package can satisfy);
    a directory outside the package loads file-based with the same checks, which a drafted
    template satisfies because it imports absolutely."""
    out: dict = {}
    for d in dirs:
        ad = os.path.abspath(d)
        if ad == DEFAULT_DRAFTS_DIR:
            out.update(contract.load_adapters(package="symbiont.adapters_draft"))
        elif ad == DEFAULT_ADAPTERS_DIR:
            out.update(contract.load_adapters(package="symbiont.adapters"))
        else:
            out.update(_load_adapter_modules([d], allow_empty=True))
    if not out:
        raise RuntimeError("no adapters found — an empty symbiont is a lie, not a harvest")
    return out


def cmd_draft(a) -> int:
    sid = a.id
    cand = _need_candidate(a, sid)
    _need_evaluation(a, sid)
    os.makedirs(a.drafts_dir, exist_ok=True)
    init = os.path.join(a.drafts_dir, "__init__.py")
    if not os.path.isfile(init):
        with open(init, "w", encoding="utf-8") as fh:
            fh.write('"""The drafts bay — adapters drafted by source_draft live here until the '
                     'gate admits them. OUTSIDE symbiont/adapters/, so a draft never trips the '
                     'loop\'s load-time refusal (an adapter with no sources.yml entry)."""\n')
    path = _draft_path(a, sid)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_template_for(sid))
    # THE CONTRACT'S LOAD CHECK — the row's NSC: a draft that does not load is not a draft.
    try:
        adapters = _load_for_check([a.drafts_dir])
        ad = adapters.get(sid)
        if ad is None or ad.name != sid:
            raise RuntimeError(f"loaded adapters {sorted(adapters)} do not name {sid!r}")
    except Exception as e:
        print(f"REFUSED[template-load] {sid}: the drafted adapter failed the contract's load "
              f"check: {type(e).__name__}: {e} — left at {path} for repair, not admitted")
        return 2
    print(f"DRAFT     {sid}  from candidate {cand['url']}")
    print(f"  adapter skeleton -> {path}")
    print(f"  contract load check: PASS  name={ad.name} role={ad.role} "
          f"methods=configure,discover,fetch,normalise,verify")
    return 0


# ═══════════════════════════════════════════════════════════════ 4 · source_sandbox
def _sandbox_plan(a, sid: str, cand: dict, ev: dict):
    """The draft's run config + its NETWORK ALLOWLIST, built from the entry the card will write.

    The allowlist is the entry's DECLARATIONS, one host each (DESIGN-STATION §4b, and
    req-P1-THE-LICENCE-HOST-IS-IN-THE-ALLOWL): the `base_url` host for the data, plus the
    `licence_url` host when that page lives somewhere else — an explicitly justified second
    door, because a station that cannot read the terms can never admit the source (measured
    2026-11-12 on MONDO). An ENDPOINT on another host is still not added: the sandbox refuses,
    because a sandboxed run whose allowlist is the union of everything the entry mentions has no
    allowlist at all — that is an operator edit to sources.yml, not a default."""
    budget = dict(SANDBOX_DEFAULT_BUDGET)
    if a.budget_requests:
        budget["requests"] = a.budget_requests
    if a.budget_bytes:
        budget["bytes"] = a.budget_bytes
    if a.budget_wall_s:
        budget["wall_s"] = a.budget_wall_s
    rate = dict(ev.get("rate_limit") or {"requests_per_minute": 20, "min_interval_s": 3.0})
    rate.pop("source", None)
    cfg = dict(
        id=sid, adapter=sid, enabled=True, base_url=cand["url"],
        endpoints={"data": ".", "metadata": "."},
        storage={"data": {"class": "store", "reason": "admission default: no licence-forbids-storage "
                                                "or bulk declaration exists for a candidate"},
                 "metadata": {"class": "store", "reason": "admission default: no licence-forbids-"
                                                    "storage or bulk declaration exists for a "
                                                    "candidate"}},
        rate_limit=rate, licence_url=cand.get("licence_url", ""),
        licence_text=(ev.get("licence") or {}).get("name")
                    or (ev.get("licence") or {}).get("quote", "")[:80]
                    or "terms not yet read",
        staleness={"policy_days": 3650}, budget=budget, quarantined=None,
    )
    allowed_hosts, foreign = sandbox.allowed_hosts_for_entry(cfg)
    return cfg, budget, rate, allowed_hosts, foreign


def cmd_sandbox(a) -> int:
    """source_sandbox <id> --budget N — the drafted adapter runs in a THROWAWAY store.

    Two modes in one command (the operator's words are the same either way):
      OUTER  creates the throwaway store under a temp directory, hashes the REAL store before,
             re-runs this same command inside the write allowlist (bwrap: read-only root, only
             the throwaway store + /tmp writable), copies out the receipt, records the throwaway
             store's dir hash, DELETES it, and proves the real store is byte-identical after.
      INNER  (SYMBIONT_SANDBOX_INSIDE=1) does the harvest with the network allowlist armed and
             writes its receipt into the throwaway store, never into the admission dir.

    The point of the split: the drafted adapter is unreviewed code. It must not be able to reach
    the real store at all — not even through a bug in its own normalise()."""
    sid = a.id
    cand = _need_candidate(a, sid)
    ev = _need_evaluation(a, sid)
    dpath = _draft_path(a, sid)
    if not os.path.isfile(dpath):
        return _refuse("no-draft", f"{sid}: no drafted adapter at {dpath} — source_draft {sid} first")
    _load_for_check([a.drafts_dir])                      # the draft must still load
    cfg, budget, rate, allowed_hosts, foreign = _sandbox_plan(a, sid, cand, ev)
    if foreign:
        print(f"REFUSED[sandbox-breach] {sid}: its sources.yml entry would reach "
              f"{', '.join(sorted(set(foreign)))} but its network allowlist is the hosts it "
              f"declares ({sandbox.allowlist_banner(cfg)[len('allowlist: '):]}) — widening a "
              f"network allowlist is an operator edit, never a sandbox default")
        return 3
    if not allowed_hosts:
        print(f"REFUSED[sandbox-breach] {sid}: the entry's base_url {cand['url']!r} names no host, "
              f"so there is no network allowlist to enforce")
        return 3
    if os.environ.get("SYMBIONT_SANDBOX_INSIDE") == "1":
        return _sandbox_inside(a, sid, cfg, budget, rate, allowed_hosts)
    return _sandbox_confined(a, sid, cfg, budget, allowed_hosts)


def _sandbox_confined(a, sid: str, cfg: dict, budget: dict, allowed_hosts: set) -> int:
    import tempfile

    before = sandbox.dirhash(sandbox.STORE_DIR)
    root = tempfile.mkdtemp(prefix=f"symbiont-sandbox-{sid}-")
    store = os.path.join(root, "store")
    for sub in ("raw", "state", "xref", "view", "runs", "rejects", "digest"):
        os.makedirs(os.path.join(store, sub), exist_ok=True)
    inner = [sys.executable, "-m", "symbiont.admission", "sandbox", sid,
             "--admission-dir", a.admission_dir, "--sources-config", a.sources_config,
             "--drafts-dir", a.drafts_dir, "--adapters-dir", a.adapters_dir,
             "--gate-file", a.gate_file, "--policy", a.policy,
             "--store", store,
             "--budget-requests", str(budget["requests"]),
             "--budget-bytes", str(budget["bytes"]),
             "--budget-wall-s", str(budget["wall_s"])]
    print(f"SANDBOX   {sid}  into throwaway store {store}")
    print(f"  budget requests={budget['requests']} bytes={budget['bytes']} wall_s={budget['wall_s']}")
    print(f"  {sandbox.allowlist_banner(cfg)}")
    try:
        rec = sandbox.run(inner, class_name="adapter", row_paths=[store], cwd=sandbox.FARM,
                          label=f"sandbox-{sid}", capture_stdout=True,
                          extra_env={"SYMBIONT_SANDBOX_INSIDE": "1"})
    except sandbox.SandboxRefused as e:
        shutil.rmtree(root, ignore_errors=True)
        print(f"REFUSED[unconfined] {sid}: {e}")
        return 2
    out = rec.get("stdout") or ""
    if out:
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
    if rec["breach"]:
        for f in rec["findings"]:
            print(f"REFUSED[sandbox-breach] {sid}: {f.get('kind')} {f.get('path') or f.get('host')} "
                  f"— outside the drafted adapter's write allowlist")
        shutil.rmtree(root, ignore_errors=True)
        return 3
    after = sandbox.dirhash(sandbox.STORE_DIR)
    throwaway_sha = sandbox.dirhash(store)
    receipt_in = os.path.join(store, "receipt.json")
    rp = ""
    if os.path.isfile(receipt_in):
        receipt = _load(receipt_in)
        receipt.update(throwaway_store=store, throwaway_store_sha256=throwaway_sha,
                       real_store_sha256_before=before,
                       network_allowlist=sorted(allowed_hosts))
        shutil.rmtree(root, ignore_errors=True)
        receipt["throwaway_deleted"] = not os.path.exists(store)
        receipt["real_store_sha256_after"] = after
        receipt["real_store_unchanged"] = (before == after)
        ts = receipt.get("run_id", "sandbox").replace("sandbox-", "")
        rp = _jdump(receipt, os.path.join(_admit_dir(a, sid), f"sandbox-{ts}.receipt.json"))
        print(f"  THROWAWAY store sha256={throwaway_sha} (deleted: {receipt['throwaway_deleted']})")
        print(f"  REAL store {before[:16]}… -> {after[:16]}… "
              f"{'UNCHANGED' if receipt['real_store_unchanged'] else 'CHANGED — FINDING'}")
        print(f"RECEIPT {rp}")
    else:
        shutil.rmtree(root, ignore_errors=True)
        print(f"  THROWAWAY store sha256={throwaway_sha} (deleted on exit)")
    if before != after:
        print(f"FINDING[sandbox-breach] {sid}: the real store changed during a sandbox run — "
              f"{before} -> {after}")
        return 3
    return rec["exit"]


def _sandbox_receipt(store: str, receipt: dict) -> str:
    """A refusal is a legal answer WITH a record: the run record and the receipt both land in the
    throwaway store, which the outer half copies out before deleting it. Nothing is lost."""
    _jdump(receipt, os.path.join(store, "runs", f"{receipt['run_id']}.json"))
    return _jdump(receipt, os.path.join(store, "receipt.json"))


def _name_licence_host(e, sid: str, cfg: dict, refusals: list) -> tuple[str, str]:
    """(code, detail) for a contract refusal raised by the harvest — same words, except ONE case.

    req-P1-THE-LICENCE-HOST-IS-IN-THE-ALLOWL deliverable 3: a licence page SERVED from a host no
    declaration names (a CDN in front of the page, an alias, a redirect to another site) is
    REFUSED BY NAME — REFUSED[licence-host-undeclared] — naming that host. Before this, the
    network guard refused the connect and the draft adapter reported it as `licence-unknown`, so
    the operator saw a dead end ('terms unreadable') instead of the one thing to fix (declare the
    host, or point licence_url at it). The host is taken from the guard's own refusal record and
    must also be named by the refusal text, so a metadata redirect that merely shares the
    message is never mistaken for the licence page."""
    code = str(getattr(e, "code", ""))
    text = str(e)
    if code != "licence-unknown":
        return code, text
    declared = [h for h, _ in sandbox.allowlist_declarations(cfg)]
    third = [r.get("host") for r in refusals
             if r.get("host") and r.get("host") not in declared and r.get("host") in text]
    if not third:
        return code, text
    hosts = ", ".join(sorted(set(third)))
    return "licence-host-undeclared", (
        f"the licence page is served from {hosts}, a host this entry does not declare (its "
        f"declarations: {', '.join(declared) or 'none'}) — the allowlist will not follow the "
        f"terms onto a host nobody named, so the source is refused rather than admitted under "
        f"terms it is forbidden to read. Declare licence_url on {hosts} (one host per "
        f"declaration) if that is really where the terms live. [{text}]")


def _sandbox_inside(a, sid: str, cfg: dict, budget: dict, rate: dict,
                    allowed_hosts: set) -> int:
    """The confined half: harvest with the network allowlist armed, into the throwaway store."""
    ts = _stamp()
    store = os.path.abspath(a.store)
    fetcher = Fetcher(budget=budget, rate_limit=rate)
    adapters = _load_for_check([a.drafts_dir])
    ad = adapters[sid]
    ad.configure(cfg)
    src: dict = {}
    refusals: list = []
    n = None
    print(f"  allowlist armed: {sandbox.allowlist_banner(cfg)[len('allowlist: '):]}")
    try:
        with sandbox.network_guard(allowed_hosts, refusals):
            n = loop_mod.harvest_source(sid, cfg, ad, fetcher, store, "none", {},
                                        f"sandbox-{ts}", src)
    except sandbox.NetworkRefused as e:
        print(f"REFUSED[sandbox-breach] {sid}: the drafted adapter tried to connect to "
              f"{e.host} — not in its network allowlist ({', '.join(sorted(allowed_hosts))})")
        _sandbox_receipt(store, dict(run_id=f"sandbox-{ts}", id=sid, at=_utc().isoformat(),
                                     refused=dict(code="sandbox-breach", host=e.host,
                                                  detail=str(e)),
                                     allowlist=sandbox.allowlist_banner(cfg),
                                     licence_host=sandbox.licence_host_of(cfg),
                                     network_refusals=refusals,
                                     network_allowlist=sorted(allowed_hosts),
                                     requests=list(fetcher.log),
                                     cost=dict(requests=len(fetcher.log),
                                               bytes_in=fetcher.bytes_in)))
        return 3
    except contract.Refusal as e:
        cost = dict(requests=len(fetcher.log), bytes_in=fetcher.bytes_in,
                    wall_s=fetcher.wall_seconds, rate_limit_waits=fetcher.waits)
        code, detail = _name_licence_host(e, sid, cfg, refusals)
        _sandbox_receipt(store, dict(run_id=f"sandbox-{ts}", id=sid, at=_utc().isoformat(),
                                     refused=dict(code=code, detail=detail), cost=cost,
                                     allowlist=sandbox.allowlist_banner(cfg),
                                     licence_host=sandbox.licence_host_of(cfg),
                                     network_refusals=refusals,
                                     network_allowlist=sorted(allowed_hosts),
                                     requests=list(fetcher.log)))
        print(f"REFUSED[{code}] {sid}: {detail}")
        print(f"  the throwaway store holds what landed before the refusal: {store}")
        return 3
    except FetchError as e:
        code = "source-gone" if getattr(e, "status", 0) in (404, 410) else "source-unreachable"
        _sandbox_receipt(store, dict(run_id=f"sandbox-{ts}", id=sid, at=_utc().isoformat(),
                                     refused=dict(code=code, detail=str(e)),
                                     allowlist=sandbox.allowlist_banner(cfg),
                                     licence_host=sandbox.licence_host_of(cfg),
                                     network_refusals=refusals,
                                     network_allowlist=sorted(allowed_hosts),
                                     requests=list(fetcher.log)))
        print(f"REFUSED[{code}] {sid}: {e}")
        print(f"  the throwaway store holds what landed before the refusal: {store}")
        return 3

    # the sample rows and the xref rate come from the normalised output itself, not a summary
    recs = list(n.records) if n else []
    total_rec = len(recs)
    with_xref = sum(1 for r in recs if r.get("xrefs"))
    xref_rate = (round(with_xref / total_rec, 3) if total_rec else None)
    sample = [dict(key=r.get("key"), xrefs=r.get("xrefs"), coords=r.get("coords"))
              for r in recs[:3]]
    receipt = dict(
        run_id=f"sandbox-{ts}", id=sid, at=_utc().isoformat(), store=store, budget=budget,
        verify=src.get("verify"), cost=dict(requests=len(fetcher.log), bytes_in=fetcher.bytes_in,
                                            wall_s=fetcher.wall_seconds,
                                            rate_limit_waits=fetcher.waits),
        rows_in=src.get("normalise", {}).get("rows_in"),
        records=total_rec, rejects=src.get("normalise", {}).get("rejects"),
        xref_rate=xref_rate, sample_rows=sample,
        release=(src.get("discover") or {}).get("release"),
        licence=(src.get("discover") or {}).get("licence"),
        licence_host=sandbox.licence_host_of(cfg),
        allowlist=sandbox.allowlist_banner(cfg),
        requests=list(fetcher.log),
        network_refusals=refusals, network_allowlist=sorted(allowed_hosts),
        refusal=None,
    )
    _jdump(receipt, os.path.join(store, "receipt.json"))
    v = src.get("verify") or {}
    print(f"  release={receipt['release']}  rows_in={receipt['rows_in']} "
          f"records={total_rec} rejects={receipt['rejects']}")
    print(f"  VERIFY ours={v.get('ours')} source={v.get('source_count')} "
          f"{'OK' if v.get('ok') else 'DISAGREE'}")
    print(f"  cost requests={receipt['cost']['requests']} bytes={receipt['cost']['bytes_in']}")
    print(f"  xref rate {with_xref}/{total_rec} records carry >=1 xref"
          if total_rec else "  xref rate n/a (no records)")
    print(f"  network refusals: {len(refusals)}")
    print(f"RECEIPT-INNER {os.path.join(store, 'receipt.json')}")
    return 0

# ═══════════════════════════════════════════════════════════════════ 5 · source_card
def _machine_block(sid: str, receipt: dict, lic: dict, v: dict, question: str) -> str:
    """The card's machine-readable lines — the SAME card the gate's word admits, in the shape
    `admission_policy.read_card` reads (req-P1-ADMISSION-POLICY: source_admit consults the policy
    over the card, so the card must be readable by a program and not only by a person).

    Every line is DERIVED from the receipts already on disk — nothing here is typed by an agent:
      licence      the licence page was read and hashed (cmd_card refuses earlier if it was not)
      count_match  our count equals the source's own published count (the sandbox verify)
      sample_rows  the sampled rows were parsed and each carries its key
      xref_rate    the xref rate is KNOWN and non-zero (the ids actually join to our map)
      cost         the sandbox cost is inside the source's own budget
    A check that fails is written with its evidence, so the failure is visible on the card and
    the operator can still admit the source by word.
    """
    cost = receipt.get("cost") or {}
    budget = receipt.get("budget") or {}
    sample = receipt.get("sample_rows") or []
    over = [f"{k}={cost.get(k)} > {budget.get(k)}" for k in ("requests", "bytes", "wall_s")
            if budget.get(k) is not None and (cost.get(k) or 0) > budget[k]]
    rate = receipt.get("xref_rate")
    checks = [
        ("licence", "ok" if lic.get("status") == "read" and lic.get("sha256")
         else f"terms never read ({lic.get('status')})"),
        ("count_match", "ok" if v.get("ok") is True
         else f"ours={v.get('ours')} vs source={v.get('source_count')} at {v.get('url')}"),
        ("sample_rows", "ok" if sample and all(r.get("key") for r in sample)
         else (f"{len(sample)} sample row(s), one carries no key" if sample
               else "no parsed sample rows — the source's own format claims were never checked")),
        ("xref_rate", "ok" if rate not in (None, 0)
         else f"{rate} — the source's ids do not join our xref map"),
        ("cost", "ok" if not over else "over budget (" + ", ".join(over) + ")"),
    ]
    return ("\n## machine-readable — the lines source_admit's policy consult reads\n"
            f"source: {sid}\n"
            f"changed: carded {receipt.get('at')}: {question}\n"
            + "".join(f"check {k}: {val}\n" for k, val in checks))


def cmd_card(a) -> int:
    sid = a.id
    if not a.question:
        return _refuse("question", f"{sid}: the card must name the question it helps answer — "
                                   f"pass --question; a source with no question is a cost")
    cand = _need_candidate(a, sid)
    ev = _need_evaluation(a, sid)
    receipts = sorted(glob.glob(os.path.join(_admit_dir(a, sid), "sandbox-*.receipt.json")))
    if not receipts:
        return _refuse("no-sandbox", f"{sid}: no sandbox run on disk under "
                                     f"{_admit_dir(a, sid)} — source_sandbox {sid} first")
    receipt = _load(receipts[-1])
    lic = ev.get("licence") or {}
    if lic.get("status") != "read":
        return _refuse("licence", f"{sid}: the licence terms were never read "
                                 f"({lic.get('note') or 'no licence url at probe time'}) — a "
                                 f"source may not be carded under terms nobody read")

    v = receipt.get("verify") or {}
    ours, theirs = v.get("ours"), v.get("source_count")
    counts_line = (f"ours={ours} vs source={theirs} at {v.get('url')}"
                   if theirs is not None else
                   f"ours={ours}; the source publishes no count of its own — rows_in="
                   f"{receipt.get('rows_in')} accounted by records+rejects "
                   f"({receipt.get('records')}+{receipt.get('rejects')})")
    cost = receipt.get("cost") or {}
    sample_txt = "; ".join(
        f"{r.get('key', '?')} {json.dumps(r.get('xrefs') or {})}" for r in
        (receipt.get("sample_rows") or [])[:3]) or "—"
    n_sample = len(receipt.get("sample_rows") or [])
    x_sample = sum(1 for r in (receipt.get("sample_rows") or []) if r.get("xrefs"))
    cadence = (ev.get("cadence") or {}).get("last_modified") or "unknown"
    accessions = ", ".join(sorted((ev.get("accessions") or {}))) or "none found in sample"
    checks_txt = "; ".join(f"{c['check']}={c['status']}" for c in (ev.get("checks") or []))
    card = (
        f"# ADMISSION CARD — {sid}\n"
        f"\n"
        f"source       {cand['url']}\n"
        f"probed       {cand['probed_at']} ({cand['bytes']} bytes, sha256 {cand['sha256'][:16]}…)\n"
        f"licence      {lic.get('name') or lic.get('quote', '')[:60]}\n"
        f"             {lic.get('url')} (read, sha256 {lic.get('sha256', '')[:16]}…)\n"
        f"counts       {'MATCHED' if v.get('ok') else 'DISAGREED'}: {counts_line}\n"
        f"sample rows  {sample_txt}\n"
        f"xref rate    {receipt.get('xref_rate')} ({x_sample} of {n_sample} sample rows carry ids)\n"
        f"cost         requests={cost.get('requests')} bytes={cost.get('bytes_in')} "
        f"wall={cost.get('wall_s')}s (sandbox {receipt.get('run_id')}, budget "
        f"{json.dumps(receipt.get('budget', {}))})\n"
        f"cadence      last-modified {cadence}\n"
        f"rate limit   {json.dumps(ev.get('rate_limit', {}))}\n"
        f"accessions   {accessions}\n"
        f"question     {a.question}\n"
        f"\n"
        f"checks       {checks_txt}; sandbox={'green' if v.get('ok') else 'RED'}\n"
        + _machine_block(sid, receipt, lic, v, a.question)
    )
    cpath = os.path.join(_admit_dir(a, sid), "card.md")
    with open(cpath, "w", encoding="utf-8") as fh:
        fh.write(card)

    # -- register in sources.yml as enabled:false — the one definition site carries the ------
    #    definition, INACTIVE; source_admit is the flip. Atomic, header preserved, re-read.
    sources = contract.load_sources(a.sources_config)
    if sid in sources:
        return _refuse("id-exists", f"{sid}: already defined in {a.sources_config} — a source "
                                    f"is defined once; repoint it, do not duplicate it")
    sources[sid] = dict(
        id=sid, adapter=sid, enabled=False,
        note=f"agent-added source, carded {receipt.get('at')}: {a.question}",
        base_url=cand["url"], endpoints={"data": ".", "metadata": "."},
        # required per endpoint (a source whose class is missing is refused at load): a carded
        # candidate is STORE, with the reason stated — admission read the licence, and nothing
        # forbids storage; a later operator edit to point-at is exactly that, an operator edit
        storage={"data": {"class": "store", "reason": "admission card: the licence was read and "
                                                "does not forbid storage"},
                 "metadata": {"class": "store", "reason": "admission card: the licence was read "
                                                    "and does not forbid storage"}},
        rate_limit={k: ev["rate_limit"][k] for k in ("requests_per_minute", "min_interval_s")},
        licence_url=cand["licence_url"], licence_text=lic.get("name") or lic.get("quote", "")[:80],
        staleness={"policy_days": 3650},
        budget=dict(SANDBOX_DEFAULT_BUDGET), quarantined=None,
    )
    with open(a.sources_config, encoding="utf-8") as fh:
        header = contract._header(fh.read())
    tmp = a.sources_config + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(header + contract.dump_sources(sources))
    os.replace(tmp, a.sources_config)
    back = contract.load_sources(a.sources_config)
    if sid not in back or back[sid]["enabled"]:
        return _refuse("register", f"{sid}: the registration did not survive a re-read of "
                                   f"{a.sources_config}")
    print(f"CARD      {sid} -> {os.path.join(_admit_dir(a, sid), 'card.md')}")
    print(f"  counts {'MATCHED' if v.get('ok') else 'DISAGREED'}: {counts_line[:90]}")
    print(f"  question: {a.question}")
    print(f"REGISTERED {sid}  enabled:false in {os.path.abspath(a.sources_config)} "
          f"(card: {cpath})")
    return 0


# ═══════════════════════════════════════════════════════════════════ 6 · source_admit
def _gate_line(path: str) -> str:
    if not os.path.isfile(path):
        raise SystemExit(_refuse("gate", f"the gate file is missing: {path} — an admission gate "
                                          f"that is not on record cannot be quoted, so nothing "
                                          f"may be admitted"))
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    raise SystemExit(_refuse("gate", f"the gate file carries no owner's line: {path}"))


def cmd_admit(a) -> int:
    sid = a.id
    card = os.path.join(_admit_dir(a, sid), "card.md")
    if not os.path.isfile(card):
        return _refuse("card", f"{sid}: no admission card at {card} — source_card {sid} first; "
                               f"a gate's word admits a CARD, not a wish")
    sources = contract.load_sources(a.sources_config)
    if sid not in sources:
        return _refuse("not-registered", f"{sid}: not defined in {a.sources_config} — the card "
                                         f"registers the source (enabled: false) before the gate "
                                         f"is asked anything")

    # -- THE POLICY CONSULT (req-P1-ADMISSION-POLICY, owner D3 2026-11-10) -----------------
    # The station READS the owner's policy file and never sets a number itself. A source whose
    # card passes every check the policy names AND that has been stable for N consecutive
    # cycles SELF-ADMITS: no gate word is demanded, and the receipt says "by policy, rule
    # stable_cycles_before_self_admit, N=<n>". EVERYTHING ELSE still needs the operator's word
    # — the policy can only ever REMOVE the need for it, never become a second blocker. A
    # policy that is missing, unreadable or malformed fails CLOSED: self-admission is refused
    # and the parse error is named, and the operator's word still admits.
    verdict = admission_policy.consult(sid, card, a.store, a.policy, sources)
    by = "policy"
    gate_word = None
    if verdict["ok"]:
        print(f"POLICY    {verdict['policy_path']} — {sid} self-admits: {verdict['line']} "
              f"({verdict['cycles']} stable cycle(s), card {os.path.abspath(card)})")
    else:
        print(f"POLICY    {sid} does NOT self-admit [{verdict['code']}] {verdict['reason']}")
        gate_word = _gate_line(a.gate_file)
        if (a.gate or "").strip() != gate_word:
            print(f"REFUSED[gate] {sid}: admission is the ONE gated step — the policy does not "
                  f"self-admit it ({verdict['code']}), so the gate on record "
                  f"({os.path.abspath(a.gate_file)}) is the owner's word:")
            print(f'  "{gate_word}"')
            print(f"  --gate must quote it exactly. Everything before this step was agent work; "
                  f"going live is not.")
            return 2
        by = "operator"
    dpath = _draft_path(a, sid)
    if not os.path.isfile(dpath):
        return _refuse("no-draft", f"{sid}: no drafted adapter at {dpath} — nothing to move into "
                                   f"{os.path.abspath(a.adapters_dir)}")
    dest = os.path.join(a.adapters_dir, f"{sid}.py")
    if os.path.isfile(dest):
        return _refuse("adapter-exists", f"{dest} already exists — one adapter, one source")

    # -- the flip, PROVED before it is written: config (enabled: true) + the moved adapter must
    #    satisfy the contract's registry check, or the admission rolls back and names itself.
    flip = dict(sources)
    flip[sid] = dict(sources[sid], enabled=True)
    moved_dir = os.path.join(_admit_dir(a, sid), "_admit_move")
    os.makedirs(moved_dir, exist_ok=True)
    shutil.copyfile(dpath, os.path.join(moved_dir, f"{sid}.py"))
    try:
        adapters = _load_for_check([a.adapters_dir, moved_dir])
        contract.check_registry(flip, adapters)
    except Exception as e:
        shutil.rmtree(moved_dir, ignore_errors=True)
        return _refuse("registry", f"{sid}: admitting it would leave config and code in "
                                   f"disagreement — REFUSED, nothing written: {e}")

    # ── THE DIARY (req-P1-THE-DIARY-appended-by-the-code-nev) ─────────────────────────────────
    # ADMITTED is the DECISION this whole pipeline exists to record, so the line is written BEFORE
    # the switch moves: if the diary refuses (an illegal transition — e.g. an `admitted` after a
    # `refused` with no `reconsidered`), nothing is admitted and nothing is flipped. A decision
    # with no history is a rumour, and a switch flipped by a rumour is the "two truths" this row
    # exists to end.
    source_id, how = events.dataset_of(flip[sid])
    try:
        ev = events.append(a.store, "admitted", source_id=source_id,
                           actor=("operator" if by == "operator" else "machine"),
                           why=(f"{sid}: the admission gate said yes ("
                                + (f"by policy: {verdict['line']}" if by == "policy"
                                   else "the operator's word on record")
                                + f"); card {os.path.basename(card)}, every required check green, "
                                  f"the flip survived its own re-read"),
                           evidence=dict(adapter=sid, card=os.path.abspath(card),
                                         card_sha256=_sha(open(card, "rb").read()),
                                         gate_file=os.path.abspath(a.gate_file),
                                         gate_word=gate_word, by=by,
                                         policy_line=verdict["line"] or None,
                                         policy_sha256=verdict.get("policy_sha"),
                                         adapter_file=os.path.abspath(dest),
                                         sources_config=os.path.abspath(a.sources_config),
                                         source_id_source=how))
    except events.DiaryRefused as e:
        shutil.rmtree(moved_dir, ignore_errors=True)
        if e.code == "truncated-diary":
            print(str(e), flush=True)
        return _refuse(e.code, f"{sid}: the diary refused the `admitted` event, so NOTHING was "
                               f"admitted and sources.yml was not touched: {e}")
    print(f"DIARY     seq {ev['seq']}  admitted  {source_id}  "
          f"({os.path.basename(events.diary_path(a.store))})")

    with open(a.sources_config, encoding="utf-8") as fh:
        header = contract._header(fh.read())
    tmp = a.sources_config + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(header + contract.dump_sources(flip))
    os.replace(tmp, a.sources_config)
    os.makedirs(a.adapters_dir, exist_ok=True)
    shutil.move(dpath, dest)
    shutil.rmtree(moved_dir, ignore_errors=True)

    # -- the re-read proof: the flip survived, the adapter loads, the registry is green ---------
    back = contract.load_sources(a.sources_config)
    ok_back = back.get(sid, {}).get("enabled") is True
    try:
        final = _load_for_check([a.adapters_dir])
        contract.check_registry(back, final)
        registry = "green"
    except Exception as e:
        # roll the flip back rather than leave the one definition site half-admitted
        sources[sid]["enabled"] = False
        with open(a.sources_config, "w", encoding="utf-8") as fh:
            fh.write(header + contract.dump_sources(sources))
        shutil.move(dest, dpath)
        return _refuse("registry", f"{sid}: the flip did not survive its own re-read "
                                   f"({type(e).__name__}: {e}) — ROLLED BACK, nothing admitted")

    receipt = dict(id=sid, admitted_at=_utc().isoformat(), by=by,
                   gate_word=gate_word, gate_file=os.path.abspath(a.gate_file),
                   policy=dict(path=verdict["policy_path"], sha256=verdict["policy_sha"],
                               rule=verdict["rule"], N=verdict["N"],
                               numbers=verdict["numbers"],
                               line=verdict["line"] or None,
                               stable_cycles=verdict["cycles"],
                               stable_cycle_runs=verdict["runs"],
                               refusal=None if verdict["ok"] else
                               dict(code=verdict["code"], reason=verdict["reason"])),
                   card=os.path.abspath(card), sources_config=os.path.abspath(a.sources_config),
                   adapter=os.path.abspath(dest), registry=registry)
    rp = _jdump(receipt, os.path.join(_admit_dir(a, sid), "admitted.json"))
    asked = (verdict["line"] if by == "policy" else
             "the operator's word (policy did not self-admit: " + verdict["code"] + ")")
    if a.store:
        ddir = os.path.join(a.store, "digest")
        os.makedirs(ddir, exist_ok=True)
        with open(os.path.join(ddir, f"{sid}.md"), "a", encoding="utf-8") as fh:
            fh.write(f"admitted {_utc().replace(microsecond=0).isoformat()}: {sid} active in "
                     f"{os.path.abspath(a.sources_config)} (card {os.path.abspath(card)}; "
                     f"{asked})\n")
    print(f"ADMITTED  {sid}  enabled:true in {os.path.abspath(a.sources_config)}")
    print(f"  adapter moved: {os.path.abspath(dest)}")
    print(f"  registry check after the flip: {registry}")
    print(f"  {asked} is on the receipt: {rp}")
    return 0


# ═══════════════════════════════════════════════════════════════════════ the CLI
def main(argv=None) -> int:
    # the shared options sit on the main parser AND on every subparser (via `parents`), so an
    # agent may write them before or after the subcommand — the CLI must not care which.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--admission-dir", default=DEFAULT_ADMISSION_DIR,
                        help="where candidates, evaluations, sandbox stores and cards land")
    common.add_argument("--sources-config", default=contract.DEFAULT_SOURCES)
    common.add_argument("--drafts-dir", default=DEFAULT_DRAFTS_DIR)
    common.add_argument("--adapters-dir", default=DEFAULT_ADAPTERS_DIR)
    common.add_argument("--gate-file", default=DEFAULT_GATE_FILE)
    common.add_argument("--policy", default=admission_policy.DEFAULT_POLICY,
                        help="the owner's admission policy (req-P1-ADMISSION-POLICY); the station "
                             "READS it and never writes it — a missing/malformed policy fails "
                             "closed for SELF-admission only")
    common.add_argument("--store", default=DEFAULT_STORE, help="digest lines are written here")

    ap = argparse.ArgumentParser(
        prog="symbiont.admission",
        description="the agent-added-sources pipeline: probe -> evaluate -> draft -> sandbox -> "
                    "card -> admit (the one gated step)",
        parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", parents=[common],
                       help="read a URL under a hard budget; no source -> NO-SOURCE")
    p.add_argument("url")
    p.add_argument("--id", default=None, help="candidate id (default: a slug of the URL)")
    p.add_argument("--licence-url", default="", help="the licence page, if the probe knows it")

    p = sub.add_parser("evaluate", parents=[common],
                       help="licence, accessions, rate limit, size, cadence")
    p.add_argument("id")

    p = sub.add_parser("draft", parents=[common],
                       help="adapter skeleton from the template + the contract load check")
    p.add_argument("id")

    p = sub.add_parser("sandbox", parents=[common],
                       help="run the drafted adapter into a throwaway store, on a budget")
    p.add_argument("id")
    p.add_argument("--budget-requests", type=int, default=None)
    p.add_argument("--budget-bytes", type=int, default=None)
    p.add_argument("--budget-wall-s", type=int, default=None)

    p = sub.add_parser("card", parents=[common],
                       help="the admission card; registers the source enabled:false")
    p.add_argument("id")
    p.add_argument("--question", default="", help="the question this source helps answer")

    p = sub.add_parser("admit", parents=[common],
                       help="THE gated step: flip active in sources.yml")
    p.add_argument("id")
    p.add_argument("--gate", default="", help="the gate's word, quoted")

    a = ap.parse_args(argv)
    a.admission_dir = os.path.abspath(a.admission_dir)
    a.sources_config = os.path.abspath(a.sources_config)
    a.drafts_dir = os.path.abspath(a.drafts_dir)
    a.adapters_dir = os.path.abspath(a.adapters_dir)
    a.gate_file = os.path.abspath(a.gate_file)
    a.store = os.path.abspath(a.store)
    return {"probe": cmd_probe, "evaluate": cmd_evaluate, "draft": cmd_draft,
            "sandbox": cmd_sandbox, "card": cmd_card, "admit": cmd_admit}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
