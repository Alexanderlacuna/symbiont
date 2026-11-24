# PER-SOURCE DATA TERMS

The station's own **code** is MIT — see [`LICENCE`](../LICENCE). The **data** it hands over carries each source's own terms, and those
govern the data, not this repository's licence. This file is the appendix that used to sit under the MIT text.

```
The station stores and serves data under each source's own terms, recorded per run with the
terms page's sha256 in every run record, and stitched into ONE attribution string the handover
manifest records (symbiont/manifest.py, §7 `licence.attribution`) — quoted verbatim from the
first ship run's manifest (symbiont/store/ship1-fire/handover/…json):

  "ensembl_biomart ensembl-116: Ensembl data: no restrictions on access or use
   https://www.ensembl.org/info/about/legal/disclaimer.html;
   mgi mgi-2026-11-09: MGI data and annotations: CC-BY 4.0 (attribution required)
   https://www.informatics.jax.org/mgihome/other/copyright.shtml"

The per-source table, exactly as sources.yml and every run record carry it:

  source          terms (licence_text, verbatim)                      terms page (licence_url)
  ensembl_biomart "Ensembl data: no restrictions on access or use"
                  https://www.ensembl.org/info/about/legal/disclaimer.html
  mgi             "MGI data and annotations: CC-BY 4.0 (attribution required)"
                  https://www.informatics.jax.org/mgihome/other/copyright.shtml
  pubmed_europepmc "EuropePMC (EMBL-EBI): per-record rights prevail — 'The original data may be
                  subject to rights claimed by third parties'; abstracts stored only under the
                  record's own whitelisted licence"
                  https://www.ebi.ac.uk/about/terms-of-use
                  (the literature arm wraps @cyanheads/pubmed-mcp-server 2.12.0, Apache-2.0)

A consumer of the station's output must honour those terms, not this one. Where a source's
licence could not be read, the station refuses the source at discovery (test T6) — it never
serves data under terms nobody read.
```
