"""THE LITERATURE LOOP — one fixed route, keyed by the xref table:

    gene scope (validated against the gene arm's xref.tsv, by name)
      -> the pubmed adapter -> search per gene (the MCP's own count quoted with the URL)
      -> fetch the new PMIDs' EuropePMC records -> abstract policy (per-record licence)
      -> store publications (one row per PMID, source_release = fetch date + MCP version)
      -> join gene -> PMIDs (view/gene_publication.v<PUBLICATION_VERSION>.tsv — v2 carries
                                 evidence_level per pair) -> run record

Run:   python3 -m symbiont.literature.loop --run-id lit-run-1     (first run: stores records)
       python3 -m symbiont.literature.loop --run-id lit-run-2     (second run: zero new records)

Idempotency is the NSC: run two searches AGAIN (that is how zero-new is proven, not assumed),
stores only what it does not already have, and rewrites the views deterministically. Literature
has no release id (the court's change), so a run never claims freshness it cannot measure — it
records its fetch date and the pinned MCP version, which together ARE the release.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys

from .. import contract as gene_contract
from ..http import Fetcher
from . import contract as lit
from . import evidence
from .adapters.pubmed import PubMedAdapter
from .mcp import MCPClient, MCPError

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORE = os.path.normpath(os.path.join(HERE, "..", "store"))
DEFAULT_CONFIG = os.path.join(HERE, "sources.yml")
XREF_TABLE = "xref/xref.tsv"          # the gene arm's OUTPUT: the keys this arm joins on


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def say(line: str) -> None:
    print(line, flush=True)


def jdump(obj, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def _cell(text: str) -> str:
    """TSV cell: the abstract is the largest thing stored, and even it is flattened — no full text."""
    return (text or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def _read_xref(store: str) -> tuple[dict, str, int]:
    """The join keys come from the gene arm's published xref table — never from the source."""
    path = os.path.join(store, XREF_TABLE)
    if not os.path.isfile(path):
        raise gene_contract.ConfigError(f"the gene arm's xref table is missing: {path} — the "
                                        f"literature arm joins on it and may not invent gene ids")
    raw = open(path, "rb").read()
    out: dict[str, dict] = {}
    with open(path, encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        idx = {name: i for i, name in enumerate(header)}
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) < 3:
                continue
            key, authority, provider, status = f[0], f[1], f[2], (f[3] if len(f) > 3 else "ok")
            if status == "ok":
                out[key] = dict(symbol=key, ensembl_gene_id=authority, mgi_id=provider)
    return out, sha256(raw), len(out)


def _load_publications(store: str) -> dict[str, dict]:
    """The store's publication rows, keyed by PMID — this is what makes run two store nothing.
    Loads through evidence.load_records: a row with no evidence_level is REFUSED at load,
    never defaulted (evidence.py is the ONE definition site of that refusal)."""
    return evidence.load_records(os.path.join(
        store, "literature", "publications",
        f"{lit.PUBLICATION_VIEW}.v{lit.PUBLICATION_VERSION}.tsv"))


def _carry_v1_forward(store: str, rows: dict[str, dict], whitelist: list[str]) -> int:
    """The v1 -> v2 carry-forward (one explicit, named migration — NOT a load of unstamped
    records into the current store): publication.v1.tsv predates the evidence_level column,
    so its rows are stamped here by COMPUTATION (assign_evidence_level reads the row's own
    fields — the same function that stamps every new record; nothing is assumed 'abstract'),
    and a PMID already in the v1 store may not vanish from the v2 store just because the
    schema moved. The strict refusal still governs the CURRENT store: a publication.v2.tsv
    row without evidence_level is refused at load, always (evidence.load_records)."""
    v1 = os.path.join(store, "literature", "publications", f"{lit.PUBLICATION_VIEW}.v1.tsv")
    if not os.path.isfile(v1):
        return 0
    carried = 0
    with open(v1, encoding="utf-8") as fh:
        cols = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) != len(cols):
                raise gene_contract.ConfigError(f"{v1}: a row has {len(f)} cells, the header has "
                                                f"{len(cols)} — refusing to guess over a corrupted store")
            rec = dict(zip(cols, f))
            if rec["pmid"] in rows:
                continue
            evidence.stamp_record(rec, whitelist)
            rows[rec["pmid"]] = rec
            carried += 1
    return carried


