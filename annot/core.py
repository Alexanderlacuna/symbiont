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
  * `reason`     (change events only) null when the item's own instant could be placed against the
                 query, else the NAMED reason it could not — today `uncomparable_timestamp`: the
                 record carries no instant, or one `_iso()` cannot parse, so the item is REPORTED
                 with that reason instead of being dropped or crashing the door
                 (req-P1-WHAT-CHANGED-CANNOT-COMPARE-A-NAIVE)
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

CORE = "annot"
CORE_VERSION = 1

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VIEW_NAME = "mouse_gene"
VIEW_VERSION = 1

# --------------------------------------------------------------------------------------------------
# WHICH STORE THE DOORS READ, and what a door does when there is none.
# The harvested store (symbiont/store) is WITHHELD from the public tree, so a fresh clone has no store
# where the docs point — and the first call a stranger makes is the first call that used to die with a
# FileNotFoundError. Three candidates, in the order a box can satisfy them:
#   $ANNOT_STORE                   a pointed store is OBEYED EVEN WHEN IT IS ABSENT: its absence is the
#                                  REFUSED[store-missing] refusal, never a silent fall-back to another
#                                  store (a door that quietly reads a different store is lying)
#   symbiont/store                 the harvested store, when this tree has one
#   symbiont/fixtures/store-doors  the COMMITTED fixture store (241 published rows, raw snapshots,
#                                  run/state records, xref, hashes and licence lines intact) so a clone
#                                  answers out of the box
# With NOTHING on disk the refusal names DEFAULT_STORE, the path the docs tell a stranger to create.
LIVE_STORE = os.path.join(FARM, "symbiont", "store")
FIXTURE_STORE = os.path.join(FARM, "symbiont", "fixtures", "store-doors")
STORE_CANDIDATES = (LIVE_STORE, FIXTURE_STORE)
DEFAULT_STORE = LIVE_STORE          # the path a refusal names when no candidate is on disk

# THE ONE LINE SAYING WHICH STORE ANSWERED. A stranger's first call must not have to open the
# provenance block and compare paths to learn whether the answer came from a store they mined or from
# the fixture this repository ships. The sentence travels IN the response (`provenance.store_line`),
# so it is the same bytes at every door — CLI, MCP, REST and page all pass dispatch()'s bytes through
# untouched — and no door can print a different line than the one the core chose.
STORE_LINE_LIVE = "store: symbiont/store \u2014 the live store"
STORE_LINE_FIXTURE = "store: fixtures/store-doors \u2014 no live store found"


def store_line(path: str, explicit: bool = False) -> str:
    """ONE line: which store answered, and why that one (live, shipped fixture, or pointed)."""
    ap = os.path.abspath(path)
    if not os.path.isdir(ap):
        return "store: %s \u2014 NOT FOUND (neither a live store nor the committed fixture)" % ap
    if ap == os.path.abspath(LIVE_STORE):
        return STORE_LINE_LIVE
    if ap == os.path.abspath(FIXTURE_STORE) and not explicit:
        return STORE_LINE_FIXTURE
    return "store: %s \u2014 pointed by ANNOT_STORE or --store" % ap

# The two commands that CREATE a store, in the order they are run: harvest + project the published
# view, then stage it. Quoted in the refusal with the store path substituted, because a refusal that
# does not say what to do next is a dead end wearing a name.
STORE_CREATE_COMMANDS = (
    "python3 -m symbiont.loop --sources mgi --since auto --store %s",
    "python3 -m symbiont.manifest --store %s --entity gene --out manifest.json",
)

# Sources that CAN answer from the local store, and the sources the surface's shape needs that no
# harvest has delivered yet. A missing source is named in every response that would have used it —
# an absence that is not named is indistinguishable from a blank.
STORE_SOURCES = ("ensembl_biomart", "mgi")

# THE PHENOTYPE ARM'S NAME IS THE NAME THE VIEW CARRIES (req-P1-THE-DOOR-READS-THE-PHENOTYPE-ARM).
# The store's phenotype view is <store>/phenotypes/view/gene_phenotype.v<N>.tsv and it has a `source`
# column; all 699 annotations in the live store carry the token below. Until 2026-11-12 the door
# looked for the name `mgi_phenotype_annotations`, which no row, no receipt and no run record ever
# carried — so a reader was told the station LACKED an arm the station held. One definition, here:
# the lookup name and the view's own `source` value are the same string, and selftest.sh asserts it.
PHENOTYPE_VIEW = "gene_phenotype"
PHENOTYPE_SOURCE = "mgi_genepheno"

