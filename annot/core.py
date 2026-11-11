"""annot — the ONE core of the ship's surface.

Every door (CLI, MCP, REST, the GN page) is a thin reader of `dispatch()`: it takes a tool name and
a dict of arguments and returns canonical JSON bytes. Nothing outside this module is allowed to
answer a genetics query, and nothing here writes: the core opens store files read-only and never
opens a network connection. The store is the symbiont's published artefact — this core is a
reader of that store, never a second fetcher.

The item shape, stated once and enforced structurally (see `_assert_items`):

    {"value": …, "accession": <PMID|ENSG…|MGI:…>, "source": …, "release": …, "fetched_at": …, "sha256": …}

  * `value`      what the source asserts on this item
  * `accession`  the item's identifier IN ITS SOURCE'S OWN NAMESPACE. No item is emitted without one;
                 for a change event (what_changed) the namespace is the release id, and the rule is
                 written down in PROVENANCE["rules"]["accession"].
  * `source`     which harvested source asserts it
  * `release`    that source's release id
  * `fetched_at` the instant that release was fetched — read from the harvest run record, NEVER a
                 clock reading at query time (that is what makes two doors byte-identical)
  * `sha256`     the content hash of the bytes the item was read from (content-addressed raw
                 snapshot hash, or the hash of the run/state record for an event item)
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from datetime import datetime, timezone

CORE = "annot"
CORE_VERSION = 1

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_STORE = os.path.join(FARM, "symbiont", "store")

VIEW_NAME = "mouse_gene"
VIEW_VERSION = 1

# Sources that CAN answer from the local store, and the sources the surface's shape needs that no
# harvest has delivered yet. A missing source is named in every response that would have used it —
# an absence that is not named is indistinguishable from a blank.
STORE_SOURCES = ("ensembl_biomart", "mgi")

MISSING_SOURCES = {
    "mgi_phenotype_annotations": {
        "why": "no phenotype-annotation file has been harvested into this store",
        "needed": "MGI GenePheno.rpt / MGI_PhenoGenoMP.rpt (or IMPC), joined on MGI accession",
        "would_answer": "phenotype_evidence",
    },
    "pubmed": {
        "why": "no publication source has been harvested into this store",
        "needed": "PubMed/Europe PMC search-by-gene, joined on MGI id → PMID",
        "would_answer": "publications_for",
    },
    "trait_gene_annotations": {
        "why": "the store holds trait MARKERS (MGI QTL rows) but no trait→gene association table",
        "needed": "MGI MP/HP or QTL→gene associations, joined on MGI accession",
        "would_answer": "gene_dossier.traits",
    },
}

# --------------------------------------------------------------------------------------------------
# THE SURFACE: one declaration of the tools, their purpose, and their parameters.
# The CLI argv parser, the MCP inputSchema and the REST query-string parser are all GENERATED from
# this dict, so no door can accept a parameter the core does not implement.
#   param -> (json type, required, default)
SPECS = {
    "gene_dossier": {
        "purpose": ("Everything the store can say about one mouse gene, in named sections: identity, "
                    "xref, phenotype evidence, publications, traits. Sections with no harvested source "
                    "are returned as named empty sections that say which source is missing."),
        "params": {"gene": ("string", True, None), "limit": ("integer", False, 50)},
    },
    "gene_xref": {
        "purpose": ("The cross-reference for one gene: Ensembl gene id, MGI accession and symbol, plus "
                    "every conflict and every retired symbol the store records for it."),
        "params": {"gene": ("string", True, None), "limit": ("integer", False, 50)},
    },
    "phenotype_evidence": {
        "purpose": ("Phenotype annotations asserted for one gene, one item per annotation with its "
                    "accession. If no phenotype source is in the store the result is a NAMED empty "
                    "result stating which source is missing — never a blank."),
        "params": {"gene": ("string", True, None), "limit": ("integer", False, 50)},
    },
    "publications_for": {
        "purpose": ("Publications for one gene, one item per PMID. If no publication source is in the "
                    "store the result is a NAMED empty result stating which source is missing."),
        "params": {"gene": ("string", True, None), "limit": ("integer", False, 50)},
    },
    "traits_containing": {
        "purpose": ("Trait markers whose name or symbol contains a term (MGI Marker Type QTL/phenotype "
                    "from the harvested MGI marker list). One item per marker, accession = MGI id."),
        "params": {"term": ("string", True, None), "limit": ("integer", False, 25)},
    },
    "what_changed": {
        "purpose": ("Which harvest run changed what, for one source (or 'all'), since an instant: "
                    "release id, rows in / records / rejects, and the view hash at that run."),
        "params": {"source": ("string", True, None), "since": ("string", True, None)},
    },
    "answer": {
        "purpose": ("OPT-IN answer mode, next to the six read tools (which answer unchanged). It "
                    "takes {tool, params} — a read tool and that tool's own parameters — plus the "
                    "composed answer, a JSON array of sentences. Alongside `items` it returns "
                    "`sentences`, one entry per submitted sentence: {text, accessions, grounded, "
                    "reason}. A sentence is grounded ONLY if every accession it cites is in `items` "
                    "AND re-reading the stored record for that accession agrees with the value the "
                    "sentence names; otherwise grounded:false with reason 'no accession' / 'accession "
                    "not in items' / 'stored value differs: X vs Y'. A refused sentence is returned "
                    "with its reason, never silently dropped. The provenance block names the "
                    "accessions checked and how many sentences grounded."),
        "params": {"tool": ("string", False, "gene_dossier"),
                   "params": ("object", False, {}),
                   "sentences": ("array", True, None),
                   "limit": ("integer", False, 50)},
    },
}

TOOLS = tuple(SPECS)


class ToolError(Exception):
    """A caller error the core can name (unknown tool, missing/bad parameter)."""


# --------------------------------------------------------------------------------------------------
# canonical JSON — the bytes every door passes through untouched
# --------------------------------------------------------------------------------------------------
def dumps(obj) -> bytes:
    """The one serialisation. sort_keys + fixed indent + trailing LF: same object, same bytes."""
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:                      # READ ONLY
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:      # READ ONLY
        return json.load(fh)


def _read_tsv(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8", newline="") as fh:   # READ ONLY
        header = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            rows.append(dict(zip(header, line.split("\t"))))
    return rows


def _iso(dt: str) -> datetime | None:
    if not dt:
        return None
    try:
        return datetime.fromisoformat(dt.replace("Z", "+00:00"))
    except ValueError:
        return None


def _norm_arg(tool: str, name: str, typ: str, raw):
    if typ == "integer":
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ToolError("%s.%s must be an integer, got %r" % (tool, name, raw))
    if typ == "object":
        # a nested parameter block: the MCP and `call --json` deliver a real dict, the CLI flag and
        # the REST query string deliver a JSON string — parsed HERE, once, for every door.
        if isinstance(raw, dict):
            return dict(raw)
        try:
            parsed = json.loads(str(raw))
        except ValueError:
            raise ToolError("%s.%s must be a JSON object, got %r" % (tool, name, raw))
        if not isinstance(parsed, dict):
            raise ToolError("%s.%s must be a JSON object, got %r" % (tool, name, raw))
        return dict(parsed)
    if typ == "array":
        # every door must reach the same bytes: the MCP and `call --json` deliver a real list, the
        # CLI flag and the REST query string deliver a JSON (or bare) string — parsed HERE, once, so
        # the canonical `query` echo is the same list whichever door asked.
        if isinstance(raw, list):
            out = raw
        else:
            s = str(raw)
            try:
                parsed = json.loads(s)
            except ValueError:
                parsed = s
            out = parsed if isinstance(parsed, list) else [parsed]
        if not all(isinstance(x, str) for x in out):
            raise ToolError("%s.%s must be an array of strings, got %r" % (tool, name, raw))
        return list(out)
    if raw is None:
        raise ToolError("%s.%s is required" % (tool, name))
    return str(raw)


def normalize(tool: str, args: dict | None) -> dict:
    """Turn any door's arguments into THE argument dict. Defaults applied here, once."""
    if tool not in SPECS:
        raise ToolError("unknown tool %r; the surface is %s" % (tool, ", ".join(TOOLS)))
    args = dict(args or {})
    spec = SPECS[tool]["params"]
    unknown = sorted(set(args) - set(spec))
    if unknown:
        raise ToolError("%s does not take %s; it takes %s"
                        % (tool, ", ".join(unknown), ", ".join(sorted(spec))))
    out = {}
    for name, (typ, required, default) in spec.items():   # dict order = declared order
        raw = args.get(name, default)
        if raw is None:
            raise ToolError("%s requires %r" % (tool, name))
        out[name] = _norm_arg(tool, name, typ, raw)
    out["limit"] = max(1, min(int(out.get("limit", 50)), 500))
    return out


