"""PROOF of sources.yml and the four refusals (req-P0-SOURCES-YML-AND-THE-FOUR-REFUSALS-T5).

Every fixture below is FAILING ON PURPOSE, and each one fails ONE refusal by name. Nothing here
touches the network: a local HTTP server serves a three-gene miniature of both providers, and every
config is a copy of the SHIPPED symbiont/sources.yml with only the endpoints repointed at it — so
what is exercised is the real adapters, the real contract and the real loop.

  F1 budget          a budget-capped source (requests: 2)        -> REFUSED[budget], exit 3   (T5)
  F2 licence         an unreachable licence URL                  -> REFUSED[licence-unknown], exit 3 (T6)
  F3 stale           a release nobody has refreshed for 100 days -> SOURCE-STALE finding, "stale
                                                                   since <date>" on the claim (T7)
  F4 source-gone     a base_url that answers 404                 -> REFUSED[source-gone], exit 3,
                                                                   quarantined IN sources.yml, and
                                                                   every later run SKIPS IT BY NAME
                                                                   (requests=0, the skip line in the
                                                                   run record AND the digest) (T9)
  F5 mirror          base_url repointed at a mirror, alone       -> the SAME run fetches from it
  F6 registry        a source with no adapter / an adapter with no source -> refused BY NAME, exit 2
  F8 counts          the provider's own count lies, twice        -> the SECOND disagreement flips the
                                                                   source to QUARANTINED automatically,
                                                                   the two failing runs as evidence (T9)
  F9 card            re-admission without a card / without what changed -> REFUSED BY NAME; with a
                                                                   card the quarantine clears and the
                                                                   source runs again (T9)
  F10 no-reason      a quarantine with an empty reason           -> REFUSED BY NAME at load, exit 2:
                                                                   never an unexplained skip (T9)
  F11 budget         the rate budget exceeded twice              -> the source flips to QUARANTINED
                                                                   automatically, evidence attached (T9)
  F12 health         source_health reports quarantined with the reason and the date (T9)
  F13 point-at       an endpoint marked storage.class point-at     -> NO payload in raw/, the
                                                                   accession + fetch recipe (url, params,
                                                                   hash) recorded under pointat/, and
                                                                   run 2 RE-FETCHES it on demand
  F14 store           an endpoint marked storage.class store       -> the payload IS written into
                                                                   raw/, no point-at record (the tier that
                                                                   was policy text, now both enforced)
  F15 no class        a source whose storage class is missing       -> REFUSED BY NAME at load, exit 2:
                                                                   never defaulted to store
  R  resolve          the door re-fetches a point-at accession on demand -> fetched_now: true +
                                                                   bytes + cost; an unknown accession is
                                                                   refused by name
                                                                   (req-P1-POINT-AT-ENFORCED-IN-CODE-the-tiers)

Plus the config writer's own invariant: load -> dump round-trips the shipped sources.yml byte for
byte, which is what makes an automatic quarantine a one-hunk diff and not a reformat.

Usage: python3 -m symbiont.selftest_sources [--keep]
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import contract

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_CONFIG = contract.DEFAULT_SOURCES

GENES = [  # symbol, ensembl id, chrom, start, end
    ("Trp53", "ENSMUSG00000059552", "11", 69580359, 69591767),
    ("Actb", "ENSMUSG00000029580", "5", 142903097, 142906754),
    ("Brca1", "ENSMUSG00000017146", "11", 101488405, 101537537),
]
MGI_ID = {"Trp53": "MGI:104798", "Actb": "MGI:88056", "Brca1": "MGI:105051"}

MART_TSV = ("MGI symbol\tGene stable ID\tChromosome/scaffold name\tGene start (bp)\tGene end (bp)\n"
            + "".join(f"{s}\t{e}\t{c}\t{a}\t{b}\n" for s, e, c, a, b in GENES))
MRK_ENSEMBL = "".join(
    "\t".join([MGI_ID[s], s, f"{s} name", "0.0", c, e, "1 transcript", "-", "protein coding",
               str(a), str(b), "+", "protein_coding"]) + "\n" for s, e, c, a, b in GENES)
MRK_LIST1 = ("MGI Accession ID\tChr\tcM Position\tcm Offset\tMGI Name\tMarker Synonym(s)\t"
             "Marker Symbol\tStatus\tMarker Type\tFeature Type\tGene Name\tGene ID(s)\t"
             "Current MGI Accession ID\tCurrent Symbol\n")
MRK_LIST1 += "".join(
    "\t".join([MGI_ID[s], c, "0.0", "0.0", f"{s} name", "", s, "O", "Gene", "protein coding",
               f"{s} name", "0", "", ""]) + "\n" for s, e, c, a, b in GENES)

ENSEMBL_LICENCE = ("<html><body><h1>Legal</h1><p>Ensembl imposes no restrictions on access to, "
                   "or use of, the data provided and the software used to analyse and present it."
                   "</p></body></html>\n")
MGI_LICENCE = ("<html><body><h1>Copyright</h1><p>MGI data and annotations are licensed under a "
               "Creative Commons Attribution 4.0 International License.</p></body></html>\n")


# ────────────────────────────────────────────────────────────── the local provider
class _Handler(SimpleHTTPRequestHandler):
    """Serves the fixture tree, and answers the two BioMart queries that share one path."""

    def log_message(self, *a):        # silence
        pass

    def do_HEAD(self):
        return super().do_HEAD()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.endswith("martservice"):
            q = urllib.parse.unquote(parsed.query)
            body = (self.server.count_body if 'count="1"' in q else self.server.mart_tsv.encode())
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()


def build_tree(root: str) -> None:
    def w(rel: str, data: bytes, mtime: float | None = None) -> None:
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(data)
        if mtime:
            os.utime(p, (mtime, mtime))

    for base in ("ensembl", "mirror"):
        rel = 116 if base == "ensembl" else 117          # the mirror is a newer release
        w(f"{base}/software.json", json.dumps({"release": rel}).encode())
        w(f"{base}/licence.html", ENSEMBL_LICENCE.encode())
    for base, mtime in (("reports", 1794258000), ("mirror", 1796850000)):   # 2026-11-10 / 2026-12-10
        w(f"{base}/MRK_ENSEMBL.rpt", MRK_ENSEMBL.encode(), mtime)
        w(f"{base}/MRK_List1.rpt.gz", gzip.compress(MRK_LIST1.encode()), mtime)
        w(f"{base}/licence.html", MGI_LICENCE.encode())


def start_server(root: str):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: _Handler(*a, directory=root, **k))
    srv.mart_tsv = MART_TSV
    srv.count_body = b"3\n"          # F8 flips this to a lie to fail the count check twice
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ────────────────────────────────────────────────────────────── config copies
def base_sources(host: str) -> dict:
    """The SHIPPED sources.yml, with only the URLs repointed at the local provider."""
    s = contract.load_sources(REAL_CONFIG)
    e = s["ensembl_biomart"]
    e["base_url"] = f"{host}/ensembl/"
    e["endpoints"] = {"mart": "martservice", "metadata": "software.json"}
    e["licence_url"] = f"{host}/ensembl/licence.html"
    e["rate_limit"] = {"requests_per_minute": 0, "min_interval_s": 0}
    e["budget"] = {"requests": 12, "bytes": 4000000, "wall_s": 90}
    m = s["mgi"]
    m["base_url"] = f"{host}/reports/"
    m["endpoints"] = {"metadata": "MRK_ENSEMBL.rpt", "mrk_ensembl": "MRK_ENSEMBL.rpt",
                      "mrk_list1_gz": "MRK_List1.rpt.gz"}
    m["licence_url"] = f"{host}/reports/licence.html"
    m["rate_limit"] = {"requests_per_minute": 0, "min_interval_s": 0}
    m["budget"] = {"requests": 12, "bytes": 4000000, "wall_s": 90}
    return s


def write_config(path: str, sources: dict) -> str:
    header = contract._header(open(REAL_CONFIG, encoding="utf-8").read())
    open(path, "w", encoding="utf-8").write(header + contract.dump_sources(sources))
    return path


def loop(store: str, cfg: str, *extra: str):
    return subprocess.run([sys.executable, "-m", "symbiont.loop", "--store", store,
                           "--sources-config", cfg, *extra],
                          capture_output=True, text=True, cwd=FARM)


def readmit(store: str, cfg: str, sid: str, card: str | None):
    """--readmit is a loop-side command, not a run: it either refuses BY NAME or clears."""
    args = [sys.executable, "-m", "symbiont.loop", "--store", store, "--sources-config", cfg,
            "--readmit", sid]
    if card is not None:
        args += ["--card", card]
    return subprocess.run(args, capture_output=True, text=True, cwd=FARM)


def record_of(store: str, run_id: str) -> dict:
    return json.load(open(os.path.join(store, "runs", f"{run_id}.json"), encoding="utf-8"))


# ────────────────────────────────────────────────────────────── the fixtures
def main(argv=None) -> int:
    keep = "--keep" in (argv or sys.argv[1:])
    tmp = tempfile.mkdtemp(prefix="symbiont-sources-")
    tree = os.path.join(tmp, "www")
    build_tree(tree)
    srv, host = start_server(tree)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name:52} {detail}", flush=True)

    try:
        # 0 the config writer's invariant -----------------------------------
        text = open(REAL_CONFIG, encoding="utf-8").read()
        rt = contract._header(text) + contract.dump_sources(contract.load_sources(REAL_CONFIG))
        check("F0 sources.yml load->dump round-trip", rt == text,
              f"{len(text)} bytes, byte-identical={rt == text}")

        # F1 budget ---------------------------------------------------------
        cfg = write_config(os.path.join(tmp, "f1.yml"), base_sources(host))
        s = contract.load_sources(cfg)
        s["ensembl_biomart"]["budget"] = {"requests": 2, "bytes": 4000000, "wall_s": 90}
        write_config(os.path.join(tmp, "f1.yml"), s)
        st = os.path.join(tmp, "f1.store")
        p = loop(st, cfg, "--sources", "ensembl_biomart", "--since", "none", "--run-id", "F1")
        rec = record_of(st, "F1")
        r0 = (rec["refusals"] or [{}])[0]
        check("F1 budget  -> REFUSED[budget], exit 3",
              p.returncode == 3 and r0.get("code") == "budget" and "REFUSED[budget]" in p.stdout,
              f"exit={p.returncode} code={r0.get('code')} status={rec['status']} "
              f"detail={(r0.get('detail') or '')[:78]}")

        # F2 licence --------------------------------------------------------
        cfg2 = write_config(os.path.join(tmp, "f2.yml"), base_sources(host))
        s = contract.load_sources(cfg2)
        s["ensembl_biomart"]["licence_url"] = f"{host}/ensembl/NO_SUCH_LICENCE.html"
        write_config(os.path.join(tmp, "f2.yml"), s)
        st2 = os.path.join(tmp, "f2.store")
        p = loop(st2, cfg2, "--sources", "ensembl_biomart", "--since", "none", "--run-id", "F2")
        rec = record_of(st2, "F2")
        r0 = (rec["refusals"] or [{}])[0]
        rawdir = os.path.join(st2, "raw", "ensembl_biomart")
        landed = os.path.isdir(rawdir) and [f for f in os.listdir(rawdir) for _ in [0]]
        check("F2 licence -> REFUSED[licence-unknown], exit 3",
              p.returncode == 3 and r0.get("code") == "licence-unknown" and not landed,
              f"exit={p.returncode} code={r0.get('code')} data_landed={bool(landed)} "
              f"detail={(r0.get('detail') or '')[:70]}")

        # F3 stale ----------------------------------------------------------
        cfg3 = write_config(os.path.join(tmp, "f3.yml"), base_sources(host))
        st3 = os.path.join(tmp, "f3.store")
        p1 = loop(st3, cfg3, "--since", "none", "--run-id", "F3-fresh")
        state_p = os.path.join(st3, "state", "ensembl_biomart.json")
        state = json.load(open(state_p, encoding="utf-8"))
        old = "2026-08-02T00:00:00+00:00"          # 100 days before the 2026-11-10 run
        state["release_at"] = state["updated_at"] = old
        json.dump(state, open(state_p, "w", encoding="utf-8"))
        s = contract.load_sources(cfg3)
        s["ensembl_biomart"]["staleness"] = {"policy_days": 30}
        write_config(cfg3, s)
        p2 = loop(st3, cfg3, "--since", "auto", "--run-id", "F3-stale")
        rec = record_of(st3, "F3-stale")
        prov = json.load(open(os.path.join(st3, "view", "mouse_gene.v1.provenance.json"),
                              encoding="utf-8"))
        st_claim = prov["sources"]["ensembl_biomart"]["stale"]
        fresh = p1.returncode == 0
        check("F3 stale   -> SOURCE-STALE finding + stale_since on the claim",
              fresh and "SOURCE-STALE ensembl_biomart" in p2.stdout
              and st_claim["stale_since"] == "2026-08-02" and st_claim["age_days"] > 30,
              f"run1_exit={p1.returncode} run2_exit={p2.returncode} "
              f"stale_since={st_claim['stale_since']} age={st_claim['age_days']}d "
              f"policy={st_claim['policy_days']}d findings={len(rec['findings'])}")

        # F4 source-gone -> quarantine -> skipped by name -------------------
        cfg4 = write_config(os.path.join(tmp, "f4.yml"), base_sources(host))
        s = contract.load_sources(cfg4)
        s["mgi"]["base_url"] = f"{host}/gone/"      # nothing is served there: 404 on both release files
        write_config(cfg4, s)
        st4 = os.path.join(tmp, "f4.store")
        p = loop(st4, cfg4, "--since", "none", "--run-id", "F4-gone")
        rec = record_of(st4, "F4-gone")
        r0 = (rec["refusals"] or [{}])[0]
        q = contract.load_sources(cfg4)["mgi"].get("quarantined") or {}
        check("F4 gone    -> REFUSED[source-gone], exit 3, quarantined in sources.yml",
              p.returncode == 3 and r0.get("code") == "source-gone" and q.get("status") == "source-gone",
              f"exit={p.returncode} code={r0.get('code')} quarantined.status={q.get('status')} "
              f"at={q.get('at')}")
        p = loop(st4, cfg4, "--since", "auto", "--run-id", "F4-after")
        rec = record_of(st4, "F4-after")
        skip = (rec["sources"].get("mgi") or {}).get("skipped") or {}
        digest_p = skip.get("digest", "")
        digest_t = open(digest_p, encoding="utf-8").read() if os.path.isfile(digest_p) else ""
        check("F4 after   -> the loop SKIPS it BY NAME, the line in the run record AND the digest",
              "SKIP     mgi  skipped: quarantined since" in p.stdout
              and skip.get("reason") == "quarantined"
              and (skip.get("line") or "").startswith("skipped: quarantined since ")
              and "skipped: quarantined since" in digest_t,
              f"exit={p.returncode} skipped.reason={skip.get('reason')} "
              f"digest={os.path.relpath(digest_p, st4) if digest_p else 'MISSING'} "
              f"line={(skip.get('line') or '')[:58]}")
        check("F4 after   -> and refuses to publish a claim it cannot join",
              rec["status"] == "REFUSED" and rec["xref"] == {}
              and (rec["refusals"] or [{}])[0].get("code") == "xref-incomplete",
              f"status={rec['status']} code={(rec['refusals'] or [{}])[0].get('code')} "
              f"published={'publish' in rec and bool(rec['publish'])}")

        # F5 mirror: one key changes ---------------------------------------
        cfg5 = write_config(os.path.join(tmp, "f5.yml"), base_sources(host))
        st5 = os.path.join(tmp, "f5.store")
        p1 = loop(st5, cfg5, "--since", "none", "--run-id", "F5-a")
        before = json.load(open(os.path.join(st5, "state", "mgi.json"), encoding="utf-8"))
        s = contract.load_sources(cfg5)
        s["ensembl_biomart"]["base_url"] = f"{host}/mirror/"     # <<< THE ONLY EDIT
        s["mgi"]["base_url"] = f"{host}/mirror/"                 # <<< THE ONLY EDIT
        write_config(cfg5, s)
        p2 = loop(st5, cfg5, "--since", "auto", "--run-id", "F5-b")
        after = json.load(open(os.path.join(st5, "state", "mgi.json"), encoding="utf-8"))
        e_after = json.load(open(os.path.join(st5, "state", "ensembl_biomart.json"), encoding="utf-8"))
        e_url = e_after["snapshots"]["biomart_tsv"]["url"]
        m_url = after["snapshots"]["mrk_ensembl"]["url"]
        check("F5 mirror  -> the same run fetches from the new base_url",
              p1.returncode == 0 and p2.returncode == 0
              and f"{host}/mirror/martservice" in e_url and m_url.startswith(f"{host}/mirror/")
              and after["release"] != before["release"],
              f"exits={p1.returncode},{p2.returncode} ensembl_url={e_url[-46:]} "
              f"mgi_release={before['release']}->{after['release']}")

        # F6a a source with no adapter -------------------------------------
        cfg6 = write_config(os.path.join(tmp, "f6.yml"), base_sources(host))
        s = contract.load_sources(cfg6)
        ghost = json.loads(json.dumps(s["mgi"]))
        ghost.update(id="ghost", adapter="ghost", base_url=f"{host}/reports/", quarantined=None)
        s["ghost"] = ghost
        write_config(cfg6, s)
        p = loop(os.path.join(tmp, "f6.store"), cfg6, "--since", "none", "--run-id", "F6a")
        check("F6a config without adapter -> refused BY NAME, exit 2",
              p.returncode == 2 and "ghost" in p.stdout and "REFUSED[config]" in p.stdout,
              f"exit={p.returncode} out={(p.stdout or p.stderr).strip().splitlines()[-1][:96]}")

        # F6b an adapter with no config ------------------------------------
        cfg7 = write_config(os.path.join(tmp, "f7.yml"), base_sources(host))
        s = contract.load_sources(cfg7)
        del s["mgi"]
        write_config(cfg7, s)
        p = loop(os.path.join(tmp, "f7.store"), cfg7, "--since", "none", "--run-id", "F6b")
        check("F6b adapter without config -> refused BY NAME, exit 2",
              p.returncode == 2 and "'mgi'" in p.stdout and "REFUSED[config]" in p.stdout,
              f"exit={p.returncode} out={(p.stdout or p.stderr).strip().splitlines()[-1][:96]}")

        # F7 idempotency still holds on a repointed config (T1, no regression) ----
        cfg8 = write_config(os.path.join(tmp, "f8.yml"), base_sources(host))
        st8 = os.path.join(tmp, "f8.store")
        p1 = loop(st8, cfg8, "--since", "none", "--run-id", "F7-a")
        vdir = os.path.join(st8, "view")
        sha1 = hashlib.sha256(open(os.path.join(vdir, "mouse_gene.v1.tsv"), "rb").read()).hexdigest()
        n1 = len(record_of(st8, "F7-a")["cost"]["log"])
        p2 = loop(st8, cfg8, "--since", "auto", "--run-id", "F7-b")
        rec2 = record_of(st8, "F7-b")
        sha2 = hashlib.sha256(open(os.path.join(vdir, "mouse_gene.v1.tsv"), "rb").read()).hexdigest()
        new = {s: rec2["sources"][s]["fetch"]["new"] for s in rec2["sources"]}
        check("F7 idem    -> run 2 fetches NOTHING and the view is byte-identical",
              p1.returncode == 0 and p2.returncode == 0 and not any(new.values()) and sha1 == sha2,
              f"exits={p1.returncode},{p2.returncode} requests={n1}->"
              f"{len(rec2['cost']['log'])} fetch_new={new} view_sha={sha1[:12]}=={sha2[:12]}")

        # F8 the count check fails TWICE -> automatic quarantine, evidence attached (T9) --------
        cfg9 = write_config(os.path.join(tmp, "f8.yml"), base_sources(host))
        st9 = os.path.join(tmp, "f8.store")
        srv.count_body = b"4\n"                # the provider's own count now LIES (ours will be 3)
        p1 = loop(st9, cfg9, "--run-id", "F8-a")
        q1 = contract.load_sources(cfg9)["ensembl_biomart"].get("quarantined")
        p2 = loop(st9, cfg9, "--run-id", "F8-b")
        rec = record_of(st9, "F8-b")
        r0 = (rec["refusals"] or [{}])[0]
        q = contract.load_sources(cfg9)["ensembl_biomart"].get("quarantined") or {}
        check("F8 counts  -> 1st disagreement publishes (exit 1); the 2nd QUARANTINES, evidence attached",
              p1.returncode == 1 and not q1 and p2.returncode == 3
              and r0.get("code") == "count-mismatch" and q.get("status") == "count-mismatch"
              and (r0.get("evidence") or []) == ["F8-a", "F8-b"]
              and "F8-a" in q.get("reason", "") and "F8-b" in q.get("reason", ""),
              f"exits={p1.returncode},{p2.returncode} quarantined_after_run1={bool(q1)} "
              f"code={r0.get('code')} status={q.get('status')} evidence={r0.get('evidence')}")
        srv.count_body = b"3\n"                # the provider fixes its count endpoint
        p3 = loop(st9, cfg9, "--run-id", "F8-c")
        rec3 = record_of(st9, "F8-c")
        e = rec3["sources"]["ensembl_biomart"]
        mgi_cost = (rec3["sources"].get("mgi") or {}).get("cost") or {}
        dtext = open(e["skipped"]["digest"], encoding="utf-8").read()
        check("F8 skip    -> the quarantined source: requests=0, run record names it, digest lists it,"
              " the other source still runs",
              "SKIP     ensembl_biomart  skipped: quarantined since" in p3.stdout
              and (e.get("cost") or {}).get("requests") == 0
              and (e.get("skipped") or {}).get("line", "").startswith("skipped: quarantined since ")
              and "skipped: quarantined since" in dtext and "count-mismatch" in dtext
              and mgi_cost.get("requests", 0) > 0,
              f"exit={p3.returncode} ensembl_requests={(e.get('cost') or {}).get('requests')} "
              f"mgi_requests={mgi_cost.get('requests')} "
              f"digest={e['skipped']['digest'].replace(st9 + os.sep, '')}")

        # F9 re-admission goes back through the card (T9) -----------------------------
        p_none = readmit(st9, cfg9, "ensembl_biomart", None)
        bad_card = os.path.join(tmp, "card-nochange.md")
        open(bad_card, "w", encoding="utf-8").write("source: ensembl_biomart\nlicence: unchanged\n")
        p_bad = readmit(st9, cfg9, "ensembl_biomart", bad_card)
        check("F9 card    -> no card / no what-changed: re-admission REFUSED BY NAME, exit 2",
              p_none.returncode == 2 and "ensembl_biomart" in p_none.stdout
              and "re-admission requires the card" in p_none.stdout
              and p_bad.returncode == 2 and "does not name what changed" in p_bad.stdout,
              f"no_card_exit={p_none.returncode} no_card_out={p_none.stdout.strip().splitlines()[-1][:74]} "
              f"bad_card_exit={p_bad.returncode}")
        card = os.path.join(tmp, "card.md")
        open(card, "w", encoding="utf-8").write(
            "source: ensembl_biomart\nchanged: provider fixed its count endpoint; counts match again "
            "(ours 3 == source 3, failing runs F8-a/F8-b cleared)\n")
        p_ok = readmit(st9, cfg9, "ensembl_biomart", card)
        q2 = contract.load_sources(cfg9)["ensembl_biomart"].get("quarantined")
        p4 = loop(st9, cfg9, "--run-id", "F9-after")
        rec4 = record_of(st9, "F9-after")
        ok4 = (rec4["sources"]["ensembl_biomart"].get("verify") or {}).get("ok")
        check("F9 readmit -> WITH the card the quarantine clears and the source RUNS again",
              p_ok.returncode == 0 and q2 is None and p4.returncode == 0 and ok4 is True,
              f"readmit_exit={p_ok.returncode} quarantined={q2} run_exit={p4.returncode} "
              f"verify_ok={ok4}")

        # F10 a quarantine with no reason is REFUSED by name (T9) ----------------------
        cfg10 = write_config(os.path.join(tmp, "f10.yml"), base_sources(host))
        s = contract.load_sources(cfg10)
        s["mgi"]["quarantined"] = {"reason": "", "at": "2026-11-10T00:00:00+00:00",
                                  "status": "source-gone"}
        write_config(cfg10, s)
        p = loop(os.path.join(tmp, "f10.store"), cfg10, "--run-id", "F10")
        check("F10 no-reason -> a quarantine with no reason is REFUSED BY NAME, exit 2",
              p.returncode == 2 and "mgi" in p.stdout and "['reason']" in p.stdout
              and "REFUSED[config]" in p.stdout,
              f"exit={p.returncode} out={p.stdout.strip().splitlines()[-1][:90]}")

        # F11 the rate budget exceeded TWICE -> automatic quarantine (T9) --------------
        cfg11 = write_config(os.path.join(tmp, "f11.yml"), base_sources(host))
        s = contract.load_sources(cfg11)
        s["ensembl_biomart"]["budget"] = {"requests": 2, "bytes": 4000000, "wall_s": 90}
        write_config(cfg11, s)
        st11 = os.path.join(tmp, "f11.store")
        pa = loop(st11, cfg11, "--run-id", "F11-a")
        qa = contract.load_sources(cfg11)["ensembl_biomart"].get("quarantined")
        pb = loop(st11, cfg11, "--run-id", "F11-b")
        rb = (record_of(st11, "F11-b")["refusals"] or [{}])[0]
        qb = contract.load_sources(cfg11)["ensembl_biomart"].get("quarantined") or {}
        pc = loop(st11, cfg11, "--run-id", "F11-c")
        ec = record_of(st11, "F11-c")["sources"]["ensembl_biomart"]
        check("F11 budget -> the 2nd overage QUARANTINES with both runs as evidence; then requests=0",
              pa.returncode == 3 and not qa and pb.returncode == 3
              and rb.get("code") == "budget" and qb.get("status") == "budget"
              and (rb.get("evidence") or []) == ["F11-a", "F11-b"]
              and (ec.get("cost") or {}).get("requests") == 0
              and "skipped: quarantined since" in pc.stdout,
              f"exits={pa.returncode},{pb.returncode},{pc.returncode} "
              f"quarantined_after_run1={bool(qa)} status={qb.get('status')} "
              f"evidence={rb.get('evidence')} requests_run3={(ec.get('cost') or {}).get('requests')}")

        # F12 source_health reports quarantined with the reason and the date (T9) -------
        # (--store + --json names the FIXTURE store this selftest just built — the dev tree's
        #  default store must never be a test's hidden dependency; the quarantine state is read
        #  from cfg4's sources.yml, the ok-ness from st9's own successful F9-after run)
        h = subprocess.run([sys.executable, "-m", "symbiont.health", "--sources-config", cfg4,
                            "--store", st9, "--json"],
                           capture_output=True, text=True, cwd=FARM)
        try:
            hrec = json.loads(h.stdout)
            mgi_h = next(r for r in hrec["sources"] if r["id"] == "mgi")
            ens_h = next(r for r in hrec["sources"] if r["id"] == "ensembl_biomart")
            hdetail = (f"mgi: state={mgi_h.get('state')} reason={str(mgi_h.get('reason'))[:60]} · "
                      f"ensembl: state={ens_h.get('state')}")
        except Exception as e:
            mgi_h, ens_h, hdetail = {}, {}, f"unparseable health output: {e}: {h.stdout[:70]}"
        check("F12 health -> source_health reports quarantined WITH the reason and the date",
              h.returncode == 0 and mgi_h.get("state") == "quarantined"
              and "quarantined since" in (mgi_h.get("reason") or "")
              and "source-gone" in (mgi_h.get("reason") or "")
              and ens_h.get("state") == "ok",
              f"exit={h.returncode} {hdetail}")

        # F13 POINT-AT — an endpoint whose class is point-at writes NO payload (req-P1-POINT-AT-ENFORCED-IN-CODE) --
        cfg13 = write_config(os.path.join(tmp, "f13.yml"), base_sources(host))
        s = contract.load_sources(cfg13)
        s["mgi"]["storage"]["mrk_list1_gz"] = {
            "class": "point-at",
            "reason": "bulk above a declared size (fixture declaration: the marker list is bulk this store points at)"}
        write_config(cfg13, s)
        st13 = os.path.join(tmp, "f13.store")
        p = loop(st13, cfg13, "--since", "none", "--run-id", "F13")
        rec = record_of(st13, "F13")
        mstate = json.load(open(os.path.join(st13, "state", "mgi.json"), encoding="utf-8"))
        release = mstate["release"]
        pa_snap = mstate["snapshots"]["mrk_list1_gz"]
        raw_dir13 = os.path.join(st13, "raw", "mgi", release)
        raw_files = sorted(f for f in os.listdir(raw_dir13) if not f.endswith(".meta.json"))
        raw_shas = {hashlib.sha256(open(os.path.join(raw_dir13, f), "rb").read()).hexdigest()
                    for f in raw_files}
        pa_path = os.path.join(st13, "pointat", "mgi", release, "mrk_list1_gz.json")
        pa = json.load(open(pa_path, encoding="utf-8"))
        fetched_sha = rec["sources"]["mgi"]["fetch"]["resources"]["mrk_list1_gz"]["sha256"]
        rec_pa = (rec["sources"]["mgi"].get("pointat") or {}).get("mrk_list1_gz") or {}
        check("F13 point-at -> NO payload in raw/, accession + recipe hash recorded instead",
              p.returncode == 0 and pa_snap.get("pointat") is True and "path" not in pa_snap
              and len(raw_files) == 1 and fetched_sha not in raw_shas
              and pa["accession"] == f"mgi/{release}/mrk_list1_gz"
              and pa["recipe"]["url"] == f"{host}/reports/MRK_List1.rpt.gz"
              and pa["recipe_sha256"] == contract.recipe_hash(pa["recipe"])
              and rec_pa.get("accession") == pa["accession"]
              and "POINT-AT" in p.stdout and "REFUSED a raw copy" in p.stdout,
              f"exit={p.returncode} raw_payloads={len(raw_files)} "
              f"payload_in_raw={fetched_sha[:12] in {s[:12] for s in raw_shas}} "
              f"accession={pa.get('accession')} recipe_sha256={pa.get('recipe_sha256', '')[:12]}")

        # F13 reuse — run 2 re-fetches the point-at resource ON DEMAND from the recorded recipe -----
        p2 = loop(st13, cfg13, "--since", "auto", "--run-id", "F13-b")
        rec2 = record_of(st13, "F13-b")
        ref = ((rec2["sources"]["mgi"].get("pointat_refetched") or {}).get("mrk_list1_gz") or {})
        new2 = {s_: rec2["sources"][s_]["fetch"]["new"] for s_ in rec2["sources"]
                if "fetch" in rec2["sources"][s_]}
        check("F13 reuse   -> run 2 re-fetches the point-at resource ON DEMAND, nothing else moves",
              p2.returncode == 0 and not any(new2.values())
              and "RE-FETCHED ON DEMAND" in p2.stdout
              and ref.get("sha256") == fetched_sha and ref.get("bytes") == pa["payload_bytes"],
              f"exit={p2.returncode} fetch_new={new2} refetched_sha={str(ref.get('sha256'))[:12]} "
              f"refetched_bytes={ref.get('bytes')} requests={rec2['cost']['requests']}")

        # F14 STORE — an endpoint whose class is store writes the payload, as always ------------------
        cfg14 = write_config(os.path.join(tmp, "f14.yml"), base_sources(host))
        st14 = os.path.join(tmp, "f14.store")
        p = loop(st14, cfg14, "--since", "none", "--run-id", "F14")
        mstate14 = json.load(open(os.path.join(st14, "state", "mgi.json"), encoding="utf-8"))
        raw14 = os.path.join(st14, "raw", "mgi", mstate14["release"])
        payloads14 = sorted(f for f in os.listdir(raw14) if not f.endswith(".meta.json"))
        check("F14 store   -> the payload IS written into raw/, and no point-at record exists",
              p.returncode == 0 and len(payloads14) == 2
              and all(s.get("pointat") is None and "path" in s
                      for s in mstate14["snapshots"].values())
              and not os.path.isdir(os.path.join(st14, "pointat"))
              and "POINT-AT" not in p.stdout,
              f"exit={p.returncode} raw_payloads={len(payloads14)} "
              f"snapshots={sorted(mstate14['snapshots'])}")

        # F15 NO CLASS — a source whose class is missing is REFUSED BY NAME at load ------------------
        # F15a deletes the whole storage block; the contract's own emitter is TOTAL over the schema
        # and cannot express a missing key, so this config is written with plain yaml — that
        # inability is itself the guarantee being tested.
        import yaml
        cfg15 = os.path.join(tmp, "f15.yml")
        write_config(cfg15, base_sources(host))
        with open(cfg15, encoding="utf-8") as fh:
            raw15 = yaml.safe_load(fh)
        mg15 = next(c for c in raw15["sources"] if c["id"] == "mgi")
        del mg15["storage"]
        with open(cfg15, "w", encoding="utf-8") as fh:
            yaml.safe_dump(raw15, fh, sort_keys=False)
        p = loop(os.path.join(tmp, "f15.store"), cfg15, "--since", "none", "--run-id", "F15a")
        check("F15a no class -> a source with no storage block is REFUSED BY NAME, exit 2",
              p.returncode == 2 and "'mgi'" in p.stdout and "REFUSED[config]" in p.stdout
              and "storage" in p.stdout,
              f"exit={p.returncode} out={p.stdout.strip().splitlines()[-1][:96]}")
        cfg15b = write_config(os.path.join(tmp, "f15b.yml"), base_sources(host))
        s = contract.load_sources(cfg15b)
        del s["mgi"]["storage"]["mrk_list1_gz"]
        write_config(cfg15b, s)
        p = loop(os.path.join(tmp, "f15b.store"), cfg15b, "--since", "none", "--run-id", "F15b")
        check("F15b one endpoint without a class -> REFUSED BY NAME, never defaulted to store",
              p.returncode == 2 and "'mgi'" in p.stdout and "mrk_list1_gz" in p.stdout
              and "REFUSED[config]" in p.stdout and "never defaulted to store" in p.stdout,
              f"exit={p.returncode} out={p.stdout.strip().splitlines()[-1][:96]}")

        # R RESOLVE — the door answers a point-at accession by fetching ON DEMAND ---------------------
        r = subprocess.run([sys.executable, "-m", "symbiont.resolve", pa["accession"],
                            "--store", st13, "--sources-config", cfg13],
                           capture_output=True, text=True, cwd=FARM)
        try:
            answer = json.loads([l for l in r.stdout.splitlines()
                                 if l.startswith("ANSWER ")][-1][7:])
        except Exception:
            answer = {}
        expected_bytes = os.path.getsize(os.path.join(tree, "reports", "MRK_List1.rpt.gz"))
        check("R  resolve  -> fetched_now: true + the bytes fetched + the cost, in the answer",
              r.returncode == 0 and answer.get("fetched_now") is True
              and answer.get("bytes") == expected_bytes
              and answer.get("recipe_sha256") == pa["recipe_sha256"]
              and (answer.get("cost") or {}).get("requests") == 1
              and answer.get("matches_recorded_payload") is True,
              f"exit={r.returncode} fetched_now={answer.get('fetched_now')} "
              f"bytes={answer.get('bytes')} (expected {expected_bytes}) "
              f"requests={(answer.get('cost') or {}).get('requests')}")
        rb = subprocess.run([sys.executable, "-m", "symbiont.resolve", "mgi/no-such-release/x",
                             "--store", st13, "--sources-config", cfg13],
                            capture_output=True, text=True, cwd=FARM)
        check("R  resolve  -> an accession with no point-at record is REFUSED BY NAME, exit 2",
              rb.returncode == 2 and "REFUSED[no-such-accession]" in rb.stdout,
              f"exit={rb.returncode} out={rb.stdout.strip().splitlines()[-1][:96]}")
    finally:
        srv.shutdown()
        if keep:
            print(f"  (kept {tmp})")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    bad = [r for r in results if not r[1]]
    print(f"SELFTEST {'PASS' if not bad else 'FAIL'}: {len(results) - len(bad)}/{len(results)} "
          f"fixtures held — sources.yml is the one definition site, and the four refusals refuse",
          flush=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
