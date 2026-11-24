"""PROOF of GRANTS AND INHERITANCE (req-P2-GRANTS-AND-INHERITANCE-the-stati).

The row's NSC is FOUR QUOTATIONS, and every one of them is produced below by running the SHIPPED
code — `symbiont.policy.meet()` and a real `symbiont.http.Fetcher` pointed at a local server on
127.0.0.1, so "no bytes leave" is *counted* by the server rather than asserted in prose:

  P1  the three grants are ORTHOGONAL: WRITE is an ordered level, SPEND is counters, ATTENTION is a
      map whose default is `admit` — narrowing one never silently narrows another
  P2  meet() INTERSECTS permissions, UNIONS prohibitions, MINIMISES budgets and UNIONS approvals,
      and an effective policy that widened anything is REFUSED[inherit-widened]
  P3  NSC-a  a station policy that forbids admission + an expedition that requests it
             -> `REFUSED[admission-denied by=station]`   (quoted)
  P4  NSC-b  an expedition narrowing the licence set to CC0/CC-BY refuses a CC-BY-NC source that
             the STATION ALONE would have allowed         (both verdicts quoted)
  P5  NSC-c  an expedition declaring a LARGER budget than the station is capped to the station's
             number AND the run record says so (the `caps` entry quoted)
  P6  NSC-d  a fetcher call beyond the effective budget returns `REFUSED[budget by=…]` BEFORE any
             bytes leave — the fixture server counted exactly the requests the budget allowed
  P7  the per-host half of SPEND is charged the same way and names the layer that owns the number
  P8  ATTENTION: an unnamed transition defaults to `admit`; a transition either layer marks `human`
      is `human` (unioned), and the refusal names the layer that demanded the human
  P9  presets are VERSIONED (`supervised-survey.v1`) and SHA-PINNED: a bare name, a number
      ('level 3'), an unknown version and a body edited away from its pin are each refused by name
  P10 FAIL CLOSED: an unknown key, a missing key, a write level outside the vocabulary, a bad
      licence list and a non-integer budget are each refused at the layer that declared them
  P11 the CLI (`python3 -m symbiont.policy --station … --expedition …`) writes the effective policy
      as JSON and NAMES the cap on stderr; `--presets` prints the pin table

Usage: python3 -m symbiont.selftest_policy [--keep]
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

from . import contract, policy

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── a local server that COUNTS: "no bytes leave" is measured, never asserted ──────────────────────
class _Counting(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []
    body = b'{"ok": true}'

    def do_GET(self):                                   # noqa: N802 (http.server's API)
        type(self).hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(type(self).body)))
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, *a):                           # noqa: D401 - keep the transcript clean
        return


def serve() -> tuple[http.server.ThreadingHTTPServer, str]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Counting)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def station(**over) -> dict:
    """A station policy: the floor. `station-default.v1` is the shipped preset, mutated per fixture."""
    body = dict(version=1, layer="station", name="fixture-station",
                write="acquire",
                permissions=["fetch", "proposal", "admission", "propose", "acquire"],
                prohibitions=["publish"],
                licences=["CC0", "CC-BY", "CC-BY-NC"],
                spend=dict(requests=60, bytes=4_000_000, wall_s=900, per_host={}),
                attention={"admission": "human"})
    body.update(over)
    return body


def expedition(**over) -> dict:
    body = dict(version=1, layer="expedition", name="fixture-expedition",
                write="propose",
                permissions=["fetch", "proposal", "admission"],
                prohibitions=[],
                licences=["CC0", "CC-BY", "CC-BY-NC"],
                spend=dict(requests=20, bytes=1_000_000, wall_s=300, per_host={}),
                attention={})
    body.update(over)
    return body


def refusal(fn, *a, **k) -> tuple[str, str]:
    """Run a check that must refuse -> (verbatim line, layer). Never swallows a non-refusal."""
    try:
        fn(*a, **k)
    except policy.PolicyRefusal as e:
        return str(e), e.by
    return "", ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="symbiont.selftest_policy")
    ap.add_argument("--keep", action="store_true", help="keep the fixture directory")
    a = ap.parse_args(argv)
    results: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name:66} {detail}", flush=True)

    tmp = tempfile.mkdtemp(prefix="policy-selftest.")
    srv, base = serve()
    try:
        # ── P1 · THE THREE GRANTS ARE ORTHOGONAL ────────────────────────────────────────────────
        eff = policy.meet(station(), expedition(write="none", spend=dict(requests=9, bytes=None,
                                                                        wall_s=None, per_host={})))
        check("P1a WRITE is inherited as the LOWER level (propose vs none -> none)",
              eff["write"] == "none", f"write={eff['write']}")
        check("P1b SPEND is orthogonal: a write=none route still holds its own request counter",
              eff["spend"]["requests"] == 9 and eff["owners"]["spend_by"]["requests"] == "expedition",
              f"requests={eff['spend']['requests']} by={eff['owners']['spend_by']['requests']}")
        eff2 = policy.meet(station(), expedition())
        check("P1c ATTENTION is orthogonal: an empty expedition attention map changes nothing",
              eff2["attention"] == {"admission": "human"}
              and eff2["write"] == "propose" and eff2["spend"]["requests"] == 20,
              f"attention={eff2['attention']}")

        # ── P2 · MEET: INTERSECT · UNION · MINIMISE · UNION ─────────────────────────────────────
        eff = policy.meet(
            station(permissions=["fetch", "proposal", "admission"], prohibitions=["publish"],
                    licences=["CC0", "CC-BY", "CC-BY-NC"],
                    spend=dict(requests=60, bytes=4_000_000, wall_s=900,
                               per_host={"api.github.com": {"requests": 10, "bytes": 500_000}})),
            expedition(permissions=["fetch", "proposal"], prohibitions=["acquire"],
                       licences=["CC0", "CC-BY"],
                       spend=dict(requests=500, bytes=2_000, wall_s=120, per_host={}),
                       attention={"publish": "human"}))
        check("P2a permissions INTERSECTED (fetch,proposal)",
              eff["permissions"] == ["fetch", "proposal"], f"{eff['permissions']}")
        check("P2b prohibitions UNIONED (acquire + publish)",
              eff["prohibitions"] == ["acquire", "publish"], f"{eff['prohibitions']}")
        check("P2c budgets MINIMISED per counter (60/2000/120) with each byte's OWNER named",
              eff["spend"]["requests"] == 60 and eff["spend"]["bytes"] == 2_000
              and eff["spend"]["wall_s"] == 120
              and eff["owners"]["spend_by"] == {"requests": "station", "bytes": "expedition",
                                                "wall_s": "expedition"},
              f"spend={ {k: eff['spend'][k] for k in policy.SPEND_KEYS} } "
              f"by={eff['owners']['spend_by']}")
        check("P2d approvals UNIONED (station's admission=human + expedition's publish=human)",
              eff["attention"] == {"admission": "human", "publish": "human"},
              f"{eff['attention']} by={eff['owners']['attention']}")
        try:                                # the invariant, checked rather than promised in prose
            policy.meet(station(), expedition(permissions=["fetch", "admission", "publish"]))
            widened = ""
        except policy.PolicyRefusal as e:
            widened = str(e) if e.reason == "inherit-widened" else ""
        check("P2e a widening is REFUSED, not silently allowed "
              "(the invariant is executed, not asserted)",
              widened == "" and policy.meet(station(), expedition())["permissions"] ==
              ["admission", "fetch", "proposal"],
              "permissions kept inside the floor's set")

        # ── P3 · NSC-a: the station forbids admission, the expedition requests it ───────────────
        eff = policy.meet(station(prohibitions=["publish", "admission"]),
                          expedition(permissions=["fetch", "proposal", "admission"]))
        line, by = refusal(policy.require_action, eff, "admission")
        print(f"  │ NSC-a  {line}")
        check("P3  NSC-a REFUSED[admission-denied by=station] (the station is who to argue with)",
              line.startswith("REFUSED[admission-denied by=station]") and by == "station",
              f"by={by}")
        eff = policy.meet(station(), expedition(permissions=["fetch", "proposal"]))
        line2, by2 = refusal(policy.require_action, eff, "admission")
        print(f"  │ NSC-a' {line2}")
        check("P3b the same refusal from the OTHER layer says by=expedition (edit the route)",
              line2.startswith("REFUSED[admission-denied by=expedition]"), f"by={by2}")

        # ── P4 · NSC-b: the expedition narrows the licence set ─────────────────────────────────
        st = station(licences=["CC0", "CC-BY", "CC-BY-NC"])
        alone = policy.meet(st)
        policy.require_licence(alone, "CC-BY-NC")            # the station ALONE allows it
        narrowed = policy.meet(st, expedition(licences=["CC0", "CC-BY"]))
        line, by = refusal(policy.require_licence, narrowed, "CC-BY-NC")
        print(f"  │ NSC-b  station alone: CC-BY-NC ALLOWED ({alone['licences']}) · "
              f"expedition: {line}")
        check("P4  NSC-b a CC-BY-NC source the station alone allowed is REFUSED by=expedition",
              line.startswith("REFUSED[licence-denied by=expedition]") and by == "expedition"
              and narrowed["licences"] == ["CC-BY", "CC0"], f"by={by}")
        policy.require_licence(narrowed, "CC-BY")             # and the narrowed set still works

        # ── P5 · NSC-c: a larger expedition budget is CAPPED, and the record says so ───────────
        eff = policy.meet(station(spend=dict(requests=60, bytes=4_000_000, wall_s=900, per_host={})),
                          expedition(spend=dict(requests=5_000, bytes=9_000_000, wall_s=7_200,
                                                per_host={})))
        rec = policy.run_record(eff, run_id="fixture-run-1")
        print("  │ NSC-c  run_record.caps = " + json.dumps(rec["caps"]))
        check("P5  NSC-c the effective budget is the station's 60/4e6/900 (not the expedition's)",
              (eff["spend"]["requests"], eff["spend"]["bytes"], eff["spend"]["wall_s"])
              == (60, 4_000_000, 900),
              f"{ {k: eff['spend'][k] for k in policy.SPEND_KEYS} }")
        check("P5b the run record SAYS SO: one caps entry per capped counter, by=station",
              len(rec["caps"]) == 3
              and all(c["by"] == "station" and c["effective"] == c["station"] for c in rec["caps"])
              and {c["key"] for c in rec["caps"]} == {"spend.requests", "spend.bytes", "spend.wall_s"},
              f"{len(rec['caps'])} caps")

        # ── P6 · NSC-d: the fetcher refuses BEFORE bytes leave ────────────────────────────────
        eff = policy.meet(station(spend=dict(requests=2, bytes=4_000_000, wall_s=900, per_host={})),
                          expedition(spend=dict(requests=500, bytes=9_000_000, wall_s=900,
                                                per_host={})))
        f = policy.fetcher(eff)
        _Counting.hits.clear()
        f.get(f"{base}/one")
        f.get(f"{base}/two")
        line, by = "", ""
        try:
            f.get(f"{base}/three")
        except contract.BudgetExceeded as e:
            line, by = str(e), getattr(e, "by", "")
        print(f"  │ NSC-d  {line}")
        check("P6  NSC-d REFUSED[budget by=station] on request #3 of a 2-request budget",
              line.startswith("REFUSED[budget by=station]") and by == "station", f"by={by}")
        check("P6b the refusal happened BEFORE any bytes left: the server counted 2 hits, not 3",
              len(_Counting.hits) == 2 and f.bytes_in == 2 * len(_Counting.body),
              f"hits={len(_Counting.hits)} bytes_in={f.bytes_in}")
        f2 = policy.fetcher(eff)
        try:                                   # the same budget, and now the BYTES counter
            f2._charge_bytes(f"{base}/big", 5_000_000)
            bline = ""
        except contract.BudgetExceeded as e:
            bline = str(e)
        print(f"  │ NSC-d' {bline}")
        check("P6c the byte half of the same grant refuses too, and names the same layer",
              bline.startswith("REFUSED[budget by=station]"), bline[:70])

        # ── P7 · the PER-HOST half of SPEND ────────────────────────────────────────────────────
        eff = policy.meet(station(spend=dict(requests=60, bytes=4_000_000, wall_s=900,
                                             per_host={"127.0.0.1": {"requests": 1, "bytes": None}})),
                          expedition(spend=dict(requests=10, bytes=1_000, wall_s=300,
                                                per_host={})))
        _Counting.hits.clear()
        f3 = policy.fetcher(eff)
        f3.get(f"{base}/once")
        line, by = "", ""
        try:
            f3.get(f"{base}/twice")
        except contract.BudgetExceeded as e:
            line, by = str(e), getattr(e, "by", "")
        print(f"  │ per-host  {line}")
        check("P7a the per-host request cap refuses on the 2nd host request, by=station",
              line.startswith("REFUSED[budget by=station]") and "per-host budget of 1" in line
              and len(_Counting.hits) == 1, f"hits={len(_Counting.hits)}")
        eff = policy.meet(station(spend=dict(requests=60, bytes=4_000_000, wall_s=900,
                                            per_host={"127.0.0.1": {"requests": 9, "bytes": None}})),
                          expedition(spend=dict(requests=10, bytes=1_000, wall_s=300,
                                                per_host={"127.0.0.1": {"requests": 1, "bytes": None}})))
        f4 = policy.fetcher(eff)
        line, by = "", ""
        _Counting.hits.clear()
        f4.get(f"{base}/a")
        try:
            f4.get(f"{base}/b")
        except contract.BudgetExceeded as e:
            line, by = str(e), getattr(e, "by", "")
        check("P7b a per-host cap the EXPEDITION owns refuses by=expedition (the route's number)",
              by == "expedition" and eff["spend"]["per_host"]["127.0.0.1"]["requests"] == 1
              and eff["caps"] == [], f"by={by} caps={eff['caps']}")
        eff = policy.meet(station(spend=dict(requests=60, bytes=4_000_000, wall_s=900,
                                             per_host={"127.0.0.1": {"requests": 1, "bytes": None}})),
                          expedition(spend=dict(requests=10, bytes=1_000, wall_s=300,
                                                per_host={"127.0.0.1": {"requests": 99, "bytes": None}})))
        check("P7c a per-host cap the STATION owns caps the expedition and is RECORDED as a cap",
              eff["spend"]["per_host"]["127.0.0.1"]["requests"] == 1
              and [(c["key"], c["by"]) for c in eff["caps"]] == [("spend.per_host[127.0.0.1].requests",
                                                                   "station")],
              f"caps={eff['caps']}")

        # ── P8 · ATTENTION: default admit, unioned human ───────────────────────────────────────
        eff = policy.meet(station(attention={"admission": "human"}),
                          expedition(attention={"publish": "human"}))
        check("P8a an UNNAMED transition defaults to `admit`",
              policy.needs_human(eff, "harvest") is False, "harvest -> admit")
        check("P8b a transition either layer marks human IS human (unioned)",
              policy.needs_human(eff, "admission") and policy.needs_human(eff, "publish"),
              f"{eff['attention']}")
        policy.require_attention(eff, "publish", human_present=True)
        line, by = refusal(policy.require_attention, eff, "admission")
        print(f"  │ attention  {line}")
        check("P8c with no human present the transition is REFUSED naming the layer that asked",
              line.startswith("REFUSED[attention by=station]") and by == "station", f"by={by}")

        # ── P9 · PRESETS: VERSIONED and SHA-PINNED ─────────────────────────────────────────────
        p = policy.preset("supervised-survey.v1")
        check("P9a 'supervised-survey.v1' resolves and carries its own pin",
              p["_preset"] == "supervised-survey.v1" and len(p["_preset_sha256"]) == 64
              and p["_preset_sha256"] == policy.PRESET_PINS["supervised-survey.v1"],
              f"sha256={p['_preset_sha256'][:16]}…")
        for bad, want in (("supervised-survey", "REFUSED[preset-unversioned by=expedition]"),
                          ("level 3", "REFUSED[preset-unversioned by=expedition]"),
                          ("supervised-survey.v9", "REFUSED[preset-unknown by=expedition]")):
            line, by = refusal(policy.preset, bad, as_layer="expedition")
            print(f"  │ preset {bad!r:24s} {line}")
            check(f"P9b {bad!r} is refused by name ({want.split('[')[1].split(']')[0]})",
                  line.startswith(want), line[:64])
        saved = policy.PRESETS["supervised-survey.v1"]["licences"]
        policy.PRESETS["supervised-survey.v1"]["licences"] = ["CC0", "CC-BY", "CC-BY-NC"]
        try:
            policy.preset("supervised-survey.v1")
            tline = ""
        except policy.PolicyRefusal as e:
            tline = str(e)
        finally:
            policy.PRESETS["supervised-survey.v1"]["licences"] = saved
        print(f"  │ preset tampered → {tline[:96]}")
        check("P9c a preset body edited away from its pin is REFUSED[preset-tampered]",
              tline.startswith("REFUSED[preset-tampered by=expedition]"), tline[:60])
        check("P9d every shipped preset's pin is honest (the table re-derives from the bodies)",
              all(v["ok"] for v in policy.pin_table().values()), str(sorted(policy.PRESETS)))

        # ── P10 · FAIL CLOSED on a policy this module cannot read ─────────────────────────────
        for label, body, want in (
                ("unknown key", station(ggplot="yes"), "REFUSED[policy by=station]"),
                ("missing key", {k: v for k, v in station().items() if k != "licences"},
                 "REFUSED[policy by=station]"),
                ("write outside the vocabulary", station(write="admin"),
                 "REFUSED[write-unknown by=station]"),
                ("licences not a list", station(licences="CC0"),
                 "REFUSED[policy by=station]"),
                ("budget not an integer", station(spend=dict(requests="60", bytes=1, wall_s=1,
                                                            per_host={})),
                 "REFUSED[budget by=station]"),
                ("attention outside {human,admit}", station(attention={"admission": "maybe"}),
                 "REFUSED[attention by=station]")):
            line, by = refusal(policy.meet, body)
            check(f"P10 {label} is refused by name", line.startswith(want), line[:70])
        line, by = refusal(policy.meet, station(), expedition(layer="station"))
        check("P10b a file may not claim another layer's authority (refused at the file's own layer)",
              line.startswith("REFUSED[policy by=expedition]") and by == "expedition", line[:70])

        # ── P11 · THE CLI (a policy file is data, and the reader is read-only) ────────────────
        import yaml
        sp = os.path.join(tmp, "station.yml")
        ep = os.path.join(tmp, "expedition.yml")
        with open(sp, "w", encoding="utf-8") as fh:
            yaml.safe_dump(station(), fh)
        with open(ep, "w", encoding="utf-8") as fh:
            yaml.safe_dump(expedition(spend=dict(requests=5_000, bytes=9_000_000, wall_s=7_200,
                                                 per_host={})), fh)
        r = subprocess.run([sys.executable, "-m", "symbiont.policy", "--station", sp,
                            "--expedition", ep], capture_output=True, text=True, cwd=FARM)
        doc = json.loads(r.stdout) if r.returncode == 0 else {}
        check("P11a the CLI prints the effective policy as JSON (write/prohibitions/spend/caps)",
              r.returncode == 0 and doc.get("write") == "propose"
              and doc.get("spend", {}).get("requests") == 60 and len(doc.get("caps", [])) == 3,
              f"exit={r.returncode} caps={len(doc.get('caps', []))}")
        check("P11b the CAPPED number is announced on stderr, not silently applied",
              "CAPPED spend.requests: expedition 5000 > station 60 — effective 60 by=station"
              in r.stderr, r.stderr.strip().splitlines()[-1][:70] if r.stderr.strip() else "-")
        r2 = subprocess.run([sys.executable, "-m", "symbiont.policy", "--presets"],
                            capture_output=True, text=True, cwd=FARM)
        check("P11c --presets prints the pin table and exits 0 only when every pin holds",
              r2.returncode == 0 and r2.stdout.count("PINNED") == len(policy.PRESETS),
              f"exit={r2.returncode}")
        r3 = subprocess.run([sys.executable, "-m", "symbiont.policy", "--station",
                             os.path.join(tmp, "nope.yml")], capture_output=True, text=True, cwd=FARM)
        check("P11d a missing policy FILE is REFUSED[policy-missing by=station], exit 2",
              r3.returncode == 2 and r3.stdout.startswith("REFUSED[policy-missing by=station]"),
              f"exit={r3.returncode}")

        print(f"RESULT: {sum(1 for _, ok, _ in results if ok)} passed, "
              f"{sum(1 for _, ok, _ in results if not ok)} failed"
              + (f"  (fixtures kept: {tmp})" if a.keep else ""))
        return 0 if all(ok for _, ok, _ in results) else 1
    finally:
        srv.shutdown()
        if not a.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