def input_schema(tool: str) -> dict:
    """The MCP inputSchema, generated from SPECS — never hand-maintained."""
    props, required = {}, []
    for name, (typ, req, default) in SPECS[tool]["params"].items():
        if typ == "array":
            p = {"type": "array", "items": {"type": "string"}}
        elif typ == "object":
            p = {"type": "object"}
        else:
            p = {"type": typ}
        if default is not None:
            p["default"] = default
        props[name] = p
        if req:
            required.append(name)
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


# --------------------------------------------------------------------------------------------------
# the store reader — read once per process, never written
# --------------------------------------------------------------------------------------------------
class Store:
    def __init__(self, path: str | None = None):
        self.path = os.path.abspath(path or os.environ.get("ANNOT_STORE") or DEFAULT_STORE)
        self._cache: dict = {}

    # -- paths -------------------------------------------------------------------------------------
    def p(self, *parts) -> str:
        return os.path.join(self.path, *parts)

    def _memo(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    # -- the published view ------------------------------------------------------------------------
    @property
    def provenance(self) -> dict:
        return self._memo("prov", lambda: _read_json(self.p("view", "%s.v%d.provenance.json"
                                                            % (VIEW_NAME, VIEW_VERSION))))

    @property
    def view_rows(self) -> list[dict]:
        return self._memo("view", lambda: _read_tsv(self.p("view", "%s.v%d.tsv" % (VIEW_NAME, VIEW_VERSION))))

    @property
    def view_index(self) -> dict:
        def build():
            by_symbol, by_mgi, by_ens = {}, {}, {}
            for r in self.view_rows:
                by_symbol.setdefault(r["symbol"].casefold(), r)
                if r.get("mgi_id"):
                    by_mgi.setdefault(r["mgi_id"], r)
                if r.get("ensembl_gene_id"):
                    by_ens.setdefault(r["ensembl_gene_id"], r)
            return {"symbol": by_symbol, "mgi": by_mgi, "ensembl": by_ens}
        return self._memo("idx", build)

    def resolve(self, gene: str) -> tuple[dict | None, str]:
        """A gene may be named by symbol (case-insensitive), MGI accession or Ensembl gene id."""
        idx = self.view_index
        g = (gene or "").strip()
        for name, table in (("symbol_casefold", idx["symbol"]), ("mgi_accession", idx["mgi"]),
                            ("ensembl_gene_id", idx["ensembl"])):
            row = table.get(g.casefold()) if name == "symbol_casefold" else table.get(g.upper())
            if row is not None:
                return row, name
        if g.upper().startswith("MGI:"):
            row = idx["mgi"].get(g.upper())
            if row is not None:
                return row, "mgi_accession"
        if g.upper().startswith("ENSMUSG"):
            row = idx["ensembl"].get(g.upper())
            if row is not None:
                return row, "ensembl_gene_id"
        # last resort: a RETIRED symbol that was folded into a current one
        cur = self.retired_index.get(g.casefold())
        if cur:
            row = idx["symbol"].get(cur.casefold())
            if row is not None:
                return row, "retired_symbol"
        return None, "none"

    def unpublished(self, gene: str) -> dict | None:
        """A symbol the sources DO assert but the view refused to publish (conflict / unresolved).

        The store keeps those rows deliberately (xref.tsv status != ok). A door that answered
        "nothing here" for such a key would be lying by omission, so it is looked up too.
        """
        g = (gene or "").strip()
        row = self.xref_rows.get(g)
        if row:
            return row
        cf = g.casefold()
        for key, row in self.xref_rows.items():
            if key.casefold() == cf:
                return row
        return None

    def answer_record(self, accession: str) -> dict | None:
        """RE-READ the stored record for an accession — a fresh open of the published view file.

        The answer mode verifies a composed sentence by re-reading THIS record, not by trusting the
        memoised index or the response's own bytes: the claim is checked against the store on disk
        at verification time, read-only, once per cited accession.
        """
        acc = (accession or "").strip().upper()
        if not acc:
            return None
        path = self.p("view", "%s.v%d.tsv" % (VIEW_NAME, VIEW_VERSION))
        with open(path, "r", encoding="utf-8", newline="") as fh:   # READ ONLY, fresh open
            header = fh.readline().rstrip("\n").split("\t")
            col = {name: i for i, name in enumerate(header)}
            mi = col.get("mgi_id")
            ei = col.get("ensembl_gene_id")
            for line in fh:
                f = line.rstrip("\n").split("\t")
                if mi is not None and mi < len(f) and acc == (f[mi] or "").upper():
                    return dict(zip(header, f))
                if ei is not None and ei < len(f) and acc == (f[ei] or "").upper():
                    return dict(zip(header, f))
        return None

    # -- xref artefacts ----------------------------------------------------------------------------
    @property
    def xref_rows(self) -> dict:
        return self._memo("xref", lambda: {r["key"]: r for r in _read_tsv(self.p("xref", "xref.tsv"))})

    @property
    def conflicts(self) -> dict:
        def build():
            out = {}
            for r in _read_tsv(self.p("xref", "conflicts.tsv")):
                out.setdefault(r["key"], []).append(r)
            return out
        return self._memo("conf", build)

    @property
    def retired_index(self) -> dict:
        """retired_symbol(casefold) -> current_symbol (the symbiont's own identity map)."""
        return self._memo("retired_idx", lambda: {
            r["retired_symbol"].casefold(): r["current_symbol"]
            for r in _read_tsv(self.p("xref", "retired.tsv"))
        })

    @property
    def retired_by_current(self) -> dict:
        def build():
            out = {}
            for r in _read_tsv(self.p("xref", "retired.tsv")):
                out.setdefault(r["current_symbol"], []).append(r)
            return out
        return self._memo("retired_cur", build)

    # -- runs / state ------------------------------------------------------------------------------
    @property
    def runs(self) -> list[dict]:
        def build():
            d = self.p("runs")
            out = []
            if not os.path.isdir(d):
                return out
            for name in sorted(os.listdir(d)):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(d, name)
                rec = _read_json(path)
                rec["_path"] = path
                rec["_sha256"] = _sha256_file(path)
                out.append(rec)
            out.sort(key=lambda r: (r.get("finished_at") or "", r.get("run_id") or ""))
            return out
        return self._memo("runs", build)

    @property
    def state(self) -> dict:
        def build():
            d = self.p("state")
            out = {}
            if not os.path.isdir(d):
                return out
            for name in sorted(os.listdir(d)):
                if name.endswith(".json"):
                    src = name[:-5]
                    path = os.path.join(d, name)
                    out[src] = {"record": _read_json(path), "sha256": _sha256_file(path), "path": path}
            return out
        return self._memo("state", build)

    def source_fetched_at(self, source: str) -> str:
        """When were THESE bytes fetched? The run that fetched them says so; a query clock does not.

        R2 (fetch time): the newest run whose snapshot sha256 for this source equals the sha256 the
        published view's provenance names AND whose fetch actually pulled new bytes. None of the
        eight runs in this store is trusted on its name alone.
        """
        prov = self.provenance.get("sources", {}).get(source, {})
        want = {v.get("sha256") for v in (prov.get("snapshots") or {}).values() if v.get("sha256")}
        stamps = []
        for run in self.runs:
            rec = (run.get("sources") or {}).get(source) or {}
            fetched_new = bool((rec.get("fetch") or {}).get("new"))
            shas = {v.get("sha256") for v in (rec.get("snapshots") or {}).values()}
            if fetched_new and want and (want & shas) and run.get("finished_at"):
                stamps.append(run["finished_at"])
        if stamps:
            return max(stamps)
        # the bytes may have been carried in from another store: the snapshot file's own mtime is the
        # only clock left, and it is still the store's record rather than the query's.
        for snap in (prov.get("snapshots") or {}).values():
            path = snap.get("path") or ""
            if path and os.path.exists(path):
                return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat()
        return self.provenance.get("generated_at", "")

    def source_snapshot_sha(self, source: str, reskey: str | None = None) -> str:
        snaps = self.provenance.get("sources", {}).get(source, {}).get("snapshots", {}) or {}
        if reskey:
            return (snaps.get(reskey) or {}).get("sha256", "")
        if snaps:
            return sorted(snaps.items())[0][1].get("sha256", "")
        return ""

    # -- the MGI marker list (traits) --------------------------------------------------------------
    @property
    def trait_markers(self) -> list[dict]:
        """MGI markers whose Marker Type is a trait class (QTL / phenotype), from the raw release file."""
        def build():
            snaps = self.provenance.get("sources", {}).get("mgi", {}).get("snapshots", {}) or {}
            snap = snaps.get("mrk_list1_gz") or {}
            path = snap.get("path") or ""
            if not path or not os.path.exists(path):
                return []
            out = []
            opener = gzip.open if path.endswith(".gz") else open
            # Fast path: 734k rows in the release file, 8.8k of them traits. Testing the raw line for
            # the tab-delimited Marker Type token before splitting keeps a one-shot query near a second.
            qtl_needle, pheno_needle = "\tQTL\t", "\tphenotype\t"
            with opener(path, "rt", encoding="utf-8", errors="replace") as fh:   # READ ONLY
                header = next(fh, "").rstrip("\n").split("\t")
                col = {name: i for i, name in enumerate(header)}
                ti = col.get("Marker Type", 9)
                for line in fh:
                    if qtl_needle not in line and pheno_needle not in line:
                        continue
                    f = line.rstrip("\n").split("\t")
                    if len(f) <= ti or f[ti] not in ("QTL", "phenotype"):
                        continue
                    out.append({
                        "accession": f[col.get("MGI Accession ID", 0)],
                        "symbol": f[col.get("Marker Symbol", 6)],
                        "name": f[col.get("Marker Name", 8)],
                        "type": f[ti],
                        "chromosome": f[col.get("Chr", 1)],
                        "synonyms": f[col.get("Marker Synonyms (pipe-separated)", 11)],
                    })
            return out
        return self._memo("traits", build)


_DEFAULT_STORE: Store | None = None


def store(path: str | None = None) -> Store:
    """One store reader per process (an in-memory cache; still zero writes)."""
    global _DEFAULT_STORE
    if path:
        return Store(path)
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = Store()
    return _DEFAULT_STORE


# --------------------------------------------------------------------------------------------------
# provenance + item construction
# --------------------------------------------------------------------------------------------------
def provenance(st: Store, unreachable: list[dict] | None = None) -> dict:
    prov = st.provenance
    view_path = st.p("view", "%s.v%d.tsv" % (VIEW_NAME, VIEW_VERSION))
    view_sha = _sha256_file(view_path)          # recomputed on every query: integrity, not a claim
    sources = {}
    for name in STORE_SOURCES:
        s = prov.get("sources", {}).get(name)
        if not s:
            continue
        sources[name] = {
            "release": s.get("release"),
            "fetched_at": st.source_fetched_at(name),
            "sha256": {k: v.get("sha256") for k, v in (s.get("snapshots") or {}).items()},
            "licence": (s.get("licence") or {}).get("name"),
            "licence_url": (s.get("licence") or {}).get("url"),
            "published_count": s.get("published_count"),
        }
    return {
        "core": CORE,
        "core_version": CORE_VERSION,
        "read_only": True,
        "store": st.path,
        "view": {"name": VIEW_NAME, "version": VIEW_VERSION, "rows": len(st.view_rows),
                 "sha256": view_sha,
                 "sha256_recorded_in_provenance": prov.get("view_sha256"),
                 "intact": view_sha == prov.get("view_sha256")},
        "sources": sources,
        "unreachable": sorted(unreachable or [], key=lambda u: u["source"]),
        "rules": {
            "accession": ("every item carries the identifier of its source's own namespace "
                          "(MGI:… / ENSMUSG… / PMID…). For a change event the namespace id is the "
                          "release id of the source that changed."),
            "fetched_at": ("the instant the release was fetched, read from the harvest run record "
                           "named in the view provenance — never a clock reading at query time"),
            "sha256": ("content hash of the bytes the item was read from: the raw release snapshot "
                       "for data items, the run/state record for change items"),
        },
    }


def item(value: str, accession: str, source: str, st: Store, reskey: str | None = None,
         sha256: str | None = None) -> dict:
    return {
        "value": value,
        "accession": accession,
        "source": source,
        "release": (st.provenance.get("sources", {}).get(source, {}) or {}).get("release", ""),
        "fetched_at": st.source_fetched_at(source),
        "sha256": sha256 or st.source_snapshot_sha(source, reskey),
    }


def _assert_items(obj, path="response") -> None:
    """Structural guarantee: an item with no accession refuses to be emitted at all."""
    if isinstance(obj, dict):
        if "value" in obj and "accession" in obj:
            if not obj.get("accession"):
                raise AssertionError("item at %s has no accession: %r" % (path, obj))
            for k in ("source", "release", "fetched_at", "sha256"):
                if not obj.get(k):
                    raise AssertionError("item at %s has no %s: %r" % (path, k, obj))
        for k, v in obj.items():
            _assert_items(v, path + "." + str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_items(v, "%s[%d]" % (path, i))


def _empty_subject(tool: str, args: dict, st: Store, unreachable: list[dict]) -> dict:
    """A named empty result. Never `{}`, never a blank string."""
    what = args.get("gene") or args.get("term") or args.get("source") or ""
    return {
        "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": tool},
        "status": "empty",
        "reason": "no_record_for_query",
        "query": args,
        "items": [],
        "message": ("no record for %r in view %s v%d (%d rows); the sources this query needs are "
                    "named above — this is a named empty result, not a blank"
                    % (what, VIEW_NAME, VIEW_VERSION, len(st.view_rows))),
        "provenance": provenance(st, unreachable),
    }


def _unpublished_items(st: Store, xr: dict) -> list[dict]:
    """Every id the sources assert for a key the view did NOT publish — with its accession."""
    items, seen = [], set()
    for ens in [x for x in dict.fromkeys((xr.get("authority_id") or "").split(",")) if x]:
        if ens in seen:
            continue
        seen.add(ens)
        items.append(item("%s (unpublished: %s)" % (ens, xr.get("reason") or xr.get("status")),
                          ens, "ensembl_biomart", st, "biomart_tsv"))
    for mgi in [x for x in dict.fromkeys((xr.get("provider_id") or "").split(",")) if x]:
        if mgi in seen:
            continue
        seen.add(mgi)
        items.append(item("%s (unpublished: %s)" % (mgi, xr.get("reason") or xr.get("status")),
                          mgi, "mgi", st, "mrk_ensembl"))
    return items


def _unpublished_subject(tool: str, a: dict, st: Store, xr: dict,
                         unreachable: list[dict], what: str = "gene") -> dict:
    body = {
        "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": tool},
        "status": "empty",
        "reason": "symbol_not_published",
        "query": a,
        "subject": {"gene": a.get(what), "matched_by": "xref_table", "published": False,
                    "why": xr.get("reason") or xr.get("status")},
        "items": _unpublished_items(st, xr),
        "message": ("%r is asserted by the sources but the published view excludes it: %s. The ids "
                    "are named here rather than dropped — a named empty result, not a blank"
                    % (a.get(what), xr.get("reason") or xr.get("status"))),
        "provenance": provenance(st, unreachable),
    }
    return body


