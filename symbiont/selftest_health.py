"""SELFTEST of the narrow source_health record (req-P1-SOURCE-HEALTH-AND-AGENT-ADDED-SOURCES).

`symbiont.health.health(store)` reads sources.yml + the last run record + the xref/view counts and
returns PER SOURCE one dict with exactly: state (ok|degraded|down|quarantined) · last_run_age_s ·
last_success_age_s · count_check{ours,theirs,url} · rate_used_this_cycle · licence(known|unknown) ·
staleness{our_release, upstream_release|null} · reason (ONE string, ALWAYS set). Health never
fetches — every fact is read off the disk — and F3 proves it with a counting server.

The healthy store under test is BUILT HERE by the real loop against the miniature provider
(selftest_arc's five-gene local server): no hidden dependency on a dev tree's own store — the
selftest passes in a fresh clone exactly as on the dev tree.

  F1 a healthy store      -> both sources `ok`, and each `reason` says WHY it is ok
  F2 a count mismatched   -> `degraded`, the reason names the mismatch AND the URL that states theirs
  F3 quarantined in yml   -> `quarantined`, the reason names the date and the cause, and NO fetch is
                             attempted (the counting server the source points at is hit 0 times)

Usage: python3 -m symbiont.selftest_health [--keep]
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import contract
from .selftest_sources import write_config
from .selftest_arc import build_tree, start_server, base_sources as arc_base_sources

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_CONFIG = contract.DEFAULT_SOURCES

MGI_COUNT_URL = "https://www.informatics.jax.org/downloads/reports/MRK_ENSEMBL.rpt"


class _CountingHandler(SimpleHTTPRequestHandler):
    """Serves nothing, counts everything: a request that arrives here is a fetch that happened."""
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        self.send_error(404)

    def log_message(self, *a):   # silent: the fixture's assertion speaks
        pass


def _cli(*args: str):
    return subprocess.run([sys.executable, "-m", "symbiont.health", *args],
                          capture_output=True, text=True, cwd=FARM)


def _records(stdout: str) -> dict:
    """The JSONL the CLI prints -> {id: record}; one JSON object per source per line."""
    out = {}
    for line in stdout.splitlines():
        if line.strip():
            k, v = next(iter(json.loads(line).items()))
            out[k] = v
    return out


def _quote(title: str, p) -> str:
    text = f"$ python3 -m symbiont.health {' '.join(p.args[3:])}\n{p.stdout.rstrip()}"
    bar = "-" * 78
    return f"{title}\n{bar}\n{text}\n{bar}"


def main(argv=None) -> int:
    keep = "--keep" in (argv or sys.argv[1:])
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = ""):
        results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" {detail}" if detail and not ok else ""))

    tmp = tempfile.mkdtemp(prefix="symbiont-health-")
    try:
        # ── F0 build the healthy store: the real loop, the miniature provider ────────────────
        web = os.path.join(tmp, "web")
        os.makedirs(web)
        build_tree(web)
        srv0, host = start_server(web)
        st0 = os.path.join(tmp, "healthy.store")
        cfg0 = write_config(os.path.join(tmp, "healthy.yml"), arc_base_sources(host))
        p0 = subprocess.run([sys.executable, "-m", "symbiont.loop", "--store", st0,
                             "--sources-config", cfg0, "--run-id", "health-f0"],
                            capture_output=True, text=True, cwd=FARM)
        srv0.shutdown()
        check("F0 the healthy store built (real loop, five-gene miniature, zero external requests)",
              p0.returncode == 0, f"exit={p0.returncode} out={p0.stdout.strip().splitlines()[-1][:80] if p0.stdout else p0.stderr[:80]}")

        # ── F1 the healthy store -> both sources ok, each reason says why ─────────────────────
        print(_quote("F1 a healthy store (both sources ok, each reason says why)",
                     p1 := _cli("--store", st0)))
        r1 = _records(p1.stdout)
        for sid in ("ensembl_biomart", "mgi"):
            rec = r1.get(sid) or {}
            check(f"F1 {sid} -> ok, reason set and says why",
                  rec.get("state") == "ok" and bool(rec.get("reason"))
                  and rec["reason"].startswith("ok: ")
                  and "count check agreed" in rec["reason"],
                  f"state={rec.get('state')} reason={rec.get('reason')!r}")
        check("F1 -> the record carries exactly the eight keys",
              all(sorted(r1[s]) == ["count_check", "last_run_age_s", "last_success_age_s",
                                     "licence", "rate_used_this_cycle", "reason",
                                     "staleness", "state"] for s in r1),
              f"keys={ {s: sorted(v) for s, v in r1.items()} }")
        check("F1 -> exit 0", p1.returncode == 0, f"exit={p1.returncode}")

        # ── F2 a source with its count mismatched -> degraded, reason names it and the URL ─────
        st2 = os.path.join(tmp, "f2.store")
        os.makedirs(os.path.join(st2, "runs"))
        shutil.copytree(os.path.join(st0, "state"), os.path.join(st2, "state"))
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        json.dump(dict(run_id="f2-count-mismatch", started_at=now, sources={
            "ensembl_biomart": dict(  # the authority agrees
                verify=dict(ok=True, ours=78348, source_count=78348, url="https://www.ensembl.org/biomart/martservice"),
                cost=dict(requests=2)),
            "mgi": dict(  # the provider's own count disagrees
                verify=dict(ok=False, ours=77645, source_count=80000, url=MGI_COUNT_URL),
                cost=dict(requests=2)),
        }), open(os.path.join(st2, "runs", "f2-count-mismatch.json"), "w", encoding="utf-8"))
        print(_quote("F2 a source with its count mismatched (degraded, mismatch + URL named)",
                     p2 := _cli("--store", st2)))
        r2 = _records(p2.stdout)
        m2 = r2.get("mgi") or {}
        check("F2 mgi -> degraded, reason names the mismatch AND the URL",
              m2.get("state") == "degraded" and "ours=77645" in (m2.get("reason") or "")
              and "theirs=80000" in (m2.get("reason") or "") and MGI_COUNT_URL in (m2.get("reason") or ""),
              f"state={m2.get('state')} reason={m2.get('reason')!r}")
        check("F2 ensembl_biomart -> still ok (one bad source does not paint the other)",
              (r2.get("ensembl_biomart") or {}).get("state") == "ok")
        check("F2 -> count_check carries ours/theirs/the mismatched url",
              (m2.get("count_check") or {}).get("ours") == 77645
              and m2["count_check"].get("theirs") == 80000
              and m2["count_check"].get("url") == MGI_COUNT_URL,
              f"count_check={m2.get('count_check')}")
        check("F2 -> exit 0", p2.returncode == 0, f"exit={p2.returncode}")

        # ── F3 quarantined in sources.yml -> quarantined, and NO fetch attempted ───────────────
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _CountingHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        host = f"http://127.0.0.1:{srv.server_address[1]}"
        s = contract.load_sources(REAL_CONFIG)
        s["mgi"]["base_url"] = f"{host}/gone/"          # if anything fetches, the counter sees it
        s["mgi"]["endpoints"] = {"metadata": "MRK_ENSEMBL.rpt", "mrk_ensembl": "MRK_ENSEMBL.rpt",
                                 "mrk_list1_gz": "MRK_List1.rpt.gz"}
        s["mgi"]["quarantined"] = dict(
            reason="404 on every endpoint (runs q3-a and q3-b both refused source-gone)",
            at="2026-11-10T09:15:00+00:00", status="source-gone")
        cfg3 = write_config(os.path.join(tmp, "f3.yml"), s)
        print(_quote("F3 a source marked quarantined in sources.yml (quarantined, zero fetches)",
                     p3 := _cli("--store", st0, "--sources-config", cfg3)))
        r3 = _records(p3.stdout)
        m3 = r3.get("mgi") or {}
        check("F3 mgi -> quarantined, reason names the date AND the cause",
              m3.get("state") == "quarantined" and "2026-11-10T09:15:00+00:00" in (m3.get("reason") or "")
              and "source-gone" in (m3.get("reason") or "") and "404" in (m3.get("reason") or ""),
              f"state={m3.get('state')} reason={m3.get('reason')!r}")
        check("F3 -> NO fetch attempted: the counting server it points at was hit 0 times",
              _CountingHandler.hits == 0, f"hits={_CountingHandler.hits}")
        check("F3 ensembl_biomart -> still ok (a quarantine is per source, by name)",
              (r3.get("ensembl_biomart") or {}).get("state") == "ok")
        check("F3 -> exit 0", p3.returncode == 0, f"exit={p3.returncode}")
        srv.shutdown()
    finally:
        if keep:
            print(f"  (kept {tmp})")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    bad = [r for r in results if not r[1]]
    print(f"SELFTEST {'PASS' if not bad else 'FAIL'}: {len(results) - len(bad)}/{len(results)} "
          "fixtures held — the narrow source_health record reads the store and fetches nothing",
          flush=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
