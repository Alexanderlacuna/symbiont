"""THE LITERATURE ADAPTER CONTRACT — a publication source is a plug-in file, the loop is unchanged.

Mirrors the gene arm's contract in shape (symbiont/contract.py) but for publications:

    search_gene(mcp, symbol) -> {symbol, query, returned, source_count, url, pmids}
    fetch_records(mcp, pmids) -> [{pmid, doi, title, year, journal, pmcid, open_access,
                                   licence, abstract}]        (one per PMID the source gave back)

Refusal types are REUSED from the gene arm (one definition site of refusal codes):
BudgetExceeded, LicenceUnknown, ConfigError. There is no release id in literature — the court's
change: `source_release` is required only where a source HAS releases, so for literature it is
the FETCH DATE + the MCP version, stamped on every publication record by the loop.
"""
from __future__ import annotations

import os
import re

import yaml

from ..contract import BudgetExceeded, ConfigError, LicenceUnknown  # noqa: F401  (re-exported: one definition site)

# ─────────────────────────────────────────────────────────────────────────────────────────────
# sources.yml — the ONE definition site for the literature source (schema is total: unknown keys
# are refused by name, so config and code can never disagree about what exists).
# ─────────────────────────────────────────────────────────────────────────────────────────────
LIT_KEYS = ("id", "adapter", "enabled", "note", "mcp", "query", "per_gene_cap", "gene_scope",
            "abstract_licence_whitelist", "licence_url", "licence_text", "terms_pattern", "budget",
            "fulltext")
MCP_KEYS = ("package", "version", "install", "server_entry", "call_timeout_s")
LIT_BUDGET_KEYS = ("tool_calls", "wall_s")
FULLTEXT_KEYS = ("enabled", "note")


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def load_sources(path: str) -> dict:
    """Parse + validate the literature sources.yml -> the cfg mapping for its single source.

    The literature arm runs ONE source per config; a second entry is refused (this arm's config
    is not a roster — one source, one file, one adapter, zero ambiguity)."""
    _need(os.path.isfile(path), f"literature sources config missing: {path}")
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    _need(isinstance(raw, dict), f"{path}: top level must be a mapping with `version` and `sources`")
    _need(raw.get("version") == 1, f"{path}: unsupported version {raw.get('version')!r}")
    srcs = raw.get("sources")
    _need(isinstance(srcs, list) and len(srcs) == 1,
          f"{path}: `sources` must be a list with exactly one entry (the literature arm runs one source)")
    cfg = srcs[0]
    _need(isinstance(cfg, dict), f"{path}: sources[0] is not a mapping")
    unknown = [k for k in cfg if k not in LIT_KEYS]
    _need(not unknown, f"{path}: sources[0] carries unknown key(s) {sorted(unknown)} — "
                        f"the schema is {list(LIT_KEYS)}")
    missing = [k for k in LIT_KEYS if k not in cfg]
    _need(not missing, f"{path}: sources[0] is missing required key(s) {missing}")
    _need(cfg["id"] == "pubmed" and cfg["adapter"] == "pubmed",
          f"{path}: this config is for the `pubmed` source/adapter, got {cfg['id']!r}/{cfg['adapter']!r}")
    _need(cfg["enabled"] is True or cfg["enabled"] is False, f"{path}: `enabled` must be true or false")
    mcp = cfg["mcp"]
    _need(isinstance(mcp, dict), f"{path}: `mcp` must be a mapping")
    miss = [k for k in MCP_KEYS if k not in mcp]
    _need(not miss, f"{path}: mcp is missing {miss}")
    _need(re.fullmatch(r"\d+\.\d+\.\d+", str(mcp["version"])),
          f"{path}: mcp.version must be an exact pin like '2.12.0' — a caret or a range is refused: "
          f"the tool output format this adapter parses is version-specific, so the pin IS the contract")
    _need(isinstance(mcp["call_timeout_s"], (int, float)) and mcp["call_timeout_s"] > 0,
          f"{path}: mcp.call_timeout_s must be a positive number")
    _need(isinstance(cfg["per_gene_cap"], int) and 1 <= cfg["per_gene_cap"] <= 200,
          f"{path}: per_gene_cap must be an integer in 1..200 (the fetch tool takes up to 200 PMIDs)")
    _need(isinstance(cfg["gene_scope"], list) and cfg["gene_scope"],
          f"{path}: gene_scope must be a non-empty list of symbols")
    for sym in cfg["gene_scope"]:
        _need(isinstance(sym, str) and sym and not sym.isspace(), f"{path}: bad gene_scope entry {sym!r}")
    _need(len(set(cfg["gene_scope"])) == len(cfg["gene_scope"]), f"{path}: gene_scope has a duplicate")
    wl = cfg["abstract_licence_whitelist"]
    _need(isinstance(wl, list) and wl, f"{path}: abstract_licence_whitelist must be a non-empty list")
    for k in LIT_BUDGET_KEYS:
        v = (cfg["budget"] or {}).get(k)
        _need(isinstance(v, int) and v > 0, f"{path}: budget.{k} must be a positive integer, got {v!r}")
    _need("{SYM}" in cfg["query"], f"{path}: `query` must contain the {{SYM}} placeholder")
    ft = cfg["fulltext"]
    _need(isinstance(ft, dict), f"{path}: `fulltext` must be a mapping")
    unknown_ft = [k for k in ft if k not in FULLTEXT_KEYS]
    _need(not unknown_ft, f"{path}: fulltext carries unknown key(s) {sorted(unknown_ft)} — "
                          f"the schema is {list(FULLTEXT_KEYS)}")
    _need(ft["enabled"] is True or ft["enabled"] is False, f"{path}: fulltext.enabled must be true or false")
    return cfg