def _unreachable_for(tool: str, sections=()) -> list[dict]:
    out = []
    wanted = set(MISSING_SOURCES)
    for name in sorted(wanted):
        m = MISSING_SOURCES[name]
        if m["would_answer"] == tool or m["would_answer"] in sections:
            out.append({"source": name, "why": m["why"], "needed": m["needed"],
                        "would_answer": m["would_answer"]})
    return out


def _clip(items: list[dict], limit: int) -> tuple[list[dict], dict]:
    return items[:limit], {"total": len(items), "returned": min(len(items), limit),
                           "truncated": len(items) > limit}


# --------------------------------------------------------------------------------------------------
# the queries — every one of them is a reader of the store (six read tools + the opt-in answer mode)
# --------------------------------------------------------------------------------------------------
def q_gene_dossier(st: Store, a: dict) -> dict:
    row, matched_by = st.resolve(a["gene"])
    unreachable = _unreachable_for("gene_dossier",
                                   sections=("phenotype_evidence", "publications", "traits"))
    if row is None:
        xr0 = st.unpublished(a["gene"])
        if xr0 is not None:
            return _unpublished_subject("gene_dossier", a, st, xr0, unreachable)
        return _empty_subject("gene_dossier", a, st, unreachable)

    sym, mgi, ens = row["symbol"], row.get("mgi_id", ""), row.get("ensembl_gene_id", "")
    ident = [
        item(sym, mgi, "mgi", st, "mrk_ensembl"),
        item(mgi, mgi, "mgi", st, "mrk_ensembl"),
        item(ens, ens, "ensembl_biomart", st, "biomart_tsv"),
        item("%s:%s-%s" % (row.get("chromosome", "?"), row.get("start", "?"), row.get("end", "?")),
             ens, "ensembl_biomart", st, "biomart_tsv"),
    ]
    xr = st.xref_rows.get(sym) or {}
    xref_items = []
    if xr:
        xref_items = [
            item("%s = %s (%s)" % (sym, xr.get("authority_id", ""), xr.get("status", "")),
                 xr.get("authority_id") or ens, "ensembl_biomart", st, "biomart_tsv"),
            item("%s = %s (%s)" % (sym, xr.get("provider_id", ""), xr.get("status", "")),
                 xr.get("provider_id") or mgi, "mgi", st, "mrk_ensembl"),
        ]
    conflicts = st.conflicts.get(sym, [])
    conflict_items = [
        item("%s: %s" % (c.get("reason", ""), c.get("detail", "")),
             (c.get("provider_ids") or c.get("authority_ids") or mgi).split(",")[0],
             "mgi", st, "mrk_ensembl")
        for c in conflicts
    ]
    retired = st.retired_by_current.get(sym, [])
    retired_items, retired_meta = _clip(
        [item("%s → %s" % (r["retired_symbol"], r["current_symbol"]),
              r.get("current_provider_id") or mgi, "mgi", st, "mrk_list1_gz") for r in retired],
        a["limit"])

    ph = _unreachable_for("phenotype_evidence")
    pub = _unreachable_for("publications_for")
    tr = _unreachable_for("traits_containing")
    sections = {
        "identity": {"status": "present", "items": ident, "total": len(ident)},
        "xref": {"status": "present" if xref_items else "empty",
                 "items": xref_items + conflict_items + retired_items,
                 "total": len(xref_items) + len(conflict_items) + len(retired_items),
                 "conflicts": len(conflicts), "retired": retired_meta,
                 "reason": None if xref_items else "symbol_not_in_xref_table"},
        "phenotype_evidence": {
            "status": "empty", "items": [], "total": 0,
            "reason": "source_not_in_store", "source": ph[0]["source"] if ph else "",
            "message": ("no phenotype annotation for %s can be answered from this store; the "
                        "missing source is %s" % (sym, ph[0]["source"] if ph else "unknown")),
        },
        "publications": {
            "status": "empty", "items": [], "total": 0,
            "reason": "source_not_in_store", "source": pub[0]["source"] if pub else "",
            "message": ("no publication list for %s can be answered from this store" % sym),
        },
        "traits": {
            "status": "empty", "items": [], "total": 0,
            "reason": "source_not_in_store", "source": (tr[-1] if tr else {"source": ""})["source"],
            "message": ("the store has trait MARKERS (MGI QTL rows) but no trait→gene table; use the "
                        "tool traits_containing for markers by name"),
        },
    }
    return {
        "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": "gene_dossier"},
        "status": "ok",
        "query": a,
        "subject": {"gene": a["gene"], "matched_symbol": sym, "matched_by": matched_by,
                    "mgi_accession": mgi, "ensembl_gene_id": ens},
        "sections": sections,
        "provenance": provenance(st, unreachable),
    }


