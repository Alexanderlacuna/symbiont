"""PROOF of the evidence contract — an abstract cannot carry a claim that needs the full text
(req-P1-AN-ABSTRACT-CANNOT-CARRY-A-CLAIM-that).

Every fixture below is OFFLINE (no network, no MCP): they exercise the classifier, the loader
and the level assignment against fixture records under fixtures/evidence/. The three the row's
GATE names:

  F1 abstract-only record asked for 'sample size' -> REFUSED naming the kind
     ('needs fulltext: sample-size'), exit-shape refused
  F2 the SAME record asked 'is this a review'    -> PASSES (an abstract carries it)
  F3 a record with no evidence_level              -> REFUSED AT LOAD, never defaulted to abstract

Plus F4 (the boundary the refusal must NOT cross): a fulltext-level record asked for 'sample
size' PASSES — the refusal is level-based, not a ban on the kind — and the trigger markers are
sanity-checked both ways (a marker that never fires cannot justify a rule; one that always
fires cannot measure anything).

Usage: python3 -m symbiont.literature.selftest_evidence      (exit 0 only when all pass)
"""
from __future__ import annotations

import json
import os
import sys

from ..contract import ConfigError
from . import evidence

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "evidence")
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


def load_fixture(name: str) -> dict:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    print("== evidence-contract selftest (offline fixtures under symbiont/literature/fixtures/evidence/) ==")

    # F1 — the fixture abstract-only record, asked for 'sample size'
    rec = load_fixture("record-abstract-only.json")
    ans = evidence.answer_claim(rec, "what was the sample size of this study?")
    check("F1 sample-size at abstract level is REFUSED naming the kind",
          ans["ok"] is False and ans["reason"] == "needs fulltext: sample-size"
          and ans["evidence_level"] == "abstract",
          f"kind={ans['kind']} reason={ans['reason']!r}")

    # F2 — the SAME record, asked 'is this a review'
    ans2 = evidence.answer_claim(rec, "is this a review?")
    check("F2 is-review at abstract level PASSES",
          ans2["ok"] is True and ans2["kind"] == "is-review",
          f"kind={ans2['kind']} reason={ans2['reason']!r}")

    # F3 — a record with no evidence_level is refused at load, never defaulted to abstract
    try:
        evidence.load_records(os.path.join(FIXTURES, "publication-no-evidence-level.tsv"))
        check("F3 no-evidence_level record refused at load", False, "load_records returned without refusing")
    except ConfigError as e:
        msg = str(e)
        named = "99992001" in msg and "never defaulted" in msg
        check("F3 no-evidence_level record refused at load, named, never defaulted",
              named, msg[:140] + "…")
    raw = dict(rec)
    raw.pop("evidence_level")
    try:
        evidence.answer_claim(raw, "is this a review?")
        check("F3b an unstamped record is refused, not answered", False,
              "answer_claim accepted a record with no evidence_level")
    except ConfigError as e:
        check("F3b an unstamped record is refused, not answered", "never defaulted" in str(e), str(e)[:140] + "…")

    # F4 — the boundary: a fulltext-level record asked for 'sample size' PASSES
    full = load_fixture("record-fulltext.json")
    ans3 = evidence.answer_claim(full, "what was the sample size of this study?")
    check("F4 sample-size at fulltext level PASSES (the refusal is level-based)",
          ans3["ok"] is True and ans3["evidence_level"] == "fulltext",
          f"kind={ans3['kind']} reason={ans3['reason']!r}")

    # the measured trigger's markers, both ways
    check("F5a trigger marker fires on an abstract that states a sample",
          evidence.abstract_answers("We analysed n = 12 mice per group.", "sample-size") is True,
          "marker matched 'n = 12 mice'")
    check("F5b trigger marker stays silent on an abstract with no operational detail",
          evidence.abstract_answers("The gene is expressed in the gonad.", "method-application") is False,
          "no dose/dilution/manufacturer phrase — the DRA shape (0 of 121)")
    lv = evidence.assign_evidence_level(dict(abstract="x"))
    check("F5c level assignment is computed from fields, never declared",
          lv == "abstract" and evidence.assign_evidence_level(dict()) == "metadata-only"
          and evidence.assign_evidence_level(dict(abstract="x", fulltext_sha256="s")) == "fulltext",
          f"abstract->'abstract', empty->'metadata-only', sha->'fulltext' (got {lv!r})")

    failed = [r for r in RESULTS if not r[1]]
    print(f"NSC: {len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
