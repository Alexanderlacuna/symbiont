"""THE EVIDENCE CONTRACT — an abstract cannot carry a claim that needs the full text
(req-P1-AN-ABSTRACT-CANNOT-CARRY-A-CLAIM-that).

Borrowed from Deep-Research-Agent (MIT), which measured '0 of 121 abstracts said how a method
was applied' and made full-text fetching CONDITIONAL on that number. This module is that idea
in three parts, one definition site each:

  · EVIDENCE LEVELS  {metadata-only, abstract, fulltext} on EVERY publication record, plus the
    field-level provenance (which fields came from which level) — `assign_evidence_level`,
    `field_provenance`, `stamp_record`. A record with no evidence_level is REFUSED at load
    (`load_records`), never defaulted to abstract: a default would be exactly the silent
    over-claim this row exists to end.

  · THE CLAIM CLASSIFIER  claim text -> kind -> the minimum evidence level the kind needs.
    Kinds that need the FULL TEXT (method-application, sample-size, direction-of-effect,
    numeric-result) are REFUSED at evidence_level=abstract with reason 'needs fulltext: <kind>';
    kinds an abstract can carry (paper-exists, is-about, is-review) pass. `classify_claim`
    refuses an unknown claim by name rather than guessing a kind.

  · THE MEASURED TRIGGER  per source, the fraction of stored abstracts that answer each claim
    kind (TRIGGER_MARKERS) — the rule is justified by a number, not by taste. The fulltext
    FETCH itself is claim-driven (the DRA borrow): it happens only when a fulltext-kind claim
    demands a record, via `fetch_fulltext()` — the pinned MCP's own OA chain
    (PMC EFetch -> EuropePMC fullTextXML -> Unpaywall), never a bespoke client and never a
    bot-detection bypass. The Unpaywall tier stays CLOSED until the deployment sets
    UNPAYWALL_EMAIL (the server skips it by name — the channel is refused, not worked around).

No HTTP lives in this module: the channels are the pinned server's. No fulltext is stored
without the record's OWN whitelisted licence (same rule as abstracts — prose under terms
nobody read is not consent).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys

from ..contract import ConfigError
from . import contract as lit
from .mcp import MCPClient

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORE = os.path.normpath(os.path.join(HERE, "..", "store"))
DEFAULT_CONFIG = os.path.join(HERE, "sources.yml")

# ── the levels ────────────────────────────────────────────────────────────────────────────────
EVIDENCE_LEVELS = ("metadata-only", "abstract", "fulltext")
LEVEL_RANK = {level: i for i, level in enumerate(EVIDENCE_LEVELS)}

# ── the claim kinds ────────────────────────────────────────────────────────────────────────────
# (kind, phrases that name it, the minimum evidence level an answer needs). The fulltext kinds
# are the four the row names; the abstract kinds are the three it says an abstract can carry.
# paper-exists needs only metadata: a PMID + its bibliography IS the existence claim.
CLAIM_KINDS: dict[str, tuple[tuple[str, ...], str]] = {
    "method-application": (("how was the method applied", "how the method was applied",
                            "how was it applied", "how did they apply", "method applied",
                            "how was the method used", "how was the technique applied"), "fulltext"),
    "sample-size":         (("sample size", "how many", "what was the n", "number of mice",
                             "number of samples", "number of patients"), "fulltext"),
    "direction-of-effect": (("direction of effect", "increase or decrease", "does it increase",
                            "does it decrease", "which way does"), "fulltext"),
    "numeric-result":     (("numeric result", "how much", "what number", "quantitative",
                            "the exact value", "p-value", "effect size"), "fulltext"),
    "paper-exists":        (("exists", "is this a real paper", "is this paper real"), "metadata-only"),
    "is-about":            (("what is this paper about", "what is it about", "is this about",
                            "is it about", "the topic of", "about"), "abstract"),
    "is-review":           (("is this a review", "is it a review", "a review",
                            "review or original"), "abstract"),
}


def classify_claim(claim: str) -> str:
    """Claim text -> kind. Longest matching phrase wins (so 'what is the sample size' is a
    sample-size claim, not an is-about claim); no match is REFUSED by name, never guessed."""
    text = " ".join((claim or "").lower().split())
    if not text:
        raise ConfigError("claim classifier: an empty claim names no kind — refused")
    best, best_len = None, 0
    for kind, (phrases, _level) in CLAIM_KINDS.items():
        for phrase in phrases:
            if phrase in text and len(phrase) > best_len:
                best, best_len = kind, len(phrase)
    if best is None:
        raise ConfigError(f"claim classifier: claim {claim!r} matches no known kind "
                          f"(kinds: {', '.join(CLAIM_KINDS)}) — refused rather than guessed")
    return best


def kind_needs(kind: str) -> str:
    return CLAIM_KINDS[kind][1]


def check_evidence_level(record: dict) -> str:
    """The load-time refusal: a record with no evidence_level is REFUSED, never defaulted
    (a default to abstract would silently let abstract-grade claims pass as evidence)."""
    level = record.get("evidence_level")
    if level not in EVIDENCE_LEVELS:
        raise ConfigError(f"publication {record.get('pmid', '?')!r} has evidence_level "
                          f"{level!r} — refused at load, never defaulted to abstract "
                          f"(allowed: {', '.join(EVIDENCE_LEVELS)})")
    return level


def answer_claim(record: dict, claim: str) -> dict:
    """The consumer's one call: can THIS record carry THAT claim? The record must have passed
    check_evidence_level (or come from load_records); the claim must classify. A fulltext kind
    at abstract level is refused with reason 'needs fulltext: <kind>' — the row's own words."""
    level = check_evidence_level(record)
    kind = classify_claim(claim)
    if LEVEL_RANK[level] >= LEVEL_RANK[kind_needs(kind)]:
        return dict(ok=True, pmid=record.get("pmid", ""), kind=kind, evidence_level=level,
                    reason=f"{level} carries {kind}")
    needs = kind_needs(kind)
    why = f"needs fulltext: {kind}" if needs == "fulltext" else f"needs abstract: {kind}"
    return dict(ok=False, pmid=record.get("pmid", ""), kind=kind, evidence_level=level, reason=why)


