"""THE PHENOTYPES ADAPTER CONTRACT — the schema of symbiont/phenotypes/SCHEMA.md, in code.

The record, the conflict rule and the authority split are DECIDED in SCHEMA.md (written before
this file); this module is the mechanical side of that decision: the column order of the store's
tables, the sources.yml schema (total — unknown keys are refused by name), and the refusal
vocabulary. Refusal types are REUSED from the gene arm (one definition site of refusal codes).

Adapter shape (a source is a plug-in file, the loop is unchanged):

    term_authority source        configure(cfg) · fetch_terms(store)      -> rows + file facts
    association_provider source  configure(cfg) · fetch_associations()    -> rows + file facts

Both fetches return dicts, never invent fields, and never join: the loop owns the join, the
conflict count and the accounting, so a source cannot smuggle a resolution rule of its own.
"""
from __future__ import annotations

import os
import re

import yaml

from ..contract import BudgetExceeded, ConfigError, LicenceUnknown, Refusal  # noqa: F401 (re-export)

TERM_ID_RE = re.compile(r"^[A-Za-z]+:\d+$")          # MP:0001179 — an ontology id, or it is nothing
ROLES = ("term_authority", "association_provider")

# sources.yml — the ONE definition site for this arm's sources. The schema is total: an unknown
# key is refused by name, so config and code can never disagree about what exists.
SOURCE_KEYS = ("id", "role", "adapter", "enabled", "note", "base_url", "endpoints", "paths",
               "rate_limit", "licence_url", "licence_text", "licence_pattern", "evidence_codes",
               "gene_scope", "budget", "staleness")
TERMS_ENDPOINT_KEYS = ("terms",)
ASSOC_ENDPOINT_KEYS = ("associations",)
BUDGET_KEYS = ("requests", "bytes", "wall_s")


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def load_sources(path: str) -> list[dict]:
    """Parse + validate the phenotypes sources.yml -> the roster, roles intact.

    Unlike the literature arm (one source), this arm runs a ROSTER — the conflict rule needs at
    least the possibility of two association providers — but the roles are fixed: exactly ONE
    term_authority (the ontology is the single authority for the term, SCHEMA.md §2) and one or
    more association providers."""
    _need(os.path.isfile(path), f"phenotypes sources config missing: {path}")
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    _need(isinstance(raw, dict), f"{path}: top level must be a mapping with `version` and `sources`")
    _need(raw.get("version") == 1, f"{path}: unsupported version {raw.get('version')!r}")
    srcs = raw.get("sources")
    _need(isinstance(srcs, list) and srcs,
          f"{path}: `sources` must be a non-empty list (this arm runs a roster)")
    ids = []
    for cfg in srcs:
        _need(isinstance(cfg, dict), f"{path}: a source entry is not a mapping")
        unknown = [k for k in cfg if k not in SOURCE_KEYS]
        _need(not unknown, f"{path}: source carries unknown key(s) {sorted(unknown)} — "
                           f"the schema is {list(SOURCE_KEYS)}")
        missing = [k for k in ("id", "role", "adapter", "enabled", "note") if k not in cfg]
        _need(not missing, f"{path}: source {cfg.get('id', '?')!r} is missing required key(s) {missing}")
        _need(cfg["role"] in ROLES, f"{path}: source {cfg['id']!r} role must be one of {ROLES}")
        _need(cfg["enabled"] is True or cfg["enabled"] is False,
              f"{path}: source {cfg['id']!r} enabled must be true or false")
        need_ep = TERMS_ENDPOINT_KEYS if cfg["role"] == "term_authority" else ASSOC_ENDPOINT_KEYS
        _need(isinstance(cfg.get("endpoints"), dict),
              f"{path}: source {cfg['id']!r} needs `endpoints` with {need_ep}")
        have = set(cfg["endpoints"])
        _need(need_ep[0] in have, f"{path}: source {cfg['id']!r} endpoints must define {need_ep}")
        if cfg["role"] == "association_provider":
            ev = cfg.get("evidence_codes")
            _need(isinstance(ev, list) and ev and all(isinstance(e, str) and e for e in ev),
                  f"{path}: association source {cfg['id']!r} must declare a non-empty "
                  f"`evidence_codes` vocabulary (the record's evidence_code is checked against "
                  f"it, SCHEMA.md §1/§4)")
        for k in BUDGET_KEYS:
            v = (cfg.get("budget") or {}).get(k)
            _need(isinstance(v, int) and v > 0, f"{path}: source {cfg['id']!r} budget.{k} must be "
                                                f"a positive integer, got {v!r}")
        ids.append(cfg["id"])
    _need(len(set(ids)) == len(ids), f"{path}: duplicate source id(s)")
    roles = [c["role"] for c in srcs if c["enabled"]]
    _need(roles.count("term_authority") == 1,
          f"{path}: exactly ONE enabled term_authority is required (the ontology is the single "
          f"authority for the term, SCHEMA.md §2), got {roles.count('term_authority')}")
    _need(roles.count("association_provider") >= 1,
          f"{path}: at least one enabled association_provider is required")
    return srcs