def q_gene_xref(st: Store, a: dict) -> dict:
    row, matched_by = st.resolve(a["gene"])
    if row is None:
        xr0 = st.unpublished(a["gene"])
        if xr0 is not None:
            return _unpublished_subject("gene_xref", a, st, xr0, [])
        return _empty_subject("gene_xref", a, st, [])
    sym, mgi, ens = row["symbol"], row.get("mgi_id", ""), row.get("ensembl_gene_id", "")
    xr = st.xref_rows.get(sym) or {}
    items = []
    if xr:
        items.append(item("%s → %s" % (sym, xr.get("authority_id", "")),
                          xr.get("authority_id") or ens, "ensembl_biomart", st, "biomart_tsv"))
        items.append(item("%s → %s" % (sym, xr.get("provider_id", "")),
                          xr.get("provider_id") or mgi, "mgi", st, "mrk_ensembl"))
    conflicts = st.conflicts.get(sym, [])
    for c in conflicts:
        items.append(item("conflict %s: %s" % (c.get("reason", ""), c.get("detail", "")),
                          (c.get("provider_ids") or mgi).split(",")[0], "mgi", st, "mrk_ensembl"))
    retired, meta = _clip(
        [item("%s → %s" % (r["retired_symbol"], r["current_symbol"]),
              r.get("current_provider_id") or mgi, "mgi", st, "mrk_list1_gz")
         for r in st.retired_by_current.get(sym, [])], a["limit"])
    items.extend(retired)
    return {
        "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": "gene_xref"},
        "status": "ok" if items else "empty",
        "query": a,
        "subject": {"gene": a["gene"], "matched_symbol": sym, "matched_by": matched_by},
        "items": items,
        "pair": {"symbol": sym, "authority_id": ens, "provider_id": mgi,
                 "status": xr.get("status") or "absent", "reason": xr.get("reason") or ""},
        "counts": {"items": len(items), "conflicts": len(conflicts), "retired": meta},
        "message": None if items else (
            "the symbol resolves in the view but no xref row, conflict or retired entry names it"),
        "provenance": provenance(st, []),
    }


