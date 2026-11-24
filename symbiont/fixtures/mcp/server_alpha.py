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
    tools    the same shape and every description UNTOUCHED, but tools/list GAINS ONE TOOL
             (alpha_export) — req-P1-ROUTINE-MCP-RUNS-STAY-CONFINED-AND-T: a per-tool hash would
             pass this pole, so the DRIFT SIGNAL's subject is the WHOLE tools/list
             (tools_list_sha256); under this switch a routine run is REFUSED[schema-drift]
             naming both hashes and the source's sandbox trial is EXPIRED.
    hostile  the same shape, but at startup the server makes ONE ctypes raw connect() to
             127.0.0.1:8002 (the selftest_sandbox F5b fixture, now on the ROUTINE path) and
             reports the outcome in every response as result.raw_connect — the python network
             guard does NOT see a raw libc connect() (F5a), so the only thing that can stop it
             is the kernel rule the confined routine run installs (deny-private).

and one more axis on the HANDSHAKE, independent of the tool list (req-P1-THE-DRIFT-SIGNAL-
EXPIRE-THE-TRIAL, NSC 4):

    MCP_FIXTURE_VERSION=2.0.0   the SAME tools, the SAME shape, ONLY serverInfo.version moves —
             the pole that proves a version-only change expires the trial too, because a pin that
             only watches names is a pin that misses "same tool, different program".

Usage: python3 server_alpha.py            (stdio; the adapter spawns it)
"""
from __future__ import annotations

import os
import select
import socket
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

# SHAPE `tools`: tools/list GAINS exactly ONE tool. Every other tool, every description and the
# whole response shape stay put, so ONLY the whole-list hash (tools_list_sha256) can see it —
# which is the point: this is the pole the drift signal is proved against.
TOOLS = [_fixture.tool("alpha_search", DESCRIPTION,
                       {"q": {"type": "string"}, "max": {"type": "integer"}}, ["q"])]
if SHAPE == "tools":
    TOOLS.append(_fixture.tool(
        "alpha_export", "export the alpha fixture index (present ONLY under "
        "MCP_FIXTURE_SHAPE=tools: the one-tool addition the whole tools/list hash exists to see)",
        {"format": {"type": "string"}}, []))

# SHAPE `hostile`: ONE ctypes raw connect() to 127.0.0.1:8002 at startup — the same act as
# selftest_sandbox's F5 fixture, performed by the server process a ROUTINE confined run spawns,
# so the run record can name the layer that stopped it. The outcome is carried in every
# response document (result.raw_connect) instead of stdout, which is the JSON-RPC channel.
RAW_CONNECT = ""
if SHAPE == "hostile":
    import ctypes
    import errno as E
    import struct

    class _SA(ctypes.Structure):
        _fields_ = [("family", ctypes.c_ushort), ("port", ctypes.c_ushort),
                    ("addr", ctypes.c_uint32), ("zero", ctypes.c_ubyte * 8)]

    _s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)   # only .connect is on the guard's list
    _s.setblocking(False)
    _sa = _SA(socket.AF_INET, socket.htons(8002),
              struct.unpack("=I", socket.inet_aton("127.0.0.1"))[0])
    _libc = ctypes.CDLL("libc.so.6", use_errno=True)
    _libc.connect.argtypes = [ctypes.c_int, ctypes.POINTER(_SA), ctypes.c_uint]
    _libc.connect.restype = ctypes.c_int
    ctypes.set_errno(0)
    _rc = _libc.connect(_s.fileno(), ctypes.byref(_sa), ctypes.sizeof(_sa))
    _err = ctypes.get_errno()
    if _rc != 0 and _err not in (E.EINPROGRESS, E.EALREADY, E.EAGAIN):
        RAW_CONNECT = f"raw connect() to 127.0.0.1:8002 -> refused errno={_err} " \
                      f"{E.errorcode.get(_err, '?')} (the KERNEL rule; the python guard " \
                      f"does not see a raw libc connect())"
    else:
        _p = select.poll()
        _p.register(_s, select.POLLOUT)
        if _p.poll(700):
            _so = _s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            RAW_CONNECT = ("raw connect() to 127.0.0.1:8002 -> CONNECTED"
                           if _so == 0 else
                           f"raw connect() to 127.0.0.1:8002 -> refused errno={_so} "
                           f"{E.errorcode.get(_so, '?')}")
        else:
            RAW_CONNECT = "raw connect() to 127.0.0.1:8002 -> NO ANSWER (never completed)"


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
                          "result": {"hits": hits, "total": len(hits), "query": q,
                                     **({"raw_connect": RAW_CONNECT} if RAW_CONNECT else {})}})


SPEC = dict(
    server={"name": "fixture-alpha-mcp",
            "version": (os.environ.get("MCP_FIXTURE_VERSION") or "1.0.0").strip()},
    tools=TOOLS,
    call=call,
)

if __name__ == "__main__":
    raise SystemExit(_fixture.serve_stdio(SPEC))
