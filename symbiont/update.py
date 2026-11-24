"""UPDATE AS AN MCP TOOL — check, propose, never apply (req-P1-UPDATE-AS-AN-MCP-TOOL).

The row: "i meant tool sources … so update is part of the MCP exposed, right?" The machine door
exposed seven tools (source_health · source_probe · source_evaluate · source_draft · source_sandbox
· source_card · source_admit) and NO update/version tool at all — an agent could ADD a source and
could not ask "is anything out of date?". This module is the ONE definition of the three tools that
answer it, and `symbiont/mcpserver.py` runs THIS CLI, so the MCP tool and the operator's command are
the same words and cannot drift (the pattern admission.py and health.py already follow).

    python3 -m symbiont.update check   <source>      -> source_update_check
    python3 -m symbiont.update plan    <source>      -> source_update_plan
    python3 -m symbiont.update version               -> station_version

WHAT EACH ONE ANSWERS, AND WHAT IT WILL NOT DO
  check    the source's upstream release vs the release OUR store holds, the age of ours against its
           own staleness policy, and our last success: {stored, upstream, moved, stale, age_days,
           last_success}. It reads the METADATA ENDPOINT ONLY (one HEAD, and a GET of the body only
           when the endpoint is a small document — never the release files), is capped at 64 KB of
           metadata, obeys the source's own rate limit, and NEVER fetches data.
  plan     what an update WOULD do: rows added/changed/retired (only where the metadata endpoint can
           say so — otherwise "unknown" and the reason, never a guess), the cost (measured from the
           last run, or "unknown" naming why), and the licence in force.
  version  the running station's version, the store's schema version, the adapters' pinned releases
           and whether the deployment's declared channel is ahead — "am I out of date" in one call.

THE RULE THE ROW SETS: NO TOOL HERE APPLIES AN UPDATE. An update is a ROW: the agent may file a
proposal quoting check+plan, and the operator pulls. There is no write path in this file — nothing
here opens a file for writing, removes, renames or spawns a process — and the grep that proves it is
in the lane report. The only disk access below is reads, and the only side effect of a call is a
line of JSON on stdout.

WHERE A FACT COMES FROM (never invented)
  stored release     <store>/state/<id>.json  (the release the last publish pinned)
  age / stale        release_at vs the source's own staleness.policy_days in sources.yml
  last_success       the newest run record where the source ran AND its count check agreed —
                     read through health.py (one definition of "last success", not a second one)
  upstream release   the metadata endpoint, three NAMED kinds and one refusal:
                       json_release         a small metadata document carrying a release field
                       http_last_modified   the release file's own Last-Modified date stamp
                       none                 the arm publishes NO release id (literature: a pin,
                                            not a data release) — the tool says so, it does not
                                            invent a version
                       unreadable           the endpoint refused/errored; moved is unknown, named
  cost               the last run's MEASURED requests/data_bytes for that source (kind=measured) or
                     "unknown" with the reason; the source's declared budget is quoted as the CAP an
                     update could not exceed, which is a cap and not an estimate.
  licence in force   the terms our last run read and hashed (state/<id>.json licence) — "recorded",
                     or "declared" when only sources.yml has seen the words.

Exit codes: 0 the record was produced (including moved=unknown: "I cannot tell" IS the answer) ·
            2 REFUSED[config|source|store-absent] — bad input, refused BY NAME before any request.

Wire: `check` and `plan` print ONE JSON object; `version` prints one JSON object. `--json` is
accepted so an operator can be explicit; the record is identical.
"""
from __future__ import annotations

import argparse
import email.utils
import json
import os
import platform
import re
import sys

from . import contract, health

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORE = os.path.join(ROOT, "store")
LITERATURE_CONFIG = os.path.join(ROOT, "literature", "sources.yml")
LITERATURE_STORE = os.path.join(ROOT, "store", "literature")
PYPROJECT = os.path.join(os.path.dirname(ROOT), "pyproject.toml")