# ─────────────────────────────────────────────────────────────────────────────────────────────
# The phenotype record — the store's shape (SCHEMA.md §1). Column order here is TSV order; the
# schema json is DATA-FREE so its sha256 moves only when the SHAPE moves.
# ─────────────────────────────────────────────────────────────────────────────────────────────
PHENOTYPE_VIEW = "phenotype"
PHENOTYPE_VERSION = 1
# payload = everything except source, source_release, fetched_at, first_seen_run (SCHEMA.md §0)
PHENOTYPE_COLUMNS = [
    ("gene_id", "string", "MGI accession the association is anchored on, resolved against the gene arm's xref table"),
    ("symbol", "string", "gene symbol, from the xref table (never from the association source)"),
    ("ensembl_gene_id", "string", "Ensembl gene id, from the xref table (the second xref accession)"),
    ("allele_marker_id", "string", "the allele/marker accession(s) the source names, pipe-joined when several; never empty"),
    ("genotype_id", "string", "the MGI genotype accession when the source names one; '' otherwise"),
    ("phenotype_term_id", "string", "the ontology term id (e.g. MP:0001179) — REQUIRED; a phenotype without one is refused by name"),
    ("phenotype_term", "string", "the term's own name, resolved ONLY from the term-authority (ontology) file"),
    ("evidence_code", "string", "the source's own evidence token, inside the source's declared vocabulary; the named value "
     "'not-published-in-file' where the source's file carries no per-row code token — the arm refuses to invent one"),
    ("citation", "string", "the source's own citation for the association (a PubMed id as the source writes it); '' when none"),
    ("statement", "string", "the source's own statement, quoted, short: the row's allelic-composition and background words"),
    ("source", "string", "which association source published this row (the association authority, SCHEMA.md §2)"),
    ("source_release", "string", "the source's release stamp (a release file's own Last-Modified date)"),
    ("fetched_at", "string", "ISO instant the record was FIRST stored; preserved across runs"),
    ("first_seen_run", "string", "the phenotype run id that first stored this record"),
]
PHENOTYPE_KEY = ["gene_id", "phenotype_term_id", "allele_marker_id", "genotype_id",
                 "evidence_code", "citation", "statement", "source"]
PHENOTYPE_SCHEMA = {
    "view": PHENOTYPE_VIEW, "version": PHENOTYPE_VERSION, "key": PHENOTYPE_KEY,
    "description": ("one row per a source's own association row (gene → phenotype term), stored as "
                    "published. When two sources disagree on the same (gene_id, phenotype_term_id) "
                    "BOTH rows are kept and the conflict is COUNTED in the conflict log "
                    "(conflicts.tsv) — never averaged, never dropped (SCHEMA.md §0). The term name "
                    "comes only from the term authority (the ontology); the association comes only "
                    "from the source that published it."),
    "columns": [{"name": n, "type": t, "note": note} for n, t, note in PHENOTYPE_COLUMNS],
}

# the door query (SCHEMA.md §3): gene → phenotypes with evidence, every item with accession + release
GENE_PHENOTYPE_VIEW = "gene_phenotype"
GENE_PHENOTYPE_VERSION = 1
GENE_PHENOTYPE_COLUMNS = [
    ("symbol", "string", "MGI gene symbol — the xref table's join key"),
    ("gene_id", "string", "MGI accession (the xref accession the association is anchored on)"),
    ("ensembl_gene_id", "string", "Ensembl gene id from the gene arm's xref table"),
    ("phenotype_term_id", "string", "the ontology term id — the item's accession (MP:…)"),
    ("phenotype_term", "string", "the term's own name from the term authority"),
    ("evidence_code", "string", "the source's evidence token for the association"),
    ("citation", "string", "the source's own citation (PubMed id as the source writes it); '' when none"),
    ("allele_marker_id", "string", "the allele/marker accession(s) the source names"),
    ("statement", "string", "the source's own statement, quoted, short"),
    ("source", "string", "which association source published this row"),
    ("source_release", "string", "that source's release stamp — the item's release"),
    ("fetched_at", "string", "when the record was first stored"),
]
GENE_PHENOTYPE_SCHEMA = {
    "view": GENE_PHENOTYPE_VIEW, "version": GENE_PHENOTYPE_VERSION, "key": ["symbol", "phenotype_term_id", "source"],
    "description": ("the query shape on the door (SCHEMA.md §3): gene → phenotypes with evidence, "
                    "one row per (gene, term, source) record. Every item carries its accession "
                    "(the MP term id, plus both gene accessions) and its release."),
    "columns": [{"name": n, "type": t, "note": note} for n, t, note in GENE_PHENOTYPE_COLUMNS],
}

# the conflict log — the xref conflict log's institution, this arm's own file (SCHEMA.md §0)
CONFLICT_COLUMNS = [
    ("key", "string", "the conflict unit: '<gene_id>|<phenotype_term_id>'"),
    ("reason", "string", "phenotype_disagreement (the only class this arm emits)"),
    ("source_a", "string", "the first source's id (roster order)"),
    ("source_b", "string", "the second source's id"),
    ("detail", "string", "the disagreement itself, both sides quoted: which payload(s) only source_a has, which only source_b has"),
]
CONFLICT_CLASSES = ("phenotype_disagreement",)


def payload_of(row: dict) -> tuple:
    """The row's claim, excluding everything that is not the claim (SCHEMA.md §0): source identity
    and bookkeeping never decide (or hide) a disagreement."""
    return tuple(row.get(n, "") for n in ("gene_id", "phenotype_term_id", "allele_marker_id",
                                          "genotype_id", "evidence_code", "citation", "statement"))


def check_term_id(term_id: str, gene_id: str, allele: str) -> str:
    """THE refusal the row text names: a phenotype without an ontology id is refused BY NAME."""
    t = (term_id or "").strip()
    if not t or not TERM_ID_RE.match(t):
        raise Refusal(f"gene {gene_id} ({allele}): phenotype without an ontology term id "
                      f"(term_id={term_id!r}) — refused by name, counted as a reject; the arm "
                      f"stores no phenotype that no ontology can answer for")
    return t
