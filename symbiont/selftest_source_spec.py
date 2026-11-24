"""PROOF of THE EVALUATOR — the six checks, the four quotations, and the boundary it cannot cross.

run: python3 -m symbiont.selftest_source_spec

req-P1-THE-EVALUATOR-six-checks-in-code names four things this file has to SHOW, not say:

  Q1 THE REAL PORTALS   the AMR test's own candidates, with their real field values, evaluated:
                        the two that are sources PROPOSE (never admitted), and each licence is
                        recorded with WHERE it was read and the publisher's own words.
  Q2 A REPOSITORY       a repository-shaped candidate is REFUSED[no-rows] and
                        REFUSED[no-count-endpoint] BY NAME.
  Q3 THE HF COPY        an HF-style repackaging WITHOUT an authority line is
                        REFUSED[derived-without-authority].
  Q4 NO ADMISSION PATH  nothing in the evaluator can write an `admitted` event: the code path does
                        not exist (proved by an AST + text scan of the module, with a PLANTED writer
                        caught by the same scan so the check is shown able to fail), and ATTEMPTING
                        it refuses by name — `admit()` raises REFUSED[evaluator-may-not-admit] and a
                        candidate that arrives `admitted: true` is refused with the same class.

Everything is offline and in-memory: the evaluator does no I/O, so neither does this test (except
reading the module's own source and the fixture JSON, which are its subject).

THE HONEST NOTE ON Q1, stated in the test as it is stated in the report: the AMR test lists THREE
data portals and a fourth candidate. The two SOURCES propose; Africa CDC's MAAP is the GREY one,
and the machine refuses it by naming the three cells in the AMR test's own table that are not
green — no count a machine can reproduce ("yes at study level" = a number printed in a PDF), no
release of its own ("a study edition, not a release stream"), no machine schema ("no machine schema
(PDF)"). That refusal is the spec WORKING: its licence is still read, recorded and quoted, and a
person may still admit it. Making it PROPOSE would need a fabricated schema, which is the one thing
this row forbids.
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys

from . import source_spec as spec

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "spec", "amr-candidates.json")
MODULE = os.path.join(HERE, "source_spec.py")

results: list[tuple[str, bool]] = []


def say(s: str = "") -> None:
    print(s, flush=True)


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok)))
    say(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def fixture() -> dict:
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def cand(doc: dict, cid: str) -> dict:
    for c in doc["candidates"]:
        if c.get("id") == cid:
            return dict(c)
    raise AssertionError(f"fixture has no candidate {cid!r}")


def codes(v: dict) -> list[str]:
    return [r["code"] for r in v.get("refusals") or []]


# ── THE SCANNER (Q4) — text + AST, and it is shown able to FAIL ──────────────────────────────
# The station's one-writer test scans the tree for the diary's writer. This is the same idea for
# the evaluator's boundary: a module that may not record anything may not contain a write.
WRITE_MARKERS = ("open(", "os.write", ".write(", "json.dump", "shutil.copy", "Path(", "O_APPEND")
IMPORT_FORBIDDEN = ("diary", "events", "registry", "loop", "cycle", "cycle", "store", "subprocess",
                    "socket", "urllib", "http", "requests")
EVENT_NAME = "source-" + "events.jsonl"          # assembled: this file is not itself a hit


def scan_module(path: str) -> list[str]:
    """Every reason `path` is not an evaluator that writes, as a list of findings (empty = clean)."""
    findings: list[str] = []
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:                     # a module that does not parse cannot be cleared
        return [f"does not parse: {e}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[-1] in IMPORT_FORBIDDEN:
                    findings.append(f"imports {a.name} (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[-1]
            if mod in IMPORT_FORBIDDEN:
                findings.append(f"imports from {node.module} (line {node.lineno})")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "id", "?").lower()
            if name in ("open", "write", "dump", "copy", "copyfile"):
                findings.append(f"calls {name}() at line {node.lineno}")
        elif isinstance(node, ast.Name) and node.id == "__name__":
            findings.append(f"a __main__ guard at line {node.lineno} (a library, not a script)")
    for marker in WRITE_MARKERS:
        if marker in src:
            findings.append(f"text contains {marker!r}")
    if EVENT_NAME in src:
        findings.append(f"names {EVENT_NAME}")
    return findings


def main() -> int:
    say("SELFTEST — THE EVALUATOR (the six-point source spec, in code, refusing by name)")
    doc = fixture()
    now = doc.get("read_at") or ""

    # ── S1 · THE SPEC IS DATA, in the row's order ────────────────────────────────────────────
    say("\nS1 the six checks, as data, in the row's order and with the row's classes")
    expect = [("rows", "no-rows"), ("count", "no-count-endpoint"), ("release", "no-release"),
              ("licence", "licence-unreadable"), ("schema", "no-schema"),
              ("stable", "unstable-url")]
    got = [(f, c) for f, c, _q in spec.SIX_CHECKS]
    check("S1 six checks, in the row's order, each with its refusal class", got == expect,
          f"{got}")
    check("S1 the six are the whole of what a machine may judge",
          tuple(spec.MACHINE_MAY_JUDGE) == tuple(f for f, _c, _q in spec.SIX_CHECKS)
          and "science" not in spec.MACHINE_MAY_JUDGE
          and set(spec.MACHINE_MAY_NOT_JUDGE) == {"science", "permission"},
          f"may judge {spec.MACHINE_MAY_JUDGE} · may not {spec.MACHINE_MAY_NOT_JUDGE}")
    check("S1 the boundary is a CONSTANT, not a config read: admit.by_policy is False in the code",
          spec.ADMIT_BY_POLICY is False and spec.ADMITTED is False,
          f"ADMIT_BY_POLICY={spec.ADMIT_BY_POLICY!r} ADMITTED={spec.ADMITTED!r}")

    # ── Q1 · THE REAL PORTALS ───────────────────────────────────────────────────────────────
    say("\nQ1 THE REAL DATA PORTALS from the AMR test, with their real field values")
    who = spec.evaluate(cand(doc, "who-glass-amr"), evaluate_now=now)
    hf = spec.evaluate(cand(doc, "hf-africa-owid-glass"), evaluate_now=now)
    cdc = spec.evaluate(cand(doc, "africa-cdc-maap"), evaluate_now=now)
    for v in (who, hf, cdc):
        for ln in spec.lines(v):
            say("    " + ln)
    check("Q1 WHO GLASS is PROPOSED — and admitted is False, not 'proposed-but-admitted'",
          who["verdict"] == spec.PROPOSED and who["admitted"] is False
          and who["admit_by_policy"] is False, f"verdict={who['verdict']} admitted={who['admitted']}")
    check("Q1 the HF derived copy (WITH its authority line) is PROPOSED, and it says whose copy it is",
          hf["verdict"] == spec.PROPOSED
          and hf["derived"]["line"] == "derived:who-glass-amr:via=hf-electricsheepafrica",
          hf["derived"]["line"])
    check("Q1 every portal's licence is recorded WITH WHERE it was read, the words, and the date",
          all(v["licence"].get("where") and v["licence"].get("quote")
              and v["licence"].get("read_at") and v["licence"].get("how")
              for v in (who, hf, cdc)),
          " · ".join(f"{v['id']}={v['licence']['how']}" for v in (who, hf, cdc)))
    check("Q1 the WHO reading is the page's own sentence (verbatim, greppable)",
          "Creative Commons Attribution 4.0 International License (CC BY 4.0)"
          in who["licence"]["quote"] and who["licence"]["sha256"].startswith("18ee75c7"),
          who["licence"]["where"] + " sha256=" + who["licence"]["sha256"][:12])
    check("Q1 Africa CDC's licence is READ, RECORDED and QUOTED even though the candidate is refused",
          cdc["verdict"] == spec.REFUSED
          and cdc["licence"]["licence"] == "all-rights-reserved"
          and "All rights reserved Africa CDC" in cdc["licence"]["quote"],
          f"licence={cdc['licence']['licence']!r} from {cdc['licence']['where']}")
    check("Q1 Africa CDC is refused by the three names its own field values earn — not by a blanket",
          codes(cdc) == ["no-count-endpoint", "no-release", "no-schema"], f"{codes(cdc)}")
    check("Q1 the science the candidate claims is NOT judged: it is passed through, named",
          "science" in (who["not_judged"]["science"] or {})
          and who["not_judged"]["science"]["science"] == cand(doc, "who-glass-amr")["science"],
          "the verdict is unchanged by a scientific claim — 'may not judge SCIENCE'")

    # ── Q2 · A REPOSITORY-SHAPED CANDIDATE ──────────────────────────────────────────────────
    say("\nQ2 A REPOSITORY-SHAPED CANDIDATE (the AMR test's near miss, a real Kenya AMR repo)")
    repo = spec.evaluate(cand(doc, "mikeambasa1/amr-klebsiella-kenya"), evaluate_now=now)
    for ln in spec.lines(repo):
        say("    " + ln)
    check("Q2 REFUSED[no-rows] by name — 'a repository is a program, not rows'",
          "no-rows" in codes(repo)
          and "a repository is a program, not rows" in "\n".join(r["line"] for r in repo["refusals"]),
          f"codes={codes(repo)}")
    check("Q2 REFUSED[no-count-endpoint] by name — and never PROPOSED",
          "no-count-endpoint" in codes(repo) and repo["verdict"] == spec.REFUSED,
          repo["verdict"])
    sheet = spec.evaluate(cand(doc, "ajlm-glass-2016-sheet"), evaluate_now=now)
    check("Q2 the paper's Google-Sheet supplement is REFUSED[unstable-url] by name",
          "unstable-url" in codes(sheet), f"codes={codes(sheet)}")

    # ── Q3 · THE HF COPY WITHOUT AN AUTHORITY LINE ──────────────────────────────────────────
    say("\nQ3 AN HF-STYLE REPACKAGING WITHOUT AN AUTHORITY LINE (the same rows, minus the one line)")
    naked = cand(doc, "hf-africa-owid-glass")
    for k in ("derived", "authority_line", "authority_quote"):
        naked.pop(k, None)
    vn = spec.evaluate(naked, evaluate_now=now)
    for ln in spec.lines(vn):
        say("    " + ln)
    check("Q3 REFUSED[derived-without-authority] by name", "derived-without-authority" in codes(vn),
          f"codes={codes(vn)}")
    check("Q3 the refusal says WHICH line it owes (derived:<authority>:via=<via>)",
          "derived:<authority>:via=<via>" in "\n".join(r["line"] for r in vn["refusals"]),
          "the owed line is quoted in the refusal itself")
    check("Q3 the SAME candidate WITH the line is PROPOSED — the line is the only difference",
          spec.evaluate(cand(doc, "hf-africa-owid-glass"))["verdict"] == spec.PROPOSED)

    # ── Q4 · NO ADMISSION PATH ─────────────────────────────────────────────────────────────
    say("\nQ4 NOTHING IN THE EVALUATOR CAN ADMIT — 'the code path does not exist', and trying it refuses")
    findings = scan_module(MODULE)
    check("Q4 the module contains no write, no store, no network, no __main__ guard",
          not findings, "; ".join(findings) or "0 findings over text + AST")
    check("Q4 it imports only what cannot write (re · hashlib)",
          sorted(_imports(MODULE)) == ["hashlib", "re"], f"{sorted(_imports(MODULE))}")
    # THE SCANNER CAN FAIL: a planted writer, in a temp file outside the package.
    planted = os.path.join(HERE, "fixtures", "spec", "_planted_writer.py.txt")
    with open(planted, encoding="utf-8") as fh:
        planted_src = fh.read()
    import tempfile
    tmp = tempfile.mkdtemp(prefix="spec-scan-")
    target = os.path.join(tmp, "planted.py")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(planted_src)
    planted_findings = scan_module(target)
    check("Q4 a PLANTED writer is caught by the same scan (a check that cannot fail is not a check)",
          len(planted_findings) >= 2, "; ".join(planted_findings[:3]))
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    # THE ATTEMPT ITSELF REFUSES, by name — the only function in the module that could be mistaken
    # for an admission step.
    raised = ""
    try:
        spec.admit(who)
    except spec.Refused as e:
        raised = str(e)
    check("Q4 calling admit() REFUSES[evaluator-may-not-admit] and names why",
          raised.startswith("REFUSED[evaluator-may-not-admit]")
          and "a pass is PROPOSED" in raised and "admit.by_policy is False" in raised, raised[:150] + "…")
    for verdict_key, value in (("admitted", True), ("verdict", "admitted"), ("admit", True)):
        c = cand(doc, "who-glass-amr")
        c[verdict_key] = value
        va = spec.evaluate(c)
        check(f"Q4 a candidate arriving {verdict_key}={value!r} is REFUSED[evaluator-may-not-admit]",
              "evaluator-may-not-admit" in codes(va) and va["admitted"] is False,
              f"codes={codes(va)[:2]}")
    check("Q4 no verdict this module returns carries admitted=True, and summary() counts 0 admitted",
          all(v["admitted"] is False for v in (who, hf, cdc, repo, vn))
          and "admitted" in spec.summary([who, hf, cdc]) and
          spec.summary([who, hf, cdc])["admitted"] == 0,
          json.dumps(spec.summary([who, hf, cdc, repo, vn])))
    check("Q4 the ACTUAL event name never appears in the module (it cannot write the diary)",
          EVENT_NAME not in open(MODULE, encoding="utf-8").read())

    # ── S2 · THE LICENCE GATE REFUSES GUESSWORK, in all four of its ways ────────────────────
    say("\nS2 a licence read by guesswork is not a licence read (four ways)")
    good = dict(cand(doc, "who-glass-amr"))
    ways = {
        "a bare name with no address and no words": dict(licence="CC-BY-4.0"),
        "no quote (nothing the publisher actually said)":
            dict(licence={"how": "page", "licence": "CC-BY-4.0", "where": "x", "read_at": "y"}),
        "no where (nobody can re-read it)":
            dict(licence={"how": "page", "licence": "CC-BY-4.0", "quote": "q", "read_at": "y"}),
        "a fourth way that is not api · page · tag":
            dict(licence={"how": "readme", "licence": "CC-BY-4.0", "where": "x", "quote": "q",
                          "read_at": "y"}),
    }
    for label, patch in ways.items():
        c = dict(good)
        c.update(patch)
        v = spec.evaluate(c)
        check(f"S2 refused: {label}", "licence-unreadable" in codes(v),
              (v["refusals"][0]["line"][:120] + "…") if v["refusals"] else "NOT REFUSED")

    # ── S3 · PURITY AND DETERMINISM ────────────────────────────────────────────────────────
    say("\nS3 pure: same input, same record, no I/O, no clock, no network")
    a = spec.evaluate(cand(doc, "who-glass-amr"))
    b = spec.evaluate(cand(doc, "who-glass-amr"))
    check("S3 two evaluations of the same candidate are byte-identical JSON",
          json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))
    check("S3 evaluating the whole fixture twice gives the same summary",
          spec.summary(spec.evaluate_all(doc["candidates"]))
          == spec.summary(spec.evaluate_all(doc["candidates"])),
          json.dumps(spec.summary(spec.evaluate_all(doc["candidates"]))))
    check("S3 the refuse-by-name helper prints the class first (greppable, one line per class)",
          spec.Refused("no-rows", "x: nothing").__str__() == "REFUSED[no-rows] x: nothing")

    # ── THE VERDICT ────────────────────────────────────────────────────────────────────────
    bad = [n for n, ok in results if not ok]
    say("")
    if bad:
        for n in bad:
            say(f"  FAILING: {n}")
        say(f"SELFTEST FAIL: {len(bad)} of {len(results)} checks failed")
        return 1
    say(f"SELFTEST PASS: {len(results)}/{len(results)} — the six checks refuse by name, the two "
        f"sources PROPOSE, the grey one is refused for the three cells its own table leaves open, "
        f"the derived copy without an authority line is refused by name, and no path here admits")
    return 0


def _imports(path: str) -> set[str]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[-1] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            out |= {(node.module or "").split(".")[-1]}
    return out - {"__future__"}          # `from __future__ import annotations` is not a dependency


if __name__ == "__main__":
    sys.exit(main())