# A metadata document bigger than this is NOT this tool's business: it would be the release file,
# and reading it is a data fetch. The cap is checked on the HEAD's Content-Length BEFORE any GET.
MAX_META_BYTES = 65536
# The release fields a metadata document may state, in the order we look for them.
RELEASE_KEYS = ("release", "release_id", "version", "build", "tag")
RELEASE_JSON_RE = re.compile(r'"(?:release|release_id|version|build|tag)"\s*:\s*"?([A-Za-z0-9][\w.\-]*)"?')
# the trailing version token of a release id: "ensembl-116" -> "116", "mgi-2026-11-09" -> "2026-11-09"
TOKEN_RE = re.compile(r"([0-9][0-9A-Za-z._\-]*)$")
DATE_TOKEN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Refused(RuntimeError):
    """A refusal with a name: the code lands verbatim on stdout as REFUSED[<code>] <detail>."""
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _refuse(code: str, detail: str) -> None:
    raise Refused(code, detail)


# ─────────────────────────────────────────────────────────────── the two registries
def _gene_sources(path: str) -> dict:
    return contract.load_sources(path)          # refuses by name (ConfigError)


def _literature_sources(path: str):
    """The literature arm's config, or None. Its contract admits exactly one source (pubmed) and its
    `mcp.version` is an EXACT pin — the reason that arm has no data release id to compare."""
    if not os.path.isfile(path):
        return None
    from .literature import contract as lit_contract
    try:
        return lit_contract.load_sources(path)
    except Exception:
        return None


def _find(sid: str, sources_config: str, literature_config: str) -> dict:
    """Which registry defines this source, and in which shape. Refuses an unknown name BY NAME."""
    gene = _gene_sources(sources_config)
    if sid in gene:
        return dict(arm="gene", id=sid, cfg=gene[sid])
    lit = _literature_sources(literature_config)
    if lit and lit.get("id") == sid:
        return dict(arm="literature", id=sid, cfg=lit)
    _refuse("source", f"no source {sid!r} in {sources_config} "
                      f"({', '.join(sorted(gene))}) or in {literature_config} "
                      f"({lit.get('id') if lit else 'no literature config'})")


# ─────────────────────────────────────────────────────────────── release ids, compared by rule
def _token(release: str) -> str:
    """The trailing version token of a release id, or "" — the ONE rule used to compare our stored
    release with an upstream one, so a comparison is never a guess."""
    m = TOKEN_RE.search(release or "")
    return m.group(1) if m else ""


def _upstream_release(stored: str, token: str) -> str | None:
    """The upstream release id in OUR stored release's own shape (ensembl-116 -> ensembl-117,
    mgi-2026-11-09 -> mgi-2026-12-10), so both can be quoted and compared. None when there is no
    token to compare — an id we cannot shape is not an id we invent."""
    if not token:
        return None
    if stored and stored.endswith(token):
        return stored
    ts = _token(stored)
    if stored and ts and stored.endswith(ts):
        return stored[: len(stored) - len(ts)] + token
    return token


def _moved(stored: str, upstream: str | None):
    if not stored or not upstream:
        return None
    return stored != upstream


# ─────────────────────────────────────────────────────────────── the metadata endpoint, read once
def _date_token(last_modified: str) -> str:
    """'Thu, 09 Nov 2026 05:00:00 GMT' -> '2026-11-09' (the shape a release *file* stamps)."""
    try:
        return email.utils.parsedate_to_datetime(last_modified).date().isoformat()
    except Exception:
        return ""


def _capped_get(url: str, note: str) -> tuple[bytes, dict, bool]:
    """A GET that reads at MOST MAX_META_BYTES and hangs up. It exists because a metadata endpoint
    MAY lie about its own length (measured: rest.ensembl.org's /info/software answers a HEAD with
    `content-length: 0` and a GET with the real 16-byte JSON), and a metadata read must never
    become a data fetch: past the cap the body is not read at all."""
    import urllib.request
    from .http import TIMEOUT, UA, _Redirect

    r = urllib.request.Request(url, method="GET")
    r.add_header("User-Agent", UA)
    r.add_header("Accept", "*/*")
    with urllib.request.build_opener(_Redirect).open(r, timeout=TIMEOUT) as resp:
        body = resp.read(MAX_META_BYTES + 1)
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return body, headers, len(body) > MAX_META_BYTES


def _release_field(txt: str) -> str | None:
    """The release field a metadata DOCUMENT states, or None. A key wins over a regex, and no match
    means no answer — the caller then falls back to a date stamp or says it cannot tell."""
    try:
        doc = json.loads(txt)
    except Exception:
        doc = None
    if isinstance(doc, dict):
        for k in RELEASE_KEYS:
            if isinstance(doc.get(k), (str, int)) and str(doc[k]).strip():
                return str(doc[k]).strip()
    m = RELEASE_JSON_RE.search(txt)
    return m.group(1) if m else None