def q_phenotype_evidence(st: Store, a: dict) -> dict:
    unreachable = _unreachable_for("phenotype_evidence")
    row, matched_by = st.resolve(a["gene"])
    body = {
        "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": "phenotype_evidence"},
        "status": "empty",
        "reason": "source_not_in_store",
        "query": a,
        "items": [],
        "subject": ({"gene": a["gene"], "matched_symbol": row["symbol"], "matched_by": matched_by,
                     "mgi_accession": row.get("mgi_id", "")} if row else
                    {"gene": a["gene"], "matched_symbol": None, "matched_by": "none"}),
        "message": ("no phenotype-annotation source is in this store (%s); the gene %s, if it exists "
                    "here, has no evidence that this store can assert — named empty, not a blank"
                    % (unreachable[0]["source"] if unreachable else "none", a["gene"])),
        "provenance": provenance(st, unreachable),
    }
    if row is None:
        body["reason"] = "no_record_for_query_and_no_source_in_store"
        xr0 = st.unpublished(a["gene"])
        if xr0 is not None:
            body["subject"]["matched_by"] = "xref_table"
            body["subject"]["published"] = False
            body["subject"]["why"] = xr0.get("reason") or xr0.get("status")
    return body


def q_publications_for(st: Store, a: dict) -> dict:
    unreachable = _unreachable_for("publications_for")
    row, matched_by = st.resolve(a["gene"])
    return {
        "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": "publications_for"},
        "status": "empty",
        "reason": "source_not_in_store",
        "query": a,
        "items": [],
        "subject": ({"gene": a["gene"], "matched_symbol": row["symbol"], "matched_by": matched_by,
                     "mgi_accession": row.get("mgi_id", "")} if row else
                    {"gene": a["gene"], "matched_symbol": None, "matched_by": "none"}),
        "message": ("no publication source is in this store (%s); zero PMIDs can be asserted, and "
                    "zero is reported as a named empty result, not as a blank"
                    % (unreachable[0]["source"] if unreachable else "none")),
        "provenance": provenance(st, unreachable),
    }