MISSING_SOURCES = {
    PHENOTYPE_SOURCE: {
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
        "purpose": ("Phenotype annotations asserted for one gene, one item per annotation: the MP term "
                    "id and label, the evidence code, the citation as the item's accession, the "
                    "genotype statement and the allele marker id. If no phenotype arm is in the store "
                    "the result is a NAMED empty result stating which source is missing; if the arm "
                    "IS present but names no annotation for the gene, the result says that instead — "
                    "never a blank, and never 'no source' for an arm the station holds."),
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
    "research_check": {
        "purpose": ("THE OTHER QUESTION A READER ASKS: can this corpus answer this question — and if "
                    "not, EXACTLY WHY, per dataset? Takes a research spec path, OR the arms/filters "
                    "plus the required and optional datasets, and returns a coverage verdict whose "
                    "reason for each dataset is one of five fixed words: present (with its "
                    "release) · not-admitted · not-enabled · not-acquired · stale (present, but the "
                    "release the store holds is behind the upstream one the last run observed). "
                    "Plus the arms summary and the one verdict line OK | REFUSED[missing-dataset] "
                    "naming what is missing. The verdict is symbiont.research.coverage()'s — the "
                    "same function the loop's pre-flight gate and the manifest call — so the CLI and "
                    "this door can never disagree about whether a spec is executable. It performs "
                    "NO acquisition and modifies nothing: it reads the spec, sources.yml, "
                    "state/<id>.json and runs/*.json, and makes zero requests."),
        "params": {"spec": ("string", False, ""),
                   "research_id": ("string", False, ""),
                   "question": ("string", False, ""),
                   "arms": ("object", False, {}),
                   "filters": ("object", False, {}),
                   "required": ("array", False, []),
                   "optional": ("array", False, []),
                   "sources_config": ("string", False, ""),
                   "limit": ("integer", False, 50)},
    },
}

TOOLS = tuple(SPECS)


class ToolError(Exception):
    """A caller error the core can name (unknown tool, missing/bad parameter)."""


def view_file(store_dir: str) -> str:
    return os.path.join(store_dir, "view", "%s.v%d.tsv" % (VIEW_NAME, VIEW_VERSION))


def provenance_file(store_dir: str) -> str:
    return os.path.join(store_dir, "view", "%s.v%d.provenance.json" % (VIEW_NAME, VIEW_VERSION))


def schema_file(store_dir: str) -> str:
    return os.path.join(store_dir, "view", "%s.v%d.schema.json" % (VIEW_NAME, VIEW_VERSION))


def default_store() -> str:
    """The store a door reads when nothing is pointed at it (see STORE_CANDIDATES)."""
    for cand in STORE_CANDIDATES:
        if os.path.isfile(view_file(cand)):
            return cand
    return DEFAULT_STORE


class StoreMissing(RuntimeError):
    """THE STORE IS NOT THERE — a refusal with a name, never a traceback at a door.

    Measured 2026-11-11 on a fresh clone of the public tree: `tools/call gene_dossier` answered
    `-32603 FileNotFoundError: …/symbiont/store/view/mouse_gene.v1.provenance.json`. Two defects made
    that possible: the refusal taxonomy had no class for 'the store is not there', and the core-fault
    handler called provenance() — another store read — from inside its own except block. This class is
    the missing name; `dispatch()` turns it into REFUSED[store-missing] carrying the path looked for
    and the two commands that create a store.
    """
    code = "store-missing"

    def __init__(self, store: str, looked_for: list):
        self.store = store
        self.looked_for = list(looked_for)
        super().__init__("no store at %s — looked for %s" % (store, ", ".join(self.looked_for)))


def store_missing_response(tool: str | None, args: dict | None, exc: StoreMissing) -> dict:
    """The REFUSED[store-missing] response: the path looked for, and the two commands that build one."""
    p = exc.store
    how = [c % p for c in STORE_CREATE_COMMANDS]
    message = ("REFUSED[store-missing]: no store at %s — looked for %s. A store is created by two "
               "commands, in this order: (1) %s — harvests the sources and projects the published "
               "view; then (2) %s — stages that view with its counts, rejects and licence. Nothing "
               "was read and nothing was written." % (p, " and ".join(exc.looked_for), how[0], how[1]))
    return {
        "annot": {"core": CORE, "core_version": CORE_VERSION,
                  "tool": tool if tool in SPECS else None},
        "status": "refused",
        "reason": "store-missing",
        "query": dict(args or {}),
        "items": [],
        "refusal": {
            "class": "store-missing",
            "store": p,
            "looked_for": list(exc.looked_for),
            "to_create": how,
            "why_this_class": ("the station's refusals (config · budget · source-gone · "
                               "licence-unknown · count-mismatch) are raised while FETCHING a "
                               "source; this one is raised when there is nothing to read at all, "
                               "so it must be its own class and not be mistaken for a bad request"),
        },
        "message": message,
        "provenance": {"core": CORE, "core_version": CORE_VERSION, "read_only": True,
                       "store": p, "store_line": store_line(p), "read": False,
                       "view": None, "sources": {},
                       "unreachable": [], "rules": {}},
    }


# A view's receipt is REGENERATED, never hand-written: `--rebuild-from-raw` re-derives the view,
# its schema sidecar and its provenance sidecar from the store's own raw zone, and refuses on the
# first hash that disagrees with what the store recorded. That is the one command this refusal names.
VIEW_REGEN_COMMAND = "python3 -m symbiont.loop --rebuild-from-raw --store %s"


class ViewUnreceipted(RuntimeError):
    """THE VIEW IS THERE AND ITS RECEIPT IS NOT — a refusal with a name, never a quiet read.

    req-P1-THE-PUBLISHED-DATASET-TRAVELS-WITH-IT (measured 2026-11-11): the station's own rule is
    that a view travels with `<stem>.provenance.json` (source · release · fetched_at · licence ·
    rows · the view's sha256) and `<stem>.schema.json` (the columns). The one published artefact a
    reviewer could open carried neither — no release id, no fetch date, no hash, no licence line.
    The door read it anyway, because it only ever asked for the .tsv. This class is that missing
    refusal: a view whose sidecars are absent is NOT read, and the answer names what is missing and
    the ONE command that regenerates the receipt from the store's own raw zone.
    """
    code = "view-unreceipted"

    def __init__(self, store: str, view: str, missing: list):
        self.store = store
        self.view = view
        self.missing = list(missing)
        super().__init__("view %s carries no receipt — missing %s" % (view, ", ".join(self.missing)))


def view_unreceipted_response(tool: str | None, args: dict | None, exc: ViewUnreceipted) -> dict:
    """The REFUSED[view-unreceipted] response: the view, the sidecars looked for, the one command."""
    how = [VIEW_REGEN_COMMAND % exc.store]
    message = ("REFUSED[view-unreceipted]: the view %s is on disk but its receipt is not — looked for "
               "%s. A view travels with its provenance sidecar (source · release · fetched_at · "
               "licence · rows · the view's sha256) and its schema sidecar (the columns); both are "
               "written by the same publish run that writes the view, so a view without them cannot "
               "be cited. Regenerate them from this store's own raw zone with: %s. Nothing was read "
               "and nothing was written."
               % (exc.view, " and ".join(exc.missing), how[0] if how else "symbiont.loop --rebuild-from-raw"))
    return {
        "annot": {"core": CORE, "core_version": CORE_VERSION,
                  "tool": tool if tool in SPECS else None},
        "status": "refused",
        "reason": "view-unreceipted",
        "query": dict(args or {}),
        "items": [],
        "refusal": {
            "class": "view-unreceipted",
            "store": exc.store,
            "view": exc.view,
            "missing": list(exc.missing),
            "to_regenerate": how[:1],
            "why_this_class": ("store-missing means there is no store at all; this is the opposite "
                               "defect — the data is there and the RECEIPT is gone, so reading it "
                               "would publish a number nobody could trace. Naming it separately is "
                               "what makes the missing sidecar visible instead of silently "
                               "answered around"),
        },
        "message": message,
        "provenance": {"core": CORE, "core_version": CORE_VERSION, "read_only": True,
                       "store": exc.store, "store_line": store_line(exc.store), "read": False,
                       "view": None, "sources": {},
                       "unreachable": [], "rules": {}},
    }


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


def _iso(dt) -> datetime | None:
    """THE ONE timestamp normaliser — every instant this module COMPARES goes through it.

    The rule, stated once: an instant is returned AWARE. `Z`/`z` and an explicit offset parse
    aware; a bare date (`2026-11-01`) or any offset-less timestamp parses NAIVE, and a naive
    instant is READ AS UTC and marked aware HERE. Two timestamps of either shape can therefore
    always be compared, and a naive one can never meet an aware one at a `<` (measured
    2026-11-12: `what_changed(since=2026-11-01)` raised `TypeError: can't compare offset-naive
    and offset-aware datetimes` out of the `since` filter, and every door above it reported
    JSON-RPC -32603 instead of a change list).

    An absent, blank, non-string or unparseable value returns None: the caller REPORTS that it
    cannot place the instant (`uncomparable_timestamp`) rather than raising. A normaliser that
    raises is the fault this function exists to remove.
    """
    if not isinstance(dt, str) or not dt.strip():
        return None
    s = dt.strip()
    if s[-1:] in ("Z", "z"):
        s = s[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        return d.replace(tzinfo=timezone.utc)      # a bare instant is UTC — and it is now AWARE
    return d


# the sort key for an item whose instant cannot be placed: after every real instant, and never
# a None in a key that is compared against datetimes.
_TS_LAST = datetime.max.replace(tzinfo=timezone.utc)


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
        self.path = os.path.abspath(path or os.environ.get("ANNOT_STORE") or default_store())
        self.explicit = bool(path) or bool(os.environ.get("ANNOT_STORE"))
        self.line = store_line(self.path, self.explicit)
        self._cache: dict = {}
        # THE FIRST READ OF A STRANGER'S FIRST CALL. Absence is decided ONCE, here, so that no query
        # can be the place where a missing store is discovered and so that the answer is a refusal
        # with a name (StoreMissing -> REFUSED[store-missing]) instead of a FileNotFoundError at a door.
        # A VIEW IS NOT READ UNLESS ITS RECEIPT IS THERE (req-P1-THE-PUBLISHED-DATASET-TRAVELS-WITH-IT):
        # the .tsv alone is a number nobody can trace, so the two sidecars are part of what 'a view'
        # means here — provenance.json (source · release · fetched_at · licence · rows · sha256) and
        # schema.json (the columns). Both, or the door refuses by name.
        vf, pf, sf = view_file(self.path), provenance_file(self.path), schema_file(self.path)
        if not os.path.isdir(self.path):
            raise StoreMissing(self.path, [vf, pf, sf])
        if not os.path.isfile(vf):
            raise StoreMissing(self.path, [vf, pf])      # no view at all: the store's own absence
        missing = [f for f in (pf, sf) if not os.path.isfile(f)]
        if missing:
            raise ViewUnreceipted(self.path, vf, missing)

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

    # -- the literature arm (req-P2 ... evidence_level STAMPED, 2026-11-12) ------------------------
    # The store's literature view is <store>/literature/view/gene_publication.v<N>.tsv. It is READ
    # LAZILY and ABSENT-TOLERANT: a store without a literature arm is the normal case, and the door
    # answers with a NAMED empty ("no publication source is in this store") rather than a blank.
    # evidence_level is CARRIED THROUGH TO THE READER: an abstract-derived claim and a full-text one
    # must never look the same in a door response.
    @property
    def literature_rows(self) -> list[dict]:
        def load():
            d = self.p("literature", "view")
            if not os.path.isdir(d):
                return []
            best = None
            for f in sorted(os.listdir(d)):
                m = re.match(r"gene_publication\.v(\d+)\.tsv$", f)
                if m and (best is None or int(m.group(1)) > best[0]):
                    best = (int(m.group(1)), f)
            if not best:
                return []
            self._literature_view = os.path.join(d, best[1])
            rows = _read_tsv(self._literature_view)
            return rows
        return self._memo("lit", load)

    @property
    def literature_provenance(self) -> dict:
        def load():
            d = self.p("literature", "view")
            if not os.path.isdir(d):
                return {}
            best = None
            for f in sorted(os.listdir(d)):
                m = re.match(r"gene_publication\.v(\d+)\.provenance\.json$", f)
                if m and (best is None or int(m.group(1)) > best[0]):
                    best = (int(m.group(1)), f)
            return _read_json(os.path.join(d, best[1])) if best else {}
        return self._memo("lit_prov", load)

    def literature_index(self) -> dict:
        def build():
            by_gene: dict = {}
            for r in self.literature_rows:
                for key in ("mgi_id", "ensembl_gene_id"):
                    v = (r.get(key) or "").strip()
                    if v:
                        by_gene.setdefault(v.upper(), []).append(r)
            return by_gene
        return self._memo("lit_idx", build)

    # -- the phenotype arm (req-P1-THE-DOOR-READS-THE-PHENOTYPE-ARM, 2026-11-12) -------------------
    # The store's phenotype view is <store>/phenotypes/view/gene_phenotype.v<N>.tsv, read EXACTLY as the
    # literature view is: highest version wins, LAZILY, and ABSENT-TOLERANT — a store without a
    # phenotype arm is the normal case and the door says so by name. The arm is the same shape as the
    # literature arm, so it is read the same way; what differs is that its `source` column carries
    # PHENOTYPE_SOURCE, which is the name the door looks for.
    @property
    def phenotype_rows(self) -> list[dict]:
        def load():
            d = self.p("phenotypes", "view")
            if not os.path.isdir(d):
                return []
            best = None
            for f in sorted(os.listdir(d)):
                m = re.match(r"%s\.v(\d+)\.tsv$" % PHENOTYPE_VIEW, f)
                if m and (best is None or int(m.group(1)) > best[0]):
                    best = (int(m.group(1)), f)
            return _read_tsv(os.path.join(d, best[1])) if best else []
        return self._memo("pheno", load)

    @property
    def phenotype_provenance(self) -> dict:
        """The arm's RECEIPT: terms_release, run_id (and the records sha256) travel with every item."""
        def load():
            d = self.p("phenotypes", "view")
            if not os.path.isdir(d):
                return {}
            best = None
            for f in sorted(os.listdir(d)):
                m = re.match(r"%s\.v(\d+)\.provenance\.json$" % PHENOTYPE_VIEW, f)
                if m and (best is None or int(m.group(1)) > best[0]):
                    best = (int(m.group(1)), f)
            return _read_json(os.path.join(d, best[1])) if best else {}
        return self._memo("pheno_prov", load)

    @property
    def arms_present(self) -> set:
        """The sources this store can ACTUALLY answer from — the list a named absence must consult.

        One definition, asked by every place that would otherwise name a source as unreachable: an arm
        the store holds is not unreachable, and saying it is would be the honest-looking lie this row
        exists to remove.
        """
        return self._memo("arms", lambda: ({PHENOTYPE_SOURCE} if self.phenotype_rows else set()))

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
            # a run record's timestamp is a STRING here and may be absent; coercing the sort key
            # keeps one malformed record from raising TypeError out of every door's store read
            # (`5 < "x"`), while leaving the order of every well-formed store exactly as it was.
            out.sort(key=lambda r: (str(r.get("finished_at") or ""), str(r.get("run_id") or "")))
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
        "store_line": st.line,          # ONE line: which store answered (see store_line())
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
                    # ONE named exception (req-P1-WHAT-CHANGED-CANNOT-COMPARE-A-NAIVE): a change
                    # item whose record carries NO instant says so ON the item
                    # (reason=uncomparable_timestamp) instead of being dropped or raising. The
                    # key must still be PRESENT — the item may never hide that it has no instant
                    # — and no other item, and no other tool, may be blank here.
                    if (k == "fetched_at" and "fetched_at" in obj
                            and obj.get("reason") == "uncomparable_timestamp"):
                        continue
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


def _unreachable_for(tool: str, sections=(), st: "Store | None" = None) -> list[dict]:
    """The sources this answer would have needed and the store does NOT hold.

    `st` is passed by every read door: a source listed in MISSING_SOURCES is a source THE SURFACE
    NEEDS, not a source the store LACKS — `mgi_genepheno` has been harvested since the phenotype arm
    landed, so it is unreachable only in a store whose phenotype view is absent (st.arms_present).
    Without this, every phenotype answer would name its own arm as missing.
    """
    present = st.arms_present if st is not None else set()
    out = []
    wanted = set(MISSING_SOURCES)
    for name in sorted(wanted):
        m = MISSING_SOURCES[name]
        if name in present:
            continue
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
                                   sections=("phenotype_evidence", "publications", "traits"), st=st)
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

    ph = _unreachable_for("phenotype_evidence", st=st)
    pub = _unreachable_for("publications_for", st=st)
    tr = _unreachable_for("traits_containing", st=st)
    # THE PHENOTYPE SECTION IS THE SAME READER AS THE phenotype_evidence DOOR (req-P1-THE-DOOR-READS-
    # THE-PHENOTYPE-ARM): one shape, one source name, one presence test — a dossier that said "no
    # arm" while the tool beside it answered from the arm would be two doors disagreeing.
    ph_sec = _phenotype_section(st, row, a)
    sections = {
        "identity": {"status": "present", "items": ident, "total": len(ident)},
        "xref": {"status": "present" if xref_items else "empty",
                 "items": xref_items + conflict_items + retired_items,
                 "total": len(xref_items) + len(conflict_items) + len(retired_items),
                 "conflicts": len(conflicts), "retired": retired_meta,
                 "reason": None if xref_items else "symbol_not_in_xref_table"},
        "phenotype_evidence": {
            "status": ph_sec["status"], "items": ph_sec["items"],
            "total": ph_sec["counts"]["total"], "reason": ph_sec["reason"],
            "source": ph_sec["source"], "message": ph_sec["message"], "counts": ph_sec["counts"],
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


def _phenotype_rows_for(st: Store, row: dict) -> list[dict]:
    """The arm's rows that name THIS gene. The join keys are the gene's own accessions, upper-cased."""
    keys = {k for k in ((row.get("mgi_id") or "").strip().upper(),
                        (row.get("ensembl_gene_id") or "").strip().upper()) if k}
    hits = [r for r in st.phenotype_rows
            if keys & {(r.get("gene_id") or "").strip().upper(),
                       (r.get("ensembl_gene_id") or "").strip().upper()}]
    hits.sort(key=lambda r: ((r.get("phenotype_term_id") or ""), (r.get("citation") or ""),
                             (r.get("allele_marker_id") or "")))
    return hits


def _phenotype_section(st: Store, row: dict | None, a: dict) -> dict:
    """THE ONE READER OF THE PHENOTYPE ARM — one shape, one source name, one presence test.

    Returns {status, reason, items, counts, source, message} and NOTHING else, because two doors (the
    phenotype_evidence tool and the gene_dossier section) must not be able to disagree about what the
    arm says. The three answers it can give, and they are deliberately three:
      * the store has NO phenotype view    -> status=empty reason=source_not_in_store, source named
      * the arm IS present, gene unnamed    -> status=empty reason=no_phenotype_for_gene, the arm's row
                                              count in the message (the arm is NOT called missing)
      * the arm IS present, rows for gene   -> status=ok, one item per annotation
    An item carries the MP term id and label, the EVIDENCE CODE, the CITATION as its accession, the
    genotype statement, the allele marker id, the source's own release stamp, and release/sha256
    stamped from the view's receipt (terms_release · run_id) so the bytes are traceable.
    """
    prov = st.phenotype_provenance or {}
    rel = prov.get("terms_release") or ""
    run = prov.get("run_id") or ""
    n_arm = len(st.phenotype_rows)
    sec = {"status": "empty", "reason": "source_not_in_store", "items": [],
           "counts": {"total": 0, "returned": 0, "truncated": False},
           "source": PHENOTYPE_SOURCE,
           "message": ("no phenotype-annotation source is in this store (%s): the store has no "
                       "phenotypes/view/%s.v<N>.tsv, so zero annotations can be asserted for %s — "
                       "named empty, not a blank" % (PHENOTYPE_SOURCE, PHENOTYPE_VIEW, a["gene"]))}
    if not n_arm:
        return sec                                    # (1) no arm in this store
    if row is None:
        sec["reason"] = "no_record_for_query"
        sec["message"] = ("the phenotype arm IS present (%d annotation(s) in the store) but %r has no "
                          "record in the gene view, so the arm cannot be joined to it"
                          % (n_arm, a["gene"]))
        return sec
    hits = _phenotype_rows_for(st, row)
    if not hits:                                      # (2) arm present, nothing for this gene
        sec["reason"] = "no_phenotype_for_gene"
        sec["message"] = ("the phenotype arm IS present (%d annotation(s) in the store, source %s) but "
                          "none of them names %s — an empty FOR THIS GENE, not a missing source"
                          % (n_arm, PHENOTYPE_SOURCE, row["symbol"]))
        return sec
    items = []
    for r in hits:                                    # (3) arm present, rows for this gene
        term_id = (r.get("phenotype_term_id") or "").strip()
        cite = (r.get("citation") or "").strip()
        items.append(dict(
            # THE ACCESSION IS THE CITATION (the PMID the source wrote). 52 of the store's 699 rows
            # carry none: for those the MP term id is the identifier the row does have, and an item
            # with no accession would refuse to be emitted at all (_assert_items).
            item("%s %s — %s" % (term_id, r.get("phenotype_term") or "?",
                                 r.get("statement") or "?"),
                 cite or term_id, r.get("source") or PHENOTYPE_SOURCE, st, "gene_phenotype"),
            phenotype_term_id=term_id,
            phenotype_term=r.get("phenotype_term") or "",
            evidence_code=r.get("evidence_code") or "",
            citation=cite,
            allele_marker_id=r.get("allele_marker_id") or "",
            statement=r.get("statement") or "",
            source_release=r.get("source_release") or "",
            **{"release": rel, "sha256": run, "fetched_at": r.get("fetched_at") or ""}))
    shown, meta = _clip(items, a.get("limit") or 50)
    sec.update(status="ok", reason=None, items=shown, counts=meta)
    return sec


def q_phenotype_evidence(st: Store, a: dict) -> dict:
    unreachable = _unreachable_for("phenotype_evidence", st=st)
    row, matched_by = st.resolve(a["gene"])
    sec = _phenotype_section(st, row, a)
    body = {
        "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": "phenotype_evidence"},
        "status": sec["status"],
        "reason": sec["reason"],
        "query": a,
        "items": sec["items"],
        "counts": sec["counts"],
        "subject": ({"gene": a["gene"], "matched_symbol": row["symbol"], "matched_by": matched_by,
                     "mgi_accession": row.get("mgi_id", "")} if row else
                    {"gene": a["gene"], "matched_symbol": None, "matched_by": "none"}),
        "message": sec["message"],
        "provenance": provenance(st, unreachable),
    }
    if row is None:
        if not st.phenotype_rows:
            body["reason"] = "no_record_for_query_and_no_source_in_store"
        xr0 = st.unpublished(a["gene"])
        if xr0 is not None:
            body["subject"]["matched_by"] = "xref_table"
            body["subject"]["published"] = False
            body["subject"]["why"] = xr0.get("reason") or xr0.get("status")
    return body


def q_publications_for(st: Store, a: dict) -> dict:
    unreachable = _unreachable_for("publications_for", st=st)
    row, matched_by = st.resolve(a["gene"])
    # THE LITERATURE ARM, WHEN THE STORE HAS ONE. evidence_level · licence · abstract_stored travel
    # with every item: a reader must be able to tell an abstract-derived claim from a full-text one,
    # and a licence-bearing title from one we may not store.
    if row is not None and st.literature_rows:
        hits = []
        for key in (row.get("mgi_id"), row.get("ensembl_gene_id")):
            k = (key or "").strip().upper()
            if k:
                hits.extend(st.literature_index().get(k, []))
        seen, uniq = set(), []
        for h in hits:
            if h.get("pmid") and h["pmid"] not in seen:
                seen.add(h["pmid"]); uniq.append(h)
        uniq.sort(key=lambda h: (h.get("year") or "", h.get("pmid") or ""), reverse=True)
        # THE RECEIPT IS PART OF EVERY ITEM: the literature view's provenance names the release
        # (source_release) and the run (run_id); the door stamps both, so an item is traceable to the
        # bytes it came from without the reader knowing anything about the store layout.
        rel = (st.literature_provenance or {}).get("source_release") or ""
        run = (st.literature_provenance or {}).get("run_id") or ""
        shown, meta = _clip([
            dict(item("%s (%s, %s)" % (h.get("title") or "(title not stored)",
                                       h.get("journal") or "?", h.get("year") or "?"),
                      "PMID:%s" % h["pmid"], "pubmed", st, "gene_publication"),
                 evidence_level=h.get("evidence_level") or "unknown",
                 abstract_stored=(h.get("abstract_stored") or "no"),
                 licence=h.get("licence") or "",
                 year=h.get("year") or "", journal=h.get("journal") or "",
                 **{"release": rel, "sha256": run})
            for h in uniq], a.get("limit"))
        return {
            "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": "publications_for"},
            "status": "ok" if shown else "empty",
            "reason": None if shown else "no_publication_for_gene",
            "query": a,
            "items": shown,
            "counts": meta,
            "subject": {"gene": a["gene"], "matched_symbol": row["symbol"], "matched_by": matched_by,
                        "mgi_accession": row.get("mgi_id", "")},
            "message": None if shown else (
                "the literature arm is present (%d publication(s) in the store) but none of them names %s"
                % (len(st.literature_rows), row["symbol"])),
            "provenance": provenance(st, []),
        }
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
        # a malformed ARGUMENT is refused by name at every door (status=error reason=bad_request);
        # what this row forbids is an argument set the schema ACCEPTS crashing the door instead.
        raise ToolError("what_changed.since must be an ISO-8601 instant (e.g. 2026-11-09T20:00:00Z), "
                        "got %r" % (a["since"],))
    items, considered, uncomparable = [], 0, 0
    for run in st.runs:
        stamp = run.get("finished_at")
        fin = _iso(stamp)                 # THE one normaliser: aware, or None when unplaceable
        if fin is not None and fin < since:
            continue                      # placeable AND before the instant: not a change since it
        # fin is None: the record carries no instant, or one _iso() cannot parse, so the record
        # CANNOT be placed against `since`. It is not dropped (a dropped run is a wrong answer that
        # looks complete) and it does not raise: it is carried into the answer and says why itself.
        placeable = fin is not None
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
                "fetched_at": stamp,          # the record's own instant, unconverted and unguessed
                "sha256": run["_sha256"],
                "run_id": run.get("run_id"),
                "verify_ok": ver.get("ok"),
                "reason": None if placeable else "uncomparable_timestamp",
            })
            if not placeable:
                uncomparable += 1
    # placeable items in time order, then the unplaceable ones — which have no instant to sort by,
    # so they fall after every real instant and are ordered among themselves by (source, run_id).
    # `_TS_LAST` exists so this key never compares None to a datetime.
    items.sort(key=lambda i: (_iso(i["fetched_at"]) or _TS_LAST,
                              str(i.get("source") or ""), str(i.get("run_id") or "")))
    shown, meta = _clip(items, a["limit"])
    unreachable = []
    for name in sorted(MISSING_SOURCES):
        if name != src and src.lower() not in ("all", "*"):
            continue
        if name in st.arms_present:      # an arm the store HOLDS is not "never harvested"
            continue
        unreachable.append({"source": name, "why": MISSING_SOURCES[name]["why"],
                            "needed": MISSING_SOURCES[name]["needed"], "would_answer": "n/a (never harvested)"})
    return {
        "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": "what_changed"},
        "status": "ok" if shown else "empty",
        "reason": None if shown else "no_run_since_instant_for_source",
        "query": a,
        "items": shown,
        "counts": dict(meta, source_runs_considered=considered, runs_read=len(st.runs),
                       uncomparable_timestamp=uncomparable),
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