def _from_document(out: dict, txt: str, stored: str, lastmod: str) -> None:
    raw = _release_field(txt)
    if raw:
        up = _upstream_release(stored, _token(raw))
        out.update(release_kind="json_release", upstream=up, upstream_quote=txt.strip()[:200],
                   moved=_moved(stored, up),
                   note="the metadata document states its own release field")
        return
    _from_stamp(out, stored, lastmod, f"the document states no release field; its own excerpt was "
                                      f"{txt.strip()[:120]!r}", txt.strip()[:200])


def _from_stamp(out: dict, stored: str, lastmod: str, note: str, quote: str = "") -> None:
    tok = _date_token(lastmod)
    up = _upstream_release(stored, tok)
    out.update(release_kind="http_last_modified" if tok else "none", upstream=up,
               upstream_quote=quote or (f"Last-Modified: {lastmod}" if lastmod else ""),
               moved=_moved(stored, up), note=note if tok else f"{note} (no usable Last-Modified)")


def read_metadata(url: str, cfg: dict, stored: str) -> dict:
    """Read the METADATA endpoint and derive the upstream release, or say it cannot tell.

    Bounded by construction — at most one HEAD and at most one capped GET (64 KB):
      1 HEAD              -> Content-Length + Last-Modified (no body: this is not a fetch)
      2 small document    -> GET it (<= 64 KB) and read its own release field -> json_release
      3 big document      -> the endpoint IS the release file; its own Last-Modified date is the
                             stamp, and its body is NEVER read                 -> http_last_modified
      4 no usable length  -> ONE capped GET (see _capped_get), then 2 or 3
      5 nothing usable    -> release_kind none/unreadable, the reason named, moved stays unknown
    """
    from .http import FetchError, Fetcher, HttpStatus

    # the source's OWN rate limit is obeyed, and the metadata read has its own hard budget: a
    # metadata read may never spend a data run's allowance.
    f = Fetcher(budget=dict(requests=3, bytes=MAX_META_BYTES * 2, wall_s=60),
                rate_limit=cfg.get("rate_limit") or {})
    out = dict(metadata_url=url, release_kind="none", upstream=None, upstream_quote="",
               moved=None, requests=0, bytes_read=0, note="")
    try:
        h = f.head(url, "update-check metadata head")
    except (FetchError, HttpStatus) as e:
        out.update(release_kind="unreadable", note=f"the metadata endpoint could not be read: {e}")
        out["requests"], out["bytes_read"] = len(f.log), f.bytes_in
        return out

    clen = int(h.get("content_length") or 0)
    lastmod = h.get("last_modified") or ""
    extra_requests = extra_bytes = 0
    if 0 < clen <= MAX_META_BYTES:
        try:
            txt = f.get(url, "update-check metadata release field").decode("utf-8", "replace")
        except (FetchError, HttpStatus) as e:
            out.update(release_kind="unreadable",
                       note=f"the metadata document could not be read: {e}")
        else:
            _from_document(out, txt, stored, lastmod)
    elif clen > MAX_META_BYTES:
        _from_stamp(out, stored, lastmod,
                    f"the endpoint is {clen} bytes — larger than the {MAX_META_BYTES}-byte metadata "
                    f"cap, so it is the release file itself and its body was never read")
    else:
        try:
            body, hdrs, truncated = _capped_get(url, "update-check metadata capped get")
        except Exception as e:
            out.update(release_kind="unreadable",
                       note=(f"the metadata endpoint published no Content-Length and the capped GET "
                             f"failed: {type(e).__name__}: {e}"))
            extra_requests = 1
        else:
            extra_requests, extra_bytes = 1, len(body)
            lastmod = hdrs.get("last-modified") or lastmod
            if truncated:
                _from_stamp(out, stored, lastmod,
                            f"the endpoint published no usable Content-Length and its body ran past "
                            f"the {MAX_META_BYTES}-byte metadata cap — only its Last-Modified stamp "
                            f"was read")
            else:
                _from_document(out, body.decode("utf-8", "replace"), stored, lastmod)
    out["requests"] = len(f.log) + extra_requests
    out["bytes_read"] = f.bytes_in + extra_bytes
    return out


# ─────────────────────────────────────────────────────────────── the records
def _iso_now() -> str:
    return health._now().replace(microsecond=0).isoformat()


def _age_days(at, now) -> float | None:
    if not at:
        return None
    return round((now - at).total_seconds() / 86400, 1)


