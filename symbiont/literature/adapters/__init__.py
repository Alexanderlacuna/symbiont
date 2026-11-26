"""The literature arm's plug-in directory — one source, one file.

An adapter here exposes `ADAPTER` with `name`, `role == "publication_source"` and the methods
`configure(cfg)`, `gene_query(symbol)`, `search_gene(mcp, symbol)`, `fetch_records(mcp, pmids)`,
`verify(search, accounting)`. The gene arm's own registry (symbiont/contract.py) never walks
this directory — the two arms have separate contracts, deliberately: a publication source is
neither an id_authority nor an xref_provider.
"""