# --------------------------------------------------------------------------------------------------
# research_check — THE READING DOOR THAT SAYS WHY THE EVIDENCE IS NOT THERE
# (req-P1-RESEARCH-CHECK-a-read-door-that-says)
# --------------------------------------------------------------------------------------------------
# The six read tools answer questions about the evidence the store HAS. This one answers a different
# question — can the corpus satisfy THIS question? — and, when it cannot, says WHICH dataset is
# absent and WHICH KIND of absent it is, because an empty phenotype section can mean five different
# things: no biology · no dataset admitted · admitted but disabled · admitted but never fetched ·
# fetched but stale. The reader sees one blank; the station knows five facts.
#
# THE ONE-DEFINITION RULE, stated here because it is a rule about DISAGREEMENT and not taste:
#   the coverage verdict below is `symbiont.research.coverage()`'s, called with this store's own
#   `state/` and `runs/`. This module composes NO coverage opinion of its own — it never inspects a
#   dataset's presence itself, it prints what the loop's evaluator says. A second evaluator inside
#   the MCP door is how the CLI and the MCP door start disagreeing about whether a spec is executable.
#
# WHAT IT REFUSES TO DO: acquire. `research_run` is out of scope precisely so the reading door can
# never be the thing that fetches, and the two sentences below travel in every answer so a reader
# does not have to take that on trust.
RESEARCH_READ_ONLY_SENTENCES = ("acquisition performed: NO", "corpus modified: NO")