def check_record(sid: str, sources_config: str = contract.DEFAULT_SOURCES,
                 store: str = DEFAULT_STORE, literature_config: str = LITERATURE_CONFIG,
                 literature_store: str = LITERATURE_STORE) -> dict:
    """source_update_check: our stored release vs the upstream's, and our own heartbeat."""
    found = _find(sid, sources_config, literature_config)
    now = health._now()
    out = dict(tool="source_update_check", source=sid, arm=found["arm"],
               store=os.path.abspath(store), sources_config=os.path.abspath(sources_config),
               checked_at=_iso_now(), stored=None, upstream=None, moved=None, stale=None,
               age_days=None, last_success=None, release_kind="none", metadata_url="",
               upstream_quote="", reason="", applies=False)

    if found["arm"] == "literature":
        cfg = found["cfg"]
        lst = health._state_file(os.path.abspath(literature_store), sid)
        mcpver = ((cfg.get("mcp") or {}).get("version") or "")
        out.update(
            store=os.path.abspath(literature_store),
            release_kind="none", stored=None, upstream=None, moved=None, stale=None,
            age_days=_age_days(health._parse_iso(lst.get("release_at") or ""), now),
            pinned_contract=dict(mcp_version=mcpver, pin_is="the arm's contract, not a data release"),
            stored_source_release=lst.get("source_release"),
            reason=(f"the literature arm publishes NO release id: its contract is the pinned MCP "
                    f"{cfg.get('mcp', {}).get('package')}@{mcpver}, not a data release — this tool "
                    f"reports release_kind=none rather than inventing a version"))
        last_ok = _last_success_lit(os.path.abspath(literature_store), sid)
        out["last_success"] = last_ok
        return out

    cfg = found["cfg"]
    st = health._state_file(store, sid)
    stored = st.get("release") or ""
    policy = int((cfg.get("staleness") or {}).get("policy_days") or 0)
    rel_at = health._parse_iso(st.get("release_at") or st.get("updated_at") or "")
    age = _age_days(rel_at, now)
    meta_url = contract.resolve_url(cfg["base_url"], (cfg.get("endpoints") or {}).get("metadata", "."))
    meta = read_metadata(meta_url, cfg, stored)
    last_ok = _last_success(store, sid)

    out.update(
        stored=stored or None, upstream=meta["upstream"], moved=meta["moved"],
        stale=(bool(policy) and age is not None and age > policy) if age is not None else None,
        age_days=age, last_success=last_ok, release_kind=meta["release_kind"],
        metadata_url=meta["metadata_url"], upstream_quote=meta["upstream_quote"],
        policy_days=policy, enabled=cfg.get("enabled"), quarantined=cfg.get("quarantined"),
        requests=meta["requests"], bytes_read=meta["bytes_read"],
        stored_release_at=(st.get("release_at") or st.get("updated_at")),
        reason=_check_reason(stored, meta, age, policy))
    return out


def _check_reason(stored: str, meta: dict, age, policy: int) -> str:
    if meta["release_kind"] == "unreadable":
        return f"upstream unknown — {meta['note']}"
    if meta["moved"] is None:
        return (f"moved unknown — {meta['note']}" if meta["note"]
                else "moved unknown — no comparable release id")
    if meta["moved"]:
        return (f"the upstream has moved: ours is {stored}, upstream is {meta['upstream']} "
                f"({meta['upstream_quote']}) at {meta['metadata_url']}")
    tail = f"; ours is {age}d old against the {policy}d policy" if age is not None and policy else ""
    return f"up to date: both releases are {stored}{tail}"


def _last_success(store: str, sid: str) -> dict | None:
    """The newest run where the source RAN and its count check agreed — read through health.py so
    "last success" has one definition on both surfaces."""
    recs = health._run_records(store)
    now = health._now()
    for r in reversed(recs):
        s = (r.get("sources") or {}).get(sid) or {}
        if s.get("skipped") or s.get("refused"):
            continue
        if (s.get("verify") or {}).get("ok") is True:
            return dict(run_id=r.get("run_id"), at=r.get("started_at"),
                        age_days=_age_days(health._parse_iso(r.get("started_at") or ""), now))
    return None


