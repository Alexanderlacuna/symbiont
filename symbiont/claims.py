"""claims — THE LEDGER OF CLAIMS MINTED AGAINST A RELEASE, and the door that answers them.

(req-P1-RETENTION-AND-SUPERSEDE-what-current)

SUPERSEDE semantics (the row's own words): a claim records the release it was minted against;
when a newer release lands, the claim is marked superseded_by=<new release> if the underlying
value changed, and left untouched if it did not — with the diff shown. A claim is a row in
store/claims/claims.tsv:

    claim_id  question  accession  field  value  release  view_sha256  status  superseded_by  new_value

  · claim_id is content-derived (sha256 of release|accession|field|value) — no clock, so the same
    claim minted twice is the same row, and mint refuses a duplicate BY NAME rather than forking it.
  · release is the RELEASE SET the manifest itself spells (e.g. ensembl-116|mgi-2026-11-09), read
    from the view provenance at mint time. Minting FREEZES that release's view bundle
    (retention.snapshot into releases/<relset>/) — that freeze is the mechanism that keeps the
    claim answerable as-of after the next release lands, because the live view/ files are
    overwritten by every publish.
  · supersede re-reads the CURRENT view for every open claim minted at an older release set:
    changed  -> status=superseded, superseded_by=<current>, new_value recorded, the diff printed
                with BOTH values; unchanged -> the row is untouched and the door says so.

THE DOOR answers two spellings of the question and never confuses them:
  · 'current'          answer from the live view, and SAY which release that is.
  · 'as of release R'  answer from the frozen bundle releases/<R>/ — and if R is not held
                       (never landed here, or dropped) REFUSED[release-not-held] naming R, the
                       held sets and the current one. Returning current for an old question
                       silently is the exact lie this row exists to kill.

Run:
    python3 -m symbiont.claims mint     --store symbiont/store --accession MGI:98834 \
                                       --field start --question "start position of Trp53"
    python3 -m symbiont.claims supersede --store symbiont/store
    python3 -m symbiont.claims answer   --store symbiont/store --id <claim_id> [--as-of <relset>]
    python3 -m symbiont.claims answer   --store symbiont/store --accession MGI:98834 \
                                       --field start --as-of ensembl-116|mgi-2026-11-09
    python3 -m symbiont.claims list     --store symbiont/store
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

from .loop import say
from .retention import (Refusal, held_release_sets, locate_view, read_json,
                        release_set, sha256_file, snapshot)

LEDGER = "claims.tsv"
COLUMNS = ["claim_id", "question", "accession", "field", "value", "release",
           "view_sha256", "status", "superseded_by", "new_value"]
ACCESSION_COLUMNS = ("symbol", "mgi_id", "ensembl_gene_id")


def ledger_path(store: str) -> str:
    return os.path.join(store, "claims", LEDGER)


def read_ledger(store: str) -> list[dict]:
    path = ledger_path(store)
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        if header != COLUMNS:
            raise Refusal("bad-input", f"{path} has columns {header}, expected {COLUMNS} — "
                                     f"the ledger is not this module's ledger")
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            f = line.split("\t")
            rows.append({k: (f[i] if i < len(f) else "") for i, k in enumerate(COLUMNS)})
    return rows


def write_ledger(store: str, rows: list[dict]) -> None:
    """Rewrite the ledger atomically (supersede MARKS a claim; the mark is the audit, and the
    marked row still carries its original value, release and view sha256)."""
    path = ledger_path(store)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\t".join(COLUMNS) + "\n")
        for r in rows:
            fh.write("\t".join(str(r.get(k, "")) for k in COLUMNS) + "\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- the view, current or frozen
def view_files(store: str, as_of: str | None) -> tuple[str, str, str, str]:
    """(view tsv path, relset, view sha256, which) — which is 'current' or 'frozen bundle'."""
    view_name, version, view_path = locate_view(store)
    current_relset, _ = release_set(store)
    if as_of is None:
        prov = read_json(os.path.join(store, "view", f"{view_name}.v{version}.provenance.json"),
                         "the view provenance")
        return view_path, current_relset, str(prov.get("view_sha256") or sha256_file(view_path)), "current"
    if as_of == current_relset:
        prov = read_json(os.path.join(store, "view", f"{view_name}.v{version}.provenance.json"),
                         "the view provenance")
        return view_path, current_relset, str(prov.get("view_sha256") or sha256_file(view_path)), \
            f"current (as-of {as_of} IS the current release — the door says so rather than " \
            f"pretending it looked elsewhere)"
    if as_of not in held_release_sets(store):
        raise Refusal("release-not-held",
                      f"as-of {as_of!r} is not held: held={held_release_sets(store) or 'none'}, "
                      f"current={current_relset} — the door REFUSES rather than silently answer "
                      f"the current release for an old question")
    bundle = os.path.join(store, "releases", as_of)
    rel = read_json(os.path.join(bundle, "RELEASE.json"), f"the frozen bundle for {as_of}")
    vfile = os.path.join(bundle, str((rel.get("view") or {}).get("file") or
                                     f"{view_name}.v{version}.tsv"))
    if not os.path.isfile(vfile):
        raise Refusal("bad-input", f"the frozen bundle for {as_of} names no view file: {vfile}")
    actual = sha256_file(vfile)
    stated = str((rel.get("view") or {}).get("sha256") or "")
    if stated and actual != stated:
        raise Refusal("bad-input", f"the frozen bundle for {as_of} is corrupt: RELEASE.json states "
                                   f"sha256 {stated[:12]}… but the bytes hash {actual[:12]}…")
    return vfile, as_of, actual, f"frozen bundle releases/{as_of}/"


def view_index(path: str) -> tuple[list[str], dict[str, dict]]:
    """The view TSV indexed by every accession column (symbol · mgi_id · ensembl_gene_id)."""
    with open(path, encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        idx: dict[str, dict] = {}
        for line in fh:
            f = line.rstrip("\n").split("\t")
            row = {c: (f[i] if i < len(f) else "") for i, c in enumerate(header)}
            for c in ACCESSION_COLUMNS:
                if c in header and row.get(c):
                    idx.setdefault(row[c], row)
        return header, idx


def lookup(store: str, accession: str, field: str, as_of: str | None) -> dict:
    vpath, relset, vsha, which = view_files(store, as_of)
    header, idx = view_index(vpath)
    if field not in header:
        raise Refusal("bad-input", f"field {field!r} is not a view column — columns are {header}")
    row = idx.get(accession)
    if row is None:
        where = "the current view" if (as_of is None or as_of == relset) else f"the frozen view {relset!r}"
        cols = ", ".join(c for c in ACCESSION_COLUMNS if c in header)
        raise Refusal("accession-unknown",
                      f"{accession!r} is not in {where} (indexed by {cols})")
    return dict(value=row.get(field, ""), release=relset, view_sha256=vsha, which=which,
                symbol=row.get("symbol", ""))


# ---------------------------------------------------------------- mint / supersede / answer
def mint(store: str, accession: str, field: str, question: str) -> int:
    got = lookup(store, accession, field, None)
    relset = got["release"]
    snapshot(store)                      # freeze THIS release — it is what the claim was minted against
    claim_id = hashlib.sha256(
        f"{relset}\t{accession}\t{field}\t{got['value']}".encode()).hexdigest()[:12]
    rows = read_ledger(store)
    for r in rows:
        if r["claim_id"] == claim_id:
            say(f"MINT     {claim_id} already minted against {relset} — untouched "
                f"(accession={accession} field={field} value={got['value']!r})")
            return 0
    write_ledger(store, rows + [dict(claim_id=claim_id, question=question, accession=accession,
                                     field=field, value=got["value"], release=relset,
                                     view_sha256=got["view_sha256"], status="open",
                                     superseded_by="", new_value="")])
    say(f"MINT     {claim_id}  {accession}.{field}={got['value']!r}  against release {relset}  "
        f"view sha256={got['view_sha256'][:16]}…")
    say(f"         the release is FROZEN (releases/{relset}/), so this claim stays answerable "
        f"as-of after the next release lands")
    return 0


def supersede(store: str) -> int:
    rows = read_ledger(store)
    if not rows:
        say("SUPERSEDE the ledger is empty — nothing to mark")
        return 0
    current_relset, _ = release_set(store)
    open_rows = [r for r in rows if r["status"] == "open"]
    if not open_rows:
        say(f"SUPERSEDE current release {current_relset}; no open claims — nothing to mark")
        return 0
    changed = 0
    for r in open_rows:
        if r["release"] == current_relset:
            say(f"CURRENT  {r['claim_id']}  {r['accession']}.{r['field']} was minted at "
                f"{current_relset}, which is STILL current — untouched")
            continue
        try:
            now = lookup(store, r["accession"], r["field"], None)
        except Refusal as e:
            say(f"UNCHANGED {r['claim_id']}  {r['accession']}.{r['field']} — the current view "
                f"no longer publishes the accession ({e.reason}); left untouched")
            continue
        if now["value"] == r["value"]:
            say(f"UNCHANGED {r['claim_id']}  {r['accession']}.{r['field']}={r['value']!r} holds "
                f"at {current_relset} too — untouched")
            continue
        r["status"] = "superseded"
        r["superseded_by"] = current_relset
        r["new_value"] = now["value"]
        changed += 1
        say(f"SUPERSEDED {r['claim_id']}  {r['accession']}.{r['field']}: "
            f"{r['value']!r} -> {now['value']!r}  "
            f"(release {r['release']} -> superseded_by={current_relset})")
    if changed:
        write_ledger(store, rows)
        say(f"SUPERSEDE marked {changed} claim(s) superseded_by={current_relset}; "
            f"the ledger rows keep BOTH values")
    else:
        say(f"SUPERSEDE current release {current_relset}; every open claim still holds — "
            f"no row touched")
    return 0


def answer(store: str, claim_id: str | None, accession: str | None, field: str | None,
           as_of: str | None) -> int:
    question, minted = "", None
    if claim_id:
        rows = [r for r in read_ledger(store) if r["claim_id"] == claim_id]
        if not rows:
            raise Refusal("bad-input", f"no claim {claim_id!r} in {ledger_path(store)}")
        minted = rows[0]
        accession, field = minted["accession"], minted["field"]
        question = minted["question"]
    if not accession or not field:
        raise Refusal("bad-input", "answer needs --id <claim_id> or both --accession and --field")
    got = lookup(store, accession, field, as_of)
    say(f"QUESTION {question or f'{accession}.{field}'}"
        + (f"  (claim {claim_id})" if claim_id else ""))
    say(f"ANSWER   {accession}.{field}={got['value']!r}  as of release {got['release']}  "
        f"[{got['which']}]  view sha256={got['view_sha256'][:16]}…")
    if minted is not None:
        say(f"MINTED-AT release {minted['release']}  value={minted['value']!r}  "
            f"status={minted['status']}")
        if minted["status"] == "superseded":
            say(f"SUPERSEDED-By {minted['superseded_by']}  {minted['field']}: "
                f"{minted['value']!r} -> {minted['new_value']!r}  (both values on the ledger row)")
        if as_of and as_of != minted["release"] and got["release"] != minted["release"]:
            say(f"DIFF     minted at {minted['release']}: {minted['value']!r}  ·  "
                f"as of {got['release']}: {got['value']!r}  — current and as-of differ, and the "
                f"difference is shown, not averaged away")
    return 0


def list_claims(store: str) -> int:
    rows = read_ledger(store)
    if not rows:
        say(f"CLAIMS   {ledger_path(store)} — no claims minted yet")
        return 0
    say("\t".join(COLUMNS))
    for r in rows:
        say("\t".join(str(r.get(k, "")) for k in COLUMNS))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="symbiont.claims",
                                 description="the claim ledger and the as-of/current door")
    sub = ap.add_subparsers(dest="cmd", required=True)
    store_default = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "store")
    for name, help_line in (("mint", "record a claim against the CURRENT release (and freeze it)"),
                            ("supersede", "mark changed claims superseded_by=<new release>; "
                                          "unchanged claims untouched"),
                            ("answer", "answer a claim or an ad-hoc question, current or --as-of"),
                            ("list", "the ledger")):
        p = sub.add_parser(name, help=help_line)
        p.add_argument("--store", default=store_default)
        if name == "mint":
            p.add_argument("--accession", required=True)
            p.add_argument("--field", required=True)
            p.add_argument("--question", default="", help="what the claim claims, in words")
        elif name == "answer":
            p.add_argument("--id", dest="claim_id", default=None)
            p.add_argument("--accession", default=None)
            p.add_argument("--field", default=None)
            p.add_argument("--as-of", dest="as_of", default=None, metavar="RELSET",
                            help="answer as of release R (e.g. ensembl-116|mgi-2026-11-09); "
                                 "refuses by name when R is not held")
    a = ap.parse_args(argv if argv is not None else sys.argv[1:])
    store = os.path.abspath(a.store)
    try:
        if a.cmd == "mint":
            return mint(store, a.accession, a.field, a.question)
        if a.cmd == "supersede":
            return supersede(store)
        if a.cmd == "answer":
            return answer(store, a.claim_id, a.accession, a.field, a.as_of)
        return list_claims(store)
    except Refusal as e:
        say(str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