# ── level + provenance assignment (measured from the row's own fields) ────────────────────────
# The fields at each level, in column order. A field counts at a level only when the row
# actually CARRIES it (non-empty), so the provenance is a measurement of the row, not a stencil.
LEVEL_FIELDS = {
    "metadata-only": ("pmid", "doi", "title", "year", "journal", "pmcid", "open_access", "licence"),
    "abstract": ("abstract",),
    "fulltext": ("fulltext_sha256",),
}


def assign_evidence_level(record: dict) -> str:
    """The HIGHEST level of evidence the row actually holds — computed, never declared:
    a stored fulltext (sha256 present) is fulltext; else a stored abstract is abstract;
    else the row is bibliography at most (metadata-only, the accession floor)."""
    if record.get("fulltext_sha256"):
        return "fulltext"
    if record.get("abstract") and not record.get("abstract_withheld_reason"):
        return "abstract"
    return "metadata-only"


def field_provenance(record: dict) -> str:
    """Which fields came from which level, compact and deterministic:
    'metadata-only:pmid,doi,title,...;abstract:abstract;fulltext:fulltext_sha256'."""
    parts = []
    for level in EVIDENCE_LEVELS:
        fields = [f for f in LEVEL_FIELDS[level] if (record.get(f) or "") != ""]
        if fields:
            parts.append(f"{level}:" + ",".join(fields))
    return ";".join(parts)


def fulltext_unavailable_reason(record: dict, whitelist: list[str]) -> str:
    """Why this row is not fulltext — every row carries its reason, so a consumer never has
    to wonder whether a fetch was forbidden, impossible, or simply not demanded. '' when the
    fulltext IS stored."""
    if record.get("fulltext_sha256"):
        return ""
    if record.get("abstract_withheld_reason", "").startswith("no EuropePMC record"):
        return "accession-only — no EuropePMC record, no open-access fulltext route"
    lic = (record.get("licence") or "").strip().lower()
    allowed = [w.strip().lower() for w in whitelist]
    if not lic:
        return "no licence stated on the record — fulltext storage not permitted (terms nobody read)"
    if lic not in allowed:
        return f"licence {lic!r} not in abstract_licence_whitelist {whitelist} — fulltext storage not permitted"
    if not record.get("pmcid") and not record.get("doi"):
        return "whitelisted but no pmcid and no doi — no open-access route"
    return ("not demanded — fulltext fetch is claim-driven (the measured trigger: "
            "store/literature/trigger/; fetch with: python3 -m symbiont.literature.evidence "
            "fetch-fulltext <pmid>)")


