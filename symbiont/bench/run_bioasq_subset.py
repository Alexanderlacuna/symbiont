"""BioASQ subset benchmark — OUR literature arm's retrieval vs BioASQ gold.

Runs the literature arm's real retrieval path — the pinned MCP server
(@cyanheads/pubmed-mcp-server@2.12.0, via symbiont/literature/mcp.py) with its
two legs, pubmed_search_articles (NCBI) and pubmed_europepmc_fetch (EuropePMC)
— on the FIRST N BioASQ questions of the frozen input, then scores with the
exact Elicit matcher port (symbiont/bench/bioasq_matcher.py).

Bench deviations from the gene-arm config (documented, deliberate):
  · the query is the BioASQ QUESTION TEXT verbatim (not '{SYM}[Title] AND
    mouse[MeSH Terms]' — that form is the gene arm's question, meaningless here);
  · maxResults is 50 (the deepest recall cutoff we report), not per_gene_cap 5;
  · the tool-call budget is the BENCH budget (200 calls), not the gene run's 40.

Usage:
  python3 symbiont/bench/run_bioasq_subset.py --limit 50
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

FARM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FARM))

from symbiont.literature.adapters.pubmed import PubMedAdapter  # noqa: E402
from symbiont.literature.contract import load_sources  # noqa: E402
from symbiont.literature.mcp import MCPClient  # noqa: E402

from bioasq_matcher import MATCHING, first_matches  # noqa: E402

KS = (10, 20, 50)
BENCH_BUDGET = {"tool_calls": 200, "wall_s": 3600}   # the bench budget, not the gene run's


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=Path(__file__).parent / "data" / "bioasq.jsonl")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "out")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    questions = []
    with open(args.input, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                questions.append(json.loads(line))
            if len(questions) == args.limit:
                break
    print(f"loaded {len(questions)} questions from {args.input}")

    # the arm's ONE source config, with the two documented bench overrides
    lit_dir = FARM / "symbiont" / "literature"
    cfg = load_sources(str(lit_dir / "sources.yml"))
    cfg = dict(cfg)
    cfg["query"] = "{SYM}"            # BioASQ: the query IS the question text
    cfg["per_gene_cap"] = max(KS)     # maxResults 50 = deepest cutoff reported

    adapter = PubMedAdapter()
    adapter.configure(cfg)
    mcp = MCPClient(cfg["mcp"], BENCH_BUDGET, str(lit_dir))
    print(f"MCP up: {mcp.provenance['package']}@{mcp.provenance['version']} "
          f"(pin {mcp.provenance['pin']}, server {mcp.provenance.get('server_name')} "
          f"{mcp.provenance.get('server_version')})")

    per_question = []
    tier_counts: Counter[str] = Counter()
    t0 = time.time()
    try:
        for i, row in enumerate(questions, 1):
            qid, question, gold = row["question_id"], row["question"], row["gold"]
            try:
                search = adapter.search_gene(mcp, question)      # NCBI leg, question text as query
                pmids = search["pmids"]
                records = {}                                     # EuropePMC leg, chunked:
                for j in range(0, len(pmids), 25):                 # the pinned tool caps records at 25
                    records.update(adapter.fetch_records(mcp, pmids[j:j + 25]))
                error = None
            except Exception as exc:      # a failed question is a measurement, not a dead run
                search, pmids, records, error = {"source_count": 0, "returned": 0}, [], {}, repr(exc)
            candidates = []
            for rank, pmid in enumerate(pmids, 1):
                rec = records.get(pmid, {})
                candidates.append({"rank": rank, "pmid": pmid,
                                   "doi": rec.get("doi", ""), "title": rec.get("title", "")})
            matches = first_matches(gold, candidates)
            tier_counts.update(tier for _, tier in matches.values())
            total_gold = len(gold)
            recall = {str(k): sum(1 for r, _ in matches.values() if r <= k) / total_gold
                      if total_gold else 0.0 for k in KS}
            per_question.append({
                "question_id": qid, "question": question, "error": error,
                "source_count": search["source_count"], "returned": search["returned"],
                "total_gold": total_gold, "recall": recall,
                "matches": {g: {"rank": r, "tier": t} for g, (r, t) in matches.items()},
            })
            err = f" ERROR={error[:80]}" if error else ""
            print(f"[{i:2d}/{len(questions)}] {qid} returned={search['returned']:3d} "
                  f"gold={total_gold:3d} r@10={recall['10']:.3f} r@20={recall['20']:.3f} "
                  f"r@50={recall['50']:.3f}{err} ({time.time()-t0:.0f}s, {mcp.calls} calls)")
    finally:
        mcp.close()

    macro = {str(k): sum(q["recall"][str(k)] for q in per_question) / len(per_question)
             for k in KS}
    micro_denom = sum(q["total_gold"] for q in per_question)
    micro = {str(k): sum(1 for q in per_question for m in q["matches"].values()
                         if m["rank"] <= k) / micro_denom for k in KS}

    summary = {
        "benchmark": "BioASQ (elicit/elicit-paper-search-eval data/bioasq.jsonl, frozen)",
        "input_sha256": "7031ed7d91f6bb07147d1e212914b1eba7a6c7ff8b975741d4e263bac1c1aa69",
        "subset": f"first {len(per_question)} questions of {args.limit} requested",
        "retrieval_path": (f"OUR literature arm: PubMed search (NCBI) + EuropePMC records via the "
                           f"pinned MCP server {mcp.provenance['package']}@{mcp.provenance['version']}, "
                           f"stdio JSON-RPC (symbiont/literature/mcp.py + adapters/pubmed.py)"),
        "cutoffs": list(KS),
        "num_questions": len(per_question),
        "num_error_questions": sum(1 for q in per_question if q["error"]),
        "total_gold": micro_denom,
        "macro_recall": macro,
        "micro_recall": micro,
        "match_tier_counts": dict(sorted(tier_counts.items())),
        "matching": MATCHING,
        "mcp_calls": mcp.calls,
        "wall_s": round(time.time() - t0, 1),
    }
    (args.out / f"bioasq_subset{len(per_question)}_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.out / f"bioasq_subset{len(per_question)}_results.jsonl").write_text(
        "".join(json.dumps(q) + "\n" for q in per_question), encoding="utf-8")

    print()
    print(f"| Metric | " + " | ".join(f"@{k}" for k in KS) + " |")
    print("|---|" + "---:|" * len(KS))
    print("| Macro recall | " + " | ".join(f"{macro[str(k)]:.3f}" for k in KS) + " |")
    print("| Micro recall | " + " | ".join(f"{micro[str(k)]:.3f}" for k in KS) + " |")
    print()
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
