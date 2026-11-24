"""THE PHENOTYPES LOOP — one fixed route, keyed by the gene arm's xref table:

    gene scope (validated against the gene arm's xref.tsv, by name)
      -> the term authority (the ontology) -> the phenotype TERMS
      -> each association provider -> its own rows for the scope genes
         (every row refused by name that must be: no ontology id, unknown term,
          evidence outside the source's vocabulary, no allele/marker id)
      -> the store (one row per source's own association row; BOTH rows kept on disagreement,
          the conflict COUNTED in conflicts.tsv — never averaged, never dropped)
      -> the door view gene -> phenotypes with evidence (accession + release on every item)
      -> accounting that must close: rows_in = records + rejects + used (SCHEMA.md §5)

Run:   python3 -m symbiont.phenotypes.loop --run-id pheno-run-1     (first run: stores records)
       python3 -m symbiont.phenotypes.loop --run-id pheno-run-2     (second run: zero new records)

Idempotency is the NSC: the second run stores NOTHING new (records=0, every row `used`) and
republishes byte-identical tables, because fetched_at/first_seen_run/source_release are stamped
at first store and preserved — a run never claims freshness it cannot measure.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys

from .. import contract as gene_contract
from ..http import Fetcher
from . import contract as pheno
from .adapters import _pheno_files, load_adapter

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORE = os.path.normpath(os.path.join(HERE, "..", "store"))
DEFAULT_CONFIG = os.path.join(HERE, "sources.yml")
XREF_TABLE = "xref/xref.tsv"          # the gene arm's OUTPUT: the keys this arm joins on

PAYLOAD_FIELDS = ("gene_id", "phenotype_term_id", "allele_marker_id", "genotype_id",
                  "evidence_code", "citation", "statement")


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
    return (text or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def _read_xref(store: str) -> tuple[dict, dict, str, int]:
    """The join keys come from the gene arm's published xref table — never from the source.
    Returns (by_symbol, by_mgi_id, sha256, ok_keys)."""
    path = os.path.join(store, XREF_TABLE)
    if not os.path.isfile(path):
        raise gene_contract.ConfigError(f"the gene arm's xref table is missing: {path} — the "
                                        f"phenotypes arm joins on it and may not invent gene ids")
    raw = open(path, "rb").read()
    by_symbol: dict[str, dict] = {}
    by_mgi: dict[str, dict] = {}
    with open(path, encoding="utf-8") as fh:
        fh.readline()                      # the header line
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) < 3:
                continue
            key, authority, provider, status = f[0], f[1], f[2], (f[3] if len(f) > 3 else "ok")
            if status == "ok":
                row = dict(symbol=key, ensembl_gene_id=authority, gene_id=provider)
                by_symbol[key] = row
                by_mgi[provider] = row
    return by_symbol, by_mgi, sha256(raw), len(by_symbol)


def _read_terms(store: str) -> tuple[dict, int]:
    """The store's current phenotype records, keyed by (payload, source) — run two stores nothing."""
    path = os.path.join(store, "phenotypes", "phenotypes",
                        f"{pheno.PHENOTYPE_VIEW}.v{pheno.PHENOTYPE_VERSION}.tsv")
    if not os.path.isfile(path):
        return {}, 0
    out: dict = {}
    with open(path, encoding="utf-8") as fh:
        cols = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) != len(cols):
                raise gene_contract.ConfigError(f"{path}: a row has {len(f)} cells, the header has "
                                                f"{len(cols)} — refusing to guess over a corrupted store")
            rec = dict(zip(cols, f))
            rec["_key"] = _row_key(rec)
            out[rec["_key"]] = rec
        return out, len(out)


def _row_key(rec: dict) -> tuple:
    return tuple(rec.get(n, "") for n in PAYLOAD_FIELDS) + (rec.get("source", ""),)


def _localise(cfg: dict, config_dir: str) -> dict:
    """A fixture source (base_url 'fixture:local') reads the SAME formats from disk: resolve its
    endpoint paths against the config's own directory, so the fixture exercises the real parse
    with zero requests. A network source is returned unchanged."""
    if not str(cfg.get("base_url", "")).startswith("fixture:"):
        return cfg
    out = dict(cfg)
    out["paths"] = {k: os.path.join(config_dir, v) for k, v in cfg["endpoints"].items()}
    return out


