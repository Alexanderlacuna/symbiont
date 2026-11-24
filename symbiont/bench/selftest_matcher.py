"""Selftest for the BioASQ matcher port — two fixtures, no network, no inputs.

Run:  python3 symbiont/bench/selftest_matcher.py     (prints PASS lines, exits 1 on failure)

Fixture 1 — a duplicate result cannot claim two gold papers:
  two gold papers share the same DOI and the same normalised title; the result
  list contains the SAME paper twice (duplicate rows, distinct ranks, no PMID).
  Each result row may credit at most ONE gold paper, so the first row claims one
  gold and the second row claims the other — and a SINGLE duplicate row must
  never claim both.

Fixture 2 — a DOI-only match works:
  a candidate carrying only a DOI (no PMID, non-matching title) credits the
  gold paper with the same normalised DOI, at tier 'doi'.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bioasq_matcher import first_matches  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        failures.append(name)


# ── fixture 1: duplicate result cannot claim two gold papers ────────────────────────────────
gold_dup = [
    {"pmid": "111", "doi": "10.1/dup", "title": "Same Title"},
    {"pmid": "222", "doi": "10.1/dup", "title": "Same Title"},
]
# one single duplicate result row: must claim at most ONE gold paper
m_one = first_matches(gold_dup, [{"rank": 1, "doi": "10.1/dup", "title": "Same Title"}])
check("single duplicate result claims exactly one gold paper", len(m_one) == 1,
      f"matches={m_one}")
check("the one credited gold is claimed once (not both)", set(m_one) <= {"111", "222"},
      f"claimed={sorted(m_one)}")

# the same paper twice in the result list (duplicate results, ranks 1 and 2):
# the two rows may jointly credit the two gold papers, one each — never two from one row
m_two = first_matches(gold_dup, [
    {"rank": 1, "doi": "10.1/dup", "title": "Same Title"},
    {"rank": 2, "doi": "10.1/dup", "title": "Same Title"},
])
check("two duplicate result rows credit two distinct gold papers, one each",
      len(m_two) == 2 and set(m_two) == {"111", "222"},
      f"matches={ {k: v for k, v in sorted(m_two.items())} }")
check("first duplicate row got the lower-ranked gold id (sorted first_unclaimed)",
      m_two.get("111") == (1, "doi") or m_two.get("111") == (1, "title"),
      f"row111={m_two.get('111')}")
tiers_two = {tier for _, tier in m_two.values()}
check("no result row ever credited two gold papers (2 rows -> 2 credits, tiers consistent)",
      len(tiers_two) == 1, f"tiers={tiers_two}")

# ── fixture 2: a DOI-only match works ───────────────────────────────────────────────────────
gold_doi = [{"pmid": "333", "doi": "10.2/only", "title": "The Gold Title"}]
m_doi = first_matches(gold_doi, [
    {"rank": 1, "doi": "https://doi.org/10.2/ONLY", "title": "Completely Different Title"},
])
check("DOI-only candidate credits the gold paper", len(m_doi) == 1 and "333" in m_doi,
      f"matches={m_doi}")
check("DOI-only match is credited at tier 'doi'", m_doi.get("333") == (1, "doi"),
      f"m={'(rank 1, doi)' if m_doi.get('333') == (1, 'doi') else m_doi}")

# negative control for fixture 2: a non-matching DOI credits nothing
m_none = first_matches(gold_doi, [{"rank": 1, "doi": "10.9/nope", "title": "Whatever"}])
check("non-matching DOI credits nothing", m_none == {}, f"matches={m_none}")

print()
if failures:
    print(f"SELFTEST FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("SELFTEST OK: all matcher fixture checks passed")