def _research_modules():
    """The registry parser and the coverage evaluator, imported LAZILY: this file imports only the
    standard library at load time, which is what keeps the "the server cannot write, and this core
    opens no socket" property true by construction rather than by inspection."""
    try:
        from symbiont import contract as scontract
        from symbiont import research as sresearch
    except ModuleNotFoundError:                 # a door started from outside the farm root
        if FARM not in sys.path:
            sys.path.insert(0, FARM)
        from symbiont import contract as scontract
        from symbiont import research as sresearch
    return scontract, sresearch


def _research_unavailable(why: str) -> dict:
    """The named refusal for a door that cannot reach the research layer: it says so, and it says
    that nothing was read and nothing was written — instead of a traceback or a quiet blank."""
    return {
        "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": "research_check"},
        "status": "refused",
        "reason": "research-unavailable",
        "items": [],
        "message": ("REFUSED[research-unavailable]: the coverage evaluator could not be imported (%s). "
                    "The verge a check would have to invent is a second evaluator, so this door "
                    "invents nothing: NOTHING was read from the corpus, acquisition performed: NO, "
                    "corpus modified: NO" % why),
        "provenance": {"core": CORE, "core_version": CORE_VERSION, "read_only": True,
                       "read": False, "sources": {}, "unreachable": [], "rules": {}},
    }


