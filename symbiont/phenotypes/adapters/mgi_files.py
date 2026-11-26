"""MGI phenotype adapters — the real sources of this arm, over the gene arm's Fetcher (no second
HTTP client exists in this farm, and none is written here):

    MgiVocab        role term_authority       -> VOC_MammalianPhenotype.rpt (the MP vocabulary)
    MgiGenePheno    role association_provider -> MGI_GenePheno.rpt (curated gene→MP annotations)

Both expose ONE method, fetch_payload() -> (text, facts): the file's own bytes-as-text plus its
facts (url, bytes, sha256, release, requests). The PARSE lives in adapters/_pheno_files.py and
the LOOP owns it — a source supplies bytes, never a resolution rule of its own.

WHERE THINGS LIVE (the gene arm's rule): the reports base URL, the relative release-file paths
and the licence are READ FROM sources.yml by configure(); this file keeps only what is FETCHING.
MGI publishes no count endpoint (the gene arm measured this), so a release file's own row count
IS the source's count and the loop's verify leg compares our parse against it. MGI publishes no
release id either, so the release stamp is the fetched file's own Last-Modified date — the same
stamp the gene arm already uses for MGI.
"""
from __future__ import annotations

import hashlib

from ... import http as gene_http
from ...contract import resolve_url
from ..contract import ConfigError  # noqa: F401  (this arm's refusal vocabulary)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class _MgiFileAdapter:
    endpoint_key: str = ""

    def configure(self, cfg: dict) -> None:
        self.cfg = cfg
        self.url = resolve_url(cfg["base_url"], cfg["endpoints"][self.endpoint_key])
        self.licence_url = cfg.get("licence_url", "")
        self.licence_name = cfg.get("licence_text", "")

    def fetch_payload(self) -> tuple[str, dict]:
        """One GET through the gene arm's Fetcher: budget and rate limit enforced per source,
        every request logged, the release stamped by the file's own Last-Modified."""
        fetcher = gene_http.Fetcher(budget=self.cfg.get("budget"),
                                    rate_limit=self.cfg.get("rate_limit") or {})
        head = fetcher.head(self.url, f"{self.cfg['id']} head")
        body = fetcher.get(self.url, f"{self.cfg['id']} data")
        facts = dict(url=self.url, bytes=len(body), sha256=_sha(body),
                     release=head["last_modified"], requests=len(fetcher.log),
                     waits=fetcher.waits, head_status=head["status"])
        return body.decode("utf-8", "replace"), facts


class MgiVocab(_MgiFileAdapter):
    """term_authority: the MP vocabulary file — the ONLY authority for a phenotype TERM name."""
    name = "mgi_vocab"
    role = "term_authority"
    endpoint_key = "terms"


class MgiGenePheno(_MgiFileAdapter):
    """association_provider: MGI's curated gene→phenotype annotations — the authority for the
    ASSOCIATION (the row is stored as MGI published it, its name on the record)."""
    name = "mgi_genepheno"
    role = "association_provider"
    endpoint_key = "associations"