def _last_success_lit(store: str, sid: str) -> dict | None:
    recs = health._run_records(store)
    now = health._now()
    for r in reversed(recs):
        verify = r.get("verify")
        ok = verify.get("ok") if isinstance(verify, dict) else None
        if r.get("status") in ("ok", "pass") or ok is True:
            return dict(run_id=r.get("run_id"), at=r.get("started_at"),
                        age_days=_age_days(health._parse_iso(r.get("started_at") or ""), now))
    return None


def _last_cost(store: str, sid: str):
    """The last run's MEASURED cost for this source, or None. A cap is not an estimate: the budget
    in sources.yml is quoted separately as the ceiling an update could not exceed."""
    recs = health._run_records(store)
    for r in reversed(recs):
        s = (r.get("sources") or {}).get(sid) or {}
        cost = s.get("cost") or {}
        if cost.get("requests") is not None:
            return dict(run_id=r.get("run_id"), requests=cost.get("requests"),
                        bytes=cost.get("bytes_in", cost.get("data_bytes")),
                        wall_s=cost.get("wall_s"), measured_at=r.get("started_at"))
    return None


def _last_cost_lit(store: str):
    """The literature arm counts TOOL CALLS, not HTTP requests: its newest run's own cost block."""
    for r in reversed(health._run_records(store)):
        cost = r.get("cost")
        if isinstance(cost, dict) and cost.get("tool_calls") is not None:
            return dict(run_id=r.get("run_id"), tool_calls=cost.get("tool_calls"),
                        wall_s=cost.get("wall_s"), measured_at=r.get("started_at"))
    return None


def plan_record(sid: str, sources_config: str = contract.DEFAULT_SOURCES,
                store: str = DEFAULT_STORE, literature_config: str = LITERATURE_CONFIG,
                literature_store: str = LITERATURE_STORE) -> dict:
    """source_update_plan: what an update WOULD do — rows, cost, licence — or "unknown", named."""
    chk = check_record(sid, sources_config, store, literature_config, literature_store)
    out = dict(tool="source_update_plan", source=sid, arm=chk["arm"], store=chk["store"],
               checked_at=chk["checked_at"], stored=chk["stored"], upstream=chk["upstream"],
               moved=chk["moved"], release_kind=chk["release_kind"],
               metadata_url=chk["metadata_url"], applies=False)
    found = _find(sid, sources_config, literature_config)
    cfg = found["cfg"]
    lstore = chk["store"] if chk["arm"] == "literature" else store
    st = health._state_file(lstore, sid)

    # -- rows: only where the metadata endpoint can say so ---------------------------------------
    out["rows"] = dict(added="unknown", changed="unknown", retired="unknown",
                       why=(f"the metadata endpoint {chk['metadata_url'] or '(none)'} states no row "
                            f"counts, and a row-level diff needs a fetch — no tool here fetches "
                            f"data, so added/changed/retired are unknown, not estimated"))
    if chk["arm"] == "gene" and cfg.get("endpoints", {}).get("metadata"):
        pass   # no source in this farm publishes per-release row counts at its metadata endpoint

    # -- cost: MEASURED from the last run, else unknown with the reason --------------------------
    cap = dict(cfg.get("budget") or {})
    if chk["arm"] == "literature":
        last_lit = _last_cost_lit(lstore)
        if last_lit:
            out["cost"] = dict(kind="measured", unit="tool_calls", tool_calls=last_lit["tool_calls"],
                               wall_s=last_lit["wall_s"], run_id=last_lit["run_id"],
                               basis=(f"run {last_lit['run_id']} spent tool_calls="
                                      f"{last_lit['tool_calls']} wall_s={last_lit['wall_s']} — the "
                                      f"same work an update would repeat"))
            out["cost_reason"] = ""
        else:
            out["cost"] = "unknown"
            out["cost_reason"] = (f"no run record under {os.path.join(lstore, 'runs')} carries a cost "
                                  f"block, so an update's tool calls cannot be measured")
    else:
        last = _last_cost(lstore, sid)
        if last:
            out["cost"] = dict(kind="measured", unit="requests", requests=last["requests"],
                               bytes_in=last["bytes"], wall_s=last["wall_s"],
                               basis=(f"run {last['run_id']} spent requests={last['requests']} "
                                      f"bytes_in={last['bytes']} wall_s={last['wall_s']} on this "
                                      f"source — the same work an update would repeat"),
                               run_id=last["run_id"])
            out["cost_reason"] = ""
        else:
            out["cost"] = "unknown"
            out["cost_reason"] = (f"no run record under {os.path.join(lstore, 'runs')} names {sid}, "
                                  f"and its metadata endpoint states no size, so an update's "
                                  f"requests/bytes cannot be measured — the declared budget is a "
                                  f"CAP, not an estimate")
    out["cost_cap"] = cap or "unknown"

    # -- licence in force ------------------------------------------------------------------------
    lic_state = st.get("licence") or {}
    terms_sha = lic_state.get("terms_sha256") or ""
    if not terms_sha and chk["arm"] == "literature":
        terms_sha = st.get("terms_sha256") or ""
    in_force = (lic_state.get("reused_from") or st.get("run_id") or st.get("last_run_id") or "?")
    out["licence"] = dict(name=cfg.get("licence_text", ""), url=cfg.get("licence_url", ""),
                          terms_sha256=terms_sha,
                          status="recorded" if terms_sha else "declared",
                          in_force_from=(f"run {in_force}" if terms_sha
                                         else "sources.yml only — never read in a run"))
    out["next"] = ("an update is a ROW: file a proposal quoting this plan with tools/rows.sh and the "
                   "operator pulls. No tool here applies an update.")
    out["reason"] = chk["reason"]
    return out