def q_research_check(st: Store, a: dict) -> dict:
    """Can this corpus answer this question, and if not, exactly why PER DATASET?

    Two ways in, one verdict: a SPEC PATH (the artefact a researcher writes, validated by the SAME
    schema the loop reads) or the arms/filters + required/optional datasets (validated by that same
    schema, through research.spec_from_requirements, with every default it applies named back in
    `research.defaults_applied`).
    """
    try:
        scontract, sresearch = _research_modules()
    except Exception as exc:                    # no research layer: named, not a traceback
        return _research_unavailable("%s: %s" % (type(exc).__name__, exc))
    try:
        sources_path = a.get("sources_config") or ""
        if not sources_path:
            beside = os.path.join(st.path, "sources.yml")   # the config the last run persisted
            sources_path = beside if os.path.isfile(beside) else scontract.DEFAULT_SOURCES
        sources = scontract.load_sources(sources_path)       # refuses BY NAME (ConfigError)

        defaults: list[str] = []
        spec_path = a.get("spec") or ""
        if spec_path:
            spec = sresearch.load_spec(spec_path)            # THE schema, not a copy of it
        else:
            spec, defaults = sresearch.spec_from_requirements(
                research_id=a.get("research_id") or "",
                question=a.get("question") or "",
                arms=a.get("arms") or {},
                filters=a.get("filters") or {},
                required=a.get("required") or [],
                optional=a.get("optional") or [])

        # ── THE VERDICT IS NOT THIS MODULE'S TO MAKE ────────────────────────────────────────
        # One call, the loop's own evaluator, this store's own state/ and runs/. If a reader ever
        # wants to know why the MCP door and the CLI agree, the answer is this line.
        cov = sresearch.coverage(spec, sources, store=st.path, spec_path=spec_path)
    except scontract.ConfigError as exc:
        return {
            "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": "research_check"},
            "status": "error",
            "reason": "bad_spec",
            "refusal": {"class": getattr(exc, "code", "config"), "detail": str(exc)},
            "query": a,
            "items": [],
            "message": ("REFUSED[%s]: %s — the check refuses rather than guess what was meant. "
                        "acquisition performed: NO · corpus modified: NO"
                        % (getattr(exc, "code", "config"), exc)),
            "provenance": provenance(st),
        }

    sel = cov["selection"]
    arm_rows = {}
    for arm, value in (sel.get("arms") or {}).items():
        arm_rows[arm] = {
            "declared": value is not None,
            "value": value,
            "term_sets": list((sel.get("filters") or {}).get("term_sets", {}).get(arm) or []),
        }
    arms_summary = ("arms: " + " · ".join(
        "%s: %s" % (arm, ("declared" if r["declared"] else "null (not part of this investigation)"))
        for arm, r in arm_rows.items()))
    counted = {}
    for row in cov["datasets"]:
        counted[row["status"]] = counted.get(row["status"], 0) + 1
    counts_line = "datasets: " + " · ".join("%s=%d" % (k, v) for k, v in sorted(
        counted.items(), key=lambda kv: str(kv[0])))

    message = ("%s — %s (%s). %s · %s" % (cov["verdict"], counts_line, arms_summary,
                                           RESEARCH_READ_ONLY_SENTENCES[0],
                                           RESEARCH_READ_ONLY_SENTENCES[1]))
    return {
        "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": "research_check"},
        "status": "ok" if cov["verdict_ok"] else "refused",
        "reason": "" if cov["verdict_ok"] else "missing-dataset",
        "query": a,
        # THE VERDICT LINE, verbatim from the evaluator: OK | REFUSED[missing-dataset] naming what
        # is missing and WHICH KIND of missing it is.
        "verdict": cov["verdict"],
        "verdict_ok": cov["verdict_ok"],
        "research": {
            "research_id": cov["research_id"],
            "spec_sha256": cov["spec_sha256"],
            "spec_path": cov["spec_path"] or "(inline requirement set — no spec file was named)",
            "defaults_applied": list(defaults),
            "sources_config": os.path.abspath(sources_path),
        },
        "arms": arm_rows,
        "arms_summary": arms_summary,
        "filters": sel.get("filters"),
        # ONE ROW PER DECLARED DATASET: which one, whether it was required, and WHY it is there or
        # not in one of the five fixed words, with the file that says so.
        "datasets": cov["datasets"],
        "dataset_status": cov["dataset_status"],
        "status_vocabulary": cov["status_vocabulary"],
        "presence_evaluated": cov["presence_evaluated"],
        # the loop's own block, decisive fields only, so the door cannot state coverage differently
        # from the run record and the manifest
        "coverage": {k: cov[k] for k in ("status", "complete", "missing", "missing_by_class",
                                         "missing_detail", "required", "optional", "available")},
        "evaluator": {
            "function": "symbiont.research.coverage",
            "one_definition": ("the SAME function the loop's pre-flight gate and the manifest call; "
                               "this door composes no verdict of its own, because two evaluators "
                               "would let the CLI and the MCP door disagree about whether a spec "
                               "is executable"),
            "presence_read_from": [os.path.join(st.path, "state"), os.path.join(st.path, "runs")],
            "requests_made": 0,
            "writes_made": 0,
        },
        "sentences": list(RESEARCH_READ_ONLY_SENTENCES),
        "items": [],
        "message": message,
        "provenance": provenance(st),
    }


