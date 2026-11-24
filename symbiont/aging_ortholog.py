"""aging_ortholog — THE MOUSE→HUMAN JOIN of the aging arm (req-P1-E1-c).

WHAT THIS IS. Two published dataset views already exist in the store and neither is a join:

    view/genage_models.v1.tsv   GenAge's model-organism aging genes (HAGR, CC-BY 3.0)
    view/ortholog.v1.tsv        MGI's HOM_MouseHumanSequence (CC-BY 4.0), one row per
                                (homology class, organism) — the class and both ids in it

This module produces the FIRST view in the aging arm that is a FINDING rather than a corpus:
one row per (mouse aging gene, human ortholog), with the releases of BOTH inputs on every row,
a declared transform, and an honest partition of the input rows into
joined / one-to-many / no-ortholog. Nothing is dropped silently: every input row appears either
in the view or in the absent file, and the three counts sum to the input count (asserted, not
asserted-by-comment — see self_check()).

THE JOIN KEY IS THE ID, NOT THE NAME (J1). Entrez Gene ids are globally unique per gene, so a
yeast symbol that happens to equal a mouse symbol cannot join through the id; a symbol join
would. The symbol is still carried and cross-checked (J2), because it is what a reader reads and
because a silent symbol disagreement is a finding: GenAge's `Arntl` is MGI's `Bmal1`.

THE FOUR FACTS THIS VIEW REFUSES TO FLATTEN (see REASONS):
    not_mouse                    the input row is not a Mus musculus gene (the source says so)
    empty_entrez                 the input row carries no Entrez id — there is no key to join on
    id_is_human_not_mouse        the row says Mus musculus but its Entrez id is published by the
                                 ortholog table ONLY as a human row: joining it would produce a
                                 human→human pair wearing a cross-species label
    mouse_absent_from_mgi        a Mus musculus gene the ortholog table publishes no mouse row for
    mouse_row_has_no_human_row   a mouse class with no human row in it (the source states
                                 relationship "mouse-only")

LITERATURE IS ATTACHED FROM THE STORE, BOUNDED (L1-L3). The per-gene paper list comes from the
literature arm's own published join view (gene_publication.v2), which is bounded by that arm's
per_gene_cap. This module never searches, never fetches, never invents a PMID. A gene with no row
in that view is published with literature_count=0 and the axis says so — which means "the store
holds no paper for this gene", NEVER "no literature exists".

Run:
    python3 -m symbiont.aging_ortholog run      # build, verify arithmetic, publish view+sidecars
    python3 -m symbiont.aging_ortholog verify   # re-read what is on disk and re-check the arithmetic
    python3 -m symbiont.aging_ortholog report    # the same build, printed and NOT published
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import hashlib
import io
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORE = os.path.join(HERE, "store")

VIEW = "aging_ortholog"
VERSION = 1
VIEW_TABLE = f"{VIEW}.v{VERSION}.tsv"
ABSENT_TABLE = f"{VIEW}.v{VERSION}.absent.tsv"
SCHEMA_FILE = f"{VIEW}.v{VERSION}.schema.json"
PROV_FILE = f"{VIEW}.v{VERSION}.provenance.json"

GENAGE_VIEW = "genage_models.v1.tsv"
ORTHOLOG_VIEW = "ortholog.v1.tsv"
LITERATURE_VIEW = os.path.join("literature", "view", "gene_publication.v2.tsv")
LITERATURE_SOURCES = os.path.join(HERE, "literature", "sources.yml")

# The five stated reasons a Mus-musculus-or-not row can fail to produce a human ortholog.
# A reason that fires zero times is still counted (a check that cannot be seen firing is not a
# check) — `coverage.none_by_reason` carries all five keys on every run.
REASONS = (
    "not_mouse",
    "empty_entrez",
    "id_is_human_not_mouse",
    "mouse_absent_from_mgi",
    "mouse_row_has_no_human_row",
)

COLUMNS = [
    ("genage_id", "string", "GenAge's own id for the input row (the row's identity in this join)"),
    ("genage_key", "string", "GenAge's symbol for the input row, as the source publishes it"),
    ("genage_organism", "string", "the organism the GenAge RELEASE FILE states for this row "
                                  "(read from the release, never inferred from the symbol)"),
    ("genage_entrez_gene_id", "string", "GenAge's Entrez id — THE JOIN KEY (J1)"),
    ("ortholog_class", "string", "MGI's DB Class Key: the homology class the source states"),
    ("relationship", "string", "the relationship the source states for the class "
                               "(mouse-human / mouse-only / human-only)"),
    ("class_rows", "integer", "rows the source publishes in this homology class"),
    ("human_ids_in_row", "integer", "how many human ids the source's own cell in this class states "
                                   "(1 normally; >1 is the one-to-many ortholog, J5)"),
    ("mouse_symbol", "string", "the MGI mouse symbol in the class (J2 cross-check against genage_key)"),
    ("mouse_mgi_id", "string", "the MGI accession of the mouse gene"),
    ("human_symbol", "string", "the human symbol as the source states it in the class row"),
    ("human_hgnc_id", "string", "the human HGNC id (HGNC:####) — one row per id, so a "
                                "one-to-many gene repeats its mouse side once per human id"),
    ("genage_release", "string", "the release id of input 1, on every row (the release, not a date)"),
    ("ortholog_release", "string", "the release id of input 2, on every row"),
    ("literature_pmids", "string", "comma-joined PMIDs the literature arm's published view holds "
                                   "for this gene, sorted by PMID and in the same order as "
                                   "literature_evidence_levels; bounded by that arm's per_gene_cap; "
                                   "'' means the store holds none"),
    ("literature_count", "integer", "how many PMIDs the row carries (0..per_gene_cap)"),
    ("literature_evidence_levels", "string", "per-PMID evidence level, aligned with literature_pmids"),
]

ABSENT_COLUMNS = [
    ("genage_id", "string", "GenAge's own id for the input row"),
    ("genage_key", "string", "GenAge's symbol"),
    ("genage_organism", "string", "the organism the GenAge release file states"),
    ("genage_entrez_gene_id", "string", "GenAge's Entrez id ('' when the source gives none)"),
    ("reason", "string", "ONE of: " + " · ".join(REASONS)),
    ("detail", "string", "what the two releases say, in one sentence, for this row's reason"),
    ("genage_release", "string", "release id of input 1"),
    ("ortholog_release", "string", "release id of input 2"),
]

RESOLUTION = [
    "J1 one output row per (GenAge row, human HGNC id): the join key is the Entrez id, never the "
    "symbol — an id is one gene, a name is a string that two organisms can share",
    "J2 the symbol is carried and cross-checked, and a disagreement is visible (genage_key vs "
    "mouse_symbol) rather than quietly resolved in favour of one side",
    "J3 an input row with 1 human id in its class(es) is `joined`; with >1 it is `one_to_many` and "
    "writes one row per human id; with 0 it is `none` and is WRITTEN TO THE ABSENT FILE with a reason",
    "J4 the three counts partition the input rows and are asserted to sum to the input count",
    "L1 a PMID is attached only from the literature arm's published view (bounded by its own "
    "per_gene_cap); this transform never searches and never invents an accession",
    "L2 literature_count=0 says the store holds no paper for that gene — it is NOT a claim that no "
    "literature exists",
]


# ─────────────────────────────────────────────────────────────── small helpers (one definition each)
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_tsv(path: str) -> tuple[list[str], list[dict]]:
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        rows = list(rd)
        return list(rd.fieldnames or []), rows


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def render(columns: list[tuple[str, str, str]], rows: list[dict]) -> bytes:
    """Deterministic bytes: fixed column order, cell flattened, sorted rows, LF, one trailing newline."""
    names = [c[0] for c in columns]
    out = ["\t".join(names)]
    for r in rows:
        out.append("\t".join(_cell(r.get(n, "")) for n in names))
    return ("\n".join(out) + "\n").encode("utf-8")


def _cell(v) -> str:
    return ("" if v is None else str(v)).replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def schema_document() -> dict:
    """Data-free by construction (no rows, no releases, no clock), so its hash moves only when the
    SHAPE moves and every consumer may re-read on a hash change."""
    return {
        "view": VIEW,
        "version": VERSION,
        "key": ["genage_id", "human_hgnc_id"],
        "description": (
            "the mouse→human join of the aging arm: one row per (GenAge aging-gene row, human HGNC "
            "id), carrying the MGI mouse accession, the homology class the source states, the "
            "releases of BOTH inputs on every row, and the bounded per-gene paper list the "
            "literature arm publishes. Input rows with no human ortholog are NOT in this table: "
            f"they are in {ABSENT_TABLE} with the reason, one row each, counted."),
        "columns": [{"name": n, "type": t, "note": note} for n, t, note in COLUMNS],
        "absent_table": {
            "path": ABSENT_TABLE,
            "columns": [{"name": n, "type": t, "note": note} for n, t, note in ABSENT_COLUMNS],
            "why": ("a gene with no ortholog is a FINDING, not a missing row: the row's own job is "
                    "to report it as absent with its count, so it is a table beside this one"),
        },
        "resolution": RESOLUTION,
    }


# ─────────────────────────────────────────────────────────────── the three inputs, read as bytes
def _release_file_organisms(snapshot_path: str, snapshot_sha256: str) -> dict:
    """{GenAge ID: organism} from the RELEASE FILE the genage view's provenance names, verified
    against the sha256 that provenance recorded. The view itself carries no organism column; the
    organism is read from the source's own bytes rather than inferred from a symbol, and the read
    is refused if the bytes are not the bytes the sidecar names."""
    got = sha256_file(snapshot_path)
    if got != snapshot_sha256:
        raise SystemExit(f"REFUSED[input-moved] {snapshot_path}: sha256 {got[:16]}… is not the "
                         f"{snapshot_sha256[:16]}… the view's provenance recorded — the release "
                         f"file under this path is not the one this view was built from")
    with zipfile.ZipFile(snapshot_path) as z:
        members = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(members) != 1:
            raise SystemExit(f"REFUSED[release-shape] {snapshot_path} holds {len(members)} csv "
                             f"member(s) {members} — the adapter's own rule is exactly one "
                             f"(*.csv), and this transform will not guess which one is the list")
        raw = z.read(members[0])
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))
    out = {}
    for r in rows:
        out[_cell(r.get("GenAge ID"))] = _cell(r.get("organism"))
    return out


def _literature(store: str) -> dict:
    """The per-gene paper list the literature arm already published, bounded by ITS per_gene_cap.
    Absent view = no papers attached, and the sidecar says which file was missing (never a crash
    that looks like 'no literature exists')."""
    path = os.path.join(store, LITERATURE_VIEW)
    cap = None
    if os.path.isfile(LITERATURE_SOURCES):
        try:
            import yaml
            cfg = yaml.safe_load(open(LITERATURE_SOURCES, encoding="utf-8")) or {}
            for s in (cfg.get("sources") or []):
                if s.get("id") == "pubmed":
                    cap = s.get("per_gene_cap")
        except Exception:
            cap = None
    out = dict(path=path, present=os.path.isfile(path), sha256="", rows=0, per_gene_cap=cap,
               scope=None, note="")
    if not out["present"]:
        out["note"] = (f"{path} does not exist: no gene in this join can name a paper, and that is "
                       f"the store's state, not a fact about the literature")
        return out
    _, rows = read_tsv(path)
    by_mgi = collections.defaultdict(list)
    for r in rows:
        by_mgi[_cell(r.get("mgi_id"))].append(r)
    out.update(sha256=sha256_file(path), rows=len(rows), by_mgi=by_mgi)
    if cap is not None:
        over = [m for m, rs in by_mgi.items() if len(rs) > int(cap)]
        out["note"] = (f"bounded by the literature arm's per_gene_cap={cap}; {len(over)} gene(s) "
                       f"exceed it in the published view (a violation, not a warning)")
    return out


# ─────────────────────────────────────────────────────────────── THE TRANSFORM
def build(store: str = DEFAULT_STORE) -> dict:
    vdir = os.path.join(store, "view")
    genage_path = os.path.join(vdir, GENAGE_VIEW)
    ortholog_path = os.path.join(vdir, ORTHOLOG_VIEW)
    for p in (genage_path, ortholog_path):
        if not os.path.isfile(p):
            raise SystemExit(f"REFUSED[missing-input] {p} — this join has two inputs and will not "
                             f"run on one; the view that is missing is the one to publish first")

    gprov = load_json(os.path.join(vdir, "genage_models.v1.provenance.json"))
    oprov = load_json(os.path.join(vdir, "ortholog.v1.provenance.json"))
    gsrc = (gprov.get("sources") or {}).get("genage_models") or {}
    osrc = (oprov.get("sources") or {}).get("mgi_ortholog") or {}
    genage_release = gsrc.get("release") or ""
    ortholog_release = osrc.get("release") or ""
    if not genage_release or not ortholog_release:
        raise SystemExit("REFUSED[release-unknown] a provenance sidecar names no release for one of "
                         "the two inputs — a row may not carry a release the sidecar cannot state")

    # input 3, declared: the release FILE the genage view came from, for the organism column only
    gsnap = (gsrc.get("snapshots") or {}).get("archive") or {}
    organisms = _release_file_organisms(gsnap.get("path", ""), gsnap.get("sha256", ""))

    _, genage_rows = read_tsv(genage_path)
    _, orth_rows = read_tsv(ortholog_path)

    # the ortholog side, indexed twice: by class (the source's own unit) and by mouse EntreZ id
    classes = collections.defaultdict(lambda: dict(mouse=[], human=[]))
    for r in orth_rows:
        cls = classes[_cell(r.get("key"))]
        org = _cell(r.get("organism"))
        if org.startswith("mouse"):
            cls["mouse"].append(r)
        elif org == "human":
            cls["human"].append(r)
    mouse_by_entrez = collections.defaultdict(set)
    human_entrez = set()
    for ck, c in classes.items():
        for r in c["mouse"]:
            e = _cell(r.get("entrez_gene_id"))
            if e:
                mouse_by_entrez[e].add(ck)
        for r in c["human"]:
            e = _cell(r.get("entrez_gene_id"))
            if e:
                human_entrez.add(e)

    joined, one_to_many, absent = [], [], []
    counts = dict(input_rows=0, joined=0, one_to_many=0, none=0)
    none_by_reason = {k: 0 for k in REASONS}
    one_to_many_detail = []

    def human_ids(class_keys, entrez):
        """Every human id the source STATES for this mouse gene, as (class, hgnc_id, symbol,
        ids_in_that_cell, mouse_row) in a stable order.

        J5 — THE CELL CAN HOLD MORE THAN ONE ID. An MGI human row's `HGNC ID` cell may be a
        comma-joined list (`HGNC:2440,HGNC:2441,…`) positionally aligned with its `Symbol` cell
        (`CSH1,CSH2,…`). One id is one human gene, so each id gets its own output row; reading that
        cell as a single opaque string would have hidden a one-to-many ortholog behind a joined
        label. When the two cells do not align in length the symbol is left empty and counted —
        never guessed by position.

        And the MOUSE row is chosen BY THE ID (`entrez`), not by position: 3 classes publish more
        than one mouse row, and taking mouse[0] there would attach the wrong MGI accession."""
        out, seen = [], set()
        for ck in sorted(class_keys):
            c = classes[ck]
            mrow = next((m for m in c["mouse"]
                         if _cell(m.get("entrez_gene_id")) == entrez), c["mouse"][0])
            for h in c["human"]:
                raw_ids = _cell(h.get("class_human_hgnc_id")) or _cell(h.get("id"))
                ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
                syms = [x.strip() for x in
                        (_cell(h.get("class_human_symbol")) or _cell(h.get("symbol"))).split(",")]
                for i, hid in enumerate(ids):
                    if (ck, hid) in seen:
                        continue
                    seen.add((ck, hid))
                    sym = syms[i] if len(syms) == len(ids) else ""
                    out.append((ck, hid, sym, len(ids), mrow))
        return out

    for r in genage_rows:
        counts["input_rows"] += 1
        gid = _cell(r.get("id"))
        gkey = _cell(r.get("key"))
        entrez = _cell(r.get("entrez_gene_id"))
        organism = organisms.get(gid, "")
        base = dict(genage_id=gid, genage_key=gkey, genage_organism=organism,
                    genage_entrez_gene_id=entrez,
                    genage_release=genage_release, ortholog_release=ortholog_release)
        class_keys = mouse_by_entrez.get(entrez, set()) if entrez else set()
        pairs = human_ids(class_keys, entrez) if class_keys else []

        if not class_keys:
            if not entrez:
                reason = "empty_entrez"
                detail = (f"the release gives no Entrez id for this row, so there is no key to join "
                          f"on (a symbol join would have used {gkey!r} — refused, J1)")
            elif organism == "Mus musculus" and entrez in human_entrez:
                reason = "id_is_human_not_mouse"
                detail = (f"the row says Mus musculus but Entrez {entrez} is published by the "
                          f"ortholog table only as a HUMAN row — joining it would state a "
                          f"cross-species pair that the ids do not support")
            elif organism == "Mus musculus":
                reason = "mouse_absent_from_mgi"
                detail = (f"Mus musculus in the GenAge release, and the ortholog table publishes no "
                          f"mouse row under Entrez {entrez}")
            elif organism:
                reason = "not_mouse"
                detail = f"the GenAge release states organism {organism!r} — not Mus musculus"
            else:
                reason = "not_mouse"
                detail = (f"the GenAge release states no organism for this row; it is not "
                          f"Mus musculus")
            none_by_reason[reason] += 1
            absent.append(dict(base, reason=reason, detail=detail))
            continue

        if not pairs:
            none_by_reason["mouse_row_has_no_human_row"] += 1
            absent.append(dict(base, reason="mouse_row_has_no_human_row",
                               detail=(f"the mouse row is in homology class "
                                       f"{','.join(sorted(class_keys))}, and the source publishes no "
                                       f"human row in it (relationship "
                                       f"{_cell(classes[sorted(class_keys)[0]]['mouse'][0].get('relationship'))!r})")))
            continue

        if len(pairs) == 1:
            counts["joined"] += 1
        else:
            counts["one_to_many"] += 1
            one_to_many_detail.append(dict(genage_id=gid, genage_key=gkey,
                                           human_ids=[h for _, h, _, _, _ in pairs]))
        for cls, hgnc, hsym, ids_in_cell, mrow in pairs:
            joined.append(dict(
                base,
                ortholog_class=cls,
                relationship=_cell(mrow.get("relationship")),
                class_rows=str(len(classes[cls]["mouse"]) + len(classes[cls]["human"])),
                human_ids_in_row=str(ids_in_cell),
                mouse_symbol=_cell(mrow.get("class_mouse_symbol")) or _cell(mrow.get("symbol")),
                mouse_mgi_id=_cell(mrow.get("class_mouse_mgi_id")) or _cell(mrow.get("id")),
                human_symbol=hsym,
                human_hgnc_id=hgnc,
            ))

    counts["none"] = len(absent)
    # J4 — THE PARTITION IS ASSERTED HERE, before a byte is written
    if counts["joined"] + counts["one_to_many"] + counts["none"] != counts["input_rows"]:
        raise SystemExit(f"REFUSED[partition] joined({counts['joined']}) + one_to_many("
                         f"{counts['one_to_many']}) + none({counts['none']}) != input_rows("
                         f"{counts['input_rows']}) — refusing to publish arithmetic that does not add up")
    if sum(none_by_reason.values()) != counts["none"]:
        raise SystemExit(f"REFUSED[partition] the stated reasons sum to {sum(none_by_reason.values())} "
                         f"but {counts['none']} rows have no ortholog — a reasonless absence is a "
                         f"silent drop")

    # every input row is accounted for exactly once (no silent drop, J3)
    seen = {r["genage_id"] for r in joined} | {r["genage_id"] for r in absent}
    input_ids = {_cell(r.get("id")) for r in genage_rows}
    if seen != input_ids:
        raise SystemExit(f"REFUSED[coverage] {len(input_ids - seen)} input row(s) appear in neither "
                         f"the view nor the absent file: {sorted(input_ids - seen)[:5]}")

    # THE LITERATURE AXIS (L1-L3): attached from the store, per gene, bounded by the arm's cap
    lit = _literature(store)
    by_mgi = lit.pop("by_mgi", {})
    cap = lit.get("per_gene_cap")
    with_papers = 0
    for row in joined:
        recs = by_mgi.get(row["mouse_mgi_id"], [])
        pairs = sorted(((_cell(rec.get("pmid")), _cell(rec.get("evidence_level"))) for rec in recs),
                       key=lambda p: (p[0]))          # order by PMID: a stable list, not file order
        pairs = [p for p in pairs if p[0]]              # a row with no PMID is not a paper
        if cap is not None:
            pairs = pairs[:int(cap)]                     # the cap, by config not by accident
        pmids = [p for p, _ in pairs]
        levels = [lv for _, lv in pairs]
        row["literature_pmids"] = ",".join(pmids)
        row["literature_count"] = str(len(pmids))
        row["literature_evidence_levels"] = ",".join(levels)
        if pmids:
            with_papers += 1

    joined.sort(key=lambda r: (r["genage_id"], r["human_hgnc_id"]))
    absent.sort(key=lambda r: (r["genage_id"],))
    return dict(
        genage_path=genage_path, ortholog_path=ortholog_path,
        genage_release=genage_release, ortholog_release=ortholog_release,
        genage_view_sha256=sha256_file(genage_path),
        ortholog_view_sha256=sha256_file(ortholog_path),
        genage_source=dict(release=genage_release,
                           dataset_id=gsrc.get("dataset_id"),
                           licence=(gsrc.get("licence") or {}).get("name"),
                           release_file=gsnap.get("path"),
                           release_file_sha256=gsnap.get("sha256"),
                           view_rows=len(genage_rows)),
        ortholog_source=dict(release=ortholog_release,
                             dataset_id=osrc.get("dataset_id"),
                             licence=(osrc.get("licence") or {}).get("name"),
                             snapshot=(osrc.get("snapshots") or {}).get("ortholog", {}).get("path"),
                             snapshot_sha256=(osrc.get("snapshots") or {}).get("ortholog", {}).get("sha256"),
                             view_rows=len(orth_rows)),
        counts=counts, none_by_reason=none_by_reason,
        one_to_many=one_to_many_detail,
        rows=joined, absent=absent,
        literature=dict(lit, rows_with_pmids=with_papers,
                        rows=len(joined),
                        note=((lit.get("note") or "") +
                              (f"; {with_papers} of {len(joined)} view row(s) carry >=1 PMID, "
                               f"the rest carry 0 — which says the store holds no paper for that "
                               f"gene, never that no paper exists (L2)") if lit.get("present")
                              else lit.get("note"))),
        class_structure=dict(
            classes=len(classes),
            rows=len(orth_rows),
            mouse_rows=sum(1 for r in orth_rows if _cell(r.get('organism')).startswith('mouse')),
            human_rows=sum(1 for r in orth_rows if _cell(r.get('organism')) == 'human'),
            relationships=dict(collections.Counter(_cell(r.get('relationship')) for r in orth_rows)),
        ),
    )


def publish(result: dict, store: str = DEFAULT_STORE, run_id: str = "") -> dict:
    vdir = os.path.join(store, "view")
    os.makedirs(vdir, exist_ok=True)
    table = render(COLUMNS, result["rows"])
    absent_table = render(ABSENT_COLUMNS, result["absent"])
    schema_bytes = (json.dumps(schema_document(), indent=2, sort_keys=True) + "\n").encode()

    paths = dict(table=os.path.join(vdir, VIEW_TABLE),
                 absent=os.path.join(vdir, ABSENT_TABLE),
                 schema=os.path.join(vdir, SCHEMA_FILE),
                 provenance=os.path.join(vdir, PROV_FILE))
    for p, b in ((paths["table"], table), (paths["absent"], absent_table),
                 (paths["schema"], schema_bytes)):
        with open(p, "wb") as fh:
            fh.write(b)

    code = os.path.join(HERE, "aging_ortholog.py")
    c = result["counts"]
    prov = {
        "view": VIEW, "version": VERSION,
        "run_id": run_id or "e1c-aging-ortholog-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "generated_at": utc_now(),
        "command": "python3 -m symbiont.aging_ortholog run",
        "rows": len(result["rows"]),
        "absent_rows": len(result["absent"]),
        "key": ["genage_id", "human_hgnc_id"],
        "transform": {
            "code": os.path.relpath(code, os.path.dirname(HERE)),
            "code_sha256": sha256_file(code),
            "join_keys": {
                "primary": "genage_models.v1.tsv:entrez_gene_id == ortholog.v1.tsv:entrez_gene_id "
                           "where ortholog row organism startswith 'mouse'",
                "class": "ortholog.v1.tsv:key (MGI DB Class Key) — the homology class the source states",
                "output_key": "genage_models.v1.tsv:id (GenAge ID) + ortholog.v1.tsv:class_human_hgnc_id",
                "symbol": "NOT a join key: carried (genage_key) and cross-checked (mouse_symbol) only (J1/J2)",
            },
            "input_releases": {
                "genage_models": result["genage_release"],
                "ortholog": result["ortholog_release"],
            },
            "rules": RESOLUTION,
            "reasons": list(REASONS),
        },
        "inputs": {
            "genage_models": {
                "path": result["genage_path"], "release": result["genage_release"],
                "view_sha256": result["genage_view_sha256"],
                "rows": result["genage_source"]["view_rows"],
                "dataset_id": result["genage_source"]["dataset_id"],
                "licence": result["genage_source"]["licence"],
                "release_file": result["genage_source"]["release_file"],
                "release_file_sha256": result["genage_source"]["release_file_sha256"],
                "release_file_use": "the `organism` column only (the view carries no organism); "
                                    "verified against the sha256 the view's own provenance recorded",
            },
            "ortholog": {
                "path": result["ortholog_path"], "release": result["ortholog_release"],
                "view_sha256": result["ortholog_view_sha256"],
                "rows": result["ortholog_source"]["view_rows"],
                "dataset_id": result["ortholog_source"]["dataset_id"],
                "licence": result["ortholog_source"]["licence"],
                "snapshot": result["ortholog_source"]["snapshot"],
                "snapshot_sha256": result["ortholog_source"]["snapshot_sha256"],
            },
            "literature": {
                "path": result["literature"].get("path"),
                "present": result["literature"].get("present"),
                "sha256": result["literature"].get("sha256"),
                "rows": result["literature"].get("rows"),
                "per_gene_cap": result["literature"].get("per_gene_cap"),
            },
        },
        "coverage": {
            "input_rows": c["input_rows"],
            "joined": c["joined"],
            "one_to_many": c["one_to_many"],
            "none": c["none"],
            "sum": c["joined"] + c["one_to_many"] + c["none"],
            "partition_holds": (c["joined"] + c["one_to_many"] + c["none"]) == c["input_rows"],
            "none_by_reason": result["none_by_reason"],
            "one_to_many_by_gene": result["one_to_many"],
            "view_rows": len(result["rows"]),
            "absent_rows": len(result["absent"]),
            "definition": (
                "joined = the input row's EntreZ id is in exactly one homology class and that class "
                "states exactly one human HGNC id; one_to_many = it states more than one (the row "
                "writes one output row per human id, and is counted once here); none = zero human "
                "ids, reason stated per row in the absent table. The three counts are over INPUT "
                "ROWS and are asserted to sum to input_rows."),
        },
        "literature_axis": result["literature"],
        "class_structure": result["class_structure"],
        "view_sha256": sha256_bytes(table),
        "absent_sha256": sha256_bytes(absent_table),
        "schema_sha256": sha256_bytes(schema_bytes),
        "may_conclude": [
            "this GenAge aging-gene row has a human ortholog that MGI's HOM table states, under the "
            "releases named above",
            "how many input rows do not (by reason, counted), and how many map to more than one "
            "human gene",
            "which of these genes the literature store holds papers for, at which evidence level",
        ],
        "may_not_conclude": [
            "that the human ortholog ages, or that the mouse gene's ageing role transfers to humans "
            "— an ortholog is a homology statement, never an ageing statement",
            "that a gene with literature_count=0 has no literature — the store's scope is bounded "
            "by the literature arm's per_gene_cap and gene_scope",
            "anything statistical, anything about disease or phenotype terms (out of scope for this "
            "row), or anything about a release other than the two named above",
        ],
    }
    with open(paths["provenance"], "w", encoding="utf-8") as fh:
        json.dump(prov, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return dict(paths=paths, provenance=prov)


# ─────────────────────────────────────────────────────────────── verification (a check that can fail)
def verify(store: str = DEFAULT_STORE) -> dict:
    vdir = os.path.join(store, "view")
    prov = load_json(os.path.join(vdir, PROV_FILE))
    names, rows = read_tsv(os.path.join(vdir, VIEW_TABLE))
    anames, arows = read_tsv(os.path.join(vdir, ABSENT_TABLE))
    checks = []

    def ck(name, ok, detail):
        checks.append(dict(check=name, ok=bool(ok), detail=detail))

    cov = prov["coverage"]
    ck("partition sums to input_rows", cov["sum"] == cov["input_rows"],
       f"{cov['joined']}+{cov['one_to_many']}+{cov['none']}={cov['sum']} vs input_rows {cov['input_rows']}")
    ck("reasons sum to none", sum(cov["none_by_reason"].values()) == cov["none"],
       f"{cov['none_by_reason']} = {sum(cov['none_by_reason'].values())} vs none {cov['none']}")
    ck("view row count matches sidecar", len(rows) == prov["rows"], f"{len(rows)} vs {prov['rows']}")
    ck("absent row count matches sidecar", len(arows) == prov["absent_rows"],
       f"{len(arows)} vs {prov['absent_rows']}")
    ck("view bytes match sidecar sha256",
       sha256_file(os.path.join(vdir, VIEW_TABLE)) == prov["view_sha256"],
       sha256_file(os.path.join(vdir, VIEW_TABLE))[:16] + "…")
    ck("absent bytes match sidecar sha256",
       sha256_file(os.path.join(vdir, ABSENT_TABLE)) == prov["absent_sha256"],
       sha256_file(os.path.join(vdir, ABSENT_TABLE))[:16] + "…")
    ck("every row carries both releases",
       all(r["genage_release"] == prov["transform"]["input_releases"]["genage_models"] and
           r["ortholog_release"] == prov["transform"]["input_releases"]["ortholog"] for r in rows),
       f"{len(rows)} row(s) checked against {prov['transform']['input_releases']}")
    ck("every row carries both ids",
       all(r["mouse_mgi_id"] and r["human_hgnc_id"] for r in rows),
       f"{sum(1 for r in rows if not (r['mouse_mgi_id'] and r['human_hgnc_id']))} row(s) missing an id")
    ck("every absent row names a reason from the fixed vocabulary",
       all(r["reason"] in REASONS for r in arows),
       f"{sorted({r['reason'] for r in arows})} all in {list(REASONS)}")
    ck("no input gene is both joined and absent",
       not ({r["genage_id"] for r in rows} & {r["genage_id"] for r in arows}),
       f"{len({r['genage_id'] for r in rows} | {r['genage_id'] for r in arows})} distinct input genes "
       f"accounted for (view {len(rows)} rows, absent {len(arows)} rows)")
    ck("literature never exceeds the cap",
       prov["literature_axis"].get("per_gene_cap") is None or
       all(int(r["literature_count"]) <= int(prov["literature_axis"]["per_gene_cap"]) for r in rows),
       f"cap {prov['literature_axis'].get('per_gene_cap')}, max seen "
       f"{max([int(r['literature_count']) for r in rows] or [0])}")
    ck("code on disk is the code the sidecar hashed",
       sha256_file(os.path.join(os.path.dirname(HERE), prov["transform"]["code"])) ==
       prov["transform"]["code_sha256"],
       f"{prov['transform']['code']} {prov['transform']['code_sha256'][:16]}…")
    return dict(ok=all(c["ok"] for c in checks), checks=checks, provenance=prov)


# ─────────────────────────────────────────────────────────────── the command
def _say(s: str = "") -> None:
    print(s, flush=True)


def _report(res: dict, prov: dict) -> None:
    c = res["counts"]
    _say(f"INPUTS   {res['genage_path']}  release={res['genage_release']}  rows={c['input_rows']}")
    _say(f"         {res['ortholog_path']}  release={res['ortholog_release']}  "
         f"rows={res['ortholog_source']['view_rows']}")
    _say(f"JOIN     key=entrez_gene_id (J1)  classes={res['class_structure']['classes']}  "
         f"mouse_rows={res['class_structure']['mouse_rows']}  human_rows={res['class_structure']['human_rows']}")
    _say(f"COVERAGE input_rows={c['input_rows']}  joined={c['joined']}  one_to_many={c['one_to_many']}  "
         f"none={c['none']}  sum={c['joined'] + c['one_to_many'] + c['none']}")
    _say(f"REASONS  " + "  ".join(f"{k}={v}" for k, v in res["none_by_reason"].items()))
    for g in res["one_to_many"]:
        _say(f"MANY     {g['genage_key']} ({g['genage_id']}) -> {len(g['human_ids'])} human ids "
             f"{','.join(g['human_ids'])}")
    _say(f"PUBLISH  view/{VIEW_TABLE}  rows={len(res['rows'])}  sha256={prov['view_sha256']}")
    _say(f"PUBLISH  view/{ABSENT_TABLE}  rows={len(res['absent'])}  sha256={prov['absent_sha256']}")
    _say(f"PUBLISH  view/{SCHEMA_FILE}  sha256={prov['schema_sha256']}")
    _say(f"PUBLISH  view/{PROV_FILE}  code_sha256={prov['transform']['code_sha256']}")
    _say(f"LITERATURE {res['literature'].get('rows_with_pmids')} of {len(res['rows'])} view row(s) "
         f"carry >=1 PMID (per_gene_cap={res['literature'].get('per_gene_cap')})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="aging_ortholog — the mouse→human join of the aging arm")
    ap.add_argument("command", nargs="?", default="run", choices=("run", "verify", "report"))
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--run-id", default="")
    a = ap.parse_args(argv)
    if a.command in ("run", "report"):
        res = build(a.store)
        if a.command == "report":
            _report(res, {"view_sha256": "-", "absent_sha256": "-", "schema_sha256": "-",
                          "transform": {"code_sha256": "-"}})
            return 0
        out = publish(res, a.store, a.run_id)
        _report(res, out["provenance"])
    v = verify(a.store)
    for c in v["checks"]:
        _say(f"CHECK    {'ok  ' if c['ok'] else 'FAIL'}  {c['check']}  ({c['detail']})")
    _say(f"VERIFY   {'OK' if v['ok'] else 'FAILED'}  {sum(1 for c in v['checks'] if c['ok'])}/"
         f"{len(v['checks'])} checks")
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
