"""view — ONE published table, its schema, and the hash that proves which schema wrote it.

The schema is deliberately data-free (no row counts, no release ids, no timestamps), so its
hash is stable across runs: if the hash moves, the *shape* moved and every consumer must be
re-read. Provenance (which releases, which raw sha256) lives in the sidecar, not in the schema.
"""
from __future__ import annotations

import hashlib
import json

VIEW = "mouse_gene"
VERSION = 1

SCHEMA = {
    "view": VIEW,
    "version": VERSION,
    "key": ["symbol"],
    "description": ("mouse gene identity and position: one row per gene symbol where an id "
                    "authority and an xref provider agree. Unresolved and conflicting keys are "
                    "NOT in this view; they are in xref/conflicts.tsv with a reason."),
    "columns": [
        {"name": "symbol", "type": "string", "note": "MGI gene symbol, case preserved (join key)"},
        {"name": "mgi_id", "type": "string", "note": "MGI accession, MGI:####### from the xref provider"},
        {"name": "ensembl_gene_id", "type": "string", "note": "ENSMUSG############ from the id authority"},
        {"name": "chromosome", "type": "string", "note": "as published by the id authority"},
        {"name": "start", "type": "integer", "note": "1-based inclusive, id authority"},
        {"name": "end", "type": "integer", "note": "1-based inclusive, id authority"},
    ],
    "resolution": ["one authority id + one provider id per symbol (xref.py R1)",
                   "on namespace disagreement the id authority's value is published (R2)"],
}


def schema_bytes() -> bytes:
    return (json.dumps(SCHEMA, indent=2, sort_keys=True) + "\n").encode()


def schema_sha256() -> str:
    return hashlib.sha256(schema_bytes()).hexdigest()


def render(rows: list[dict]) -> bytes:
    """rows come from xref.XrefResult.rows; deterministic bytes (sorted, LF, no clock)."""
    lines = ["\t".join(c["name"] for c in SCHEMA["columns"])]
    for r in sorted(rows, key=lambda r: (r["key"], r["authority_id"])):
        c = r["coords"]
        lines.append("\t".join([r["key"], r["provider_id"], r["authority_id"],
                                str(c.get("chrom", "")), str(c.get("start", "")), str(c.get("end", ""))]))
    return ("\n".join(lines) + "\n").encode()