def q_traits_containing(st: Store, a: dict) -> dict:
    term = a["term"].strip().casefold()
    if not term:
        raise ToolError("traits_containing.term must not be empty")
    hits = [m for m in st.trait_markers
            if term in (m["name"] or "").casefold() or term in (m["symbol"] or "").casefold()]
    hits.sort(key=lambda m: (m["name"].casefold(), m["accession"]))
    shown, meta = _clip(
        [item("%s — %s (%s)" % (m["symbol"], m["name"], m["type"]), m["accession"],
              "mgi", st, "mrk_list1_gz") for m in hits], a["limit"])
    return {
        "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": "traits_containing"},
        "status": "ok" if shown else "empty",
        "reason": None if shown else "no_marker_name_contains_term",
        "query": a,
        "items": shown,
        "counts": meta,
        "message": None if shown else (
            "no trait marker (MGI Marker Type QTL/phenotype) in this store has %r in its name or "
            "symbol; %d trait markers were searched" % (a["term"], len(st.trait_markers))),
        "provenance": provenance(st, []),
    }


def q_what_changed(st: Store, a: dict) -> dict:
    src = a["source"].strip()
    since = _iso(a["since"])
    if since is None:
        raise ToolError("what_changed.since must be an ISO-8601 instant (e.g. 2026-11-09T20:00:00Z), "
                        "got %r" % a["since"])
    items, considered = [], 0
    for run in st.runs:
        fin = _iso(run.get("finished_at") or "")
        if fin is None or fin < since:
            continue
        for name, rec in sorted((run.get("sources") or {}).items()):
            considered += 1
            if src.lower() not in ("all", "*") and name != src:
                continue
            rel = ((rec.get("discover") or {}).get("release")) or \
                  ((run.get("publish") or {}).get("release")) or ""
            norm = rec.get("normalise") or {}
            fetch = rec.get("fetch") or {}
            ver = rec.get("verify") or {}
            value = ("%s: rows_in=%s records=%s rejects=%s published_count=%s new_data=%s"
                     % (rel or name, norm.get("rows_in"), norm.get("records"), norm.get("rejects"),
                        (rec.get("discover") or {}).get("published_count"),
                        "yes" if fetch.get("new") else "no (%s)" % (fetch.get("reason") or "")[:60]))
            items.append({
                "value": value,
                "accession": rel or name,
                "source": name,
                "release": rel or name,
                "fetched_at": run.get("finished_at"),
                "sha256": run["_sha256"],
                "run_id": run.get("run_id"),
                "verify_ok": ver.get("ok"),
            })
    items.sort(key=lambda i: (i["fetched_at"], i["source"]))
    shown, meta = _clip(items, a["limit"])
    unreachable = []
    for name in sorted(MISSING_SOURCES):
        if name != src and src.lower() not in ("all", "*"):
            continue
        unreachable.append({"source": name, "why": MISSING_SOURCES[name]["why"],
                            "needed": MISSING_SOURCES[name]["needed"], "would_answer": "n/a (never harvested)"})
    return {
        "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": "what_changed"},
        "status": "ok" if shown else "empty",
        "reason": None if shown else "no_run_since_instant_for_source",
        "query": a,
        "items": shown,
        "counts": dict(meta, source_runs_considered=considered, runs_read=len(st.runs)),
        "message": None if items else (
            "no harvest run recorded a change for source %r since %s (%d run records were read)"
            % (a["source"], a["since"], len(st.runs))),
        "provenance": provenance(st, unreachable),
    }