QUERIES = {
    "gene_dossier": q_gene_dossier,
    "gene_xref": q_gene_xref,
    "phenotype_evidence": q_phenotype_evidence,
    "publications_for": q_publications_for,
    "traits_containing": q_traits_containing,
    "what_changed": q_what_changed,
    "answer": q_answer,
    "research_check": q_research_check,
}


# --------------------------------------------------------------------------------------------------
# dispatch — THE one definition site of the surface
# --------------------------------------------------------------------------------------------------
def _unreadable_provenance(st: Store | None, why: str) -> dict:
    """The provenance of a store that could not be read.

    provenance() itself reads the store (the view provenance sidecar, the view's sha256, the run and
    state records). A fault raised by a store read is therefore a fault provenance() can raise TOO —
    which is exactly how a core fault became a traceback on a fresh clone (2026-11-11). So the fault
    handler asks for provenance through HERE, outside its own except block, and a failed read answers
    a provenance that SAYS it is unreadable instead of raising again.
    """
    try:
        if st is None:
            raise RuntimeError("no store reader")
        return provenance(st, [])
    except Exception as exc:
        return {"core": CORE, "core_version": CORE_VERSION, "read_only": True,
                "store": (st.path if st is not None else None),
                "store_line": (st.line if st is not None else "store: none \u2014 no store reader"),
                "read": False,
                "view": None, "sources": {}, "unreachable": [], "rules": {},
                "unreadable": "%s: %s" % (type(exc).__name__, exc), "why": why}


