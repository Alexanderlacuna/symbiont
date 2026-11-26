"""ADAPTER `pubmed` — wraps the pinned MCP server; owns query syntax, output parsing, licence policy.

This file contains ZERO HTTP: every request it makes is a tool call on the MCP the mcp.py client
spawns (@cyanheads/pubmed-mcp-server, pinned — the pin IS the contract, because the tool output
format this file parses is version-specific). Two legs of the one server:

  · pubmed_search_articles      the NCBI leg: '<SYM>[Title] AND mouse[MeSH Terms]' — a CURATED
    MeSH species filter (the survey's live test: the tool's own count 399 == NCBI's own
    <Count>399</Count>; EuropePMC's text-mined SPECIES filter is not this precise — a
    daddy-longlegs paper answers TITLE:"Pax6" AND SPECIES:"Mus musculus").
  · pubmed_europepmc_fetch      the EuropePMC leg: the full record — title, journal, year, DOI,
    PMCID, open-access flag, the record's OWN licence, and the complete abstract. EuropePMC is
    the record source; the per-record licence decides abstract storage (contract.apply_abstract_policy).

Parsing is regex over the tool's markdown-formatted text output. That is a deliberate,
documented coupling: the server is pinned, so the format cannot drift under us; if the pin
moves, the parse is REFUSED loudly here (Returned/PMIDs lines not found), never silently empty.
"""
from __future__ import annotations

import re

from .. import contract as lit
from ..contract import ConfigError


class PubMedAdapter:
    name = "pubmed"
    role = "publication_source"      # neither id_authority nor xref_provider: this is the OTHER arm

    def __init__(self):
        self.cfg = None

    def configure(self, cfg: dict) -> None:
        self.cfg = cfg

    # ── search leg ────────────────────────────────────────────────────────────────────────────
    def gene_query(self, symbol: str) -> str:
        return self.cfg["query"].replace("{SYM}", symbol)

    def search_gene(self, mcp, symbol: str) -> dict:
        """One search per gene. The tool's own 'Returned: N of T' is the source's own hit count for
        the SAME query — quoted in the run record with the search URL; `ours` is checked against it
        in verify(). No pagination: per_gene_cap bounds what we take, and the run record says so."""
        query = self.gene_query(symbol)
        text = mcp.call("pubmed_search_articles", {"query": query, "maxResults": self.cfg["per_gene_cap"]})
        m_ret = re.search(r"\*\*Returned:\*\* (\d+) of (\d+)", text)
        m_url = re.search(r"\*\*Search URL:\*\* <([^>]+)>", text)
        m_ids = re.search(r"\*\*PMIDs:\*\* ([\d,\s]+)", text)
        if not m_ret or not m_url:
            raise ConfigError(f"pubmed adapter: search output for {symbol!r} no longer parses — the "
                              f"pinned MCP version {self.cfg['mcp']['version']} changed format? "
                              f"(first 200 chars: {text[:200]!r})")
        pmids = [p.strip() for p in (m_ids.group(1).split(",") if m_ids else []) if p.strip()]
        returned, source_count = int(m_ret.group(1)), int(m_ret.group(2))
        if returned != len(pmids):
            raise ConfigError(f"pubmed adapter: {symbol!r} — the tool says 'Returned: {returned}' "
                              f"but {len(pmids)} PMID(s) parsed from its PMIDs line; refusing to guess")
        return dict(symbol=symbol, query=query, returned=returned, source_count=source_count,
                    url=m_url.group(1), pmids=pmids, cap=self.cfg["per_gene_cap"],
                    beyond_cap=max(source_count - returned, 0))

    # ── fetch leg (EuropePMC records) ─────────────────────────────────────────────────────────
    def fetch_records(self, mcp, pmids: list[str]) -> dict:
        """One fetch call for the batch. Returns {pmid: record}; a PMID the leg returns no record
        for is simply absent from the mapping — the LOOP stores it accession-only with the reason
        (a PMID is data; it may not vanish)."""
        if not pmids:
            return {}
        args = {"records": [{"source": "MED", "epmcId": p} for p in pmids]}
        text = mcp.call("pubmed_europepmc_fetch", args)
        m_n = re.search(r"\*\*Returned:\*\* (\d+)", text)
        if not m_n:
            raise ConfigError(f"pubmed adapter: europepmc fetch output no longer parses — the pinned "
                              f"MCP version {self.cfg['mcp']['version']} changed format? "
                              f"(first 200 chars: {text[:200]!r})")
        out: dict[str, dict] = {}
        for block in re.split(r"(?m)^### ", text)[1:]:
            lines = block.splitlines()
            rec = dict(pmid="", doi="", title="", year="", journal="", pmcid="",
                       open_access="", licence="", abstract="")
            rec["title"] = lines[0].strip()
            abstract_at, body = None, []
            for line in lines[1:]:
                m = re.match(r"\*\*(Journal|Year|PMID|DOI|PMCID|Open Access|License):\*\* (.*)", line)
                if m:
                    key = {"Journal": "journal", "Year": "year", "PMID": "pmid", "DOI": "doi",
                           "PMCID": "pmcid", "Open Access": "open_access",
                           "License": "licence"}[m.group(1)]
                    rec[key] = m.group(2).strip()
                elif line.startswith("#### Abstract"):
                    abstract_at = len(body) + 1
                    continue
                elif abstract_at is not None:
                    body.append(line)
            if not rec["pmid"]:
                continue
            rec["abstract"] = "\n".join(body).strip()
            lit.apply_abstract_policy(rec, self.cfg["abstract_licence_whitelist"])
            out[rec["pmid"]] = rec
        if int(m_n.group(1)) != len(out):
            raise ConfigError(f"pubmed adapter: the fetch leg says 'Returned: {m_n.group(1)}' but "
                              f"{len(out)} record(s) parsed; refusing to guess which one is missing")
        return out

    # ── verify ───────────────────────────────────────────────────────────────────────────────
    def verify(self, search: dict, accounting: dict) -> dict:
        """Our count vs the source's own, for the SAME query, quoted with the URL. `ours` is every
        PMID the source handed back for this gene's query (stored new, already known, or accession-
        only) — the anti-silence rule: nothing the source said may vanish from our accounting."""
        ours = accounting["new"] + accounting["known"] + accounting["accession_only"]
        ok = ours == search["returned"]
        detail = (f"query {search['query']!r}: ours={ours} (new={accounting['new']} "
                  f"known={accounting['known']} accession_only={accounting['accession_only']}) "
                  f"vs the tool's own 'Returned: {search['returned']}' — "
                  f"{'OK' if ok else 'DISAGREE'}; source's own hit count for the same query: "
                  f"{search['source_count']} ({search['beyond_cap']} beyond the per_gene_cap of "
                  f"{search['cap']}, not fetched, by config not by accident) url={search['url']}")
        return dict(ok=ok, ours=ours, source_count=search["source_count"], url=search["url"], detail=detail)
