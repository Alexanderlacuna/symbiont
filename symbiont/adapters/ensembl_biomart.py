"""Ensembl BioMart adapter — the ID AUTHORITY for mouse genes (ids + coordinates).

Resources (one):
  biomart_tsv  the mmusculus_gene_ensembl mart query: mgi_symbol, ensembl_gene_id,
               chromosome_name, start_position, end_position

The source's own count is not guessed: BioMart will run the *same* query with count="1"
and answer with a single integer. That number is what verify() compares us against.

WHERE THINGS LIVE (req-P0-SOURCES-YML-AND-THE-FOUR-REFUSALS-T5): the mart endpoint, the metadata
endpoint, the licence URL and the licence's name are READ FROM sources.yml by configure(). This
file keeps only what is PARSING — the mart query XML, the attribute list, the dataset, the id
regex — because that is what the adapter is for. Repointing a mirror is an edit to base_url.
"""
from __future__ import annotations

import re
import urllib.parse

from .. import contract
from ..contract import Discovery, FetchPlan, Normalised, Verify

ATTRIBUTES = ["mgi_symbol", "ensembl_gene_id", "chromosome_name", "start_position", "end_position"]
DATASET = "mmusculus_gene_ensembl"
ENSEMBL_RE = re.compile(r"^ENSMUSG\d{11}$")
# the wording the licence page must still carry, or discovery refuses (T6, licence-unknown)
LICENCE_PATTERN = r"Ensembl imposes no restrictions on access to, or use of, the data[^.]*\."


def _query(mart: str, count: bool) -> str:
    q = (
        '<Query virtualSchemaName="default" formatter="TSV" header="1" uniqueRows="1" '
        f'count="{"1" if count else ""}" datasetConfigVersion="0.6">'
        f'<Dataset name="{DATASET}" interface="default">'
        + "".join(f'<Attribute name="{a}"/>' for a in ATTRIBUTES)
        + "</Dataset></Query>"
    )
    return mart + "?query=" + urllib.parse.quote(q, safe="")


