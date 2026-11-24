"""THE PHENOTYPES ARM — the second dataset arm (SCHEMA.md is the contract this module ships).

A SIBLING of the gene loop and the literature arm, not a source inside either: it READS the gene
arm's published xref table (store/xref/xref.tsv, read-only) and joins phenotype ASSOCIATIONS on
it. The ontology (term authority) names the term; the publishing source owns the association;
when two sources disagree, both rows are kept and the conflict is counted — never averaged,
never dropped.

    python3 -m symbiont.phenotypes.loop --run-id pheno-run-1
"""
