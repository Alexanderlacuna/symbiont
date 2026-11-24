#!/usr/bin/env python3
"""FIXTURE MCP SERVER `beta` — output SHAPE B, and a count that lives in a SECOND TOOL.

Everything about beta differs from alpha on purpose, because an adapter that only works on one
shape is the thing this row exists to kill:

  · the JSON arrives as structuredContent (MCP 2025-06-18), NOT as text
  · the envelope is {"data": {"docs": [...], "available": n}} — none of alpha's key names survive
  · the record fields are pid/label/headline, not acc/symbol/title (and /headline is OPTIONAL)
  · THE COUNT IS NOT IN THE SEARCH RESPONSE AT ALL: it is a second tool, beta_count, whose own
    answer states {"counts": {"documents": n}} — the "a count tool" arm of the count rule

Usage: python3 server_beta.py              (stdio; the adapter spawns it)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture  # noqa: E402

DOCS = [
    dict(pid="BETA:1001", label="Brca1", headline="Homologous recombination in mammary tissue"),
    dict(pid="BETA:1002", label="Pax6", headline="Eye field specification"),
    dict(pid="BETA:1003", label="Wt1", headline="Kidney mesenchyme"),
    dict(pid="BETA:1004", label="Trp53", headline="Apoptosis after DNA damage"),
    dict(pid="BETA:1005", label="Sox2", headline="Pluripotency maintenance"),
    dict(pid="BETA:1006", label="Shh", headline="Floor plate induction"),
    dict(pid="BETA:1007", label="Otx2"),          # no headline: the OPTIONAL field, exercised
]


def _matched(term: str) -> list[dict]:
    if term.strip() in ("", "*"):         # "*" = everything the fixture holds (a wildcard query)
        return list(DOCS)
    t = term.lower()
    return [d for d in DOCS if t in d["label"].lower() or t in (d.get("headline") or "").lower()]


def call(name: str, args: dict) -> dict:
    term = str(args.get("term") or "")
    if name == "beta_count":
        return dict(document={"counts": {"documents": len(_matched(term))}, "term": term})
    page = int(args.get("page_size") or len(DOCS))
    return dict(document={"data": {"docs": _matched(term)[:page], "available": len(_matched(term)),
                                   "term": term}},
                structured=True)


SPEC = dict(
    server={"name": "fixture-beta-mcp", "version": "2.3.1"},
    tools=[_fixture.tool("beta_search", "search the beta fixture index; answers shape B "
                                        "(structuredContent, /data/docs)",
                         {"term": {"type": "string"}, "page_size": {"type": "integer"}}, ["term"]),
           _fixture.tool("beta_count", "how many beta documents match the term (the COUNT TOOL)",
                         {"term": {"type": "string"}}, ["term"])],
    call=call,
)

if __name__ == "__main__":
    raise SystemExit(_fixture.serve_stdio(SPEC))