class EnsemblBioMart:
    name = "ensembl_biomart"
    role = "id_authority"
    _d = None
    namespaces = {"ensembl_gene_id": ENSEMBL_RE}

    # -- 0 configure: the ONLY place a URL enters this adapter ---------------
    def configure(self, cfg: dict) -> None:
        """Everything network-shaped comes from sources.yml. Called by the loop before discover()."""
        self.cfg = cfg
        self.mart = contract.resolve_url(cfg["base_url"], cfg["endpoints"]["mart"])
        self.metadata_url = contract.resolve_url(cfg["base_url"], cfg["endpoints"]["metadata"])
        self.licence_url = cfg["licence_url"]
        self.licence_name = cfg["licence_text"]
        self.data_url = _query(self.mart, False)
        self.count_url = _query(self.mart, True)

    # -- 1 discover --------------------------------------------------------
    def discover(self, http, since: dict) -> Discovery:
        notes: list[str] = [f"endpoints from sources.yml: mart={self.mart} metadata={self.metadata_url}"]
        rel_txt = http.get_text(self.metadata_url, "ensembl release")
        rel = re.search(r'"release"\s*:\s*(\d+)', rel_txt)
        if not rel:
            raise RuntimeError("ensembl: /info/software carried no release number")
        release = f"ensembl-{rel.group(1)}"

        count_txt = http.get_text(self.count_url, "ensembl published count").strip()
        if not re.fullmatch(r"\d+", count_txt):
            raise RuntimeError(f"ensembl: count query answered {count_txt[:120]!r}, not an integer")
        published = int(count_txt)

        # A HEAD here is refused (405) by martservice; the count query above is both cheaper and
        # more meaningful as a change signal, so the resource is registered without ETag and
        # fetch(since) decides on (release, published count).
        head = dict(status=0, etag="", last_modified="", content_length=0)
        licence = contract.licence_terms(
            http, since, release, self.licence_url, self.licence_name, LICENCE_PATTERN, notes)
        d = Discovery(
            source=self.name,
            release=release,
            resources=[dict(reskey="biomart_tsv", url=self.data_url, ext="tsv", **head)],
            published_count=published,
            published_count_url=self.count_url,
            licence=licence,
            notes=notes + [f"dataset={DATASET} attributes={'+'.join(ATTRIBUTES)} uniqueRows=1 header=1"],
        )
        self._d = d  # consumed by fetch(since)
        return d

    # -- 2 fetch(since) ----------------------------------------------------
    def fetch(self, http, since: dict) -> FetchPlan:
        # `since` is the state this adapter wrote on the last run. The loop calls discover()
        # first on the same object, so the current release/count is already in hand and the
        # decision costs no data bytes.
        d = self._d
        if since and since.get("release") == d.release and since.get("published_count") == d.published_count:
            return FetchPlan(new=False, reason=(
                f"release {d.release} and published count {d.published_count} unchanged since run "
                f"{since.get('run_id', '?')}; zero new records"), resources={})
        body = http.get(self.data_url, "ensembl data")
        return FetchPlan(new=True, reason="release or published count moved (or no prior snapshot)",
                         resources={"biomart_tsv": {"bytes": body, "filename": "biomart_mmusculus_gene.tsv"}})

    # -- 3 normalise -------------------------------------------------------
    def normalise(self, resources: dict) -> Normalised:
        raw = resources["biomart_tsv"].decode("utf-8", "replace")
        lines = raw.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if not lines:
            raise RuntimeError("ensembl: snapshot is empty")
        header = lines[0].split("\t")
        if header[:5] != ["MGI symbol", "Gene stable ID", "Chromosome/scaffold name",
                          "Gene start (bp)", "Gene end (bp)"]:
            raise RuntimeError(f"ensembl: unexpected header {header[:6]!r} — the mart contract moved")

        records, rejects = [], []
        for ln in lines[1:]:
            f = ln.split("\t")
            if len(f) != 5:
                rejects.append(dict(row=ln, reason="field_count", detail=f"{len(f)} fields, expected 5",
                               reskey="biomart_tsv"))
                continue
            symbol, eid, chrom, start, end = (x.strip() for x in f)
            if not symbol:
                rejects.append(dict(row=ln, reason="empty_symbol", reskey="biomart_tsv",
                                   detail="no MGI symbol published for this ensembl gene"))
                continue
            if not ENSEMBL_RE.match(eid):
                rejects.append(dict(row=ln, reason="bad_ensembl_id", detail=f"{eid!r}", reskey="biomart_tsv"))
                continue
            if not chrom:
                rejects.append(dict(row=ln, reason="empty_chromosome", detail=eid, reskey="biomart_tsv"))
                continue
            if not (start.isdigit() and end.isdigit()):
                rejects.append(dict(row=ln, reason="non_integer_coordinate", detail=f"{start!r}/{end!r}", reskey="biomart_tsv"))
                continue
            if int(end) < int(start):
                rejects.append(dict(row=ln, reason="start_after_end", detail=f"{start}>{end}", reskey="biomart_tsv"))
                continue
            records.append(dict(key=symbol, id=eid, xrefs={"ensembl_gene_id": eid},
                                coords=dict(chrom=chrom, start=int(start), end=int(end))))
        return Normalised(
            rows_in=len(lines) - 1,
            records=records,
            rejects=rejects,
            published_count=None,  # this source's count is the live count query, held in Discovery
            published_count_url=self.count_url,
            extra=dict(accounting=[
                dict(reskey="biomart_tsv", url=self.data_url, rows_in=len(lines) - 1,
                     records=len(records), rejects=len(rejects), used=0,
                     note="every row either became a record or a reject, with the reason kept"),
            ]),
        )

    # -- 4 verify ----------------------------------------------------------
    def verify(self, d: Discovery, n: Normalised) -> Verify:
        return Verify(
            ok=(d.published_count == n.rows_in),
            ours=n.rows_in,
            source_count=d.published_count,
            url=d.published_count_url,
            detail=("rows we read == the count BioMart publishes for the same query"
                    if d.published_count == n.rows_in else
                    f"DISAGREEMENT: we read {n.rows_in}, BioMart says {d.published_count}"),
        )


ADAPTER = EnsemblBioMart()