# ─────────────────────────────────────────────────────────────── station_version
def _release_bundles(store: str) -> list[dict]:
    """The frozen release bundles on disk (store/releases/<set>/RELEASE.json) — the deployment's own
    DECLARATION of what it serves. Newest first by frozen_at."""
    out = []
    rdir = os.path.join(store, "releases")
    if not os.path.isdir(rdir):
        return out
    for name in sorted(os.listdir(rdir)):
        p = os.path.join(rdir, name, "RELEASE.json")
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                rec = json.load(fh)
        except Exception:
            continue
        if isinstance(rec, dict):
            out.append(dict(release_set=rec.get("release_set") or name,
                            frozen_at=rec.get("frozen_at") or "",
                            sources=rec.get("sources") or {},
                            view_version=(rec.get("view") or {}).get("version")))
    out.sort(key=lambda r: r.get("frozen_at") or "", reverse=True)
    return out


def _store_views(store: str) -> list[dict]:
    out = []
    vdir = os.path.join(store, "view")
    if os.path.isdir(vdir):
        for name in sorted(os.listdir(vdir)):
            if not name.endswith(".schema.json"):
                continue
            stem = name[: -len(".schema.json")]
            ver = None
            try:
                with open(os.path.join(vdir, name), encoding="utf-8") as fh:
                    doc = json.load(fh)
                if isinstance(doc, dict):
                    ver = doc.get("version")
            except Exception:
                pass
            out.append(dict(view=stem, version=ver, schema=f"view/{name}"))
    return out


def _declared_version() -> str | None:
    """pyproject's declared version, so a drift between the code and the packaging is VISIBLE."""
    try:
        with open(PYPROJECT, encoding="utf-8") as fh:
            for ln in fh:
                m = re.match(r'\s*version\s*=\s*"([^"]+)"', ln)
                if m:
                    return m.group(1)
    except Exception:
        return None
    return None


