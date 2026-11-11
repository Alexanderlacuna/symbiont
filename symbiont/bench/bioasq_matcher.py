"""BioASQ paper-search matcher — an EXACT port of Elicit's frozen scorer.

Provenance: github.com/elicit/elicit-paper-search-eval, file score.py
(sha256 75c2961fddff7ac27e0ae6048fafd6bbfd9675607684e53b76ad66fed9602e2d,
byte-identical to bench/bioasq/upstream/score.py in this repo). The matcher
functions below are copied VERBATIM from that file: matching order is PMID →
normalised DOI → normalised exact title, and each gold paper is credited at
most once. Nothing here is reinvented — if the behaviour differs from the
upstream scorer, the port is wrong.

Match order and credit rules (their words, from the scorer's report field):
    "PMID, then DOI, then exact normalized title; one credit per gold paper"
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any

# ── verbatim from upstream score.py ────────────────────────────────────────────────────────
DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)
PUBMED_URL = re.compile(
    r"^https?://pubmed\.ncbi\.nlm\.nih\.gov/(?P<pmid>\d+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)


def normalize_pmid(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    match = PUBMED_URL.match(text)
    if match:
        text = match.group("pmid")
    return text.lstrip("0") or None


def normalize_doi(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    for prefix in DOI_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.strip() or None


def normalize_title(value: Any) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).lower()
    text = "".join("" if unicodedata.category(char).startswith("P") else char for char in text)
    return " ".join(text.split()) or None


def build_indexes(
    gold: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, set[str]], dict[str, set[str]]]:
    pmids: dict[str, str] = {}
    dois: dict[str, set[str]] = {}
    titles: dict[str, set[str]] = {}
    for item in gold:
        gold_id = normalize_pmid(item.get("pmid"))
        if gold_id is None:
            raise ValueError("every BioASQ gold item must have a PMID")
        if gold_id in pmids:
            raise ValueError(f"duplicate gold PMID: {gold_id}")
        pmids[gold_id] = gold_id
        doi = normalize_doi(item.get("doi"))
        title = normalize_title(item.get("title"))
        if doi is not None:
            dois.setdefault(doi, set()).add(gold_id)
        if title is not None:
            titles.setdefault(title, set()).add(gold_id)
    return pmids, dois, titles


def first_unclaimed(ids: set[str], claimed: set[str]) -> str | None:
    return next((gold_id for gold_id in sorted(ids) if gold_id not in claimed), None)


def first_matches(
    gold: list[dict[str, Any]], candidates: list[dict[str, Any]]
) -> dict[str, tuple[int, str]]:
    pmids, dois, titles = build_indexes(gold)
    ranks = [int(candidate["rank"]) for candidate in candidates]
    duplicates = [rank for rank, count in Counter(ranks).items() if count > 1]
    if duplicates or any(rank <= 0 for rank in ranks):
        raise ValueError(f"candidate ranks must be unique positive integers: {duplicates}")
    claimed: set[str] = set()
    matches: dict[str, tuple[int, str]] = {}
    for candidate in sorted(candidates, key=lambda item: int(item["rank"])):
        pmid = normalize_pmid(candidate.get("pmid"))
        if pmid is not None and pmid in pmids:
            gold_id = pmids[pmid] if pmids[pmid] not in claimed else None
            tier = "pmid" if gold_id is not None else None
        else:
            doi = normalize_doi(candidate.get("doi"))
            if doi is not None and doi in dois:
                gold_id = first_unclaimed(dois[doi], claimed)
                tier = "doi" if gold_id is not None else None
            else:
                title = normalize_title(candidate.get("title"))
                if title is not None and title in titles:
                    gold_id = first_unclaimed(titles[title], claimed)
                    tier = "title" if gold_id is not None else None
                else:
                    gold_id = None
                    tier = None
        if gold_id is not None and tier is not None:
            claimed.add(gold_id)
            matches[gold_id] = (int(candidate["rank"]), tier)
    return matches


# ── end verbatim port ───────────────────────────────────────────────────────────────────────
MATCHING = "PMID, then DOI, then exact normalized title; one credit per gold paper"