def _licence(cfg: dict) -> dict:
    """Read the licence page at run time and hash it — data never lands under terms nobody read.
    A local fixture source declares no licence_url: its bytes are this farm's own."""
    if not cfg.get("licence_url"):
        return dict(name=cfg.get("licence_text", "local fixture data"),
                    url="", quote="", terms_sha256="", note="no licence_url — a local fixture, "
                    "authored by this farm for its own selftest")
    fetcher = Fetcher(budget=dict(requests=4, bytes=2_000_000, wall_s=120),
                      rate_limit=dict(requests_per_minute=30, min_interval_s=1.0))
    page = fetcher.get(cfg["licence_url"], f"{cfg['id']} licence")
    text = page.decode("utf-8", "replace")
    import re
    m = re.search(cfg["licence_pattern"], text)
    if not m:
        raise gene_contract.LicenceUnknown(
            f"{cfg['id']}: licence page {cfg['licence_url']} no longer states its terms in the "
            f"expected words ({len(page)} bytes read) — refusing to ingest under terms nobody read")
    quote = re.sub(r"\s+", " ", m.group(0)).strip()
    # the page's own wording may sit mid-HTML-tag (the pattern stops at the first '.'), so a
    # PARTIAL tag can end the quote — strip whole tags and any trailing partial one, never a word
    quote = re.sub(r"<[^>]*>", "", quote)
    quote = re.sub(r"<[^>]*$", "", quote).strip()
    return dict(name=cfg.get("licence_text", ""), url=cfg["licence_url"],
                quote=quote, terms_sha256=sha256(page),
                requests=len(fetcher.log), bytes_in=fetcher.bytes_in, note="")


def _snapshot(raw_dir: str, source_id: str, fname: str, body: bytes) -> tuple[str, str]:
    """Content-addressed raw zone: the bytes the store was built from, sha256-named."""
    os.makedirs(os.path.join(raw_dir, source_id), exist_ok=True)
    path = os.path.join(raw_dir, source_id, f"{sha256(body)[:16]}-{fname}")
    with open(path, "wb") as fh:
        fh.write(body)
    return path, sha256(body)


def _write_table(path: str, columns, rows: list[dict], sort_key) -> tuple[str, str, int]:
    names = [c[0] for c in columns]
    lines = ["\t".join(names)]
    for r in sorted(rows, key=sort_key):
        lines.append("\t".join(_cell(r.get(n, "")) for n in names))
    b = ("\n".join(lines) + "\n").encode()
    with open(path, "wb") as fh:
        fh.write(b)
    return path, sha256(b), len(rows)


def _write_schema(path: str, schema: dict) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    return path


yield_store: dict = {}