def version_record(store: str = DEFAULT_STORE,
                   sources_config: str = contract.DEFAULT_SOURCES,
                   literature_config: str = LITERATURE_CONFIG) -> dict:
    """station_version: the station, its store schema, the adapters' pins, and the channel."""
    store = os.path.abspath(store)
    if not os.path.isdir(store):
        _refuse("store-absent", f"the store {store} is not a directory — station_version names the "
                                f"store schema version, so there must be a store to name")
    zones = [z for z in ("raw", "state", "runs", "xref", "view", "releases", "digest")
             if os.path.isdir(os.path.join(store, z))]
    gene = _gene_sources(sources_config)
    lit = _literature_sources(literature_config)

    from . import cli
    declared = _declared_version()

    adapters = {}
    for sid in sorted(gene):
        st = health._state_file(store, sid)
        adapters[sid] = dict(adapter=gene[sid].get("adapter"), role=_adapter_role(gene[sid]),
                             enabled=gene[sid].get("enabled"),
                             pinned_release=st.get("release") or None,
                             release_at=st.get("release_at") or st.get("updated_at"),
                             run_id=st.get("run_id"))

    bundles = _release_bundles(store)
    running = {sid: a["pinned_release"] for sid, a in adapters.items() if a["pinned_release"]}
    declared_bundle = bundles[0] if bundles else None
    ahead, why = _channel_ahead(declared_bundle, running)
    env_channel = os.environ.get("SYMBIONT_CHANNEL") or ""

    return dict(
        tool="station_version", checked_at=_iso_now(),
        station=dict(name="symbiont", version=cli.VERSION, version_from="symbiont/cli.py:VERSION",
                     declared_version=declared,
                     version_drift=(bool(declared) and declared != cli.VERSION),
                     python=platform.python_version(), package_root=os.path.dirname(ROOT)),
        store=dict(path=store, present=True,
                   schema_version=1,
                   schema_basis=(f"sources.yml `version: 1` (the registry schema this station reads, "
                                 f"{os.path.abspath(sources_config)}) + zones present: "
                                 f"{', '.join(zones)}"),
                   zones=zones, views=_store_views(store),
                   release_bundles=[b["release_set"] for b in bundles],
                   latest_release_set=(declared_bundle or {}).get("release_set")),
        adapters=adapters,
        literature=({lit.get("id"): dict(pin=f"{lit['mcp']['package']}@{lit['mcp']['version']}",
                                         release_kind="none",
                                         note="a pinned MCP contract, not a data release id")}
                    if lit else "no literature config"),
        channel=dict(declared=(env_channel or "unspecified (SYMBIONT_CHANNEL unset)"),
                     declared_bundle=(declared_bundle or {}).get("release_set"),
                     declared_at=(declared_bundle or {}).get("frozen_at"),
                     running_bundle=_bundle_name(running),
                     ahead=ahead, why=why))


def _adapter_role(cfg: dict) -> str | None:
    try:
        ad = contract.load_adapters().get(cfg.get("adapter"))
        return getattr(ad, "role", None)
    except Exception:
        return None


def _bundle_name(releases: dict) -> str:
    return "|".join(f"{k}={v}" for k, v in sorted(releases.items())) if releases else ""


def _channel_ahead(declared_bundle, running: dict):
    """Is the deployment's DECLARED release set ahead of what the running station pins? Compared
    name for name from files on disk; unknown when either side has nothing to compare."""
    if not declared_bundle:
        return None, ("no frozen release bundle under store/releases/ — nothing declares a channel, "
                      "so 'ahead' is unknown rather than assumed false")
    if not running:
        return None, ("no adapter pins a release (state/ is empty) — nothing to compare the "
                      "declared bundle against")
    ds = dict(declared_bundle.get("sources") or {})
    diffs = {k: dict(declared=ds.get(k), running=running.get(k))
             for k in sorted(set(ds) | set(running)) if ds.get(k) != running.get(k)}
    if diffs:
        return True, (f"the declared bundle {declared_bundle.get('release_set')} names releases the "
                      f"running station does not pin: {json.dumps(diffs, sort_keys=True)}")
    return False, (f"the declared bundle {declared_bundle.get('release_set')} matches every adapter "
                   f"pin exactly — the station is serving what its deployment declares")


# ─────────────────────────────────────────────────────────────── CLI
def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        prog="python3 -m symbiont.update",
        description="check · plan · version — read-only, never applies an update")
    sub = ap.add_subparsers(dest="cmd")
    for name, helptext in (("check", "our stored release vs the upstream's, and our heartbeat"),
                           ("plan", "what an update WOULD do: rows · cost · licence"),
                           ("version", "the station, its store schema, the pins, the channel")):
        p = sub.add_parser(name, help=helptext)
        if name != "version":
            p.add_argument("source", help="the source id, as sources.yml defines it")
        p.add_argument("--sources-config", default=contract.DEFAULT_SOURCES)
        p.add_argument("--store", default=DEFAULT_STORE)
        if name != "version":
            p.add_argument("--literature-config", default=LITERATURE_CONFIG)
            p.add_argument("--literature-store", default=LITERATURE_STORE)
        p.add_argument("--json", action="store_true", help="accepted; the record is always JSON")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        if a.cmd == "check":
            rec = check_record(a.source, a.sources_config, a.store, a.literature_config,
                               a.literature_store)
        elif a.cmd == "plan":
            rec = plan_record(a.source, a.sources_config, a.store, a.literature_config,
                              a.literature_store)
        else:
            rec = version_record(a.store, a.sources_config, LITERATURE_CONFIG)
    except Refused as e:
        print(f"REFUSED[{e.code}] {e.detail}")
        return 2
    except contract.ConfigError as e:
        print(f"REFUSED[config] {e}")
        return 2
    print(json.dumps(rec, indent=1, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
