#!/usr/bin/env python3
"""FIXTURE MCP SERVER `alpha` — output SHAPE A, and the drift switch.

Shape A (text content, i.e. the tool's JSON arrives as a string):

    {"status": "ok",
     "result": {"hits":  [{"symbol": …, "acc": …, "title": …, "cross": …,
                           "coords": {"chr": …, "start": …, "end": …}}, …],
                "total": 7,          <- the source's OWN count for this query
                "query": …}}

and, because a pin that has never been tested against a moved shape is a comment, this fixture can
MOVE ITS OWN SHAPE on demand (env MCP_FIXTURE_SHAPE):

    (unset)  shape A — the shape symbiont/fixtures/mcp/sources.yml maps
    v2       the same data, RENAMED: hits -> records, total -> stats.total, and the record objects
             themselves lose /acc (id) — so both the records pointer and a field pointer stop
             resolving. NO count moves, no tool disappears: the ONLY change is the shape, which is
             exactly the silent corruption REFUSED[schema-drift] exists to stop.
    desc     the same shape, but the TOOL DESCRIPTION changes — the pin's own half, so a config that
             declares tool_description_sha256 refuses before a single record is read.

Usage: python3 server_alpha.py            (stdio; the adapter spawns it)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture  # noqa: E402

ROWS = [
    dict(symbol="Brca1", acc="ALPHA:0001", title="DNA repair in the mouse mammary gland",
         cross="XREF-BRCA1", coords=dict(chr="11", start=101_000_000, end=101_500_000)),
    dict(symbol="Pax6", acc="ALPHA:0002", title="Pax6 dosage in the developing eye",
         cross="XREF-PAX6", coords=dict(chr="2", start=105_500_000, end=105_800_000)),
    dict(symbol="Wt1", acc="ALPHA:0003", title="Wt1 and the urogenital ridge",
         cross="XREF-WT1", coords=dict(chr="2", start=105_000_000, end=105_100_000)),
    dict(symbol="Trp53", acc="ALPHA:0004", title="p53 targets after irradiation",
         cross="XREF-TRP53", coords=dict(chr="11", start=69_400_000, end=69_500_000)),
    dict(symbol="Sox2", acc="ALPHA:0005", title="Sox2 in neural progenitors",
         cross="XREF-SOX2", coords=dict(chr="3", start=34_600_000, end=34_700_000)),
    dict(symbol="Shh", acc="ALPHA:0006", title="Sonic hedgehog and limb patterning",
         cross="XREF-SHH", coords=dict(chr="5", start=28_400_000, end=28_500_000)),
    dict(symbol="Otx2", acc="ALPHA:0007", title="Otx2 in anterior development",
         cross="XREF-OTX2", coords=dict(chr="14", start=48_600_000, end=48_800_000)),
]

SHAPE = (os.environ.get("MCP_FIXTURE_SHAPE") or "").strip().lower()
DESCRIPTION = ("search the alpha fixture index; answers shape A (text content, /result/hits, "
               "/result/total)")
if SHAPE == "desc":
    DESCRIPTION += " — rev 2"


def call(name: str, args: dict) -> dict:
    q = str(args.get("q") or "")
    limit = int(args.get("max") or len(ROWS))
    if q.strip() in ("", "*"):            # "*" = everything the fixture holds (a wildcard query)
        hits = [dict(r) for r in ROWS]
    else:
        hits = [dict(r) for r in ROWS if q.lower() in r["symbol"].lower() or q.lower() in r["title"].lower()]
    hits = hits[:limit]
    if SHAPE == "v2":
        renamed = [{k: v for k, v in r.items() if k != "acc"} for r in hits]
        return dict(document={"status": "ok",
                              "result": {"records": renamed, "stats": {"total": len(hits)},
                                         "query": q}})
    return dict(document={"status": "ok",
                          "result": {"hits": hits, "total": len(hits), "query": q}})


SPEC = dict(
    server={"name": "fixture-alpha-mcp", "version": "1.0.0"},
    tools=[_fixture.tool("alpha_search", DESCRIPTION,
                         {"q": {"type": "string"}, "max": {"type": "integer"}}, ["q"])],
    call=call,
)

if __name__ == "__main__":
    raise SystemExit(_fixture.serve_stdio(SPEC))