def _terms(cfg: dict) -> dict:
    """Read the data terms page at run time and hash it — data never lands under terms nobody
    read. Reuses the gene arm's Fetcher: no second HTTP client exists in this farm."""
    fetcher = Fetcher(budget=dict(requests=4, bytes=2_000_000, wall_s=120),
                      rate_limit=dict(requests_per_minute=30, min_interval_s=1.0))
    page = fetcher.get(cfg["licence_url"], "literature terms of use")
    text = page.decode("utf-8", "replace")
    m = re.search(cfg["terms_pattern"], text)
    if not m:
        raise gene_contract.LicenceUnknown(
            f"pubmed: terms page {cfg['licence_url']} no longer states its terms in the expected "
            f"words ({len(page)} bytes read) — refusing to ingest under terms nobody read")
    return dict(name="EuropePMC / EMBL-EBI terms of use", url=cfg["licence_url"],
                quote=re.sub(r"\s+", " ", m.group(0)), terms_sha256=sha256(page),
                requests=len(fetcher.log), bytes_in=fetcher.bytes_in,
                note="per-record licences govern abstract storage; see abstract_licence_whitelist")


def _write_publications(store: str, rows: dict[str, dict]) -> tuple[str, str, int]:
    """Deterministic bytes: sorted by PMID, LF, no clock in the table (dates live in the rows).
    Delegates to evidence.write_publications — the ONE writer, shared with the claim-driven
    fulltext upgrade, so both publish byte-identical tables from the same rows."""
    return evidence.write_publications(store, rows)


def _write_gene_view(store: str, pairs: list[dict], provenance: dict) -> tuple[str, str, int]:
    vdir = os.path.join(store, "literature", "view")
    os.makedirs(vdir, exist_ok=True)
    names = [c[0] for c in lit.GENE_PUBLICATION_COLUMNS]
    lines = ["\t".join(names)]
    for p in sorted(pairs, key=lambda p: (p["symbol"], int(p["pmid"]) if p["pmid"].isdigit() else 0)):
        lines.append("\t".join(_cell(p.get(n, "")) for n in names))
    b = ("\n".join(lines) + "\n").encode()
    path = os.path.join(vdir, f"{lit.GENE_PUBLICATION_VIEW}.v{lit.GENE_PUBLICATION_VERSION}.tsv")
    with open(path, "wb") as fh:
        fh.write(b)
    schema = json.dumps(lit.GENE_PUBLICATION_SCHEMA, indent=2, sort_keys=True) + "\n"
    spath = os.path.join(vdir, f"{lit.GENE_PUBLICATION_VIEW}.v{lit.GENE_PUBLICATION_VERSION}.schema.json")
    with open(spath, "w", encoding="utf-8") as fh:
        fh.write(schema)
    jdump(provenance, os.path.join(vdir, f"{lit.GENE_PUBLICATION_VIEW}.v{lit.GENE_PUBLICATION_VERSION}.provenance.json"))
    return path, sha256(b), len(pairs)


