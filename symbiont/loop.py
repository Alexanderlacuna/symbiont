"""THE LOOP — one fixed route, any source.

    question -> source adapter -> fetch(since) -> verify(ours vs the source's own count)
             -> snapshot(content-addressed raw + sha256 + release id)
             -> xref(stable ids, conflict log) -> publish(one view) -> cost

Run:  python3 -m symbiont.loop --question "mouse gene identity"        (first run)
      python3 -m symbiont.loop                                        (second run: zero fetches)
      python3 -m symbiont.loop --rebuild-from-raw                     (T4: rebuild records/, xref/ and
                                                                        view/ from raw/ alone — no fetch,
                                                                        every hash compared, first mismatch
                                                                        refuses naming the artefact)

Every run also writes THE FOUR DIGESTS (release-watch + staleness per source, gaps, suggest —
schemas and refusal rules in symbiont/docs/DIGESTS.md; the writer is symbiont/digests.py): a
run that cannot write its digest refuses, it never publishes quietly.

Nothing in this file names a source, and nothing in this file knows an endpoint. WHICH sources run,
WHERE they are fetched from, WHAT they may spend, WHICH licence they are under and WHEN they are
too old all come from sources.yml (req-P0-SOURCES-YML-AND-THE-FOUR-REFUSALS-T5). The loop reads it,
binds each entry to its adapter, and refuses to run over a config/code disagreement:

    SKIP      a source enabled:false or quarantined is skipped BY NAME, ZERO requests, the reason
              in the run record AND in the digest (store/digest/<id>.md, T9)
    POINT-AT (enforced)      a resource whose sources.yml storage.class is point-at is REFUSED a
              copy in raw/: the loop records its accession plus a fetch recipe (url, params,
              sha256 of the recipe) under pointat/<source>/<release>/ instead, and the door
              re-fetches it on demand (python3 -m symbiont.resolve) — the STORE/POINT-AT tiers
              are code, not policy text (req-P1-POINT-AT-ENFORCED-IN-CODE-the-tiers). A class
              missing from sources.yml never reaches this file: load refuses it BY NAME.
    REFUSED[budget]           the per-source budget is spent -> the run stops, no claim published (T5)
    REFUSED[licence-unknown]  the terms could not be read -> discovery dies, no data lands (T6)
    SOURCE-STALE finding      the stored release is past policy -> named finding + "stale since X" (T7)
    REFUSED[source-gone]      the provider answers 404/410 -> quarantined in sources.yml (T9)
    QUARANTINE (automatic)    a source that fails its count check TWICE, or exceeds its budget
              twice in a row, is flipped to quarantined IN sources.yml with the evidence (the two
              failing runs) attached — the loop never re-fetches a source it has quarantined
    RE-ADMISSION              a quarantined source comes back only THROUGH THE CARD:
              `python3 -m symbiont.loop --readmit <id> --card <file>` — the card names the source
              and WHAT CHANGED; without it the re-admission is REFUSED BY NAME (T9)

If you find yourself adding `if source == ...` here, the adapter contract has been broken.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.parse

from . import contract, digests, view as view_mod, xref as xref_mod
from .http import Fetcher, FetchError, GONE_STATUS

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORE = os.path.join(ROOT, "store")


# ---------------------------------------------------------------- helpers
def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def jdump(obj, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def say(line: str) -> None:
    print(line, flush=True)


def digest_line(store: str, sid: str, line: str) -> str:
    """T9 — append one line to the source's digest (store/digest/<sid>.md) and return its path.

    A run record is per RUN; the digest is per SOURCE: it is where a quarantined source stays
    visible between runs (the flip, every skip, and any re-admission), so "the source has not run
    since <date>" is a fact on disk, not an absence. The quarantined skip line is exactly
    `skipped: quarantined since <date>: <reason>` — the same words in the run record, on stdout
    and here, so three readers cannot disagree about why a source did not run."""
    ddir = os.path.join(store, "digest")
    os.makedirs(ddir, exist_ok=True)
    path = os.path.join(ddir, f"{sid}.md")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return path


def _prior_run_failures(store: str, sid: str, kind: str, before: str) -> list[str]:
    """T9 — the consecutive tail of PRIOR runs in which <sid> failed <kind> ('count-check'|'budget').

    Evidence is read from the run records on disk, because the source's own state file is written
    only after verify() and can never carry a failure. A run where the source was SKIPPED does not
    touch the chain; a run where the source was RE-ADMITTED starts a fresh one (the card named
    what changed); anything else that ran and did not fail that check ends it."""
    rdir = os.path.join(store, "runs")
    if not os.path.isdir(rdir):
        return []
    recs = []
    for name in os.listdir(rdir):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(rdir, name), encoding="utf-8") as fh:
                r = json.load(fh)
        except Exception:
            continue
        if (r.get("started_at") or "") >= before:
            continue
        if r.get("kind") in ("agent-task", "rebuild-from-raw"):
            continue    # an agent-task ledger carries no source outcome, and a T4 rebuild is a
                        # no-fetch re-derivation, not a harvest: neither may break a chain
        recs.append(r)
    recs.sort(key=lambda r: r.get("started_at") or "")
    ids: list[str] = []
    for r in reversed(recs):
        s = (r.get("sources") or {}).get(sid) or {}
        if s.get("readmitted"):
            break                            # a re-admission through the card starts a fresh chain
        if s.get("skipped"):
            continue                         # the source did not run: the chain is untouched
        ref = s.get("refused") or {}
        v = s.get("verify") or {}
        if kind == "count-check":
            if v and v.get("ok") is False and not ref:
                ids.append(r.get("run_id", ""))
                continue
            break
        if kind == "budget":
            if ref.get("code") == "budget":
                ids.append(r.get("run_id", ""))
                continue
            break
    ids.reverse()
    return ids


def _parse_iso(s: str):
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


def staleness(cfg: dict, since: dict, upstream: str, now) -> dict:
    """T7 — compare OUR stored release with what the upstream metadata endpoint states now.

    What we can measure: the date on which our stored release was first observed (`release_at`,
    preserved across runs while the release does not move). We cannot know the hour the provider
    swapped the file, so `stale_since` is a PROVEN lower bound: "we have been serving this release
    since <date>". A source whose stored release is older than sources.yml's policy_days raises the
    named finding SOURCE-STALE instead of staying silent — a provider that stops publishing must be
    visible, not invisible.
    """
    policy = int((cfg.get("staleness") or {}).get("policy_days") or 0)
    stored = (since or {}).get("release") or ""
    at_raw = (since or {}).get("release_at") or (since or {}).get("updated_at") or ""
    release_at = _parse_iso(at_raw)
    age = round((now - release_at).total_seconds() / 86400, 1) if release_at else None
    moved = bool(stored and stored != upstream)
    stale = bool(age is not None and policy > 0 and age > policy)
    finding = ""
    if stale:
        finding = (f"SOURCE-STALE {cfg['id']}: stored release {stored} first observed "
                   f"{release_at.date().isoformat()} is {age}d old (sources.yml staleness.policy_days "
                   f"{policy}); the upstream metadata endpoint now states {upstream}"
                   + (" (upstream HAS moved: this run refreshes it)" if moved else
                      " (upstream has not moved: the provider is quiet)"))
    return dict(stored_release=stored, upstream_release=upstream, upstream_moved=moved,
                release_at=at_raw or None, age_days=age, policy_days=policy, stale=stale,
                stale_since=(release_at.date().isoformat() if stale and release_at else None),
                finding=finding)


# ---------------------------------------------------------------- one source
def _storage_class(cfg: dict, url: str) -> dict:
    """The STORE/POINT-AT tier, resolved per FETCHED RESOURCE (req-P1-POINT-AT-ENFORCED-IN-CODE).

    sources.yml keys the class by ENDPOINT NAME; the adapter hands the loop resources keyed by
    reskey, each carrying the URL it was fetched from. The bridge is the URL: an endpoint's
    resolved URL is either the resource's URL exactly (MGI's release files) or its prefix with
    the query string appended (the mart: `?query=…` hangs off the endpoint itself). Two
    endpoints may legitimately share one URL (MGI's metadata IS the mrk_ensembl release file) —
    then they must declare the SAME class, or the class cannot be named and the run refuses.
    No match at all means the config and the code disagree about which endpoint owns the
    payload — refused BY NAME rather than guessed."""
    resolved = {name: contract.resolve_url(cfg["base_url"], raw)
                for name, raw in (cfg.get("endpoints") or {}).items()}
    hits = sorted(n for n, u in resolved.items() if url == u or url.startswith(u + "?"))
    if not hits:
        raise RuntimeError(
            f"{cfg['id']}: the fetched resource at {url} matches no endpoint in sources.yml — "
            f"the storage class cannot be named, so the payload can be neither stored nor "
            f"pointed at; refusing")
    storage = cfg.get("storage") or {}
    classes = {name: (storage.get(name) or {}).get("class") for name in hits}
    if None in classes.values() or len(set(classes.values())) != 1:
        raise RuntimeError(
            f"{cfg['id']}: the resource at {url} is claimed by endpoint(s) {hits} whose storage "
            f"classes disagree ({classes}) — the class cannot be named, so the payload can be "
            f"neither stored nor pointed at; refusing")
    sc = storage[hits[0]]
    return dict(endpoint=hits[0], **{"class": sc["class"]}, reason=sc.get("reason", ""))


def _fetch_recipe(url: str) -> dict:
    """The recipe a point-at accession is re-fetchable from: the URL, and its own query
    params as a mapping (empty when the URL has none). ONE shape, hashed by
    contract.recipe_hash, recorded by the loop and replayed by symbiont.resolve."""
    q = urllib.parse.urlparse(url).query
    return dict(url=url, params=dict(urllib.parse.parse_qsl(q)))


def harvest_source(sid: str, cfg: dict, ad, fetcher: Fetcher, store: str, since_mode: str,
                   replay: dict, run_id: str, src: dict):
    """discover -> staleness -> fetch(since) -> snapshot -> normalise -> verify, for ONE source.

    Every refusal it can raise is a contract.Refusal (budget | licence-unknown | source-gone) and is
    caught by the caller, which is where the run record and the quarantine are written. The source's
    own state file is written ONLY after verify(), so a refused source never advances `since`.
    """
    now = dt.datetime.now(dt.timezone.utc)

    # 1 discover ---------------------------------------------------
    state_path = os.path.join(store, "state", f"{sid}.json")
    since = {}
    if since_mode != "none" and os.path.exists(state_path):
        since = json.load(open(state_path, encoding="utf-8"))
    d = ad.discover(fetcher, since)
    src["discover"] = dict(release=d.release, published_count=d.published_count,
                           published_count_url=d.published_count_url,
                           resources=[{k: r[k] for k in ("reskey", "url", "etag", "last_modified",
                                                         "content_length")} for r in d.resources],
                           licence=d.licence, notes=d.notes)
    say(f"DISCOVER {sid}  release={d.release}  source_count={d.published_count}  "
        f"resources={len(d.resources)}  licence={d.licence.get('name', '?')}")

    # 1b staleness — OUR stored release vs the upstream metadata endpoint (T7)
    st = staleness(cfg, since, d.release, now)
    src["stale"] = st
    if st["stale"]:
        say(f"STALE    {sid}  stale since {st['stale_since']} (stored {st['stored_release']} is "
            f"{st['age_days']}d old, policy {st['policy_days']}d, upstream {st['upstream_release']})")
        say(f"FINDING  {st['finding']}")

    # 2 fetch(since) -----------------------------------------------
    plan = ad.fetch(fetcher, since)
    src["fetch"] = dict(new=plan.new, reason=plan.reason,
                        resources={k: {"bytes": len(v["bytes"]), "sha256": sha256(v["bytes"]),
                                       "filename": v["filename"]}
                                   for k, v in plan.resources.items()})
    say(f"FETCH    {sid}  new={int(plan.new)}  {plan.reason}")
    if plan.new:                       # the claim was refreshed: it is no longer stale
        st["stale_before_refresh"] = st["stale"]
        st["stale"], st["stale_since"] = False, None

    # 3 snapshot — content-addressed, release-stamped --------------
    snaps = {}
    if plan.new:
        for reskey, payload in plan.resources.items():
            rmeta = next(r for r in d.resources if r["reskey"] == reskey)
            sc = _storage_class(cfg, rmeta["url"])
            if sc["class"] == "point-at":
                # THE POINT-AT TIER, ENFORCED (req-P1-POINT-AT-ENFORCED-IN-CODE-the-tiers): the
                # payload is REFUSED a raw copy — nothing of it lands in raw/ — and what lands
                # instead is the accession plus the fetch recipe (url, params, hash of the recipe),
                # so the door can re-fetch it on demand (python3 -m symbiont.resolve).
                recipe = _fetch_recipe(rmeta["url"])
                rsha = contract.recipe_hash(recipe)
                accession = f"{sid}/{d.release}/{reskey}"
                pa = dict(accession=accession, source=sid, release=d.release, reskey=reskey,
                          storage_class="point-at", endpoint=sc["endpoint"], reason=sc["reason"],
                          recipe=recipe, recipe_sha256=rsha,
                          payload_sha256=sha256(payload["bytes"]),
                          payload_bytes=len(payload["bytes"]), filename=payload["filename"],
                          run_id=run_id, recorded_at=dt.datetime.now(dt.timezone.utc).isoformat())
                pa_path = jdump(pa, os.path.join(store, "pointat", sid, d.release, f"{reskey}.json"))
                snaps[reskey] = dict(pointat=True, accession=accession, recipe=recipe,
                                     recipe_sha256=rsha, payload_sha256=pa["payload_sha256"],
                                     bytes=pa["payload_bytes"], release=d.release,
                                     url=rmeta["url"], endpoint=sc["endpoint"], reason=sc["reason"])
                src.setdefault("pointat", {})[reskey] = dict(
                    accession=accession, reason=sc["reason"], recipe_file=os.path.relpath(pa_path, store),
                    recipe_sha256=rsha, payload_bytes=pa["payload_bytes"])
                say(f"POINT-AT {sid}:{reskey}  class point-at ({sc['reason']}) — REFUSED a raw "
                    f"copy; accession {accession}  recipe {os.path.relpath(pa_path, store)}  "
                    f"recipe_sha256={rsha}  bytes={pa['payload_bytes']} (not stored)")
                continue
            ext = rmeta.get("ext", "dat")
            digest = sha256(payload["bytes"])
            rel_dir = os.path.join(store, "raw", sid, d.release)
            path = os.path.join(rel_dir, f"{digest}.{ext}")
            os.makedirs(rel_dir, exist_ok=True)
            if os.path.exists(path):
                old = open(path, "rb").read()
                if sha256(old) != digest:
                    raise RuntimeError(f"raw zone poisoning: {path} exists with different bytes")
            else:
                with open(path, "wb") as fh:
                    fh.write(payload["bytes"])
            meta = dict(source=sid, release=d.release, reskey=reskey, url=rmeta["url"],
                        etag=rmeta.get("etag", ""), last_modified=rmeta.get("last_modified", ""),
                        sha256=digest, bytes=len(payload["bytes"]), filename=payload["filename"],
                        run_id=run_id, fetched_at=dt.datetime.now(dt.timezone.utc).isoformat())
            jdump(meta, os.path.join(rel_dir, f"{digest}.meta.json"))
            snaps[reskey] = dict(path=path, sha256=digest, release=d.release, url=rmeta["url"],
                                 bytes=len(payload["bytes"]))
            say(f"SNAPSHOT {sid}  {os.path.relpath(path, store)}  sha256={digest}  bytes={len(payload['bytes'])}")
    else:
        snaps = dict(since.get("snapshots", {}))
        if not snaps:
            raise RuntimeError(f"{sid}: nothing fetched and no prior snapshot — refusing to publish")
        for reskey, s in snaps.items():
            if s.get("pointat"):
                say(f"POINT-AT {sid}:{reskey}  accession {s.get('accession', '?')}  (no raw copy "
                    f"exists, by class point-at — it is re-fetched on demand below)")
            else:
                say(f"SNAPSHOT {sid}  reused {os.path.relpath(s['path'], store)}  sha256={s['sha256']}")

    src["snapshots"] = {
        k: (dict(pointat=True, accession=v["accession"], recipe=v["recipe"],
                 recipe_sha256=v["recipe_sha256"], payload_sha256=v["payload_sha256"],
                 release=v["release"], url=v["url"], bytes=v["bytes"], reason=v["reason"])
            if v.get("pointat") else
            dict(path=v["path"], sha256=v["sha256"], release=v["release"],
                 url=v["url"], bytes=v["bytes"]))
        for k, v in snaps.items()}

    # 4 the raw zone is read back from DISK and re-hashed before use; a POINT-AT resource has
    #   no disk copy BY CLASS: this run's own fetch supplies the bytes, and on a reuse run they
    #   are RE-FETCHED ON DEMAND from the recorded recipe — that is what the recipe is for.
    resources = {}
    for reskey, s in snaps.items():
        if replay.get(f"{sid}:{reskey}"):
            b = open(replay[f"{sid}:{reskey}"], "rb").read()
            src.setdefault("replayed", {})[reskey] = replay[f"{sid}:{reskey}"]
            say(f"REPLAY   {sid}:{reskey} <- {replay[f'{sid}:{reskey}']} (sha256={sha256(b)})")
        elif s.get("pointat"):
            if reskey in plan.resources:
                b = plan.resources[reskey]["bytes"]
                say(f"POINT-AT {sid}:{reskey}  bytes from THIS run's fetch (sha256={sha256(b)}) — "
                    f"no disk copy exists, by class point-at")
            else:
                b = fetcher.get(s["recipe"]["url"], f"{sid} {reskey} point-at on-demand re-fetch")
                if s.get("payload_sha256") and sha256(b) != s["payload_sha256"]:
                    raise RuntimeError(
                        f"{sid}:{reskey} re-fetched on demand reads sha256 {sha256(b)} but the "
                        f"recorded payload was {s['payload_sha256']} — the provider moved under a "
                        f"release that did not; refusing to normalise bytes we cannot name")
                src.setdefault("pointat_refetched", {})[reskey] = dict(
                    url=s["recipe"]["url"], bytes=len(b), sha256=sha256(b))
                say(f"POINT-AT {sid}:{reskey}  RE-FETCHED ON DEMAND ({len(b)} bytes, "
                    f"sha256={sha256(b)}) — no disk copy exists, by class point-at")
        else:
            b = open(s["path"], "rb").read()
            if sha256(b) != s["sha256"]:
                raise RuntimeError(f"{sid}:{reskey} on disk does not match its content address")
        resources[reskey] = b

    # 5 normalise + accounting -------------------------------------
    n = ad.normalise(resources)
    errs = contract.check_accounting(n)
    if errs:
        raise RuntimeError(f"{sid}: rows unaccounted for: {errs}")
    by_reason: dict[str, int] = {}
    for r in n.rejects:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
    src["normalise"] = dict(rows_in=n.rows_in, records=len(n.records), rejects=len(n.rejects),
                            rejects_by_reason=by_reason, accounting=(n.extra or {}).get("accounting", []))
    say(f"NORMALISE {sid} rows_in={n.rows_in} records={len(n.records)} rejects={len(n.rejects)}"
        + (f" ({', '.join(f'{k}={v}' for k, v in sorted(by_reason.items()))})" if by_reason else ""))
    rej_path = os.path.join(store, "rejects", f"{run_id}.{sid}.tsv")
    with open(rej_path, "w", encoding="utf-8") as fh:
        fh.write("reskey\treason\tdetail\trow\n")
        for r in n.rejects:
            fh.write(f"{r.get('reskey', '')}\t{r['reason']}\t{r['detail']}\t{r['row']}\n")
    src["rejects_file"] = rej_path

    # 6 verify: our count against the source's own -------------------
    v = ad.verify(d, n)
    src["verify"] = dict(ok=v.ok, ours=v.ours, source_count=v.source_count, url=v.url, detail=v.detail)
    say(f"VERIFY   {sid}  ours={v.ours} source={v.source_count} "
        f"{'OK' if v.ok else 'DISAGREE'}  url={v.url[:88]}{'…' if len(v.url) > 88 else ''}")

    if not replay:
        # `release_at` is the date this release was FIRST observed — kept while the release does not
        # move, which is exactly what makes "stale since" a measurement and not a guess. A state file
        # written before this row existed carries only `updated_at`; that is carried forward rather
        # than reset, or one upgrade would erase the age of a snapshot that really is old.
        prior_at = since.get("release_at") or since.get("updated_at")
        same = since.get("release") == d.release and prior_at
        state = dict(release=d.release, published_count=d.published_count, run_id=run_id,
                     release_at=(prior_at if same else now.isoformat()),
                     updated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                     snapshots=snaps, licence=d.licence,
                     etags={r["reskey"]: r.get("etag", "") for r in d.resources})
        jdump(state, state_path)
    return n


# ------------------------------------------------- T4: the full rebuild from raw
_REBUILD_PROJECTIONS = ("records", "xref", "view")   # the derived tiers; raw/ is never one of them


class RebuildRefusal(contract.Refusal):
    """T4 — the rebuild refuses, NAMING the artefact; the moved-aside copies are restored."""
    code = "rebuild"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_dir(root: str) -> dict:
    """{relative path: sha256} for every file under root — the recorded baseline of a projection,
    hashed from the moved-aside copy before a single rebuilt byte exists."""
    out: dict[str, str] = {}
    for dirpath, _subdirs, names in os.walk(root):
        for n in sorted(names):
            p = os.path.join(dirpath, n)
            out[os.path.relpath(p, root)] = _sha256_file(p)
    return out


def _write_xref_files(store: str, x) -> tuple[list[str], int]:
    """Write the three xref tables; return (their paths, retired_rows). THE ONE definition of these
    bytes: the loop's own publish and the T4 rebuild both come through here, so the two doors
    cannot drift apart."""
    os.makedirs(os.path.join(store, "xref"), exist_ok=True)
    with open(os.path.join(store, "xref", "xref.tsv"), "w", encoding="utf-8") as fh:
        fh.write("key\tauthority_id\tprovider_id\tstatus\treason\n")
        for r in sorted(x.rows, key=lambda r: r["key"]):
            fh.write(f"{r['key']}\t{r['authority_id']}\t{r['provider_id']}\t"
                     f"{'ok' if not r['conflict'] else 'ok_with_conflict'}\t{r['conflict']}\n")
        # A failure row is NOT automatically an unresolved key. R2 (xref_disagreement) logs a
        # conflict for a pair that IS published, so writing it as "unresolved" made the table
        # (332 rows) disagree with the run record's unresolved count (331) by exactly that key.
        # The status now says which of the two the row is, so counting the table and reading the
        # record give the same number.
        published = {r["key"] for r in x.rows}
        for f in x.failures:
            status = "unresolved" if f["key"] not in published else "published_with_conflict"
            fh.write(f"{f['key']}\t{','.join(f['authority_ids'])}\t{','.join(f['provider_ids'])}\t"
                     f"{status}\t{f['reason']}\n")
    with open(os.path.join(store, "xref", "conflicts.tsv"), "w", encoding="utf-8") as fh:
        fh.write("key\treason\tauthority_ids\tprovider_ids\tdetail\n")
        for f in sorted(x.failures, key=lambda f: (f["reason"], f["key"])):
            fh.write(f"{f['key']}\t{f['reason']}\t{','.join(f['authority_ids'])}\t"
                     f"{','.join(f['provider_ids'])}\t{f['detail']}\n")
    seen = set()
    with open(os.path.join(store, "xref", "retired.tsv"), "w", encoding="utf-8") as fh:
        fh.write("retired_symbol\tcurrent_symbol\tcurrent_provider_id\n")
        for r in sorted(x.retired, key=lambda r: r["key"]):
            if r["key"] in seen:
                continue
            seen.add(r["key"])
            fh.write(f"{r['key']}\t{r['current_symbol']}\t{r['current_id']}\n")
    return [os.path.join(store, "xref", f) for f in ("xref.tsv", "conflicts.tsv", "retired.tsv")], len(seen)


def _rebuild_from_raw(a) -> int:
    """T4 FULL REBUILD — drop the projection and rebuild it from raw, byte-identically.

    `--rebuild-from-raw` NEVER fetches: no discover, no fetch, no Fetcher is ever constructed, so
    the run works with the network down because nothing in it can express a request. The ONLY
    data input is raw/; state/<id>.json supplies the CURRENT-release pointer and each snapshot's
    recorded content address (cross-checked against the raw file's own name and bytes), and
    sources.yml supplies the adapter bindings exactly as a normal run would.

    The order of operations IS the contract:
      1. preflight  read the recorded output hashes (the view provenance sidecar and the last
                    PUBLISHED run record) and REFUSE, naming the artefact, if the store's own
                    records already disagree — before anything moves;
      2. aside      records/, xref/ and view/ are MOVED to aside/<run_id>/ — never deleted;
      3. rebuild    every recorded snapshot is resolved INSIDE this store's raw/ by content
                    address, read and re-hashed; records/, xref/ and view/ are rebuilt from
                    those bytes alone, and each output's sha256 is compared to the recorded value
                    the moment it exists — the FIRST mismatch refuses, naming the artefact;
      4. on failure the half-built projections are kept in aside/<run_id>/failed-*/ (evidence,
                    never deleted) and the moved-aside copies are RESTORED — the store ends the
                    refused run exactly as it began it;
      5. on success the moved-aside copies stay in aside/<run_id>/ (never deleted), the rebuild
                    run record lands in runs/ with every compared hash, and the wall time and
                    bytes read are printed.
    """
    t0 = time.time()
    store = os.path.abspath(a.store)
    run_id = a.run_id or f"rebuild-{utc_stamp()}"
    only = [s for s in (a.sources or "").split(",") if s] or None
    sources_path = a.sources_config or contract.DEFAULT_SOURCES
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    say(f"REBUILD  run {run_id}  records/, xref/ and view/ from raw/ alone — no fetch, ever")

    # config refusals propagate to main() exactly as a normal run's would (exit 2, nothing moved)
    sources = contract.load_sources(sources_path)
    adapters = contract.load_adapters()
    contract.check_registry(sources, adapters)

    aside = os.path.join(store, "aside", run_id)
    rdir = os.path.join(store, "runs")
    if os.path.exists(aside):
        say(f"REFUSED[rebuild] aside/{run_id}/ already exists — a rebuild never overwrites its evidence")
        return 2
    os.makedirs(rdir, exist_ok=True)

    record = dict(run_id=run_id, kind="rebuild-from-raw",
                  question="T4 full rebuild: records/, xref/ and view/ from raw/ alone",
                  started_at=started, since="", replay=[], sources_path=os.path.abspath(sources_path),
                  sources={}, skipped={}, findings=[], refusals=[], xref={}, publish={},
                  artefacts=[], aside=aside, aside_moved=[], restored=False, status="")
    moved: list[str] = []
    aside_done = False
    bytes_read = 0

    def refuse(msg: str) -> int:
        """The one failure path: keep the evidence, put the originals back, say so, exit 2."""
        nonlocal bytes_read
        if aside_done:
            for d in _REBUILD_PROJECTIONS:      # the half-built projections are evidence, not garbage
                p = os.path.join(store, d)
                if os.path.isdir(p):
                    os.rename(p, os.path.join(aside, "failed-" + d))
        for d in reversed(moved):               # the moved-aside copies go back where they were
            os.rename(os.path.join(aside, d), os.path.join(store, d))
        record.update(restored=True, status="REFUSED", finished_at=dt.datetime.now(dt.timezone.utc).isoformat())
        record["refusals"].append(dict(code="rebuild", source="", detail=msg))
        record["cost"] = dict(requests=0, bytes_in=0, bytes_read=bytes_read,
                              wall_seconds=round(time.time() - t0, 1))
        try:
            jdump(record, os.path.join(rdir, f"{run_id}.json"))
        except Exception as e:
            say(f"REFUSED[rebuild] the refusal record itself could not be written: {type(e).__name__}: {e}")
        say(f"RESTORED  the moved-aside copies are back in place "
            f"({', '.join(moved) if moved else 'nothing had been moved yet'})")
        say(f"REFUSED[rebuild] {msg}")
        return 2

    try:
        # 1 preflight — the store must agree with itself before anything moves ---------------
        vdir = os.path.join(store, "view")
        vstem = f"{view_mod.VIEW}.v{view_mod.VERSION}"
        recorded: dict[str, str] = {}
        if os.path.isdir(vdir):
            tsv = os.path.join(vdir, vstem + ".tsv")
            sch = os.path.join(vdir, vstem + ".schema.json")
            prov_p = os.path.join(vdir, vstem + ".provenance.json")
            if not (os.path.isfile(tsv) and os.path.isfile(sch) and os.path.isfile(prov_p)):
                return refuse(f"view/ is an incomplete projection — it needs {vstem}.tsv, "
                              f"{vstem}.schema.json and {vstem}.provenance.json")
            with open(prov_p, encoding="utf-8") as fh:
                prov = json.load(fh)
            recorded["view"], recorded["schema"] = prov.get("view_sha256") or "", prov.get("schema_sha256") or ""
            on_disk = _sha256_file(tsv)
            if on_disk != recorded["view"]:
                return refuse(f"view/{vstem}.tsv reads back sha256 {on_disk} but its own provenance "
                              f"records {recorded['view']} — the store disagrees with itself before "
                              f"any rebuild; nothing has been moved")
            best = None                       # the last PUBLISHED run record must agree with it too
            for name in os.listdir(rdir):
                if not name.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(rdir, name), encoding="utf-8") as fh:
                        r = json.load(fh)
                except Exception:
                    continue
                if isinstance(r, dict) and r.get("status") == "PUBLISHED":
                    fin = r.get("finished_at") or ""
                    if best is None or fin >= best[0]:
                        best = (fin, r)
            if best is not None:
                p = (best[1].get("publish") or {}).get("view_sha256") or ""
                if p and p != recorded["view"]:
                    return refuse(f"runs/{best[1].get('run_id')}.json records view_sha256 {p} but "
                                  f"view/{vstem}.provenance.json records {recorded['view']} — two "
                                  f"records of the same artefact disagree; nothing has been moved")
            say(f"RECORDED view sha256={recorded['view']}  schema_sha256={recorded['schema']}  "
                f"(view/{vstem}.provenance.json, cross-checked against the last PUBLISHED run record)")
        else:
            say("RECORDED no view/ on disk — the rebuild builds one and nothing can be compared")

        # 2 aside — moved, never deleted -----------------------------------------------
        os.makedirs(aside, exist_ok=True)
        for d in _REBUILD_PROJECTIONS:
            src = os.path.join(store, d)
            if os.path.isdir(src):
                os.rename(src, os.path.join(aside, d))
                moved.append(d)
                record["aside_moved"].append(d)
                say(f"ASIDE    {d}/ -> aside/{run_id}/{d}/  (moved aside, never deleted)")
            else:
                say(f"ASIDE    {d}/ absent — no prior projection to move aside")
        aside_done = True
        baseline = {d: _hash_dir(os.path.join(aside, d)) for d in moved}   # the recorded values

        # 3 rebuild — raw/ is the only data input ----------------------------------------
        parts, roles = {}, {}
        for sid in sorted(sources):
            cfg = sources[sid]
            src = record["sources"][sid] = dict(id=sid, adapter=cfg["adapter"], enabled=cfg["enabled"],
                                                quarantined=cfg["quarantined"])
            if only and sid not in only:
                src["skipped"] = dict(reason="not-selected", detail=f"--sources did not select {sid}")
                continue
            if not cfg["enabled"]:
                src["skipped"] = dict(reason="disabled", detail="sources.yml: enabled: false")
                record["skipped"][sid] = src["skipped"]
                say(f"SKIP     {sid}  reason=disabled  (sources.yml enabled: false)")
                continue
            if cfg["quarantined"]:
                q = cfg["quarantined"]
                line = f"skipped: quarantined since {q['at']}: {q['reason']}"
                src["skipped"] = dict(reason="quarantined", quarantined_since=q["at"],
                                      status=q["status"], detail=q["reason"], line=line)
                record["skipped"][sid] = src["skipped"]
                say(f"SKIP     {sid}  {line}")
                continue

            ad = adapters[cfg["adapter"]]
            ad.configure(cfg)
            roles[sid] = ad.role
            src["role"] = ad.role

            state_path = os.path.join(store, "state", f"{sid}.json")
            if not os.path.isfile(state_path):
                return refuse(f"state/{sid}.json is absent — the current release cannot be named "
                              f"without it, and the rebuild never fetches")
            with open(state_path, encoding="utf-8") as fh:
                state = json.load(fh)
            snaps = state.get("snapshots") or {}
            if not snaps:
                return refuse(f"state/{sid}.json records no snapshots — nothing in raw/ is named current")
            src["release"] = state.get("release", "")
            src["published_count"] = state.get("published_count")
            src["licence"] = state.get("licence")
            src["snapshots"] = {k: dict(v) for k, v in sorted(snaps.items())}   # as recorded
            src["read_from"] = {}

            resources = {}
            for reskey in sorted(snaps):
                s = snaps[reskey]
                if s.get("pointat"):
                    return refuse(f"state/{sid}.json names {sid}:{reskey} point-at (accession "
                                  f"{s.get('accession', '?')}, class declared in sources.yml): "
                                  f"a point-at payload is not in raw/ BY CLASS and a rebuild "
                                  f"never fetches — the projection cannot be rebuilt from bytes "
                                  f"this store refused to store")
                rel = s.get("release") or state.get("release") or ""
                dig = s.get("sha256") or ""
                raw_dir = os.path.join(store, "raw", sid, rel)
                if not os.path.isdir(raw_dir):
                    return refuse(f"raw/{sid}/{rel}/ is absent — the recorded release of "
                                  f"{sid}:{reskey} (sha256 {dig}) is not in this store's raw zone")
                hits = [n for n in sorted(os.listdir(raw_dir))
                        if n.startswith(dig) and not n.endswith(".meta.json")]
                if len(hits) != 1:
                    return refuse(f"raw/{sid}/{rel}/{dig}\u2026: {len(hits)} file(s) carry the recorded "
                                  f"content address of {sid}:{reskey} — the raw zone cannot name the "
                                  f"artefact uniquely")
                path = os.path.join(raw_dir, hits[0])
                with open(path, "rb") as fh:
                    b = fh.read()
                bytes_read += len(b)
                if sha256(b) != dig:
                    return refuse(f"raw/{sid}/{rel}/{hits[0]} reads back sha256 {sha256(b)} but its "
                                  f"recorded content address is {dig} — the raw zone is corrupted")
                say(f"RAW      {sid}:{reskey}  raw/{sid}/{rel}/{hits[0]}  bytes={len(b)}  "
                    f"sha256={dig}  (verified against the recorded content address)")
                resources[reskey] = b
                src["read_from"][reskey] = os.path.relpath(path, store)

            n = ad.normalise(resources)
            errs = contract.check_accounting(n)
            if errs:
                return refuse(f"{sid}: the rebuilt normalise does not close its accounting: {errs}")
            by_reason: dict[str, int] = {}
            for r in n.rejects:
                by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
            rej_path = os.path.join(store, "rejects", f"{run_id}.{sid}.tsv")
            os.makedirs(os.path.dirname(rej_path), exist_ok=True)
            with open(rej_path, "w", encoding="utf-8") as fh:
                fh.write("reskey\treason\tdetail\trow\n")
                for r in n.rejects:
                    fh.write(f"{r.get('reskey', '')}\t{r['reason']}\t{r['detail']}\t{r['row']}\n")
            src["normalise"] = dict(rows_in=n.rows_in, records=len(n.records), rejects=len(n.rejects),
                                    rejects_by_reason=by_reason, accounting=(n.extra or {}).get("accounting", []))
            src["rejects_file"] = rej_path
            src["rebuilt"] = True
            say(f"NORMALISE {sid} rows_in={n.rows_in} records={len(n.records)} rejects={len(n.rejects)}"
                + (f" ({', '.join(f'{k}={v}' for k, v in sorted(by_reason.items()))})" if by_reason else ""))

            # records/ — the STORED records tier (DESIGN \u00a75): every field the adapter normalised,
            # materialised as JSON lines so the tier is rebuildable byte-identically
            rec_path = os.path.join(store, "records", sid, f"{src['release']}.jsonl")
            os.makedirs(os.path.dirname(rec_path), exist_ok=True)
            blob = b"".join((json.dumps(r, sort_keys=True) + "\n").encode() for r in n.records)
            with open(rec_path, "wb") as fh:
                fh.write(blob)
            rsha = sha256(blob)
            rrel = os.path.relpath(rec_path, store)
            prev = (baseline.get("records") or {}).get(f"{sid}/{src['release']}.jsonl")
            if prev is not None and prev != rsha:
                return refuse(f"{rrel}: rebuilt sha256 {rsha} != recorded {prev}")
            record["artefacts"].append(dict(path=rrel, sha256=rsha, recorded_sha256=prev,
                                            match=(None if prev is None else prev == rsha)))
            say(f"RECORDS  {rrel}  rows={len(n.records)}  sha256={rsha}  "
                + (f"MATCH recorded {prev}" if prev is not None
                   else "recorded (first materialisation — no prior copy to compare)"))
            parts[sid] = n

        if not parts:
            return refuse("no source participated — nothing can be rebuilt")

        # 3b xref — the same writer the loop's own publish uses ---------------------------
        x = xref_mod.join(parts, roles)
        excluded = x.authority_rows - len(x.rows)
        say(f"XREF     authority_rows={x.authority_rows} provider_rows={x.provider_rows} "
            f"pairs={len(x.rows)} unresolved_keys={x.unresolved} "
            f"authority_rows_not_published={excluded}")
        xfiles, retired_rows = _write_xref_files(store, x)
        for p in xfiles:
            h = _sha256_file(p)
            rel = os.path.relpath(p, store)
            prev = (baseline.get("xref") or {}).get(os.path.basename(p))
            if prev is not None and prev != h:
                return refuse(f"{rel}: rebuilt sha256 {h} != recorded {prev}")
            record["artefacts"].append(dict(path=rel, sha256=h, recorded_sha256=prev,
                                            match=(None if prev is None else prev == h)))
            say(f"XREF     {rel}  sha256={h}  "
                + (f"MATCH recorded {prev}" if prev is not None
                   else "recorded (first materialisation — no prior copy to compare)"))
        record["xref"] = dict(authority_rows=x.authority_rows, provider_rows=x.provider_rows,
                              pairs=len(x.rows), unresolved_keys=x.unresolved,
                              unresolved_by_reason=x.unresolved_by_reason,
                              authority_rows_not_published=excluded, conflicts=x.conflict_counts,
                              retired_rows=retired_rows, files=xfiles)

        # 3c view — the same render the loop's own publish uses --------------------------
        vb = view_mod.render(x.rows)
        vsha, ssha = sha256(vb), view_mod.schema_sha256()
        os.makedirs(vdir, exist_ok=True)
        vpath = os.path.join(vdir, vstem + ".tsv")
        with open(vpath, "wb") as fh:
            fh.write(vb)
        with open(os.path.join(vdir, vstem + ".schema.json"), "wb") as fh:
            fh.write(view_mod.schema_bytes())
        if recorded.get("view") and vsha != recorded["view"]:
            return refuse(f"view/{vstem}.tsv: rebuilt sha256 {vsha} != recorded {recorded['view']}")
        if recorded.get("schema") and ssha != recorded["schema"]:
            return refuse(f"view/{vstem}.schema.json: rebuilt sha256 {ssha} != recorded "
                          f"{recorded['schema']}")
        record["artefacts"].append(dict(path=f"view/{vstem}.tsv", sha256=vsha,
                                        recorded_sha256=recorded.get("view") or None,
                                        match=(None if not recorded.get("view")
                                               else recorded["view"] == vsha)))
        record["artefacts"].append(dict(path=f"view/{vstem}.schema.json", sha256=ssha,
                                        recorded_sha256=recorded.get("schema") or None,
                                        match=(None if not recorded.get("schema")
                                               else recorded["schema"] == ssha)))
        say(f"PUBLISH  view/{vstem}.tsv rows={len(x.rows)} sha256={vsha}  "
            + (f"MATCH recorded {recorded['view']}" if recorded.get("view")
               else "recorded (first materialisation — no prior copy to compare)"))
        say(f"PUBLISH  schema_sha256={ssha}"
            + (f"  MATCH recorded {recorded['schema']}" if recorded.get("schema") else ""))
        jdump(dict(view=view_mod.VIEW, version=view_mod.VERSION, rows=len(x.rows), view_sha256=vsha,
                   schema_sha256=ssha, run_id=run_id,
                   generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                   rebuilt_from_raw=True, findings=[], skipped=record["skipped"],
                   sources={k: dict(release=record["sources"][k]["release"],
                                    snapshots=record["sources"][k]["snapshots"],
                                    published_count=record["sources"][k].get("published_count"),
                                    url=[v.get("url", "") for v in
                                         record["sources"][k]["snapshots"].values()],
                                    licence=record["sources"][k].get("licence"),
                                    stale=None)   # staleness needs the upstream endpoint: a
                                                  # rebuild never fetches, so it records no guess
                    for k in sorted(record["sources"]) if record["sources"][k].get("rebuilt")}),
              os.path.join(vdir, vstem + ".provenance.json"))
        record["publish"] = dict(path=vpath, rows=len(x.rows), view_sha256=vsha, schema_sha256=ssha)

        # 4 success ----------------------------------------------------------------------
        wall = round(time.time() - t0, 1)
        record.update(status="REBUILT", finished_at=dt.datetime.now(dt.timezone.utc).isoformat())
        record["cost"] = dict(requests=0, bytes_in=0, data_bytes=0, bytes_read=bytes_read,
                              wall_seconds=wall, log=[],
                              rows_in=sum(s["normalise"]["rows_in"] for s in record["sources"].values()
                                          if "normalise" in s),
                              rows_records=sum(s["normalise"]["records"] for s in record["sources"].values()
                                               if "normalise" in s),
                              rows_rejected=sum(s["normalise"]["rejects"] for s in record["sources"].values()
                                                if "normalise" in s),
                              rows_published=len(x.rows))
        rec_path = jdump(record, os.path.join(rdir, f"{run_id}.json"))
        say(f"RUN-RECORD {os.path.relpath(rec_path, store)}")
        say(f"REBUILD  wall_seconds={wall}  bytes_read={bytes_read}  requests=0  "
            f"(a rebuild cannot fetch)")
        say(f"REBUILD  the moved-aside copies stay at aside/{run_id}/ — never deleted")
        say(f"REBUILT  run {run_id}: records/, xref/ and view/ rebuilt from raw/ alone, "
            f"every compared hash MATCH")
        return 0
    except Exception as e:
        return refuse(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- the loop
def run(store: str, question: str, since_mode: str, only: list[str] | None,
        replay: dict, run_id: str | None, sources_path: str | None = None) -> dict:
    t0 = time.time()
    run_id = run_id or utc_stamp()
    for sub in ("raw", "state", "xref", "view", "runs", "rejects", "digest"):
        os.makedirs(os.path.join(store, sub), exist_ok=True)

    sources_path = sources_path or contract.DEFAULT_SOURCES
    sources = contract.load_sources(sources_path)
    adapters = contract.load_adapters()
    contract.check_registry(sources, adapters)   # refused BY NAME before a request leaves the box

    record = dict(
        run_id=run_id, question=question, started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        since=since_mode, replay=sorted(replay), sources_path=os.path.abspath(sources_path),
        sources={}, skipped={}, findings=[], refusals=[], xref={}, publish={}, status="",
    )
    say(f"HARVEST run {run_id}  question: {question}")
    say(f"SOURCES  {len(sources)} defined in {os.path.abspath(sources_path)}; "
        f"{len(adapters)} adapter(s) on disk")

    parts, roles = {}, {}
    log: list[dict] = []
    bytes_in = 0.0
    waits: list[float] = []
    refused = False

    def quarantine(sid: str, reason: str, status: str, evidence: list[str]) -> dict:
        """T9 — flip the source to QUARANTINED IN sources.yml, atomically, with the EVIDENCE (the
        failing run ids) attached; say so in the run record, on stdout and in the digest. The only
        way back is the card (--readmit --card)."""
        when = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        rec = dict(code=status, source=sid, detail=reason, evidence=evidence)
        try:
            rec["quarantined"] = contract.set_quarantine(
                sources_path, sid, reason=reason[:400], status=status, when=when)
            say(f"QUARANTINE {sid}  written to {os.path.abspath(sources_path)} at {when} — "
                f"the loop skips it BY NAME until a card re-admits it")
            rec["digest"] = digest_line(store, sid, f"quarantined {when} ({status}): {reason[:400]}")
        except Exception as qe:
            rec["quarantine_error"] = f"{type(qe).__name__}: {qe}"
            say(f"QUARANTINE {sid}  FAILED to write sources.yml: {rec['quarantine_error']}")
        record["refusals"].append(rec)
        record["sources"][sid]["refused"] = rec
        return rec

    for sid in sorted(sources):
        cfg = sources[sid]
        src = record["sources"][sid] = dict(id=sid, adapter=cfg["adapter"], enabled=cfg["enabled"],
                                            quarantined=cfg["quarantined"])

        # -- the three ways a configured source does not run today --
        if only and sid not in only:
            src["skipped"] = dict(reason="not-selected", detail=f"--sources did not select {sid}")
            src["cost"] = dict(requests=0, bytes_in=0, wall_s=0.0, rate_limit_waits=[])
            continue
        if not cfg["enabled"]:
            src["skipped"] = dict(reason="disabled", detail="sources.yml: enabled: false")
            src["cost"] = dict(requests=0, bytes_in=0, wall_s=0.0, rate_limit_waits=[])
            record["skipped"][sid] = src["skipped"]
            say(f"SKIP     {sid}  reason=disabled  (sources.yml enabled: false)")
            continue
        if cfg["quarantined"]:
            # T9: a quarantined source is SKIPPED BY NAME — not one request leaves the box — and
            # the skip is printed, recorded in the run record and written to the digest, so it can
            # never be an unexplained silence. sources.yml refuses a quarantine with no reason at
            # load time, so the reason here is always present.
            q = cfg["quarantined"]
            line = f"skipped: quarantined since {q['at']}: {q['reason']}"
            src["skipped"] = dict(reason="quarantined", quarantined_since=q["at"],
                                  status=q["status"], detail=q["reason"], line=line,
                                  digest=digest_line(store, sid, line))
            src["cost"] = dict(requests=0, bytes_in=0, wall_s=0.0, rate_limit_waits=[])
            record["skipped"][sid] = src["skipped"]
            say(f"SKIP     {sid}  {line}")
            continue

        ad = adapters[cfg["adapter"]]
        ad.configure(cfg)
        roles[sid] = ad.role
        src["role"] = ad.role
        src["budget"] = cfg["budget"]
        fetcher = Fetcher(budget=cfg["budget"], rate_limit=cfg["rate_limit"])
        try:
            parts[sid] = harvest_source(sid, cfg, ad, fetcher, store, since_mode, replay, run_id, src)
        except contract.Refusal as e:
            refused = True
            if e.code == "source-gone":
                # T9: the source moves to quarantined IN sources.yml, by name, right here.
                quarantine(sid, str(e), e.code, [run_id])
            elif e.code == "budget":
                # T9 loop side: the SECOND consecutive budget overage means the source structurally
                # costs more than sources.yml allows — it flips to quarantined, evidence attached.
                priors = _prior_run_failures(store, sid, "budget", record["started_at"])
                if priors:
                    quarantine(sid, f"budget exceeded twice (failing runs {priors[-1]}, {run_id}): {e}",
                               e.code, [priors[-1], run_id])
                else:
                    rec = dict(code=e.code, source=sid, detail=str(e))
                    record["refusals"].append(rec)
                    src["refused"] = rec
                    say(f"REFUSED[{e.code}] {sid}: {e}")
            else:
                rec = dict(code=e.code, source=sid, detail=str(e))
                record["refusals"].append(rec)
                src["refused"] = rec
                say(f"REFUSED[{e.code}] {sid}: {e}")
        except FetchError as e:
            refused = True
            code = "source-gone" if getattr(e, "status", 0) in GONE_STATUS else "source-unreachable"
            rec = dict(code=code, source=sid, detail=str(e))
            record["refusals"].append(rec)
            src["refused"] = rec
            say(f"REFUSED[{code}] {sid}: {e}")
        else:
            v = src.get("verify") or {}
            if not v.get("ok", True):
                priors = _prior_run_failures(store, sid, "count-check", record["started_at"])
                if priors:
                    # T9 loop side: the count check has now disagreed TWICE in a row — the source
                    # flips to QUARANTINED automatically, the two failing runs as the evidence.
                    quarantine(sid, f"count check failed twice (failing runs {priors[-1]}, {run_id}): "
                                    f"ours={v.get('ours')} vs source={v.get('source_count')} "
                                    f"at {v.get('url')}",
                               "count-mismatch", [priors[-1], run_id])
                    refused = True
        finally:
            src["cost"] = dict(requests=len(fetcher.log), bytes_in=fetcher.bytes_in,
                               wall_s=fetcher.wall_seconds, rate_limit_waits=fetcher.waits)
            log.extend(fetcher.log)
            bytes_in += fetcher.bytes_in
            waits.extend(fetcher.waits)

    record["findings"] = [s["stale"]["finding"] for s in record["sources"].values()
                          if (s.get("stale") or {}).get("finding")]

    def finish(status: str, note: str) -> dict:
        record["status"] = status
        record["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        record["cost"] = dict(requests=len(log), requests_by_method=_by_method(log),
                              bytes_in=bytes_in, rate_limit_wait_s=round(sum(waits), 3),
                              wall_seconds=round(time.time() - t0, 1),
                              data_bytes=sum(s.get("bytes", 0) for src in record["sources"].values()
                                             for s in src.get("fetch", {}).get("resources", {}).values()),
                              rows_in=sum(s.get("normalise", {}).get("rows_in", 0)
                                          for s in record["sources"].values()),
                              rows_records=sum(s.get("normalise", {}).get("records", 0)
                                               for s in record["sources"].values()),
                              rows_rejected=sum(s.get("normalise", {}).get("rejects", 0)
                                                for s in record["sources"].values()),
                              rows_published=(record.get("publish") or {}).get("rows", 0),
                              log=log)
        rdir = os.path.join(store, "runs")
        # THE FOUR DIGESTS (req-P1-THE-FOUR-DIGESTS, docs/DIGESTS.md): the digest section is
        # measured and pinned BEFORE the record is dumped, so the per-run digest blocks can
        # reproduce every count from immutable run records; then the digests themselves are
        # written — a run that cannot write its digest is a run that refuses, never a quiet one.
        record["digest"] = digests.collect(store, record)
        rec_path = jdump(record, os.path.join(rdir, f"{run_id}.json"))
        dg_paths = digests.write(store, record)
        say("DIGESTS  " + " ".join(os.path.relpath(p, store) for p in dg_paths))
        say(f"COST     requests={record['cost']['requests']} bytes_in={record['cost']['bytes_in']} "
            f"data_bytes={record['cost']['data_bytes']} wall={record['cost']['wall_seconds']}s "
            f"rate_limit_wait={record['cost']['rate_limit_wait_s']}s "
            f"rows_in={record['cost']['rows_in']} rows_out={record['cost']['rows_published']} "
            f"rejects={record['cost']['rows_rejected']}")
        say(f"RUN-RECORD {os.path.relpath(rec_path, store)}")
        for f in record["findings"]:
            say(f"FINDING  {f}")
        for r in record["refusals"]:
            say(f"REFUSAL  [{r['code']}] {r.get('source', '')}: {r['detail']}")
        say(note)
        return record

    if refused:
        # A refused source leaves the claim incomplete. Publishing a view with a hole in it would
        # be the exact silence this row exists to kill, so nothing is published and the reason is
        # on disk. (A source switched OFF in sources.yml is a different case: that is the operator's
        # intent, it is skipped by name, and the run goes on.)
        return finish("REFUSED", f"REFUSED  run {run_id}: {len(record['refusals'])} source(s) "
                                 f"refused — no claim published")

    # 7 xref ------------------------------------------------------------
    try:
        x = xref_mod.join(parts, roles)
    except RuntimeError as e:
        record["refusals"].append(dict(code="xref-incomplete", source="", detail=str(e)))
        return finish("REFUSED", f"REFUSED  run {run_id}: the claim could not be joined — no view "
                                 f"published ({e})")
    excluded = x.authority_rows - len(x.rows)
    say(f"XREF     authority_rows={x.authority_rows} provider_rows={x.provider_rows} "
        f"pairs={len(x.rows)} unresolved_keys={x.unresolved} authority_rows_not_published={excluded}")
    say(f"XREF     conflicts={json.dumps(x.conflict_counts, sort_keys=True)}")

    xfiles, retired_rows = _write_xref_files(store, x)
    record["xref"] = dict(
        authority_rows=x.authority_rows, provider_rows=x.provider_rows, pairs=len(x.rows),
        unresolved_keys=x.unresolved, unresolved_by_reason=x.unresolved_by_reason,
        authority_rows_not_published=excluded,
        conflicts=x.conflict_counts, retired_rows=retired_rows,
        files=xfiles,
    )

    # 8 publish ---------------------------------------------------------
    vdir = os.path.join(store, "view")
    vb = view_mod.render(x.rows)
    vpath = os.path.join(vdir, f"{view_mod.VIEW}.v{view_mod.VERSION}.tsv")
    with open(vpath, "wb") as fh:
        fh.write(vb)
    vsha, ssha = sha256(vb), view_mod.schema_sha256()
    with open(os.path.join(vdir, f"{view_mod.VIEW}.v{view_mod.VERSION}.schema.json"), "wb") as fh:
        fh.write(view_mod.schema_bytes())
    jdump(dict(view=view_mod.VIEW, version=view_mod.VERSION, rows=len(x.rows), view_sha256=vsha,
               schema_sha256=ssha, run_id=run_id,
               generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
               findings=record["findings"], skipped=record["skipped"],
               sources={k: dict(release=r_["discover"]["release"],
                                snapshots=r_.get("snapshots", {}),
                                published_count=r_["discover"]["published_count"],
                                url=[q["url"] for q in r_["discover"]["resources"]],
                                licence=r_["discover"]["licence"],
                                stale=r_.get("stale"))
                        for k, r_ in record["sources"].items() if "discover" in r_}),
          os.path.join(vdir, f"{view_mod.VIEW}.v{view_mod.VERSION}.provenance.json"))
    say(f"PUBLISH  {os.path.relpath(vpath, store)} rows={len(x.rows)} sha256={vsha}")
    say(f"PUBLISH  schema_sha256={ssha}")
    record["publish"] = dict(path=vpath, rows=len(x.rows), view_sha256=vsha, schema_sha256=ssha)

    return finish("PUBLISHED", f"PUBLISHED run {run_id}")


def _by_method(log) -> dict:
    out: dict[str, int] = {}
    for e in log:
        out[e["method"]] = out.get(e["method"], 0) + 1
    return out


def _readmit(a) -> int:
    """T9 — re-admission goes back through THE CARD, and the card must name what changed.

    A card is a text file with a `source: <id>` line and a `changed: <what changed>` line (the
    admission card source_card() will mint for new sources is exactly this shape). Without a card
    — or with a card that names no change — the re-admission is REFUSED BY NAME, because the
    quarantine was EARNED by evidence and may not be undone by an unexplained edit. On success the
    quarantine is cleared atomically in sources.yml, a receipt run record is written, and the
    digest carries the re-admission line."""
    import re
    sid, cfg_path = a.readmit, a.sources_config

    def refuse(msg: str) -> int:
        say(f"REFUSED[readmit] {sid}: {msg}")
        return 2

    try:
        sources = contract.load_sources(cfg_path)
    except contract.ConfigError as e:
        say(f"REFUSED[{e.code}] {e}")
        return 2
    if sid not in sources:
        return refuse(f"no source {sid!r} in {os.path.abspath(cfg_path)}")
    q = sources[sid].get("quarantined")
    if q is None:
        return refuse("not quarantined — nothing to re-admit")
    if not a.card:
        return refuse("re-admission requires the card (a file naming this source and what changed) "
                      "— a bare `quarantined: null` edit is not re-admission")
    if not os.path.isfile(a.card):
        return refuse(f"the card {a.card!r} does not exist")
    card_text = open(a.card, encoding="utf-8").read()
    if f"source: {sid}" not in card_text:
        return refuse(f"the card {a.card!r} does not name this source (it needs a `source: {sid}` line)")
    m = re.search(r"^changed:[ \t]*(\S.*)$", card_text, re.M)
    if not m:
        return refuse(f"the card {a.card!r} does not name what changed (it needs a `changed:` line)")
    changed = m.group(1).strip()

    when = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    header = contract._header(open(cfg_path, encoding="utf-8").read())
    sources[sid]["quarantined"] = None
    tmp = cfg_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(header + contract.dump_sources(sources))
    os.replace(tmp, cfg_path)
    back = contract.load_sources(cfg_path)
    if back[sid].get("quarantined") is not None:
        return refuse("the clear did not survive a re-read of sources.yml — nothing was changed")

    run_id = f"readmit-{utc_stamp()}"
    card_abspath = os.path.abspath(a.card)
    rec = dict(run_id=run_id, question="re-admission through the card", started_at=when,
               finished_at=when, since="", replay=[], sources_path=os.path.abspath(cfg_path),
               sources={sid: dict(id=sid, readmitted=dict(card=card_abspath, changed=changed,
                                                         was=dict(reason=q["reason"], at=q["at"],
                                                                  status=q["status"])))},
               skipped={}, findings=[], refusals=[], xref={}, publish={}, status="READMITTED",
               cost=dict(requests=0))
    rec_path = jdump(rec, os.path.join(a.store, "runs", f"{run_id}.json"))
    dpath = digest_line(a.store, sid, f"re-admitted {when}: {changed} (card {card_abspath})")
    say(f"READMITTED {sid}  quarantined -> null  card={card_abspath}")
    say(f"READMITTED {sid}  changed: {changed}")
    say(f"DIGEST    {dpath}")
    say(f"RUN-RECORD {os.path.relpath(rec_path, a.store)}")
    say(f"READMITTED {sid} — the next run fetches it again")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the symbiont loop")
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--question", default="what mouse gene identity and position can we publish")
    ap.add_argument("--since", default="auto", choices=["auto", "none"],
                    help="auto = honour the last run's state (this is what makes run 2 fetch nothing)")
    ap.add_argument("--sources", default="", help="comma list of source ids from sources.yml, default all")
    ap.add_argument("--sources-config", default=contract.DEFAULT_SOURCES,
                    help="path to sources.yml (the one definition site per source)")
    ap.add_argument("--replay-raw", action="append", default=[],
                    metavar="SOURCE:RESKEY=PATH", help="normalise from a substituted snapshot (corruption tests)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--rebuild-from-raw", action="store_true",
                    help="T4: move records/, xref/ and view/ aside (never delete) and rebuild all "
                         "three from raw/ alone — no fetch, no request; each output's sha256 is "
                         "compared to the recorded value and the FIRST mismatch refuses, naming "
                         "the artefact; on failure the moved-aside copies are restored")
    ap.add_argument("--readmit", default="", metavar="SOURCE",
                    help="re-admit a quarantined source THROUGH the card and exit (no run happens)")
    ap.add_argument("--card", default="", metavar="PATH",
                    help="the admission card for --readmit: names the source and WHAT CHANGED")
    a = ap.parse_args(argv)

    replay = {}
    for spec in a.replay_raw:
        left, _, path = spec.partition("=")
        if not path or ":" not in left:
            ap.error(f"--replay-raw wants SOURCE:RESKEY=PATH, got {spec!r}")
        replay[left] = path

    if a.readmit:
        return _readmit(a)

    if a.rebuild_from_raw:
        return _rebuild_from_raw(a)

    try:
        rec = run(a.store, a.question, a.since,
                  [s for s in a.sources.split(",") if s] or None, replay, a.run_id,
                  sources_path=a.sources_config)
    except contract.ConfigError as e:
        say(f"REFUSED[{e.code}] {e}")
        return 2
    except (contract.Refusal, FetchError, RuntimeError) as e:
        say(f"REFUSED[{getattr(e, 'code', 'error')}] {type(e).__name__}: {e}")
        return 2
    if rec["status"] == "REFUSED":
        return 3
    # exit 0 with rejects is correct: a rejected row is data with a reason, not a crash.
    return 0 if all(s["verify"]["ok"] for s in rec["sources"].values() if "verify" in s) else 1


if __name__ == "__main__":
    sys.exit(main())
