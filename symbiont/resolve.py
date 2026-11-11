"""THE POINT-AT RESOLVER — the door that answers a point-at accession by fetching ON DEMAND.

(req-P1-POINT-AT-ENFORCED-IN-CODE-the-tiers) A resource whose sources.yml storage.class is
point-at NEVER lands in raw/: the loop records its accession plus a fetch recipe (url, params,
sha256 of the recipe) under pointat/<source>/<release>/<reskey>.json, and this door is how those
bytes come back — fetched NOW, on demand, with the cost said out loud:

    python3 -m symbiont.resolve <source>/<release>/<reskey> --store <store>
    python3 -m symbiont.resolve <source>/<reskey>          --store <store>
                                                          (the newest recorded release)

The response is machine-readable and says `fetched_now: true` with the cost (requests, bytes,
wall seconds, rate-limit wait) and the recipe hash it satisfied. A resource whose class is store
has no point-at record and is REFUSED here BY NAME: it lives in raw/ and does not need
re-fetching. The recipe is re-hashed from the record before the fetch — a recipe that cannot say
its own name on re-read is refused, never silently re-derived.

Exit codes (the loop's contract): 0 resolved · 2 REFUSED by name (no such accession, a store
resource, a corrupted recipe) · 3 the on-demand fetch failed (source-gone | unreachable).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys

from . import contract
from .http import Fetcher, FetchError, GONE_STATUS

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORE = os.path.join(ROOT, "store")

POINTAT_DIR = "pointat"          # the store tier where the loop records point-at accessions


def say(line: str) -> None:
    print(line, flush=True)


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _refuse(code: str, msg: str) -> int:
    say(f"REFUSED[{code}] {msg}")
    return 2


def _find_record(store: str, accession: str) -> tuple[dict, str]:
    """<source>/<release>/<reskey> -> that record; <source>/<reskey> -> the newest recorded
    release's record. Returns (record, path) or raises FileNotFoundError naming what was sought."""
    parts = [p for p in accession.strip("/").split("/") if p]
    if len(parts) == 3:
        sid, release, reskey = parts
        path = os.path.join(store, POINTAT_DIR, sid, release, f"{reskey}.json")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no point-at record at {os.path.relpath(path, store)} "
                                    f"(accession {accession!r})")
    elif len(parts) == 2:
        sid, reskey = parts
        rdir = os.path.join(store, POINTAT_DIR, sid)
        best = None
        if os.path.isdir(rdir):
            for release in sorted(os.listdir(rdir)):
                p = os.path.join(rdir, release, f"{reskey}.json")
                if os.path.isfile(p):
                    try:
                        with open(p, encoding="utf-8") as fh:
                            rec = json.load(fh)
                    except Exception:
                        continue
                    key = rec.get("recorded_at") or ""
                    if best is None or key >= best[0]:
                        best = (key, rec, p)
        if best is None:
            raise FileNotFoundError(f"no point-at record for {sid}/{reskey} under "
                                    f"{os.path.relpath(rdir, store)}/ (accession {accession!r})")
        return best[1], best[2]
    else:
        raise ValueError(f"an accession is <source>/<release>/<reskey> (or <source>/<reskey> "
                         f"for the newest recorded release), got {accession!r}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh), path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="resolve a point-at accession by fetching it on demand (fetched_now + cost)")
    ap.add_argument("accession", metavar="ACCESSION",
                    help="<source>/<release>/<reskey> (or <source>/<reskey> for the newest)")
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--sources-config", default=contract.DEFAULT_SOURCES,
                    help="path to sources.yml — read only for the source's rate limit")
    a = ap.parse_args(argv)

    try:
        rec, path = _find_record(a.store, a.accession)
    except FileNotFoundError as e:
        return _refuse("no-such-accession", f"{e} — a store-class resource lives in raw/ and has "
                                           f"no point-at record; nothing is resolved")
    except ValueError as e:
        return _refuse("bad-accession", str(e))

    if rec.get("storage_class") != "point-at":
        return _refuse("not-point-at", f"{rec.get('accession', a.accession)} is class "
                                       f"{rec.get('storage_class')!r}, not point-at — this door "
                                       f"re-fetches accessions the loop refused to store")

    recipe = rec.get("recipe") or {}
    rsha = contract.recipe_hash(recipe)
    if rsha != (rec.get("recipe_sha256") or ""):
        return _refuse("recipe-integrity",
                       f"{rec['accession']}: the recorded recipe hashes to {rsha} but the record "
                       f"says {rec.get('recipe_sha256')!r} — a recipe that cannot say its own "
                       f"name on re-read is never fetched against")

    # the source's rate limit, IF sources.yml still configures it: the resolve door obeys the
    # same politeness the loop does. A source since removed from config still resolves (the
    # recipe is self-contained); it just runs without a rate limit, and says so.
    rate = None
    try:
        cfg = contract.load_sources(a.sources_config).get(rec.get("source", ""))
        rate = (cfg or {}).get("rate_limit")
    except contract.ConfigError:
        say(f"NOTE     sources.yml would not load — resolving without the source's rate limit")
    fetcher = Fetcher(rate_limit=rate)

    say(f"RESOLVE  {rec['accession']}  (class point-at: {rec.get('reason', '?')})")
    say(f"RECIPE   {os.path.relpath(path, a.store)}  recipe_sha256={rec['recipe_sha256']}  "
        f"url={recipe.get('url', '?')}")
    say(f"FETCH    on demand — the payload is not in raw/, by class point-at")
    try:
        body = fetcher.get(recipe.get("url", ""), f"resolve {rec['accession']}")
    except FetchError as e:
        code = "source-gone" if getattr(e, "status", 0) in GONE_STATUS else "source-unreachable"
        say(f"REFUSED[{code}] the on-demand fetch of {rec['accession']} failed: {e}")
        return 3
    except contract.BudgetExceeded as e:
        say(f"REFUSED[budget] {e}")
        return 3

    fetched = sha256(body)
    recorded = rec.get("payload_sha256") or ""
    matches = (fetched == recorded) if recorded else None
    answer = dict(
        accession=rec["accession"], source=rec.get("source", ""), release=rec.get("release", ""),
        reskey=rec.get("reskey", ""), storage_class="point-at", reason=rec.get("reason", ""),
        fetched_now=True, bytes=len(body), fetched_sha256=fetched,
        recorded_payload_sha256=recorded or None,
        matches_recorded_payload=matches,
        recipe_sha256=rec["recipe_sha256"], url=recipe.get("url", ""),
        resolved_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        cost=dict(requests=len(fetcher.log), bytes_in=fetcher.bytes_in,
                  rate_limit_wait_s=round(sum(fetcher.waits), 3),
                  wall_s=fetcher.wall_seconds),
    )
    if matches is False:
        say(f"NOTE     the fetched payload reads sha256 {fetched} but the recorded run saw "
            f"{recorded} — the provider has moved since the accession was recorded; both names "
            f"are in the answer")
    say(f"RESOLVED {rec['accession']}  fetched_now=true  bytes={len(body)}  "
        f"requests={answer['cost']['requests']}")
    print("ANSWER " + json.dumps(answer, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