def stamp_record(record: dict, whitelist: list[str]) -> dict:
    """Stamp evidence_level + field_provenance (+ the fulltext reason) from the row's own
    fields. Deterministic: the same row always stamps the same bytes."""
    record["evidence_level"] = assign_evidence_level(record)
    record["field_provenance"] = field_provenance(record)
    if not record.get("fulltext_unavailable_reason"):
        record["fulltext_unavailable_reason"] = fulltext_unavailable_reason(record, whitelist)
    elif record.get("fulltext_sha256"):
        record["fulltext_unavailable_reason"] = ""
    return record


# ── the store's loader — the refusal at load lives here, ONE definition site ─────────────────
def _publications_path(store: str) -> str:
    return os.path.join(store, "literature", "publications",
                        f"{lit.PUBLICATION_VIEW}.v{lit.PUBLICATION_VERSION}.tsv")


def load_records(path: str) -> dict[str, dict]:
    """Load publication rows keyed by PMID. REFUSES, naming the PMID: a row with the wrong cell
    count (a corrupted store — never guess over it), and a row with no evidence_level (this row's
    NSC: refused at load, never defaulted to abstract)."""
    if not os.path.isfile(path):
        return {}
    rows: dict[str, dict] = {}
    with open(path, encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            cells = line.rstrip("\n").split("\t")
            if len(cells) != len(header):
                raise ConfigError(f"{path}: a row has {len(cells)} cells, the header has "
                                  f"{len(header)} — refusing to guess over a corrupted store")
            rec = dict(zip(header, cells))
            check_evidence_level(rec)        # the refusal: no evidence_level, no load
            rows[rec["pmid"]] = rec
    return rows


def _cell(text: str) -> str:
    return (text or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def write_publications(store: str, rows: dict[str, dict]) -> tuple[str, str, int]:
    """Deterministic publication bytes: sorted by PMID, LF, no clock in the table. ONE
    definition site — the loop and the claim-driven fulltext upgrade both publish through it."""
    pdir = os.path.join(store, "literature", "publications")
    os.makedirs(pdir, exist_ok=True)
    names = [c[0] for c in lit.PUBLICATION_COLUMNS]
    lines = ["\t".join(names)]
    for pmid in sorted(rows, key=lambda p: int(p) if p.isdigit() else 0):
        lines.append("\t".join(_cell(rows[pmid].get(n, "")) for n in names))
    b = ("\n".join(lines) + "\n").encode()
    path = os.path.join(pdir, f"{lit.PUBLICATION_VIEW}.v{lit.PUBLICATION_VERSION}.tsv")
    with open(path, "wb") as fh:
        fh.write(b)
    schema = json.dumps(lit.PUBLICATION_SCHEMA, indent=2, sort_keys=True) + "\n"
    with open(os.path.join(pdir, f"{lit.PUBLICATION_VIEW}.v{lit.PUBLICATION_VERSION}.schema.json"),
              "w", encoding="utf-8") as fh:
        fh.write(schema)
    return path, hashlib.sha256(b).hexdigest(), len(rows)


# ── the measured trigger — the fraction of abstracts that answer each claim kind ─────────────
# MEASUREMENT heuristics, not the rule: the rule is the kind -> level table above (fixed by the
# row's own words). The markers exist so the refusal is justified by a published number per
# source — the DRA measurement ('0 of 121 abstracts said how a method was applied').
TRIGGER_MARKERS: dict[str, str] = {
    "method-application":
        r"at\s+a\s+(?:final\s+)?(?:dose|concentration|dilution)\s+of|dilution\s+of\s+\d"
        r"|according\s+to\s+the\s+manufacturer|per\s+the\s+manufacturer",
    "sample-size":
        r"\b[nn]\s*=\s*\d|\bsample\s+size\b|\d+\s+(?:mice|rats|embryos|samples|patients"
        r"|participants|cell\s+lines|subjects|animals)\b",
    "direction-of-effect":
        r"\b(?:increas\w*|decreas\w*|reduc\w*|elevat\w*|impair\w*|improv\w*|promot\w*|inhibit\w*"
        r"|enhanc\w*|attenuat\w*|abolish\w*|diminish\w*)\b",
    "numeric-result":
        r"\bp\s*[<=≤]\s*0?\.\d|\d+(?:\.\d+)?\s*(?:%|percent|fold|×)|\b(?:IC50|EC50|Ki)\b\s*[=:]?\s*\d",
    "is-review": r"\b(?:review|survey)\b",
    "is-about": r"\b(?:we\s+(?:study|report|show|investigate|examined?|analy[sz]e?)|this\s+(?:study|paper|work)\s+(?:reports?|shows?|examines?|investigates?))\b",
    "paper-exists": r"[\s\S]*",   # a stored abstract IS the paper existing — always answered
}


def abstract_answers(abstract: str, kind: str) -> bool:
    return bool(re.search(TRIGGER_MARKERS[kind], abstract, re.IGNORECASE))


def measure_trigger(store: str, config: str | None = None) -> list[dict]:
    """Per source, per claim kind: answered/total over the source's stored abstracts, in the
    KIND order of the classifier. The total is the honest denominator — every stored abstract
    of the source, none silently dropped."""
    cfg = lit.load_sources(config or DEFAULT_CONFIG)
    records = load_records(_publications_path(store))
    abstracts = {p: r for p, r in records.items()
                 if r.get("abstract") and not r.get("abstract_withheld_reason")}
    out = []
    for kind in CLAIM_KINDS:
        answered = sum(1 for r in abstracts.values() if abstract_answers(r["abstract"], kind))
        total = len(abstracts)
        out.append(dict(source=cfg["id"], kind=kind, requires=kind_needs(kind),
                         answered=answered, total=total,
                         fraction=round(answered / total, 2) if total else ""))
    return out


def cmd_trigger(store: str, config: str | None = None) -> int:
    rows = measure_trigger(store, config)
    tdir = os.path.join(store, "literature", "trigger")
    os.makedirs(tdir, exist_ok=True)
    lines = ["\t".join(("source", "kind", "requires", "answered", "total", "fraction"))]
    for r in rows:
        lines.append("\t".join(str(r[c]) for c in
                               ("source", "kind", "requires", "answered", "total", "fraction")))
    source = rows[0]["source"] if rows else "source"
    path = os.path.join(tdir, f"{source}.tsv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"# measured trigger — fraction of stored abstracts that answer each claim kind")
    print(f"# measured_at {dt.datetime.now(dt.timezone.utc).isoformat()} · store {store}")
    print("\n".join(lines))
    print(f"TRIGGER-TABLE {os.path.relpath(path, store)}")
    return 0


def cmd_claim(pmid: str, claim: str, store: str, config: str | None = None) -> int:
    records = load_records(_publications_path(store))
    if pmid not in records:
        print(f"REFUSED[no-such-record] {pmid} is not in the store "
              f"({_publications_path(store)})", file=sys.stderr)
        return 2
    rec = records[pmid]
    ans = answer_claim(rec, claim)
    print(f"pmid={pmid} claim={claim!r}")
    print(f"evidence_level={rec['evidence_level']} field_provenance={rec['field_provenance']}")
    print(f"{'PASS' if ans['ok'] else 'REFUSED'}[{ans['kind']}] {ans['reason']}")
    return 0 if ans["ok"] else 1


# ── the claim-driven fulltext fetch — the pinned MCP's OA chain, nothing else ────────────────
def _parse_fulltext(text: str) -> tuple[dict[str, dict], dict[str, str]]:
    """Parse the fulltext tool's markdown into ({pmid: {source, title, body}}, {caller-id:
    reason}). The parse is REFUSED loudly if the counts disagree — the same rule as the search
    and fetch legs: never guess which article is missing."""
    m_n = re.search(r"\*\*Articles Returned:\*\* (\d+)", text)
    if not m_n:
        raise ConfigError("fulltext leg: the tool's output no longer parses (no 'Articles "
                          "Returned' line) — the pinned MCP version changed format?")
    articles: dict[str, dict] = {}
    for block in re.split(r"(?m)^### ", text)[1:]:
        if block.startswith("Full-Text Articles"):
            continue
        lines = block.splitlines()
        pmid, source = "", ""
        for line in lines:
            m = re.match(r"\*\*PMID:\*\* (\d+)", line)
            if m:
                pmid = m.group(1)
            m = re.match(r"\*\*Source:\*\* (\S+)", line)
            if m:
                source = m.group(1).lower().rstrip(":")
        if pmid:
            articles[pmid] = dict(source=source, title=lines[0].strip(), body=block.strip())
    unavailable: dict[str, str] = {}
    m_un = re.search(r"(?s)\*\*Unavailable \((\d+)\):\*\*\n(.*?)(?:\n> |\Z)", text)
    if m_un:
        cur_id = None
        for line in m_un.group(2).splitlines():
            m = re.match(r"- \[(pmid|pmcid|doi)\] (\S+) — (.+)", line)
            if m:
                cur_id = m.group(2)
                unavailable[cur_id] = m.group(3)
            elif cur_id and line.startswith("  ") and line.strip():
                unavailable[cur_id] += " | " + line.strip()
    if int(m_n.group(1)) != len(articles):
        raise ConfigError(f"fulltext leg: the tool says 'Articles Returned: {m_n.group(1)}' but "
                          f"{len(articles)} article(s) parsed; refusing to guess")
    return articles, unavailable


def fetch_fulltext(pmids: list[str], store: str, config: str | None = None,
                   run_id: str | None = None) -> dict:
    """The claim-driven upgrade: fulltext for the NAMED records only, via the pinned MCP's own
    OA chain (PMC EFetch -> EuropePMC fullTextXML -> Unpaywall). Eligibility is the storage
    licence (the record's OWN whitelisted licence — the same rule as abstracts) plus a route
    (pmcid or doi). Where it cannot be fetched or is not permitted, the RECORD says so in
    fulltext_unavailable_reason and its evidence_level stays what it actually holds."""
    t0 = dt.datetime.now(dt.timezone.utc)
    run_id = run_id or t0.strftime("%Y%m%dT%H%M%SZ")
    cfg = lit.load_sources(config or DEFAULT_CONFIG)
    if not cfg["fulltext"]["enabled"]:
        raise ConfigError("fulltext leg is disabled in sources.yml (fulltext.enabled: false)")
    whitelist = cfg["abstract_licence_whitelist"]
    records = load_records(_publications_path(store))
    record = dict(run_id=run_id, started_at=t0.isoformat(), command="fetch-fulltext",
                 pmids={}, mcp=cfg["mcp"]["package"] + "@" + cfg["mcp"]["version"],
                 unpaywall_tier=("active (UNPAYWALL_EMAIL is set)"
                                 if os.environ.get("UNPAYWALL_EMAIL")
                                 else "closed (UNPAYWALL_EMAIL not set — the server skips the "
                                      "tier by name; the channel is never bypassed)"),
                 status="", cost={})
    eligible: list[str] = []
    for pmid in pmids:
        if pmid not in records:
            raise ConfigError(f"fulltext leg: {pmid} is not in the store — nothing to upgrade")
        rec = records[pmid]
        why = fulltext_unavailable_reason(rec, whitelist)
        if rec.get("fulltext_sha256"):
            record["pmids"][pmid] = "already fulltext"
        elif "not permitted" in why or "no open-access fulltext route" in why or "no open-access route" in why:
            record["pmids"][pmid] = f"REFUSED: {why}"
            rec["fulltext_unavailable_reason"] = why
        else:
            eligible.append(pmid)
            record["pmids"][pmid] = "eligible"
    fetched: dict[str, dict] = {}
    refused: dict[str, str] = {}
    if eligible:
        mcp = MCPClient(cfg["mcp"], cfg["budget"], HERE)
        try:
            args: dict[str, list] = {}
            for pmid in eligible:
                rec = records[pmid]
                key = "pmcids" if rec.get("pmcid") else "dois"
                args.setdefault(key, []).append(rec["pmcid"] if key == "pmcids" else rec["doi"])
            text = mcp.call("pubmed_fetch_fulltext", args)
            fetched, refused = _parse_fulltext(text)
        finally:
            mcp.close()
        record["cost"] = dict(tool_calls=mcp.calls)
    fdir = os.path.join(store, "literature", "fulltext")
    os.makedirs(fdir, exist_ok=True)
    outcomes = dict(fulltext_stored=0, unavailable=0, refused=0, already=0)
    for pmid in pmids:
        state = record["pmids"][pmid]
        if state == "already fulltext":
            outcomes["already"] += 1
        elif state.startswith("REFUSED"):
            outcomes["refused"] += 1
    for pmid in eligible:
        rec = records[pmid]
        art = fetched.get(pmid)
        if art is None:
            why = (refused.get(rec.get("pmcid", ""), None) or refused.get(rec.get("doi", ""), None)
                   or refused.get(pmid, None) or "not returned by the OA chain (PMC/EPMC/Unpaywall)")
            rec["fulltext_unavailable_reason"] = f"unavailable: {why}"
            record["pmids"][pmid] = f"unavailable: {why}"
            outcomes["unavailable"] += 1
            continue
        body = art["body"].encode("utf-8")
        path = os.path.join(fdir, f"{pmid}.md")
        with open(path, "wb") as fh:
            fh.write(body)
        rec["fulltext_sha256"] = hashlib.sha256(body).hexdigest()
        rec["fulltext_source"] = art["source"]
        rec["fulltext_unavailable_reason"] = ""
        stamp_record(rec, whitelist)
        record["pmids"][pmid] = (f"fulltext stored: fulltext/{pmid}.md "
                                 f"({len(body)} bytes, sha256 {rec['fulltext_sha256'][:16]}…, "
                                 f"via {art['source']})")
        outcomes["fulltext_stored"] += 1
    # re-stamp every record and republish the table deterministically (idempotent: an
    # untouched row stamps to the bytes it already carries)
    for pmid in records:
        stamp_record(records[pmid], whitelist)
    path, sha, n = write_publications(store, records)
    record["status"] = "PUBLISHED"
    record["publish"] = dict(publications=dict(path=path, sha256=sha, rows=n))
    record["outcomes"] = outcomes
    rpath = os.path.join(store, "literature", "runs", f"{run_id}.json")
    os.makedirs(os.path.dirname(rpath), exist_ok=True)
    with open(rpath, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
        fh.write("\n")
    for pmid in pmids:
        print(f"FULLTEXT {pmid}  {record['pmids'][pmid]}")
    print(f"PUBLISH  {os.path.relpath(path, store)}  rows={n}  sha256={sha}")
    print(f"RUN-RECORD {os.path.relpath(rpath, store)}")
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the evidence contract: levels, the claim "
                                             "classifier, the measured trigger, the "
                                             "claim-driven fulltext fetch")
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("trigger", help="print + write the per-source trigger table")
    p_claim = sub.add_parser("claim", help="can this record carry that claim?")
    p_claim.add_argument("pmid")
    p_claim.add_argument("claim")
    p_ft = sub.add_parser("fetch-fulltext", help="claim-driven OA fulltext upgrade of named records")
    p_ft.add_argument("pmids", nargs="+")
    p_ft.add_argument("--run-id", default=None)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "trigger":
            return cmd_trigger(a.store, a.config)
        if a.cmd == "claim":
            return cmd_claim(a.pmid, a.claim, a.store, a.config)
        if a.cmd == "fetch-fulltext":
            fetch_fulltext(a.pmids, a.store, a.config, a.run_id)
            return 0
    except ConfigError as e:
        print(f"REFUSED[{getattr(e, 'code', 'config')}] {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
