"""MGI adapter — the XREF PROVIDER for mouse genes (symbols, MGI ids, MGI's own Ensembl xrefs).

Resources (two, same provider, both are release files):
  mrk_ensembl   MRK_ENSEMBL.rpt  — one row per MGI marker with an Ensembl gene model:
                 MGI id, symbol, name, cM, chr, ENSMUSG..., transcripts, -, feature type,
                 start, end, strand, biotype            (13 tab fields, NO header)
  mrk_list1_gz  MRK_List1.rpt.gz — MGI's marker list with Status (O=current, W=withdrawn) and,
                 for withdrawn rows, the current MGI accession + symbol it was folded into.
                 This is where RETIRED IDs come from.

There is no MGI count endpoint (probe of /mgihome/other/stats.shtml: 404; the downloads index
states no counts). So MGI's own published count IS the release file's own record count, and our
parse is required to account for every one of those rows — nothing dropped.

WHERE THINGS LIVE (req-P0-SOURCES-YML-AND-THE-FOUR-REFUSALS-T5): the reports base URL, both
relative release-file paths, the metadata endpoint and the licence are READ FROM sources.yml by
configure(). This file keeps only what is PARSING — 13/14 tab fields, Status O/W, the release
stamp's own date format — so repointing a mirror is an edit to base_url alone.
"""
from __future__ import annotations

import gzip
import re

from .. import contract
from ..contract import Discovery, FetchPlan, Normalised, Verify

MGI_RE = re.compile(r"^MGI:\d+$")
ENSEMBL_RE = re.compile(r"^ENSMUSG\d{11}$")
RELEASE_DATE_RE = re.compile(r"(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})")
# the wording the licence page must still carry, or discovery refuses (T6, licence-unknown)
LICENCE_PATTERN = r"MGI data and annotations are licensed under[^.]*\."

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


