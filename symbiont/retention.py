"""retention — WHAT 'CURRENT' MEANS when release N+1 lands on top of N (the store half).

(req-P1-RETENTION-AND-SUPERSEDE-what-current)

THE HOLE (the row's own words): the manifest says append-only; nothing says how many releases we
keep, or what happens to a claim minted against the old one. This module owns the STORE half:

  policy     how many releases per source, and what that costs in MB, are the OWNER's numbers in
             the policy file (reports/symbiont-policy.yml — keys retention_releases_per_source
             and retention_disk_consequence_mb; the blank form is docs/POLICY-TEMPLATE.yml). The
             station only READS them. null = fail closed: the sweep refuses to drop anything,
             because a number the owner has not set does not exist to enforce.
  snapshot   freeze the CURRENT published view into releases/<relset>/ (view tsv + schema +
             provenance + RELEASE.json). The view file is OVERWRITTEN by every publish, so an
             un-frozen release is a release the door can no longer quote: freezing is what makes
             'as of release R' answerable after R+1 lands. The relset id is the manifest's own
             spelling — the per-source releases pipe-joined in sorted-source order
             (e.g. ensembl-116|mgi-2026-11-09).
  sweep      for each source: order raw/<source>/<release>/ by the newest fetched_at recorded in
             the release's own .meta.json files, keep the newest N, and for each drop there are
             exactly TWO legal outcomes — ARCHIVED (moved to archive/<source>/<release>/ with
             RETENTION.json holding every sha256, each file re-verified against its own meta
             BEFORE the move) or REFUSED by name:
               REFUSED[policy]           the owner has not set retention_releases_per_source
               REFUSED[current-release]  the release state/<source>.json points at
               REFUSED[hash-mismatch]    a payload no longer hashes to its recorded sha256
               REFUSED[no-meta]          a release dir with no .meta.json (order and hash are
                                         unprovable, and unprovable is not droppable)
             A silent removal is not representable. A sweep without --apply is a dry run: it
             prints what it WOULD archive and writes nothing. The sweep MOVES, it never frees
             disk: the archive keeps the bytes and the hashes, and deleting an archive is the
             operator's decision, not the station's.

Run:
    python3 -m symbiont.retention show     --store symbiont/store
    python3 -m symbiont.retention snapshot --store symbiont/store
    python3 -m symbiont.retention sweep    --store symbiont/store [--apply] [--source mgi]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys

from .admission_policy import load_policy, PolicyError, DEFAULT_POLICY
from .loop import jdump, say
from .manifest import find_view

VIEW_FILE_RE = __import__("re").compile(r"^(?P<view>.+)\.v(?P<version>\d+)\.tsv$")


class Refusal(Exception):
    """A legal answer with a reason: REFUSED[<reason>] <detail>, exit 2."""
    def __init__(self, reason: str, detail: str):
        super().__init__(f"REFUSED[{reason}] {detail}")
        self.reason, self.detail = reason, detail


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: str, what: str) -> dict:
    if not os.path.isfile(path):
        raise Refusal("bad-input", f"{what} is missing at run time: {path}")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise Refusal("bad-input", f"{what} at {path} is not JSON: {e}")


# ---------------------------------------------------------------- the view, located and named
def locate_view(store: str) -> tuple[str, int, str]:
    """THE published view of this store — refuses to pick one of many (manifest.find_view's
    doctrine, applied to the whole view/ directory: an ambiguous store is not a store)."""
    vdir = os.path.join(store, "view")
    if not os.path.isdir(vdir):
        raise Refusal("bad-input", f"store has no view/ directory: {store}")
    found = []
    for name in sorted(os.listdir(vdir)):
        m = VIEW_FILE_RE.match(name)
        if m:
            found.append((m.group("view"), int(m.group("version")), os.path.join(vdir, name)))
    if not found:
        raise Refusal("bad-input", f"no view under {vdir} (looked for *.v<N>.tsv)")
    if len(found) > 1:
        names = ", ".join(f"{v}.v{n}" for v, n, _ in found)
        raise Refusal("bad-input", f"{len(found)} views under {vdir} ({names}) — refusing to pick one")
    return found[0]


def release_set(store: str) -> tuple[str, dict]:
    """The CURRENT release set, spelled the way the manifest spells source_release: the
    per-source releases pipe-joined in sorted-source order. Read from the view provenance —
    never invented, never from a clock."""
    view_name, version, view_path = locate_view(store)
    stem = f"{view_name}.v{version}"
    prov = read_json(os.path.join(store, "view", f"{stem}.provenance.json"), "the view provenance")
    sources = prov.get("sources") or {}
    if not sources:
        raise Refusal("bad-input", f"view provenance names no sources: {store}/view/{stem}.provenance.json")
    per = {}
    for s in sorted(sources):
        rel = (sources[s] or {}).get("release") or ""
        if not rel:
            raise Refusal("bad-input", f"view provenance names no release for source {s!r}")
        per[s] = rel
    return "|".join(per[s] for s in sorted(per)), per


def held_release_sets(store: str) -> list[str]:
    """The frozen release bundles under releases/ — the only releases the door can answer AS OF."""
    rdir = os.path.join(store, "releases")
    if not os.path.isdir(rdir):
        return []
    return sorted(d for d in os.listdir(rdir)
                  if os.path.isdir(os.path.join(rdir, d)))


# ---------------------------------------------------------------- snapshot: freeze the current view
def snapshot(store: str) -> str:
    """Freeze the current published view into releases/<relset>/. Idempotent when the relset is
    already frozen with the SAME view sha; REFUSED (never an overwrite) when it differs."""
    view_name, version, view_path = locate_view(store)
    stem = f"{view_name}.v{version}"
    vdir = os.path.join(store, "view")
    schema_path = os.path.join(vdir, f"{stem}.schema.json")
    prov_path = os.path.join(vdir, f"{stem}.provenance.json")
    for p in (schema_path, prov_path):
        if not os.path.isfile(p):
            raise Refusal("bad-input", f"the view is missing its {os.path.basename(p)}: {p}")
    view_sha = sha256_file(view_path)
    relset, per = release_set(store)
    prov = read_json(prov_path, "the view provenance")
    rdir = os.path.join(store, "releases", relset)
    rel_json = os.path.join(rdir, "RELEASE.json")
    if os.path.isdir(rdir):
        old = read_json(rel_json, f"the frozen release bundle for {relset}")
        if (old.get("view") or {}).get("sha256") == view_sha:
            say(f"FROZEN   releases/{relset}/ already holds this exact view (sha256={view_sha[:12]}…)"
                f" — untouched")
            return rdir
        raise Refusal("release-exists",
                      f"releases/{relset}/ is already frozen with a DIFFERENT view "
                      f"(held {(old.get('view') or {}).get('sha256', '?')[:12]}…, now {view_sha[:12]}…) "
                      f"— a frozen release is never overwritten; re-free under a new relset")
    os.makedirs(rdir, exist_ok=True)
    for src in (view_path, schema_path, prov_path):
        shutil.copy2(src, os.path.join(rdir, os.path.basename(src)))
    jdump(dict(
        release_set=relset,
        sources=per,
        view=dict(name=view_name, version=version, rows=prov.get("rows"),
                  sha256=view_sha, run_id=prov.get("run_id"),
                  file=os.path.basename(view_path)),
        frozen_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        note=("the frozen answer to 'as of release " + relset + "': the live view/ files are "
              "overwritten by every publish, so this bundle is the only place this release's "
              "bytes survive. RETENTION (sweep) never touches releases/ — an as-of question the "
              "door can no longer answer is exactly the silence this row exists to kill"),
    ), rel_json)
    say(f"FROZEN   releases/{relset}/  view sha256={view_sha}  rows={prov.get('rows')}  "
        f"(as-of answers for this release are now grounded in the bundle, not the live view)")
    return rdir


# ---------------------------------------------------------------- the sweep (the delete path)
def _release_dirs(store: str, source: str | None) -> dict[str, dict]:
    """raw/<source>/<release>/ -> {release: {'order': newest fetched_at, 'metas': [...]}}.
    A release dir with no .meta.json is returned with order='' — the sweep refuses it by name."""
    out: dict[str, dict] = {}
    raw = os.path.join(store, "raw")
    if not os.path.isdir(raw):
        return out
    for src in sorted(os.listdir(raw)):
        if source and src != source:
            continue
        sdir = os.path.join(raw, src)
        if not os.path.isdir(sdir):
            continue
        for rel in sorted(os.listdir(sdir)):
            rdir = os.path.join(sdir, rel)
            if not os.path.isdir(rdir):
                continue
            metas = sorted(f for f in os.listdir(rdir) if f.endswith(".meta.json"))
            order = ""
            for mf in metas:
                m = read_json(os.path.join(rdir, mf), f"a raw snapshot meta in {src}/{rel}")
                if (m.get("fetched_at") or "") > order:
                    order = m["fetched_at"]
            out[f"{src}/{rel}"] = {"source": src, "release": rel, "dir": rdir,
                                   "order": order, "metas": metas}
    return out


def _verify_release(rdir: str) -> tuple[list[dict], list[str]]:
    """Re-hash every payload against its own .meta.json BEFORE any move. Returns
    (manifest rows, refusals) — a refusal names the file and BOTH hashes. The payload of
    <sha256>.meta.json is the content-addressed file <sha256>.<ext> in the same dir."""
    rows, refuses = [], []
    files = sorted(os.listdir(rdir))
    for f in files:
        if not f.endswith(".meta.json"):
            continue
        m = read_json(os.path.join(rdir, f), f"a raw snapshot meta in {rdir}")
        stem = f[: -len(".meta.json")]
        payloads = [x for x in files if x != f and x.split(".")[0] == stem]
        if not payloads:
            refuses.append(f"REFUSED[hash-mismatch] {os.path.join(rdir, stem)}: named by its "
                           f"meta but not on disk — refusing rather than guess")
            continue
        payload = os.path.join(rdir, payloads[0])
        actual = sha256_file(payload)
        if actual != m.get("sha256"):
            refuses.append(f"REFUSED[hash-mismatch] {payload}: recorded sha256 "
                           f"{m.get('sha256')} but the bytes hash {actual}")
        rows.append(dict(file=payloads[0], sha256=actual, bytes=m.get("bytes"),
                          fetched_at=m.get("fetched_at"), run_id=m.get("run_id")))
    return rows, refuses


def sweep(store: str, apply: bool, source: str | None, policy_path: str) -> int:
    store = os.path.abspath(store)
    try:
        pol = load_policy(policy_path)
    except PolicyError as e:
        say(f"REFUSED[policy] {e}")
        return 2
    n = pol.get("retention_releases_per_source")
    stated_mb = pol.get("retention_disk_consequence_mb")
    if n is None:
        say(f"REFUSED[policy] retention_releases_per_source is not set in {policy_path} — the "
            f"owner's number does not exist to enforce, so NOTHING is dropped (fail closed)")
        return 2
    say(f"POLICY   retention_releases_per_source={n}  "
        f"retention_disk_consequence_mb={stated_mb!r}  (from {os.path.abspath(policy_path)})")

    rels = _release_dirs(store, source)
    if not rels:
        say(f"RETENTION no raw release directories under {store}/raw"
            + (f" for source {source!r}" if source else ""))
        return 0
    by_src: dict[str, list[dict]] = {}
    for key, r in rels.items():
        by_src.setdefault(r["source"], []).append(r)
    archived, refused, kept_bytes, freed_bytes = [], [], 0, 0
    for src in sorted(by_src):
        rels_here = sorted(by_src[src], key=lambda r: r["order"], reverse=True)
        # the CURRENT release is read from the state file — the sweep never drops what it points at
        state_path = os.path.join(store, "state", f"{src}.json")
        current = (read_json(state_path, f"state for {src}").get("release")
                   if os.path.isfile(state_path) else None)
        keep, drops = rels_here[:n], rels_here[n:]
        for r in keep:
            kept_bytes += sum(os.path.getsize(os.path.join(r["dir"], f)) for f in os.listdir(r["dir"]))
        say(f"SOURCE   {src}  releases={len(rels_here)}  keep={n}  "
            f"current={current or '(no state file)'}  drop={[r['release'] for r in drops] or 'none'}")
        for r in drops:
            tag = f"{src}/{r['release']}"
            if current and r["release"] == current:
                refused.append(f"REFUSED[current-release] {tag}: state/{src}.json points at it — "
                              f"the current release is never dropped")
                continue
            if not r["metas"]:
                refused.append(f"REFUSED[no-meta] {tag}: no .meta.json in the release dir, so "
                              f"neither its order nor its hashes are provable — unprovable is "
                              f"not droppable")
                continue
            rows, verifies = _verify_release(r["dir"])
            if verifies:
                refused.extend(verifies)
                continue
            dest = os.path.join(store, "archive", src, r["release"])
            if os.path.exists(dest):
                refused.append(f"REFUSED[archive-exists] {tag}: archive/{src}/{r['release']}/ "
                               f"already exists — refusing rather than overwrite an archive")
                continue
            freed_bytes += sum(row["bytes"] or 0 for row in rows)
            if not apply:
                say(f"WOULD-ARCHIVE {tag}  ->  archive/{src}/{r['release']}/  "
                    f"files={len(rows)}  sha256s kept")
                archived.append((tag, dest, rows, False))
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(r["dir"], dest)
            jdump(dict(source=src, release=r["release"], moved_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                       policy=dict(retention_releases_per_source=n,
                                   retention_disk_consequence_mb=stated_mb),
                       files=rows,
                       note=("MOVED, not deleted: every payload was re-hashed against its own "
                             ".meta.json before the move, and the hashes are kept here so the "
                             "archive can be re-verified (or honestly deleted) later")),
                  os.path.join(dest, "RETENTION.json"))
            say(f"ARCHIVED {tag}  ->  archive/{src}/{r['release']}/  files={len(rows)}  "
                f"sha256s kept in RETENTION.json")
            archived.append((tag, dest, rows, True))
    mb = lambda b: f"{b / 1_000_000:.1f} MB"                                  # noqa: E731
    say(f"RETENTION {'applied' if apply else 'DRY RUN (nothing written; --apply to archive)'}  "
        f"archived={sum(1 for a in archived if a[3])}  would-archive={sum(1 for a in archived if not a[3])}"
        f"  refused={len(refused)}")
    say(f"RETENTION raw keeps {mb(kept_bytes)}; the sweep moves (never frees) {mb(freed_bytes)} "
        f"to archive/ — the policy states the ceiling: retention_disk_consequence_mb="
        f"{stated_mb!r} MB")
    for line in refused:
        say(line)
    return 2 if refused else 0


def show(store: str) -> int:
    relset, per = release_set(store)
    say(f"CURRENT  {relset}  " + " ".join(f"{s}={r}" for s, r in sorted(per.items())))
    held = held_release_sets(store)
    if held:
        say("HELD     " + ", ".join(held))
    else:
        say("HELD     (no frozen release bundles — run `retention snapshot` before the next "
            "release lands)")
    for h in held:
        rj = os.path.join(store, "releases", h, "RELEASE.json")
        if os.path.isfile(rj):
            v = (read_json(rj, f"the frozen bundle for {h}").get("view") or {})
            say(f"  {h}  view sha256={str(v.get('sha256'))[:16]}…  rows={v.get('rows')}  "
                f"run={v.get('run_id')}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="symbiont.retention",
                                 description="retention: the N-releases rule, the frozen "
                                             "release bundles, and the archive-or-refuse delete path")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_line in (("show", "the current release set and the frozen bundles"),
                            ("snapshot", "freeze the current view into releases/<relset>/"),
                            ("sweep", "apply the retention rule: archive-or-refuse, never silent")):
        p = sub.add_parser(name, help=help_line)
        p.add_argument("--store", default=os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "store"))
        if name == "snapshot":
            continue
        if name == "sweep":
            p.add_argument("--apply", action="store_true",
                           help="actually archive; without it the sweep is a dry run")
            p.add_argument("--source", default=None,
                           help="sweep one source only (by name)")
            p.add_argument("--policy", default=DEFAULT_POLICY,
                           help="the owner's policy file (reports/symbiont-policy.yml)")
    a = ap.parse_args(argv if argv is not None else sys.argv[1:])
    store = os.path.abspath(a.store)
    try:
        if a.cmd == "show":
            return show(store)
        if a.cmd == "snapshot":
            snapshot(store)
            return 0
        return sweep(store, apply=a.apply, source=getattr(a, "source", None),
                     policy_path=getattr(a, "policy", DEFAULT_POLICY))
    except Refusal as e:
        say(str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