# --------------------------------------------------------------------------------------------------
# the opt-in answer mode — citation-required at the dossier door
# (req-P1-CITATION-REQUIRED-AT-THE-DOSSIER-DOOR)
#
# The six read tools return structured items with accessions; none of them checks that a COMPOSED
# answer about those items is grounded. This mode does, and it is opt-in: a response carries `mode`
# only when the caller asked for it. It takes {tool, params} — a read tool and that tool's own
# parameters — dispatches it exactly as any door would, and then verifies the composed answer.
#
# The rule, stated once: a sentence is GROUNDED only if
#   * every accession it cites (MGI:… / ENSMUSG…) is present in this response's own `items`, and
#   * the stored record RE-READ for that accession (Store.answer_record — a fresh open of the view
#     file on disk, not the memoised index, not the response's own bytes) agrees with the value the
#     sentence names.
# Otherwise grounded is false and the reason says which of the three failures it was:
#   "no accession"                    the sentence cites no accession at all
#   "accession not in items"          a cited accession is not among this response's items
#   "stored value differs: X vs Y"    the re-read record says Y, the sentence names X
# A refused sentence is returned WITH its reason — never silently dropped, never rewritten.
# --------------------------------------------------------------------------------------------------
ANSWER_ACC_RE = re.compile(r"\b(MGI:\d+|ENSMUSG\d+)\b", re.IGNORECASE)
ANSWER_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z][A-Za-z0-9]*)*")
ANSWER_REASONS = ("no accession", "accession not in items", "stored value differs: X vs Y")


def _answer_sentences(raw) -> list[str]:
    """The composed answer: a JSON array of sentences, or one bare string (one sentence)."""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = raw
    else:
        parsed = raw
    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, list) or not all(isinstance(s, str) for s in parsed):
        raise ToolError("answer.sentences must be a JSON array of strings (or a single sentence string)")
    return list(parsed)