def answer(tool: str, args: dict | None = None, store_path: str | None = None) -> dict:
    """Return the response OBJECT (for callers that need structure, e.g. an HTML renderer)."""
    a = normalize(tool, args)
    st: Store | None = None
    result: dict | None = None
    fault = ""
    try:
        st = store(store_path)
        result = QUERIES[tool](st, a)
    except ToolError:
        raise
    except StoreMissing:
        raise                                 # dispatch() owns the store-missing REFUSAL
    except ViewUnreceipted:
        raise                                 # dispatch() owns the view-unreceipted REFUSAL
    except Exception as exc:                  # a core fault is named, never a traceback to a door
        fault = "%s: %s" % (type(exc).__name__, exc)
    if result is None:
        # THE HANDLER BODY LIVES OUT HERE, not inside the except block: nothing in it can re-enter the
        # store and raise a second exception from inside the handler that claims to prevent exactly
        # that (measured on a fresh clone, 2026-11-11: provenance() raised FileNotFoundError HERE).
        result = {
            "annot": {"core": CORE, "core_version": CORE_VERSION, "tool": tool},
            "status": "error",
            "reason": "core_fault",
            "query": a,
            "items": [],
            "message": fault,
            "provenance": _unreadable_provenance(st, fault),
        }
    _assert_items(result)
    return result


def dispatch(tool: str, args: dict | None = None, store_path: str | None = None) -> bytes:
    """tool + args -> canonical JSON bytes. The CLI, the MCP server, the REST route and the page
    all call THIS and pass the bytes through untouched."""
    try:
        return dumps(answer(tool, args, store_path))
    except StoreMissing as exc:
        # the store is not there: a REFUSAL with a class, the path looked for and the two commands
        # that create one — from every door, because every door calls this function.
        return dumps(store_missing_response(tool, args, exc))
    except ViewUnreceipted as exc:
        # the view is there and its receipt is not: also a REFUSAL with a class, and from every door.
        return dumps(view_unreceipted_response(tool, args, exc))
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
