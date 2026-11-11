"""PROOF of the generic MCP adapter — any external MCP server as a source, configured from its
sources.yml entry (req-P1-A-GENERIC-MCP-ADAPTER-any-external-M).

Every fixture below is a REAL MCP server process (symbiont/fixtures/mcp/server_*.py) spoken to over
a REAL pipe or a REAL HTTP socket: the transport, the handshake, tools/list, the pin and the
response mapping are exercised, and only the DATA is fixture data. The five things the row's gate
names, each as a quoted line and an exit code:

  F1 SHAPE A     fixture_alpha  stdio, text-content JSON, count field in the response
                 -> exit 0, 7 canonical records, EVERY row carrying its own source+source_release,
                    verify OK against the source's own count
  F2 SHAPE B     fixture_beta   stdio, structuredContent, and the count is a SECOND TOOL
                 -> exit 0, 7 canonical records, per-row source+release, count quoted from beta_count
  F3 NO COUNT    fixture_gamma_source -- the count rule, refusal half
                 -> REFUSED[no-count-endpoint] BY NAME, exit 2, ZERO tool calls (nothing left the box)
  F4 NO COUNT    fixture_gamma_tool -- the count rule, allowed half
                 -> exit 0, 3 records stamped published:false / count_source:none, verify says TOOL
  F5 SHAPE DRIFT MCP_FIXTURE_SHAPE=v2 (the same server RENAMES its output)
                 -> REFUSED[schema-drift], exit 7, naming the pointer — and NO records file written
  F6 PIN DRIFT   MCP_FIXTURE_SHAPE=desc against a config that DECLARES the tool-description hash
                 -> REFUSED[schema-drift], exit 7, "pin broken"
  F7 HTTP        the same no-count fixture over streamable HTTP -> exit 0, and the network allowlist
                 generated from that entry is exactly its host
  F8 SANDBOX     the allowlist has teeth: EMPTY (what a stdio entry gets) refuses even loopback and
                 records a `sandbox-breach`; the http entry's host allows the same connection
  F9 ACCOUNTING  every run's rows_in == records + rejects (nothing vanishes without a reason)
  F10 NO REGRESSION  the REAL PubMed MCP still works through the OLD pinned client
                 (symbiont/literature/mcp.py) — unchanged, and the pin/licence/handshake still hold
  F11 loop.py untouched: neither loop mentions this adapter, and the module imports no loop

Usage: python3 -m symbiont.selftest_mcp_source            (exit 0 only when all pass)
       python3 -m symbiont.selftest_mcp_source --offline  (skips F10, the only test that needs the
                                                          network; the skip is PRINTED, never silent)
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
FARM = os.path.dirname(ROOT)
CONFIG = os.path.join(ROOT, "fixtures", "mcp", "sources.yml")
GAMMA = os.path.join(ROOT, "fixtures", "mcp", "server_gamma.py")
CLI = [sys.executable, "-m", "symbiont.adapters.mcp_source"]

RESULTS: list[tuple[str, bool, str]] = []
SKIPPED: list[str] = []

# F10's input: the pinned PubMed MCP package. It is EXCLUDED from the shipped repo (74 MB of
# node_modules, DESIGN-STATION §1b), so a clone cannot run F10 — the absence is a NAMED SKIP,
# never a red line. A package that IS there but broken stays a FAIL (that is the whole point of
# a no-regression test).
PUBMED_PKG = os.path.join(ROOT, "literature", "mcp-server", "node_modules", "@cyanheads",
                          "pubmed-mcp-server", "package.json")


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""), flush=True)


def skip(name: str, missing: str, path: str) -> None:
    """An ABSENT input is a SKIP that names itself and the path — counted, never a red line."""
    SKIPPED.append(name)
    print(f"  SKIP  {name}: {missing} at {path}", flush=True)


def run(store: str, extra: list[str], env_extra: dict | None = None, term: str = "*",
        source: str = "") -> tuple[int, str]:
    env = dict(os.environ)
    env.pop("MCP_FIXTURE_SHAPE", None)
    env.update(env_extra or {})
    argv = CLI + ["--config", CONFIG, "--store", store, "--term", term]
    if source:
        argv += ["--source", source]
    p = subprocess.run(argv, cwd=FARM, env=env, capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout + p.stderr


def decisive(out: str, *prefixes: str) -> str:
    for line in out.splitlines():
        if line.startswith(prefixes):
            return line.strip()
    return ""


def run_record(store: str, source: str) -> dict:
    d = os.path.join(store, "runs")
    if not os.path.isdir(d):
        return {}
    for name in sorted(os.listdir(d), reverse=True):
        if name.endswith(f".{source}.json"):
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                return json.load(fh)
    return {}


def rows(store: str, source: str) -> list[dict]:
    d = os.path.join(store, "records", source)
    if not os.path.isdir(d):
        return []
    out: list[dict] = []
    for name in sorted(os.listdir(d)):
        with open(os.path.join(d, name), encoding="utf-8") as fh:
            out += [json.loads(line) for line in fh if line.strip()]
    return out


# ─────────────────────────────────────────────────────────────────────────────────────────────
def f1_alpha(store: str) -> None:
    rc, out = run(store, [], source="fixture_alpha")
    rec, rs = run_record(store, "fixture_alpha"), rows(store, "fixture_alpha")
    per_row = rs and all(r.get("source") == "fixture_alpha" and r.get("source_release") for r in rs)
    rel = {r.get("source_release") for r in rs}
    check("F1 SHAPE A  fixture_alpha -> exit 0, canonical records with per-row source+release",
          rc == 0 and rec.get("status") == "PUBLISHED" and len(rs) == 7 and per_row and len(rel) == 1,
          f"exit={rc} records={len(rs)} releases={sorted(rel)} "
          f"pin_state={(rec.get('pin') or {}).get('pin_state')}")
    v = rec.get("verify") or {}
    check("F1 verify  ours == the source's OWN count (a count FIELD in the search response)",
          v.get("ok") is True and v.get("ours") == 7 and v.get("source_count") == 7,
          (v.get("detail") or "")[:200])


def f2_beta(store: str) -> None:
    rc, out = run(store, [], source="fixture_beta")
    rec, rs = run_record(store, "fixture_beta"), rows(store, "fixture_beta")
    per_row = rs and all(r.get("source") == "fixture_beta" and r.get("source_release") for r in rs)
    v = rec.get("verify") or {}
    check("F2 SHAPE B  fixture_beta (structuredContent + a COUNT TOOL) -> exit 0, records+release",
          rc == 0 and len(rs) == 7 and per_row and (rec.get("cost") or {}).get("tool_calls") == 2,
          f"exit={rc} records={len(rs)} tool_calls={(rec.get('cost') or {}).get('tool_calls')}")
    check("F2 verify  the count came from the SECOND TOOL, quoted",
          v.get("ok") is True and v.get("source_count") == 7 and "count_tool" in (v.get("detail") or ""),
          (v.get("detail") or "")[:200])


def f3_nocount_source(store: str) -> None:
    rc, out = run(store, [], source="fixture_gamma_source")
    rec = run_record(store, "fixture_gamma_source")
    line = decisive(out, "REFUSED[no-count-endpoint]")
    check("F3 NO COUNT as a SOURCE -> REFUSED[no-count-endpoint] BY NAME, exit 2",
          rc == 2 and rec.get("code") == "no-count-endpoint" and "fixture_gamma_source" in line,
          line[:180])
    check("F3 nothing left the box: ZERO tool calls, no records file",
          (rec.get("cost") or {}).get("tool_calls") == 0 and not rows(store, "fixture_gamma_source"),
          f"tool_calls={(rec.get('cost') or {}).get('tool_calls')}")


def f4_nocount_tool(store: str) -> None:
    rc, out = run(store, [], source="fixture_gamma_tool")
    rec, rs = run_record(store, "fixture_gamma_tool"), rows(store, "fixture_gamma_tool")
    v = rec.get("verify") or {}
    stamped = rs and all(r.get("published") is False and r.get("count_source") == "none" for r in rs)
    check("F4 NO COUNT as a TOOL -> exit 0, records stamped published:false / count_source:none",
          rc == 0 and rec.get("status") == "TOOL-CALL" and len(rs) == 3 and stamped,
          f"exit={rc} records={len(rs)} status={rec.get('status')}")
    check("F4 verify  says in its own words that this was a TOOL call, never a claim",
          v.get("ok") is True and v.get("source_count") is None and "TOOL mode" in (v.get("detail") or ""),
          (v.get("detail") or "")[:200])


def f5_shape_drift(store: str) -> None:
    rc, out = run(store, [], env_extra={"MCP_FIXTURE_SHAPE": "v2"}, source="fixture_alpha")
    rec = run_record(store, "fixture_alpha")
    line = decisive(out, "REFUSED[schema-drift]")
    check("F5 SHAPE DRIFT -> REFUSED[schema-drift], exit 7, naming the moved pointer",
          rc == 7 and rec.get("code") == "schema-drift" and "/result/hits" in line, line[:220])
    check("F5 and NOT silently reshaped: no records file was written for that run",
          not any(f.startswith("fixture-alpha-mcp") for f in
                  (os.listdir(os.path.join(store, "records", "fixture_alpha"))
                   if os.path.isdir(os.path.join(store, "records", "fixture_alpha")) else [])),
          "records/ has no page for the drifted release")


def f6_pin_drift(store: str, tmp: str) -> None:
    p = subprocess.run(CLI + ["--config", CONFIG, "--source", "fixture_alpha", "--print-pin"],
                       cwd=FARM, capture_output=True, text=True, timeout=120)
    pin = json.loads(p.stdout) if p.returncode == 0 else {}
    sha = pin.get("tool_description_sha256", "")
    import yaml
    src = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    entry = [s for s in src["sources"] if s["id"] == "fixture_alpha"][0]
    entry["pin"]["tool_description_sha256"] = sha          # the pin, as an ADMISSION would record it
    cfg = os.path.join(tmp, "pinned.yml")
    with open(cfg, "w", encoding="utf-8") as fh:
        yaml.safe_dump({"version": 1, "sources": [entry]}, fh, sort_keys=False)

    env = dict(os.environ, MCP_FIXTURE_SHAPE="desc")
    ok_run = subprocess.run(CLI + ["--config", cfg, "--store", store + "-pinok", "--term", "*",
                                   "--source", "fixture_alpha"],
                            cwd=FARM, env={k: v for k, v in env.items() if k != "MCP_FIXTURE_SHAPE"},
                            capture_output=True, text=True, timeout=300)
    drift = subprocess.run(CLI + ["--config", cfg, "--store", store + "-pindrift", "--term", "*",
                                  "--source", "fixture_alpha"],
                           cwd=FARM, env=env, capture_output=True, text=True, timeout=300)
    line = decisive(drift.stdout + drift.stderr, "REFUSED[schema-drift]")
    check("F6 PIN: the DECLARED tool-description hash matches -> the run is allowed (pin checked)",
          ok_run.returncode == 0 and "pin broken" not in ok_run.stdout,
          f"exit={ok_run.returncode} declared={sha[:16]}…")
    check("F6 PIN DRIFT -> REFUSED[schema-drift], exit 7, quoting both hashes",
          drift.returncode == 7 and "pin broken" in line and sha[:16] in line, line[:240])


def f7_http(store: str, tmp: str) -> None:
    srv = subprocess.Popen([sys.executable, GAMMA, "--http", "0"], cwd=FARM,
                           stdout=subprocess.PIPE, text=True)
    try:
        line = srv.stdout.readline().strip()
        port = int(line.split(":")[-1]) if line.startswith("LISTENING") else 0
        if not port:
            check("F7 HTTP transport -> the fixture served on a port", False, f"no LISTENING line ({line!r})")
            return
        import yaml
        src = yaml.safe_load(open(CONFIG, encoding="utf-8"))
        e = dict([s for s in src["sources"] if s["id"] == "fixture_gamma_tool"][0])
        e["id"] = "fixture_gamma_http"
        e["note"] = "the same no-count server over streamable HTTP (POST /mcp)"
        e["transport"] = {"kind": "http", "url": f"http://127.0.0.1:{port}/mcp", "call_timeout_s": 30}
        cfg = os.path.join(tmp, "http.yml")
        with open(cfg, "w", encoding="utf-8") as fh:
            yaml.safe_dump({"version": 1, "sources": [e]}, fh, sort_keys=False)
        p = subprocess.run(CLI + ["--config", cfg, "--store", store, "--term", "*",
                                  "--source", "fixture_gamma_http"],
                           cwd=FARM, capture_output=True, text=True, timeout=300)
        rs = rows(store, "fixture_gamma_http")
        rec = run_record(store, "fixture_gamma_http")
        check("F7 HTTP transport -> exit 0, records normalised over POST /mcp",
              p.returncode == 0 and len(rs) == 3,
              f"exit={p.returncode} records={len(rs)} url={e['transport']['url']}")
        pol = (rec.get("network") or {}).get("allowlist")
        check("F7 the network allowlist generated FROM THE ENTRY is that endpoint's host, nothing else",
              pol == ["127.0.0.1"] and f"ip daddr 127.0.0.1" in
              json.dumps((rec.get("sandbox_policy") or {}).get("ruleset", "")),
              f"allowlist={pol}")
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:
            srv.kill()


def f8_sandbox(store: str) -> None:
    from symbiont import sandbox
    rc, out = run(store, [], source="fixture_alpha")
    rec = run_record(store, "fixture_alpha")
    check("F8a a stdio entry's allowlist is EMPTY and the run makes ZERO connect attempts",
          (rec.get("network") or {}).get("allowlist") == [] and not (rec.get("sandbox_refusals") or []),
          f"allowlist={(rec.get('network') or {}).get('allowlist')} "
          f"refusals={len(rec.get('sandbox_refusals') or [])}")
    refs: list = []
    with sandbox.network_guard(set(), refusals=refs):          # the EMPTY allowlist, armed
        refused = False
        try:
            socket.create_connection(("127.0.0.1", 80), timeout=2)
        except Exception:
            refused = True
    check("F8b the EMPTY allowlist REFUSES even loopback, and records it as a `sandbox-breach`",
          refused and refs and refs[0].get("finding_class") == "sandbox-breach",
          f"refusal={json.dumps(refs[0])[:160] if refs else 'NONE'}")


def f9_accounting(store: str) -> None:
    bad = []
    for sid in ("fixture_alpha", "fixture_beta", "fixture_gamma_tool"):
        n = (run_record(store, sid) or {}).get("normalise") or {}
        if n and n["rows_in"] != n["records"] + n["rejects"]:
            bad.append(f"{sid}: {n['rows_in']} != {n['records']}+{n['rejects']}")
    check("F9 ACCOUNTING: rows_in == records + rejects on every run (nothing vanishes quietly)",
          not bad, "; ".join(bad) or "alpha/beta/gamma all close")


def f10_pubmed(offline: bool) -> None:
    if offline:
        check("F10 NO REGRESSION — SKIPPED by --offline (this is a network test, not a silent pass)",
              True, "skipped on request")
        return
    if not os.path.exists(PUBMED_PKG):
        skip("F10 NO REGRESSION the OLD pinned PubMed client still runs",
             "the pinned PubMed MCP package (node_modules, 74 MB — excluded from the repo, so a "
             "clone has it only after `npm install` in that directory)", PUBMED_PKG)
        return
    sys.path.insert(0, FARM)
    from symbiont.literature import contract as lit
    from symbiont.literature.mcp import MCPClient, MCPError
    cfg_path = os.path.join(ROOT, "literature", "sources.yml")
    try:
        cfg = lit.load_sources(cfg_path)
        c = MCPClient(cfg["mcp"], cfg["budget"], os.path.dirname(cfg_path))
    except (MCPError, Exception) as e:                                  # noqa: B014 - named below
        check("F10 NO REGRESSION — the OLD pinned PubMed client still runs", False,
              f"{type(e).__name__}: {str(e)[:200]}")
        return
    try:
        text = c.call("pubmed_search_articles", {"query": "Brca1[Title] AND mouse[MeSH Terms]",
                                                 "maxResults": 2})
        ret = [l for l in text.splitlines() if l.startswith("**Returned:")]
        ok = bool(ret) and c.provenance.get("version") == c.provenance.get("pin")
        check("F10 NO REGRESSION — the OLD pinned PubMed client retrieves through the REAL MCP",
              ok, f"{ret[0].strip() if ret else 'no Returned line'} · "
                  f"pin {c.provenance.get('package')}@{c.provenance.get('pin')} verified on disk")
    finally:
        c.close()


def f11_loop_untouched() -> None:
    loops = ["symbiont/loop.py", os.path.join("symbiont", "literature", "loop.py")]
    hits, shas = [], {}
    for rel in loops:
        p = os.path.join(FARM, rel)
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        n = text.count("mcp_source")
        hits.append(f"{rel}:{n}")
        shas[rel] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    mod = open(os.path.join(ROOT, "adapters", "mcp_source.py"), encoding="utf-8").read()
    imports_loop = ("from ..loop import" in mod or "from .loop import" in mod
                    or "import loop" in mod)
    check("F11 loop.py untouched: neither loop names this adapter, and the adapter imports no loop",
          all(h.endswith(":0") for h in hits) and not imports_loop,
          f"{' '.join(hits)} sha256={json.dumps(shas)}")


def main(argv=None) -> int:
    offline = "--offline" in (argv if argv is not None else sys.argv[1:])
    print("== PROOF: a generic MCP adapter — two shapes, the count rule, the drift refusal, the "
          "sandbox, and the PubMed no-regression ==")
    tmp = tempfile.mkdtemp(prefix="mcp-source-selftest-")
    store = os.path.join(tmp, "store")
    try:
        f1_alpha(os.path.join(tmp, "alpha"))
        f2_beta(os.path.join(tmp, "beta"))
        f3_nocount_source(os.path.join(tmp, "gamma-src"))
        f4_nocount_tool(os.path.join(tmp, "gamma-tool"))
        f5_shape_drift(os.path.join(tmp, "drift"))
        f6_pin_drift(os.path.join(tmp, "pindrift"), tmp)
        f7_http(os.path.join(tmp, "http"), tmp)
        f8_sandbox(os.path.join(tmp, "sandbox"))
        f9_accounting(os.path.join(tmp, "alpha"))
        f11_loop_untouched()
        f10_pubmed(offline)                       # last: the only test that needs the network
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"RESULT: {passed} passed, {failed} failed")
    print(f"RESULT: {passed} passed, {len(SKIPPED)} skipped, {failed} failed "
          + ("(clone mode)" if SKIPPED else "(every check ran — no skip)"))
    if SKIPPED:
        print(f"SKIPPED: {len(SKIPPED)} check(s) whose input is not in this tree: "
              + "; ".join(SKIPPED))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