# ─────────────────────────────────────────────────────────────────────────────────────────────
# The publication record — the store's shape. Column order here is the TSV column order; the
# schema json is DATA-FREE (no counts, no dates) so its sha256 moves only when the SHAPE moves.
# v2 (req-P1-AN-ABSTRACT-CANNOT-CARRY-A-CLAIM-that): every record carries evidence_level in
# {metadata-only, abstract, fulltext} + the field-level provenance + the fulltext columns
# (sha256 of the stored OA file, the channel that served it, and the reason it is absent).
# The fulltext itself is a FILE under fulltext/<pmid>.md — the table stays small; the sha is
# the join. v1 columns are unchanged and in place; v1 tables stay on disk as history.
# ─────────────────────────────────────────────────────────────────────────────────────────────
PUBLICATION_VIEW = "publication"
PUBLICATION_VERSION = 2
PUBLICATION_COLUMNS = [
    ("pmid", "string", "PubMed id — the record's key; one row per PMID, never per gene"),
    ("doi", "string", "DOI as returned by the EuropePMC record ('' when the record has none)"),
    ("title", "string", "article title from the EuropePMC record"),
    ("year", "string", "publication year from the EuropePMC record"),
    ("journal", "string", "journal (short title) from the EuropePMC record"),
    ("pmcid", "string", "PMC id where the record has one ('' otherwise)"),
    ("open_access", "string", "the record's own open-access flag, as returned (yes/no/other)"),
    ("licence", "string", "the record's OWN licence as EuropePMC states it (e.g. 'cc by'); '' when none is stated"),
    ("abstract", "string", "the abstract — stored ONLY when the record's licence permits (see abstract_withheld_reason)"),
    ("abstract_withheld_reason", "string",
     "'' when the abstract is stored; otherwise the reason it is not: the licence policy, or "
     "'no EuropePMC record returned for this PMID (accession-only)' when the fetch leg came back empty. "
     "A withheld abstract NEVER means a missing row: the PMID, DOI, title, year and journal stay."),
    ("evidence_level", "string",
     "the HIGHEST level of evidence this record holds: metadata-only | abstract | fulltext — computed "
     "from the row's own fields (never declared, never defaulted; a record with no evidence_level is "
     "REFUSED at load by evidence.load_records)"),
    ("field_provenance", "string",
     "which fields came from which level, compact: 'metadata-only:pmid,doi, title,…;abstract:abstract;"
     "fulltext:fulltext_sha256' — a field is listed at a level only when the row carries it"),
    ("fulltext_sha256", "string",
     "sha256 of the stored open-access fulltext file (fulltext/<pmid>.md); '' when none is stored"),
    ("fulltext_source", "string",
     "the OA channel that served the stored fulltext: pmc | europepmc | unpaywall (the pinned MCP's own "
     "chain — never a bespoke client, never a bot-detection bypass); '' when none is stored"),
    ("fulltext_unavailable_reason", "string",
     "'' when the fulltext is stored; otherwise WHY not, in the record's own words: not permitted (licence "
     "not whitelisted — the same rule as abstracts), no open-access route, unavailable (the channel's own "
     "refusal, e.g. 'EPMC does not mark this record open access'), or not demanded — fulltext fetch is "
     "claim-driven (the measured trigger, store/literature/trigger/), the Deep-Research-Agent borrow"),
    ("source_release", "string",
     "literature has no release id (the court's change: a release is required only where a source HAS "
     "releases) — so this is '<first fetch date> + <MCP package>@<pinned version>', stamped when the "
     "record was FIRST stored and preserved across runs"),
    ("first_seen_run", "string", "the literature run id that first stored this record"),
]
PUBLICATION_SCHEMA = {
    "view": PUBLICATION_VIEW, "version": PUBLICATION_VERSION, "key": ["pmid"],
    "description": ("one row per PMID discovered by the literature arm's gene-keyed searches. "
                    "v2: every record carries evidence_level in {metadata-only, abstract, fulltext} "
                    "plus field-level provenance; an abstract cannot carry a claim that needs the "
                    "full text (evidence.answer_claim refuses it by kind). The abstract is the "
                    "largest thing in this table; a stored fulltext is a FILE under fulltext/<pmid>.md "
                    "joined by sha256, fetched only via open access through the pinned MCP's chain "
                    "(PMC EFetch -> EuropePMC fullTextXML -> Unpaywall)."),
    "columns": [{"name": n, "type": t, "note": note} for n, t, note in PUBLICATION_COLUMNS],
}