class MGI:
    name = "mgi"
    role = "xref_provider"
    _d = None

    # -- 0 configure: the ONLY place a URL enters this adapter ---------------
    def configure(self, cfg: dict) -> None:
        """Everything network-shaped comes from sources.yml. Called by the loop before discover()."""
        self.cfg = cfg
        ep = cfg["endpoints"]
        self.ensembl_url = contract.resolve_url(cfg["base_url"], ep["mrk_ensembl"])
        self.list1_url = contract.resolve_url(cfg["base_url"], ep["mrk_list1_gz"])
        self.metadata_url = contract.resolve_url(cfg["base_url"], ep["metadata"])
        self.licence_url = cfg["licence_url"]
        self.licence_name = cfg["licence_text"]

    # -- 1 discover --------------------------------------------------------
    def discover(self, http, since: dict) -> Discovery:
        notes: list[str] = [f"endpoints from sources.yml: base={self.cfg['base_url']} "
                            f"metadata={self.metadata_url}"]
        h_ens = http.head(self.ensembl_url, "mgi mrk_ensembl head")
        h_l1 = http.head(self.list1_url, "mgi mrk_list1 head")
        m = RELEASE_DATE_RE.search(h_ens["last_modified"])
        if not m:
            raise RuntimeError(f"mgi: no usable Last-Modified on {self.ensembl_url} ({h_ens['last_modified']!r})")
        release = f"mgi-{m.group(3)}-{MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"

        licence = contract.licence_terms(
            http, since, release, self.licence_url, self.licence_name, LICENCE_PATTERN, notes)
        d = Discovery(
            source=self.name,
            release=release,
            resources=[dict(reskey="mrk_ensembl", url=self.ensembl_url, ext="rpt", **h_ens),
                       dict(reskey="mrk_list1_gz", url=self.list1_url, ext="rpt.gz", **h_l1)],
            published_count=None,   # only knowable from the file itself: set by normalise()
            published_count_url=self.ensembl_url,
            licence=licence,
            notes=notes + ["MGI publishes no count endpoint; the release file's own record count is "
                           "the source count"],
        )
        self._d = d
        return d

    # -- 2 fetch(since) ----------------------------------------------------
    def fetch(self, http, since: dict) -> FetchPlan:
        d = self._d
        etags = {r["reskey"]: r["etag"] for r in d.resources}
        # A moved RELEASE is a change even when the provider sends no ETag (a mirror may not): the
        # release stamp is read from the same file's Last-Modified, so trusting only the ETag would
        # publish last-month's file under this month's name.
        if since and since.get("etags") == etags and since.get("release") == d.release:
            return FetchPlan(new=False, reason=(
                f"both release files carry the ETags of run {since.get('run_id', '?')} "
                f"({', '.join(f'{k}={v}' for k, v in etags.items())}); zero new records"), resources={})
        out = {}
        for r in d.resources:
            body = http.get(r["url"], f"mgi {r['reskey']}")
            out[r["reskey"]] = {"bytes": body, "filename": f"{r['reskey']}.{r['ext']}"}
        return FetchPlan(new=True,
                         reason="ETag moved (or no prior snapshot): release files re-fetched",
                         resources=out)

    # -- 3 normalise -------------------------------------------------------
    def normalise(self, resources: dict) -> Normalised:
        records, rejects = [], []
        ens_raw = resources["mrk_ensembl"].decode("utf-8", "replace")
        lines = ens_raw.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        for ln in lines:
            f = ln.split("\t")
            if len(f) != 13:
                rejects.append(dict(row=ln, reason="field_count", reskey="mrk_ensembl", detail=f"{len(f)} fields, expected 13"))
                continue
            mgi_id, symbol, chrom, eid = f[0].strip(), f[1].strip(), f[4].strip(), f[5].strip()
            start, end, ftype = f[9].strip(), f[10].strip(), f[8].strip()
            if not MGI_RE.match(mgi_id):
                rejects.append(dict(row=ln, reason="bad_mgi_id", reskey="mrk_ensembl", detail=f"{mgi_id!r}"))
                continue
            if not symbol:
                rejects.append(dict(row=ln, reason="empty_symbol", reskey="mrk_ensembl", detail=mgi_id))
                continue
            if not ENSEMBL_RE.match(eid):
                rejects.append(dict(row=ln, reason="bad_ensembl_id", reskey="mrk_ensembl", detail=f"{eid!r} for {mgi_id}"))
                continue
            if not chrom:
                rejects.append(dict(row=ln, reason="empty_chromosome", reskey="mrk_ensembl", detail=mgi_id))
                continue
            if not (start.isdigit() and end.isdigit()):
                rejects.append(dict(row=ln, reason="non_integer_coordinate", reskey="mrk_ensembl", detail=f"{start!r}/{end!r}"))
                continue
            if int(end) < int(start):
                rejects.append(dict(row=ln, reason="start_after_end", reskey="mrk_ensembl", detail=f"{start}>{end}"))
                continue
            records.append(dict(key=symbol, id=mgi_id,
                                xrefs={"mgi_id": mgi_id, "ensembl_gene_id": eid},
                                coords=dict(chrom=chrom, start=int(start), end=int(end)),
                                feature_type=ftype,
                                asserts="ensembl_gene_id"))

        # ---- the retired-id resource (marker list, compressed) ----
        l1_raw = gzip.decompress(resources["mrk_list1_gz"]).decode("utf-8", "replace")
        l1 = l1_raw.split("\n")
        if l1 and l1[-1] == "":
            l1.pop()
        if not l1 or not l1[0].startswith("MGI Accession ID"):
            raise RuntimeError("mgi: MRK_List1.rpt.gz lost its header — the provider contract moved")
        l1_rows, retired, current, l1_rejects = 0, [], 0, []
        for ln in l1[1:]:
            l1_rows += 1
            f = ln.split("\t")
            if len(f) != 14:
                l1_rejects.append(dict(row=ln, reason="field_count", reskey="mrk_list1_gz",
                                       detail=f"{len(f)} fields, expected 14"))
                continue
            status, symbol, mtype = f[7].strip(), f[6].strip(), f[9].strip()
            cur_id, cur_sym = f[12].strip(), f[13].strip()
            if status not in ("O", "W"):
                l1_rejects.append(dict(row=ln, reason="bad_status", detail=f"{status!r}", reskey="mrk_list1_gz"))
                continue
            if status == "W":
                if cur_id and cur_sym:
                    retired.append(dict(key=symbol, current_symbol=cur_sym, current_id=cur_id,
                                        marker_type=mtype))
                continue
            current += 1
        rejects.extend(l1_rejects)
        return Normalised(
            rows_in=len(lines) + l1_rows,
            records=records,
            rejects=rejects,
            published_count=len(lines),                 # the release file's own record count
            published_count_url=self.ensembl_url,
            extra=dict(
                retired=retired,
                accounting=[
                    dict(reskey="mrk_ensembl", url=self.ensembl_url, rows_in=len(lines),
                         records=len(records),
                         rejects=sum(1 for r in rejects if r.get("reskey") == "mrk_ensembl"), used=0,
                         note="every row either became a record or a reject, with the reason kept"),
                    dict(reskey="mrk_list1_gz", url=self.list1_url, rows_in=l1_rows, records=0,
                         rejects=len(l1_rejects), used=l1_rows - len(l1_rejects),
                         note=f"{len(retired)} withdrawn rows carried a current accession (retired map); "
                              f"{current} current markers passed through"),
                ],
            ),
        )

    # -- 4 verify ----------------------------------------------------------
    def verify(self, d: Discovery, n: Normalised) -> Verify:
        from ..contract import check_accounting
        return Verify(
            ok=not check_accounting(n) and n.published_count is not None,
            ours=len(n.records),
            source_count=n.published_count,
            url=n.published_count_url,
            detail=(f"we read {n.rows_in} rows across two release files; {len(n.records)} became records, "
                    f"{len(n.rejects)} were rejected with a reason, "
                    f"{n.rows_in - len(n.records) - len(n.rejects)} rows served the retired map — "
                    f"the source's own count for MRK_ENSEMBL is its {n.published_count} record rows"),
        )


ADAPTER = MGI()
