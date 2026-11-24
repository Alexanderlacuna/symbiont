#!/usr/bin/env python3
"""FIXTURE MCP SERVER `gamma` — a server that CANNOT STATE HOW MANY RECORDS IT HAS.

Its one tool answers {"items": [...]} and there is no count field, no count tool, and no total
anywhere in its protocol surface (tools/list carries one tool, and that tool's answer has no
number in it). That is not a defect of the fixture: it is the majority of MCP servers in the wild,
and it is exactly what the count rule is about —

    a server that cannot state how many records it has
        may be used as a TOOL            -> mode: tool   (runs, records stamped published: false)
        and is REFUSED as a SOURCE       -> mode: source (REFUSED[no-count-endpoint], by name,
                                                          before a request leaves the box)

The same binary serves stdio (default) and streamable HTTP (--http PORT), so the adapter's TWO
transports are exercised by the same evidence, not by two different stories.

Usage: python3 server_gamma.py              (stdio)
       python3 server_gamma.py --http 8901  (POST /mcp on 127.0.0.1:8901)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture  # noqa: E402

ITEMS = [
    dict(iri="GAMMA:1", name="Brca1", kind="gene"),
    dict(iri="GAMMA:2", name="Pax6", kind="gene"),
    dict(iri="GAMMA:3", name="Wt1", kind="gene"),
]


def call(name: str, args: dict) -> dict:
    # CAUTION, and it is the fixture's whole point: NOTHING in this answer counts anything.
    q = str(args.get("question") or "")
    if q.strip() in ("", "*"):            # "*" = everything the fixture holds (a wildcard question)
        items = [dict(i) for i in ITEMS]
    else:
        items = [dict(i) for i in ITEMS if q.lower() in i["name"].lower()]
    return dict(document={"items": items})


SPEC = dict(
    server={"name": "fixture-gamma-mcp", "version": "0.4.0"},
    tools=[_fixture.tool("gamma_lookup", "look up gamma items; answers /items and NO COUNT",
                         {"question": {"type": "string"}}, ["question"])],
    call=call,
)

if __name__ == "__main__":
    if "--http" in sys.argv:
        port = int(sys.argv[sys.argv.index("--http") + 1])
        raise SystemExit(_fixture.serve_http(SPEC, port))
    raise SystemExit(_fixture.serve_stdio(SPEC))