def _answer_items(obj) -> list[dict]:
    """Every item in a response, at any depth: the `items` lists of a tool's sections and body."""
    out: list[dict] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "items" and isinstance(v, list):
                out.extend([i for i in v if isinstance(i, dict)])
            else:
                out.extend(_answer_items(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_answer_items(v))
    return out


def _known_symbols(sentence: str, st: Store) -> list[str]:
    """Every value-token in the sentence that is a symbol the published view asserts (canonical)."""
    out, seen = [], set()
    for tok in ANSWER_WORD_RE.findall(sentence):
        for cand in [tok] + tok.split("-"):
            cf = cand.casefold()
            row = st.view_index["symbol"].get(cf)
            if row is not None and cf not in seen:
                seen.add(cf)
                out.append(row["symbol"])
    return out


def q_answer(st: Store, a: dict) -> dict:
    sentences = _answer_sentences(a["sentences"])
    under = a.get("tool") or "gene_dossier"
    if under not in QUERIES or under == "answer":
        raise ToolError("answer.tool must be one of the read tools (%s), got %r"
                        % (", ".join(t for t in TOOLS if t != "answer"), a.get("tool")))
    under_params = dict(a.get("params") or {})
    if "limit" in SPECS[under]["params"]:
        under_params.setdefault("limit", a["limit"])
    base = QUERIES[under](st, normalize(under, under_params))   # the same dispatch every door uses
    items = _answer_items(base)
    item_accs = {str(it.get("accession") or "").upper() for it in items} - {""}

    out_sentences: list[dict] = []
    accs_checked: list[str] = []
    for s in sentences:
        accs = list(dict.fromkeys(m.upper() for m in ANSWER_ACC_RE.findall(s)))
        entry = {"text": s, "accessions": accs, "grounded": False, "reason": None}
        if not accs:
            entry["reason"] = "no accession"
            entry["message"] = ("the sentence cites no accession (MGI:… / ENSMUSG…); a sentence "
                               "without an accession cannot be grounded against any stored record")
            out_sentences.append(entry)
            continue
        for acc in accs:
            if acc not in accs_checked:
                accs_checked.append(acc)
        fail = None
        for acc in accs:
            if acc not in item_accs:
                fail = {"reason": "accession not in items", "accession": acc,
                        "message": ("%s is not among the accessions of this response's own items "
                                    "(%s); the claim it is cited for cannot be grounded here"
                                    % (acc, ", ".join(sorted(item_accs)) or "none"))}
                break
            row = st.answer_record(acc)            # the RE-READ: a fresh open of the view file
            if row is None:
                fail = {"reason": "accession not in items", "accession": acc,
                        "message": ("no stored record re-reads for %s in view %s v%d"
                                    % (acc, VIEW_NAME, VIEW_VERSION))}
                break
            stored = row.get("symbol", "")
            syms = _known_symbols(s, st)
            if not syms or stored.casefold() in {x.casefold() for x in syms}:
                continue                           # the record asserts every value the sentence names
            sent_value = ", ".join(dict.fromkeys(syms))
            fail = {"reason": "stored value differs: %s vs %s" % (sent_value, stored),
                    "accession": acc, "sentence_value": sent_value, "stored_value": stored,
                    "message": ("the sentence says %s → %s; the stored record re-read for %s says %s"
                                % (acc, sent_value, acc, stored))}
            break
        if fail is not None:
            entry.update(fail)
        else:
            entry["grounded"] = True
            entry["verified_against"] = {acc: (st.answer_record(acc) or {}).get("symbol", "")
                                         for acc in accs}
        out_sentences.append(entry)

    grounded_n = sum(1 for e in out_sentences if e["grounded"])
    refused_n = len(out_sentences) - grounded_n
    status = "empty" if not out_sentences else ("ok" if not refused_n else "refused")
    prov = provenance(st, (base.get("provenance") or {}).get("unreachable") or [])
    if "answer" not in prov["rules"]:
        prov["rules"]["answer"] = (
            "opt-in mode: a sentence is grounded only if every accession it cites is present in this "
            "response's own items AND the stored record re-read for that accession agrees with the "
            "value the sentence names; otherwise grounded:false with one of: "
            + " / ".join(ANSWER_REASONS))
    prov["answer_mode"] = {
        "underlying_tool": under,
        "underlying_params": dict(normalize(under, under_params)),
        "accessions_checked": accs_checked,
        "accessions_checked_count": len(accs_checked),
        "accessions_in_items": len(item_accs),
        "sentences_total": len(out_sentences),
        "sentences_grounded": grounded_n,
        "sentences_refused": refused_n,
    }
    reason = {"ok": None, "empty": "no_sentences_given",
              "refused": "ungrounded_sentences_refused"}[status]
    message = {
        "ok": ("every sentence in the composed answer is grounded: each accession cited is in this "
               "response's items and each stored record re-read agrees with the value named"),
        "empty": "the composed answer carries no sentence; nothing can be grounded",
        "refused": ("%d of %d sentence(s) are not grounded and are returned with their reason — "
                    "never silently dropped" % (refused_n, len(out_sentences))),
    }[status]
    return {
        "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": "answer"},
        "status": status,
        "mode": "answer",
        "reason": reason,
        "query": a,
        "subject": base.get("subject") or {"gene": under_params.get("gene")},
        "items": items,
        "sentences": out_sentences,
        "message": message,
        "provenance": prov,
    }


QUERIES = {
    "gene_dossier": q_gene_dossier,
    "gene_xref": q_gene_xref,
    "phenotype_evidence": q_phenotype_evidence,
    "publications_for": q_publications_for,
    "traits_containing": q_traits_containing,
    "what_changed": q_what_changed,
    "answer": q_answer,
}


# --------------------------------------------------------------------------------------------------
# dispatch — THE one definition site of the surface
# --------------------------------------------------------------------------------------------------
def answer(tool: str, args: dict | None = None, store_path: str | None = None) -> dict:
    """Return the response OBJECT (for callers that need structure, e.g. an HTML renderer)."""
    a = normalize(tool, args)
    st = store(store_path)
    try:
        result = QUERIES[tool](st, a)
    except ToolError:
        raise
    except Exception as exc:                      # a core fault is named, never a traceback to a door
        result = {
            "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": tool},
            "status": "error",
            "reason": "core_fault",
            "query": a,
            "items": [],
            "message": "%s: %s" % (type(exc).__name__, exc),
            "provenance": provenance(st, []),
        }
    _assert_items(result)
    return result


def dispatch(tool: str, args: dict | None = None, store_path: str | None = None) -> bytes:
    """tool + args -> canonical JSON bytes. The CLI, the MCP server, the REST route and the page
    all call THIS and pass the bytes through untouched."""
    try:
        return dumps(answer(tool, args, store_path))
    except ToolError as exc:
        return dumps({
            "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": tool if tool in SPECS else None},
            "status": "error",
            "reason": "bad_request",
            "query": dict(args or {}),
            "items": [],
            "message": str(exc),
            "tools": list(TOOLS),
        })