# the join view: gene (from the gene arm's xref table) → its PMIDs — v2 carries evidence_level
# on every pair, so a gene→PMIDs query SHOWS the level of each record it returns
GENE_PUBLICATION_VIEW = "gene_publication"
GENE_PUBLICATION_VERSION = 2
GENE_PUBLICATION_COLUMNS = [
    ("symbol", "string", "MGI gene symbol — the xref table's join key (case preserved)"),
    ("mgi_id", "string", "MGI accession from the gene arm's xref table"),
    ("ensembl_gene_id", "string", "Ensembl gene id from the gene arm's xref table"),
    ("pmid", "string", "a publication the source returned for this gene's query"),
    ("title", "string", "publication title ('' for accession-only rows)"),
    ("year", "string", "publication year ('' for accession-only rows)"),
    ("journal", "string", "journal ('' for accession-only rows)"),
    ("licence", "string", "the publication's own licence as EuropePMC states it ('' when none stated)"),
    ("abstract_stored", "string", "yes | no — whether the abstract column of publication.v2.tsv holds text for this PMID"),
    ("evidence_level", "string", "the publication record's evidence level: metadata-only | abstract | fulltext — "
     "the consumer sees, per PMID, whether a claim about it needs a higher level (evidence.answer_claim)"),
]
GENE_PUBLICATION_SCHEMA = {
    "view": GENE_PUBLICATION_VIEW, "version": GENE_PUBLICATION_VERSION, "key": ["symbol", "pmid"],
    "description": ("the join the row asks for: publications attached to the gene ids already in the "
                    "store (MGI/Ensembl xref), so a gene row can list its PMIDs. Gene identity comes "
                    "from the gene arm's xref table; the publication list comes from the source's own "
                    "answer to the gene's query. One row per (gene, PMID) pair."),
    "columns": [{"name": n, "type": t, "note": note} for n, t, note in GENE_PUBLICATION_COLUMNS],
}


def apply_abstract_policy(rec: dict, whitelist: list[str]) -> dict:
    """The abstract rule, one place: store the abstract ONLY when the record's OWN licence is in
    the whitelist; otherwise the row is accession-plus-bibliography with the reason recorded.

    Why conservative: EPMC records with no licence line are under terms nobody could read at
    record level — storing their prose would be a claim we cannot verify, so the row keeps the
    accession (PMID), the DOI, the title, the year and the journal and refuses the prose. A row
    with a reason is a measurement; a silently-stored abstract is not."""
    lic = (rec.get("licence") or "").strip().lower()
    if lic and lic in [w.strip().lower() for w in whitelist]:
        rec["abstract"], rec["abstract_withheld_reason"] = rec.get("abstract", ""), ""
        rec["abstract_stored"] = "yes"
    else:
        why = (f"licence {lic!r} not in abstract_licence_whitelist {whitelist} — "
               f"abstract withheld, row stored accession-only") if lic else \
              (f"no licence stated on the EuropePMC record (open_access={rec.get('open_access', '?')}) — "
               f"terms could not be read at record level, abstract withheld, row stored accession-only")
        rec["abstract"], rec["abstract_withheld_reason"] = "", why
        rec["abstract_stored"] = "no"
    return rec
