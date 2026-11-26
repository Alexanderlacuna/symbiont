"""The two MGI flat-file formats this arm parses — ONE definition site, shared by the http
adapter and the offline fixture adapter, so a fixture exercises the real parse, not a mock.

VOC_MammalianPhenotype.rpt (the term authority's file; the MP vocabulary as MGI publishes it):
    <MP:term id> \t <term> \t <definition>          (3 tab fields, no header)

MGI_GenePheno.rpt (the association provider's file; MGI's curated gene→phenotype annotations):
    <allelic composition> \t <allele symbol(s)> \t <allele id(s)> \t <background> \t
    <MP term id> \t <PubMed id> \t <MGI marker id> \t <MGI genotype id>
    (8 tab fields, no header; allele ids and symbols are pipe-joined when a genotype has several
    alleles). An OPTIONAL 9th field is an evidence code — no MGI report file publishes one; the
    field exists so a second provider's file (and the offline fixtures) can carry codes through
    the same parse. Where it is absent the row's evidence code is the NAMED value
    'not-published-in-file' (SCHEMA.md §1): the arm refuses to invent a code the source did not
    publish — the evidence MGI does publish per row (the citation, the genotype context) is
    carried in the record's `citation` and `statement` columns.
"""
from __future__ import annotations

NO_CODE = "not-published-in-file"


def parse_terms(text: str) -> tuple[list[dict], int]:
    """VOC format -> ([{term_id, term, definition}], malformed_count)."""
    out, malformed = [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) < 2 or not f[0].strip() or not f[1].strip():
            malformed += 1
            continue
        out.append(dict(term_id=f[0].strip(), term=f[1].strip(),
                        definition=(f[2].strip() if len(f) > 2 else "")))
    return out, malformed


def parse_association(text: str) -> tuple:
    """GenePheno format -> (row iterator, counters filled while iterating).

    Yields dicts with the loop's normalised names; the generator ALSO yields malformed rows as
    dict(malformed=...) so the caller can count every row without a second pass. A row with
    fewer than 5 fields cannot even be named — counted `malformed`, never skipped silently.
    """
    for line in text.splitlines():
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) < 5:
            yield dict(malformed=True, reason=f"row has {len(f)} tab field(s), the format carries 8")
            continue
        comp, allele_symbols, allele_ids, background, term_id = f[0], f[1], f[2], f[3], f[4]
        citation = f[5].strip() if len(f) > 5 else ""
        marker_id = f[6].strip() if len(f) > 6 else ""
        genotype_id = f[7].strip() if len(f) > 7 else ""
        evidence = f[8].strip() if len(f) > 8 and f[8].strip() else NO_CODE
        statement = " ".join(x for x in (comp.strip(), f"({background.strip()})" if background.strip() else "") if x)
        yield dict(gene_id=marker_id, allele_marker_id=allele_ids.strip(), genotype_id=genotype_id,
                   phenotype_term_id=term_id.strip(), evidence_code=evidence, citation=citation,
                   statement=statement)