def run(store: str, run_id: str | None, config: str | None = None,
        genes: list[str] | None = None) -> dict:
    t0 = utc_now()
    run_id = run_id or t0.strftime("%Y%m%dT%H%M%SZ")
    for sub in ("publications", "view", "runs", "state"):
        os.makedirs(os.path.join(store, "literature", sub), exist_ok=True)

    cfg = lit.load_sources(config or DEFAULT_CONFIG)
    adapter = PubMedAdapter()
    adapter.configure(cfg)

    scope = genes or cfg["gene_scope"]
    # 1 the join keys: the xref table, by name --------------------------------------------------
    xref, xref_sha, xref_rows = _read_xref(store)
    resolved = {}
    for sym in scope:
        if sym not in xref:
            raise gene_contract.ConfigError(
                f"gene_scope symbol {sym!r} is not an 'ok' row in the xref table "
                f"({os.path.join(store, XREF_TABLE)}, {xref_rows} ok keys, sha256 {xref_sha[:12]}…) — "
                f"this arm is KEYED BY THE XREF TABLE and may not join on a gene it did not publish")
        resolved[sym] = xref[sym]
    say(f"SCOPE    {len(scope)} gene(s) resolved against the xref table "
        f"({xref_rows} ok keys, sha256={xref_sha[:16]}…): "
        + ", ".join(f"{s}({resolved[s]['mgi_id']}/{resolved[s]['ensembl_gene_id']})" for s in scope))

    # 2 the terms, read at run time -------------------------------------------------------------
    terms = _terms(cfg)
    say(f"LICENCE  data terms read from {terms['url']} ({terms['bytes_in']} bytes, "
        f"sha256={terms['terms_sha256'][:16]}…): \"{terms['quote'][:96]}…\"")

    # 3 the pinned MCP server ------------------------------------------------------------------
    mcp = MCPClient(cfg["mcp"], cfg["budget"], HERE)
    prov = mcp.provenance
    say(f"MCP      {prov['server_name']} v{prov['server_version']} — pin {prov['package']}@"
        f"{prov['pin']} verified on disk ({prov['install']}); server licence line 1: "
        f"\"{prov['licence_first_line']}\"; npm integrity {prov['npm_integrity'][:24]}…")

    source_release = f"{t0.date().isoformat()} + {prov['package']}@{prov['pin']}"
    known = _load_publications(store)
    rows = dict(known)                      # the store: existing rows + what this run adds
    carried_v1 = _carry_v1_forward(store, rows, cfg["abstract_licence_whitelist"])
    say(f"STORE    {len(known)} publication row(s) already in the store before this run"
        + (f" (+{carried_v1} carried forward from publication.v1.tsv, evidence stamped by "
            f"computation — the v1 table predates the column)" if carried_v1 else ""))

    # 4 search per gene, fetch the new, account for every PMID ---------------------------------
    record = dict(run_id=run_id, started_at=t0.isoformat(), config=os.path.abspath(config or DEFAULT_CONFIG),
                 scope={}, terms=terms, mcp=prov, source_release=source_release, xref=dict(
                     table=os.path.join(store, XREF_TABLE), sha256=xref_sha, ok_keys=xref_rows),
                 migration=dict(carried_v1_forward=carried_v1,
                                note="publication.v1 rows stamped by assign_evidence_level — "
                                     "computed from the row's own fields, never defaulted"),
                 genes_searched=0, publications=dict(new_stored=0, known=0, accession_only=0),
                 abstracts=dict(stored=0, withheld_by_licence=0, withheld_no_licence=0),
                 evidence={}, verify=[], pairs=0, status="", cost={})
    pairs: list[dict] = []
    refused_exc = None
    try:
        for sym in scope:
            g = resolved[sym]
            search = adapter.search_gene(mcp, sym)
            record["genes_searched"] += 1
            new_ids = [p for p in search["pmids"] if p not in known]
            fetch_ids = [p for p in new_ids]
            recs = adapter.fetch_records(mcp, fetch_ids) if fetch_ids else {}
            acc = dict(new=0, known=0, accession_only=0)
            for pmid in search["pmids"]:
                if pmid in known:
                    acc["known"] += 1
                    record["publications"]["known"] += 1
                    rec = dict(known[pmid])
                    # a row read back from the TSV carries no abstract_stored flag — derive it,
                    # never guess: stored means a non-empty abstract and no withheld-reason
                    rec["abstract_stored"] = ("yes" if rec.get("abstract") and
                                              not rec.get("abstract_withheld_reason") else "no")
                    evidence.stamp_record(rows[pmid], cfg["abstract_licence_whitelist"])  # idempotent
                    rec["evidence_level"] = rows[pmid]["evidence_level"]
                else:
                    rec = recs.get(pmid)
                    if rec is None:
                        acc["accession_only"] += 1
                        rec = dict(pmid=pmid, doi="", title="", year="", journal="", pmcid="",
                                   open_access="", licence="", abstract="",
                                   abstract_withheld_reason="no EuropePMC record returned for this "
                                                            "PMID (accession-only)")
                        record["publications"]["accession_only"] += 1
                    else:
                        acc["new"] += 1
                        record["publications"]["new_stored"] += 1
                        if rec["abstract_stored"] == "yes":
                            record["abstracts"]["stored"] += 1
                        elif rec.get("licence"):
                            record["abstracts"]["withheld_by_licence"] += 1
                        else:
                            record["abstracts"]["withheld_no_licence"] += 1
                    row = {n: rec.get(n, "") for n, _, _ in lit.PUBLICATION_COLUMNS}
                    row["pmid"] = pmid
                    row["source_release"] = source_release      # the fetch date + the MCP version
                    row["first_seen_run"] = run_id
                    evidence.stamp_record(row, cfg["abstract_licence_whitelist"])
                    rec["evidence_level"] = row["evidence_level"]
                    rows[pmid] = row
                pairs.append(dict(symbol=sym, mgi_id=g["mgi_id"], ensembl_gene_id=g["ensembl_gene_id"],
                                  pmid=pmid, title=rec.get("title", ""), year=rec.get("year", ""),
                                  journal=rec.get("journal", ""), licence=rec.get("licence", ""),
                                  abstract_stored=rec.get("abstract_stored", "no"),
                                  evidence_level=rec.get("evidence_level", "")))
            v = adapter.verify(search, acc)
            record["verify"].append(dict(symbol=sym, ok=v["ok"], ours=v["ours"],
                                         source_count=v["source_count"], url=v["url"], detail=v["detail"]))
            say(f"SEARCH   {sym}  query={search['query']!r}  returned={search['returned']} of "
                f"source_count={search['source_count']}  pmids new={acc['new']} known={acc['known']} "
                f"accession_only={acc['accession_only']}  ({search['beyond_cap']} beyond the "
                f"per_gene_cap of {search['cap']}, not fetched)  url={search['url'][:88]}"
                + ("…" if len(search["url"]) > 88 else ""))
            say(f"VERIFY   {sym}  {'OK' if v['ok'] else 'DISAGREE'}  ours={v['ours']} vs the tool's "
                f"own Returned={search['returned']}  source's own hit count={v['source_count']}")
        record["pairs"] = len(pairs)

        # 5 publish — publications, the join view, state, the run record ------------------------
        levels = {lv: sum(1 for r in rows.values() if r.get("evidence_level") == lv)
                  for lv in evidence.EVIDENCE_LEVELS}
        record["evidence"] = dict(
            levels=levels,
            policy=("evidence_level in {metadata-only, abstract, fulltext} on every record, computed "
                    "from the row's own fields, plus field-level provenance; a record with no "
                    "evidence_level is refused at load, never defaulted (evidence.load_records)"),
            claim_classifier=("kinds that need fulltext (method-application, sample-size, "
                              "direction-of-effect, numeric-result) are refused at abstract level "
                              "with 'needs fulltext: <kind>'; paper-exists/is-about/is-review pass "
                              "(evidence.answer_claim)"),
            trigger="measured per source — the fraction of stored abstracts that answer each claim "
                    "kind: python3 -m symbiont.literature.evidence trigger (writes "
                    "store/literature/trigger/)",
            fulltext=("claim-driven (the Deep-Research-Agent borrow: fetch only what a fulltext-kind "
                     "claim demands), only via the pinned MCP's open-access chain PMC EFetch -> "
                     "EuropePMC fullTextXML -> Unpaywall, and only under the record's own whitelisted "
                     "licence — no bespoke client, no bot-detection bypass; where it cannot be "
                     "fetched the record says so in fulltext_unavailable_reason and its level is "
                     "what it holds (metadata-only when nothing else)"),
            unpaywall_tier=("active (UNPAYWALL_EMAIL set)" if os.environ.get("UNPAYWALL_EMAIL")
                            else "closed (UNPAYWALL_EMAIL not set — the server skips the tier by "
                                 "name; the channel is never bypassed)"),
            fulltext_config_enabled=cfg["fulltext"]["enabled"])
        ppath, psha, pn = _write_publications(store, rows)
        say(f"PUBLISH  {os.path.relpath(ppath, store)}  rows={pn}  sha256={psha}  "
            f"new this run={record['publications']['new_stored'] + record['publications']['accession_only']}")
        vprov = dict(view=f"{lit.GENE_PUBLICATION_VIEW}.v{lit.GENE_PUBLICATION_VERSION}",
                     rows=len(pairs), run_id=run_id, generated_at=utc_now().isoformat(),
                     keyed_by=dict(table=os.path.join(store, XREF_TABLE), sha256=xref_sha,
                                   ok_keys=xref_rows, scope={s: resolved[s] for s in scope}),
                     publications=dict(path=ppath, sha256=psha, rows=pn),
                     mcp=prov, terms=terms, source_release=source_release,
                     abstract_policy=dict(whitelist=cfg["abstract_licence_whitelist"],
                                          rule="abstract stored only under the record's own "
                                               "whitelisted licence; otherwise accession-only with reason"),
                     evidence=dict(levels=levels,
                                   claim_classifier="symbiont/literature/evidence.py::answer_claim",
                                   trigger_table="store/literature/trigger/",
                                   fulltext="claim-driven, OA-only via the pinned MCP chain "
                                            "(PMC EFetch -> EuropePMC fullTextXML -> Unpaywall)"),
                     no_full_text="no full text is stored by the LOOP, ever — fulltext is a separate "
                                  "claim-driven upgrade (evidence.fetch_fulltext); the abstract is the "
                                  "largest thing the loop writes")
        vpath, vsha, vn = _write_gene_view(store, pairs, vprov)
        say(f"PUBLISH  {os.path.relpath(vpath, store)}  rows={vn}  sha256={vsha}")
        state = dict(source="pubmed", run_id=run_id, updated_at=utc_now().isoformat(),
                     fetch_date=t0.date().isoformat(), scope=scope, stored_pmids=pn,
                     source_release=source_release, mcp=prov, terms_sha256=terms["terms_sha256"],
                     last_run_id=run_id)
        spath = jdump(state, os.path.join(store, "literature", "state", "pubmed.json"))
        say(f"STATE    {os.path.relpath(spath, store)}")
        record["status"] = "PUBLISHED"
        record["publish"] = dict(publications=dict(path=ppath, sha256=psha, rows=pn),
                                 gene_view=dict(path=vpath, sha256=vsha, rows=vn),
                                 state=spath)
    except (MCPError, gene_contract.Refusal) as e:
        refused_exc = e
        record["status"] = "REFUSED"
        record["refusal"] = dict(code=getattr(e, "code", type(e).__name__), detail=str(e))
        say(f"REFUSED[{getattr(e, 'code', 'error')}] {e}")
    finally:
        mcp.close()
        record["cost"] = dict(tool_calls=mcp.calls, wall_s=round((utc_now() - t0).total_seconds(), 1))
        rpath = jdump(record, os.path.join(store, "literature", "runs", f"{run_id}.json"))
        say(f"COST     tool_calls={record['cost']['tool_calls']}  wall={record['cost']['wall_s']}s")
        say(f"RUN-RECORD {os.path.relpath(rpath, store)}")
    if record["status"] == "REFUSED":
        record["exit"] = 2
    else:
        record["exit"] = 0 if all(v["ok"] for v in record["verify"]) else 1
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the literature arm (publications keyed by the xref table)")
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--genes", default="", help="comma list overriding gene_scope (smoke tests)")
    a = ap.parse_args(argv)
    try:
        rec = run(a.store, a.run_id, a.config, [g for g in a.genes.split(",") if g] or None)
    except gene_contract.ConfigError as e:
        say(f"REFUSED[{e.code}] {e}")
        return 2
    except (MCPError, gene_contract.Refusal) as e:
        say(f"REFUSED[{getattr(e, 'code', 'error')}] {type(e).__name__}: {e}")
        return 2
    except RuntimeError as e:
        say(f"REFUSED[error] {type(e).__name__}: {e}")
        return 2
    if rec["status"] == "REFUSED":
        return 2
    # exit 0 with accession-only rows is correct: a withheld abstract is data WITH a reason.
    return rec["exit"]


if __name__ == "__main__":
    sys.exit(main())
