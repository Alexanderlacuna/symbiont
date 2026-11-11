"""symbiont.literature — THE LITERATURE ARM (req-P1-THE-LITERATURE-ARM-EuropePMC-keyed-by-the).

The second half of 'datasets AND literature together': publications joined to the gene ids the
gene arm already published (MGI/Ensembl xref), so a gene row can list its PMIDs.

The arm is a sibling of the gene loop, NOT a source inside it: symbiont/adapters/ is the gene
arm's plug-in directory, and its contract admits only roles id_authority/xref_provider — a
publication source is neither, and putting it there would break the gene loop's load-time
registry check. So the literature arm has its own contract (symbiont/literature/contract.py),
its own plug-in directory (symbiont/literature/adapters/), and its own store subtree
(symbiont/store/literature/). It reads the gene arm's OUTPUT (store/xref/xref.tsv) and never
edits it.

The one rule the row adds over the gene arm: the adapter WRAPS the MCP server
(@cyanheads/pubmed-mcp-server, pinned) — the arm writes no E-utilities client, no pagination,
no retry, no 429 logic. Searching, fetching, throttling and retrying are the server's job;
that is the survey's whole point (reports/MCP-SURVEY-BIOINFORMATICS-REPORT.md: 399 == 399).
"""