def run(store: str, run_id: str | None, config: str | None = None,
        genes: list[str] | None = None) -> dict:
    t0 = utc_now()
    run_id = run_id or t0.strftime("%Y%m%dT%H%M%SZ")
    raw_dir = os.path.join(store, "phenotypes", "raw")
    for sub in ("phenotypes", "view", "runs", "state"):
        os.makedirs(os.path.join(store, "phenotypes", sub), exist_ok=True)

    config_path = os.path.abspath(config or DEFAULT_CONFIG)
    fixture_dir = os.path.dirname(config_path)
    roster = pheno.load_sources(config_path)
    enabled = [c for c in roster if c["enabled"]]
    skipped = [c["id"] for c in roster if not c["enabled"]]

    # 1 the join keys: the xref table, by name --------------------------------------------------
    by_symbol, by_mgi, xref_sha, xref_rows = _read_xref(store)
    scope_cfg = [c for c in enabled if c["role"] == "association_provider"][0]
    scope = genes or scope_cfg.get("gene_scope")
    resolved = {}
    for sym in scope:
        if sym not in by_symbol:
            raise gene_contract.ConfigError(
                f"gene_scope symbol {sym!r} is not an 'ok' row in the xref table "
                f"({os.path.join(store, XREF_TABLE)}, {xref_rows} ok keys, sha256 {xref_sha[:12]}…) — "
                f"this arm is KEYED BY THE XREF TABLE and may not join on a gene the gene arm "
                f"did not publish")
        resolved[sym] = by_symbol[sym]
    scope_ids = {r["gene_id"] for r in resolved.values()}
    say(f"SCOPE    {len(scope)} gene(s) resolved against the xref table ({xref_rows} ok keys, "
        f"sha256={xref_sha[:16]}…): "
        + ", ".join(f"{s}({resolved[s]['gene_id']}/{resolved[s]['ensembl_gene_id']})" for s in scope))

    record = dict(run_id=run_id, started_at=t0.isoformat(),
                  config=os.path.abspath(config or DEFAULT_CONFIG), scope={s: resolved[s] for s in scope},
                  xref=dict(table=os.path.join(store, XREF_TABLE), sha256=xref_sha, ok_keys=xref_rows),
                  skipped_sources=skipped, sources={}, conflicts=dict(count=0, pairs=[]),
                  corroborated=0, records_total=0, verify=[], accounting={}, cost={},
                  status="")
    requests_total = 0
    # 2 the term authority — the ONLY source of a phenotype TERM name ---------------------------
    terms_map: dict[str, str] = {}
    term_src = [c for c in enabled if c["role"] == "term_authority"][0]
    for cfg in [term_src]:
        cfg = _localise(cfg, fixture_dir)
        cfg = _localise(cfg, fixture_dir)
        adapter = load_adapter(cfg["adapter"])
        adapter.configure(cfg)
        lic = _licence(cfg)
        text, facts = adapter.fetch_payload()
        snapshot_path, _ = _snapshot(raw_dir, cfg["id"],
                                     os.path.basename(facts["url"].split("?")[0]),
                                     text.encode("utf-8", "replace"))
        rows, malformed = _pheno_files.parse_terms(text)
        terms_map = {r["term_id"]: r["term"] for r in rows}
        say(f"TERMS    {cfg['id']}  {len(terms_map)} MP term(s) from {os.path.basename(facts['url'])} "
            f"({facts['bytes']} bytes, sha256={facts['sha256'][:16]}…, release "
            f"\"{facts['release']}\")"
            + (f", {malformed} malformed row(s) counted" if malformed else ""))
        record["sources"][cfg["id"]] = dict(role=cfg["role"], licence=lic,
                                            release=facts["release"], terms=len(terms_map),
                                            malformed_terms=malformed, url=facts["url"],
                                            sha256=facts["sha256"], requests=facts["requests"],
                                            snapshot=snapshot_path)

    # 3 the association providers — the row IS the source's own claim ---------------------------
    known, known_n = _read_terms(store)
    say(f"STORE    {known_n} phenotype record(s) already in the store before this run")
    rows_by_key: dict = dict(known)          # the store: existing rows + what this run adds
    acc_total = dict(rows_in=0, records=0, rejects=0, used=0)
    for cfg in [c for c in enabled if c["role"] == "association_provider"]:
        cfg = _localise(cfg, fixture_dir)
        adapter = load_adapter(cfg["adapter"])
        adapter.configure(cfg)
        lic = _licence(cfg)
        text, facts = adapter.fetch_payload()
        snapshot_path, _ = _snapshot(raw_dir, cfg["id"],
                                     os.path.basename(facts["url"].split("?")[0]),
                                     text.encode("utf-8", "replace"))
        source_release = facts["release"]
        vocab = set(cfg["evidence_codes"])
        acc = dict(rows_in=0, records=0, rejects=0, used=0, out_of_scope=0, malformed=0,
                   file_rows=0, reject_reasons={})
        new_keys: list[tuple] = []
        for raw in _pheno_files.parse_association(text):
            acc["file_rows"] += 1
            if raw.get("malformed"):
                acc["malformed"] += 1
                continue
            if raw["gene_id"] not in scope_ids:
                acc["out_of_scope"] += 1
                continue
            acc["rows_in"] += 1
            g = by_mgi.get(raw["gene_id"])
            try:
                term_id = pheno.check_term_id(raw["phenotype_term_id"], raw["gene_id"],
                                              raw["allele_marker_id"] or "(no allele id)")
                if not raw["allele_marker_id"].strip():
                    raise gene_contract.Refusal(
                        f"gene {raw['gene_id']}: association without an allele/marker id — "
                        f"refused by name, counted as a reject; a row that cannot be "
                        f"re-derived may not be stored")
                if term_id not in terms_map:
                    raise gene_contract.Refusal(
                        f"gene {raw['gene_id']} ({raw['allele_marker_id']}): phenotype term id "
                        f"{term_id!r} is not published by the term authority "
                        f"({[c['id'] for c in enabled if c['role'] == 'term_authority'][0]}) — "
                        f"refused by name, counted as a reject; the ontology is the only "
                        f"authority for a term")
                if raw["evidence_code"] not in vocab:
                    raise gene_contract.Refusal(
                        f"gene {raw['gene_id']} ({raw['allele_marker_id']}, {term_id}): evidence "
                        f"code {raw['evidence_code']!r} is outside source "
                        f"{cfg['id']!r}'s declared vocabulary {sorted(vocab)} — refused by name, "
                        f"counted as a reject; the arm refuses to invent or borrow a code")
            except gene_contract.Refusal as e:
                acc["rejects"] += 1
                reason = e.__class__.__name__ if not str(e) else str(e).split(" — ")[0]
                label = ("no-ontology-id" if "without an ontology term id" in str(e) else
                         "unknown-term" if "not published by the term authority" in str(e) else
                         "evidence-outside-vocabulary" if "declared vocabulary" in str(e) else
                         "no-allele-id")
                acc["reject_reasons"][label] = acc["reject_reasons"].get(label, 0) + 1
                record.setdefault("rejects_detail", []).append(
                    dict(source=cfg["id"], gene_id=raw["gene_id"], reason=label, detail=str(e)))
                continue
            key = tuple(raw.get(n, "") for n in PAYLOAD_FIELDS) + (cfg["id"],)
            if key in rows_by_key:
                acc["used"] += 1
                continue
            row = dict(raw)
            row.update(dict(gene_id=raw["gene_id"], symbol=g["symbol"] if g else "",
                            ensembl_gene_id=g["ensembl_gene_id"] if g else "",
                            phenotype_term=terms_map[term_id], source=cfg["id"],
                            source_release=source_release, fetched_at=t0.isoformat(),
                            first_seen_run=run_id, _key=key))
            rows_by_key[key] = row
            new_keys.append(key)
            acc["records"] += 1
        # every row must be named: file_rows = rows_in + out_of_scope + malformed (SCHEMA.md §5)
        if acc["file_rows"] != acc["rows_in"] + acc["out_of_scope"] + acc["malformed"]:
            raise gene_contract.ConfigError(f"{cfg['id']}: file accounting does not close")
        # the store's books must close: rows_in = records + rejects + used (SCHEMA.md §5)
        if acc["rows_in"] != acc["records"] + acc["rejects"] + acc["used"]:
            raise gene_contract.ConfigError(
                f"{cfg['id']}: accounting does not close — rows_in={acc['rows_in']} != "
                f"records+rejects+used={acc['records'] + acc['rejects'] + acc['used']}")
        for k in acc_total:
            acc_total[k] += acc[k]
        record["sources"][cfg["id"]] = dict(
            role=cfg["role"], licence=lic, release=source_release, url=facts["url"],
            sha256=facts["sha256"], bytes=facts["bytes"], snapshot=snapshot_path,
            requests=facts["requests"], evidence_codes=sorted(vocab), accounting=acc)
        requests_total += facts["requests"]
        say(f"FETCH    {cfg['id']}  {facts['bytes']} bytes, sha256={facts['sha256'][:16]}…, "
            f"release \"{source_release}\" -> {snapshot_path}")
        say(f"ACCOUNT  {cfg['id']}  rows_in={acc['rows_in']} = records {acc['records']} + rejects "
            f"{acc['rejects']} {json.dumps(acc['reject_reasons'], sort_keys=True)} + used "
            f"{acc['used']}; file_rows={acc['file_rows']} = rows_in + out_of_scope "
            f"{acc['out_of_scope']} + malformed {acc['malformed']}")
        # the verify leg: MGI publishes no count endpoint (the gene arm measured this), so the
        # release file's own row count IS the source's count — our parse must account for every row
        file_own = sum(1 for ln in text.splitlines() if ln.strip())
        ok = acc["file_rows"] == file_own
        record["verify"].append(dict(source=cfg["id"], ok=ok, ours=acc["file_rows"],
                                     theirs=file_own, url=facts["url"],
                                     detail=(f"ours={acc['file_rows']} rows parsed of the file's own "
                                             f"row count {file_own} (MGI publishes no count endpoint; "
                                             f"the release file's own row count IS the source's "
                                             f"count — the gene arm's measured rule)")))
    if acc_total["rows_in"] != acc_total["records"] + acc_total["rejects"] + acc_total["used"]:
        raise gene_contract.ConfigError("total accounting does not close")
    record["accounting"] = dict(acc_total, closes=True)
    record["records_total"] = len(rows_by_key)

    # 4 the conflict rule — same (gene, term) from two sources, differing payloads: BOTH kept,
    #   counted, logged; identical payload sets: corroborated, still both kept (SCHEMA.md §0)
    pairs: dict[tuple, dict[str, set]] = {}
    for row in rows_by_key.values():
        pair = (row["gene_id"], row["phenotype_term_id"])
        pairs.setdefault(pair, {}).setdefault(row["source"], set()).add(pheno.payload_of(row))
    conflicts: list[dict] = []
    corroborated = 0
    for pair, by_src in sorted(pairs.items()):
        if len(by_src) < 2:
            continue
        srcs = sorted(by_src)
        for i in range(len(srcs)):
            for j in range(i + 1, len(srcs)):
                a, b = srcs[i], srcs[j]
                if by_src[a] == by_src[b]:
                    corroborated += 1
                    continue
                only_a = sorted(by_src[a] - by_src[b])
                only_b = sorted(by_src[b] - by_src[a])
                conflicts.append(dict(
                    key=f"{pair[0]}|{pair[1]}", reason="phenotype_disagreement",
                    source_a=a, source_b=b,
                    detail=f"payload(s) only {a} has: {only_a}; payload(s) only {b} has: {only_b} "
                           f"— both rows are kept; the conflict is counted, never averaged, "
                           f"never dropped"))
    record["conflicts"] = dict(count=len(conflicts), pairs=conflicts)
    record["corroborated"] = corroborated
    say(f"CONFLICT {len(conflicts)} disagreement(s) counted "
        f"({', '.join(c['key'] for c in conflicts) if conflicts else 'none'}); "
        f"{corroborated} corroborated cross-source pair(s) — every row kept in both cases")

    # 5 publish — records, the door view, the conflict log, state, the run record -------------
    all_rows = list(rows_by_key.values())
    ppath = os.path.join(store, "phenotypes", "phenotypes",
                         f"{pheno.PHENOTYPE_VIEW}.v{pheno.PHENOTYPE_VERSION}.tsv")
    ppath, psha, pn = _write_table(ppath, pheno.PHENOTYPE_COLUMNS, all_rows,
                                   lambda r: tuple(r.get(n, "") for n in
                                                   ("gene_id", "phenotype_term_id", "source",
                                                    "allele_marker_id", "genotype_id",
                                                    "evidence_code", "citation", "statement")))
    _write_schema(os.path.join(store, "phenotypes", "phenotypes",
                               f"{pheno.PHENOTYPE_VIEW}.v{pheno.PHENOTYPE_VERSION}.schema.json"),
                  pheno.PHENOTYPE_SCHEMA)
    say(f"PUBLISH  phenotypes/phenotypes/phenotype.v{pheno.PHENOTYPE_VERSION}.tsv  rows={pn}  "
        f"sha256={psha}  new this run={acc_total['records']}")

    vdir = os.path.join(store, "phenotypes", "view")
    view_rows = all_rows
    vpath = os.path.join(vdir, f"{pheno.GENE_PHENOTYPE_VIEW}.v{pheno.GENE_PHENOTYPE_VERSION}.tsv")
    vpath, vsha, vn = _write_table(vpath, pheno.GENE_PHENOTYPE_COLUMNS, view_rows,
                                   lambda r: tuple(r.get(n, "") for n in
                                                   ("symbol", "phenotype_term_id", "source",
                                                    "allele_marker_id", "genotype_id",
                                                    "evidence_code", "citation")))
    _write_schema(os.path.join(vdir, f"{pheno.GENE_PHENOTYPE_VIEW}."
                                     f"v{pheno.GENE_PHENOTYPE_VERSION}.schema.json"),
                  pheno.GENE_PHENOTYPE_SCHEMA)
    jdump(dict(view=f"{pheno.GENE_PHENOTYPE_VIEW}.v{pheno.GENE_PHENOTYPE_VERSION}", rows=vn,
               run_id=run_id, generated_at=utc_now().isoformat(),
               keyed_by=dict(table=os.path.join(store, XREF_TABLE), sha256=xref_sha,
                             ok_keys=xref_rows, scope={s: resolved[s] for s in scope}),
               records=dict(path=ppath, sha256=psha, rows=pn),
               terms_release=record["sources"][[c["id"] for c in enabled
                                                if c["role"] == "term_authority"][0]]["release"],
               conflict_rule="two sources disagreeing on (gene_id, phenotype_term_id): both rows "
                             "kept, conflict counted in conflicts.tsv — never averaged, never "
                             "dropped (SCHEMA.md §0)"),
         os.path.join(vdir, f"{pheno.GENE_PHENOTYPE_VIEW}."
                            f"v{pheno.GENE_PHENOTYPE_VERSION}.provenance.json"))
    say(f"PUBLISH  phenotypes/view/gene_phenotype.v{pheno.GENE_PHENOTYPE_VERSION}.tsv  rows={vn}  "
        f"sha256={vsha}")

    cpath = os.path.join(store, "phenotypes", "conflicts.tsv")
    names = [c[0] for c in pheno.CONFLICT_COLUMNS]
    lines = ["\t".join(names)] + ["\t".join(_cell(c.get(n, "")) for n in names) for c in conflicts]
    cb = ("\n".join(lines) + "\n").encode()
    with open(cpath, "wb") as fh:
        fh.write(cb)
    say(f"PUBLISH  phenotypes/conflicts.tsv  rows={len(conflicts)}  sha256={sha256(cb)}")

    state = dict(run_id=run_id, updated_at=utc_now().isoformat(), scope=scope,
                 records=pn, conflicts=len(conflicts), sources={sid: dict(
                     role=s.get("role"), release=s.get("release")) for sid, s in
                     record["sources"].items()})
    spath = jdump(state, os.path.join(store, "phenotypes", "state", "phenotypes.json"))
    say(f"STATE    {os.path.relpath(spath, store)}")
    record["status"] = "PUBLISHED"
    record["publish"] = dict(records=dict(path=ppath, sha256=psha, rows=pn),
                             view=dict(path=vpath, sha256=vsha, rows=vn),
                             conflicts=dict(path=cpath, sha256=sha256(cb), rows=len(conflicts)),
                             state=spath)
    record["cost"] = dict(requests=requests_total, wall_s=round((utc_now() - t0).total_seconds(), 1))
    say(f"COST     requests={requests_total}  wall={record['cost']['wall_s']}s")
    rpath = jdump(record, os.path.join(store, "phenotypes", "runs", f"{run_id}.json"))
    say(f"RUN-RECORD {os.path.relpath(rpath, store)}")
    record["exit"] = 0 if all(v["ok"] for v in record["verify"]) else 1
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the phenotypes arm (associations keyed by the xref table)")
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
    except gene_contract.Refusal as e:
        say(f"REFUSED[{getattr(e, 'code', type(e).__name__)}] {type(e).__name__}: {e}")
        return 2
    except RuntimeError as e:
        say(f"REFUSED[error] {type(e).__name__}: {e}")
        return 2
    return rec["exit"]


if __name__ == "__main__":
    sys.exit(main())
