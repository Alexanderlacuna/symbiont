"""THE MAPPING COMPILER — a plain dump is DATA, not a Python file.

WHY THIS EXISTS (owner, 2026-11-13: "why not make something generic? why do you want it specific?")
The engine already downloads a file, verifies a count, opens an archive, reads delimited text, stores
rows with provenance and publishes a view. What was still CODE in a dump adapter is not logic — it is
SEVEN FACTS peculiar to one publisher, typed once into a module:

    which url · whether it is zipped · which file inside · the delimiter · which column means what ·
    how to spot a release · how to count

Those seven facts are declared under `kind: mapping` in sources.yml (the ONE definition site per
source), and this module compiles them into the SAME four calls every code adapter implements, so the
loop, the funnel, the sandbox, the manifest and the view are UNTOUCHED:

    configure(cfg)            the only place a URL enters (resolved from `endpoints`, as always)
    discover(http, since)  -> Discovery      release · the source's own count · licence terms
    fetch(http, since)     -> FetchPlan       new bytes, or a named reason why not
    normalise(resources)   -> Normalised      records + rejects WITH reasons + per-resource accounting
    verify(d, n)           -> Verify          ours vs the source's own count

ONE COMPILER, N MAPPINGS. This file holds exactly two definitions for all sources — `MappingSource`
(the compiler) and `_Columns` (a header resolved into indices) — and no per-publisher definition
anywhere: adding a dump is a block of data, and the count of definitions here is the same for two
mappings or for twenty.

THE MAPPING BLOCK (`mapping:` under a `kind: mapping` source):

    role             xref_provider | id_authority | dataset — the part these rows play in the join,
                     or `dataset` for a source whose own rows ARE the claim (no join at all)
    urls             the source's endpoint NAME(s): the URL itself is declared ONCE, in `endpoints`
                     (that is the mirror lever — repointing a mirror edits base_url, nothing here)
    archive          zip | none
    member           the glob of the ONE file inside the archive we read (required for zip)
    delimiter        the field separator (one character)
    header           true when row 1 is the header (default true)
    columns          {<our name>: <their header name | 0-based index>}
                     `key` = the join key, `id` = the provider's OWN id for the row (required for
                     id_authority; for a provider it is what makes one row ONE thing, and the join's
                     R4 rule refuses a provider id claimed by several symbols — so a dump with a
                     per-row id column declares it here), `chrom`+`start`+`end` = coordinates (all
                     three or none); EVERY OTHER NAME becomes an xref namespace.
    release          {kind: last-modified | literal | header} (+ label · value · pattern)
    count            {kind: file | endpoint} (+ url · pattern) — `file` means the dump is its own
                     count (the rows we parsed from the bytes whose sha256 we recorded)
    licence_pattern  the wording the licence page must still carry (T6, licence-unknown)

REFUSALS — never a silent empty table:
    REFUSED[mapping-invalid]   at LOAD, naming the missing key: a mapping that cannot produce a row
                               (no `columns`, or columns with no `key`) or cannot produce a count
                               (no `count`) is refused before a request leaves the box. AND the
                               COMPILED CONFIG is refused by name if it lacks a key `loop.run()`
                               indexes (contract.LOOP_KEYS — `quarantined` above all): a dict with
                               a hole in it is a KeyError 600 lines into a run, so it never leaves
                               the compiler.
    REFUSED[mapping-mismatch]  at NORMALISE, naming both sides: a declared column absent from the
                               REAL header, an archive that does not hold exactly one file matching
                               `member` (the member names are listed), or a release fact the
                               provider does not send (no Last-Modified).
"""
from __future__ import annotations

import csv
import email.utils
import io
import re
import zipfile

from . import contract
from .contract import (Discovery, FetchPlan, MappingInvalid, MappingMismatch, Normalised,  # noqa: F401
                       Verify)

# THE ROLE VOCABULARY IS THE CONTRACT'S (req-P1-A-STANDALONE-DATASET-PUBLISHES-ITS-OWN): a mapping
# may play any of the three parts — the gene arm's two halves, or `dataset`, which means the compiled
# source publishes its own rows and does not join. The tuple is IMPORTED rather than re-spelt so the
# compiler and the loader cannot disagree about which words exist: a fourth role is added ONCE.
ROLES = contract.ROLES
ARCHIVES = ("zip", "none")
RELEASE_KINDS = ("last-modified", "literal", "header")
COUNT_KINDS = ("file", "endpoint")
REQUIRED_KEYS = ("role", "urls", "archive", "columns", "release", "count", "licence_pattern")
OPTIONAL_KEYS = ("member", "delimiter", "header", "licence_url")
KEY_COLUMN = "key"
ID_COLUMN = "id"
COORD_COLUMNS = ("chrom", "start", "end")
# the reject vocabulary of the row builder: a row never vanishes, it appears here with one of these
REJECT_REASONS = ("field_count", "empty_key", "empty_coordinate", "non_integer_coordinate")
NONE = "(none)"          # what an empty ETag is called in a run record, once


def validate(block, where: str, cfg: dict | None = None) -> None:
    """THE MAPPING SCHEMA, stated once. Total: an unknown key is refused by name, and a mapping that
    cannot produce a ROW or a COUNT is refused here — at load — naming the key it lacks. `cfg` is the
    source entry the block belongs to; when given, each `urls` entry must be an endpoint the entry
    declares (the URL has one definition site) and a declared `licence_url` must agree with it."""
    if not isinstance(block, dict) or not block:
        raise MappingInvalid(f"{where}: `mapping` must be a non-empty mapping with "
                             f"{list(REQUIRED_KEYS)} — a source declared as `kind: mapping` without "
                             f"the facts of its dump cannot produce a row or a count")
    unknown = sorted(k for k in block if k not in REQUIRED_KEYS + OPTIONAL_KEYS)
    if unknown:
        raise MappingInvalid(f"{where}: mapping carries unknown key(s) {unknown} — the schema is "
                             f"{list(REQUIRED_KEYS + OPTIONAL_KEYS)}")
    missing = [k for k in REQUIRED_KEYS if k not in block]
    if missing:
        raise MappingInvalid(f"{where}: mapping is missing required key(s) {missing} — a mapping that "
                             f"cannot produce a row or a count is refused HERE, never run as a silent "
                             f"empty table")

    role = block["role"]
    if role not in ROLES:
        raise MappingInvalid(f"{where}: mapping.role must be one of {list(ROLES)}, got {role!r}")

    urls = block["urls"]
    if not isinstance(urls, list) or not urls or not all(isinstance(u, str) and u for u in urls):
        raise MappingInvalid(f"{where}: mapping.urls must be a non-empty list of endpoint names, "
                             f"got {urls!r}")
    if cfg is not None:
        eps = cfg.get("endpoints") or {}
        bad = sorted(u for u in urls if u not in eps)
        if bad:
            raise MappingInvalid(f"{where}: mapping.urls names {bad}, which `endpoints` does not "
                                 f"declare — the URL is declared ONCE, in `endpoints` (that is what "
                                 f"makes a mirror one edit); declared: {sorted(eps)}")

    if block["archive"] not in ARCHIVES:
        raise MappingInvalid(f"{where}: mapping.archive must be one of {list(ARCHIVES)}, "
                             f"got {block['archive']!r}")
    member = block.get("member")
    if block["archive"] == "zip":
        if not isinstance(member, str) or not member.strip():
            raise MappingInvalid(f"{where}: mapping is `archive: zip` but declares no `member` — the "
                                 f"file inside the archive is one of the facts, so which file is read "
                                 f"may not be guessed")
    elif "member" in block:
        raise MappingInvalid(f"{where}: mapping is `archive: {block['archive']}` but declares "
                             f"`member` {member!r} — there is no archive to look inside")

    delim = block.get("delimiter", ",")
    if not isinstance(delim, str) or len(delim) != 1:
        raise MappingInvalid(f"{where}: mapping.delimiter must be a single character, got {delim!r}")
    if not isinstance(block.get("header", True), bool):
        raise MappingInvalid(f"{where}: mapping.header must be true or false")

    cols = block["columns"]
    if not isinstance(cols, dict) or not cols:
        raise MappingInvalid(f"{where}: mapping.columns must be a non-empty mapping of "
                             f"{{our name: their header or index}} — without COLUMNS there is no row "
                             f"to produce")
    if KEY_COLUMN not in cols:
        raise MappingInvalid(f"{where}: mapping.columns declares no {KEY_COLUMN!r} — the join key is "
                             f"the one column every record must carry, so a mapping without it cannot "
                             f"produce a row")
    for our, theirs in sorted(cols.items()):
        if isinstance(theirs, bool) or not isinstance(theirs, (str, int)):
            raise MappingInvalid(f"{where}: mapping.columns.{our} must be a header name or a 0-based "
                                 f"index, got {theirs!r}")
        if isinstance(theirs, str) and not theirs.strip():
            raise MappingInvalid(f"{where}: mapping.columns.{our} is an empty header name")
        if isinstance(theirs, int) and theirs < 0:
            raise MappingInvalid(f"{where}: mapping.columns.{our} is index {theirs} — indices are "
                                 f"0-based, so a negative index names no field")
    if role == "id_authority" and ID_COLUMN not in cols:
        raise MappingInvalid(f"{where}: mapping.role is id_authority but columns declares no "
                             f"{ID_COLUMN!r} — an authority owns the primary id, and a record without "
                             f"one cannot be joined")
    coords = [c for c in COORD_COLUMNS if c in cols]
    if coords and len(coords) != len(COORD_COLUMNS):
        raise MappingInvalid(f"{where}: mapping.columns declares {coords} but not all of "
                             f"{list(COORD_COLUMNS)} — coordinates are read as a triple or not at all")

    rel = block["release"]
    if not isinstance(rel, dict) or rel.get("kind") not in RELEASE_KINDS:
        raise MappingInvalid(f"{where}: mapping.release must be a mapping with kind in "
                             f"{list(RELEASE_KINDS)}, got {rel!r}")
    extra = sorted(k for k in rel if k not in ("kind", "label", "value", "pattern"))
    if extra:
        raise MappingInvalid(f"{where}: mapping.release carries unknown key(s) {extra}")
    if rel["kind"] == "literal" and not (isinstance(rel.get("value"), str) and rel["value"].strip()):
        raise MappingInvalid(f"{where}: mapping.release is `literal` but declares no `value` — the "
                             f"release stamp cannot be produced")
    if rel["kind"] == "header":
        _need_pattern(rel.get("pattern"), where, "mapping.release.pattern")
    if rel.get("label") is not None and not (isinstance(rel["label"], str) and rel["label"].strip()):
        raise MappingInvalid(f"{where}: mapping.release.label must be a non-empty string when given")

    cnt = block["count"]
    if not isinstance(cnt, dict) or cnt.get("kind") not in COUNT_KINDS:
        raise MappingInvalid(f"{where}: mapping.count must be a mapping with kind in "
                             f"{list(COUNT_KINDS)}, got {cnt!r} — a mapping that cannot produce a "
                             f"COUNT is refused here")
    extra = sorted(k for k in cnt if k not in ("kind", "url", "pattern"))
    if extra:
        raise MappingInvalid(f"{where}: mapping.count carries unknown key(s) {extra}")
    if cnt["kind"] == "endpoint":
        if not (isinstance(cnt.get("url"), str) and cnt["url"].strip()):
            raise MappingInvalid(f"{where}: mapping.count is `endpoint` but declares no `url` — the "
                                 f"address of the number is the fact")
        if cnt.get("pattern") is not None:
            _need_pattern(cnt["pattern"], where, "mapping.count.pattern")
    elif "url" in cnt:
        raise MappingInvalid(f"{where}: mapping.count is `file` but declares `url` {cnt['url']!r} — a "
                             f"dump is its own count; there is no endpoint to ask")

    _need_pattern(block["licence_pattern"], where, "mapping.licence_pattern")
    if block.get("licence_url") is not None:
        declared = block["licence_url"]
        if not (isinstance(declared, str) and declared.strip()):
            raise MappingInvalid(f"{where}: mapping.licence_url must be a non-empty URL when given")
        top = ((cfg or {}).get("licence_url") or "").strip()
        if top and declared.strip() != top:
            raise MappingInvalid(f"{where}: mapping.licence_url {declared!r} disagrees with the "
                                 f"source's `licence_url` {top!r} — the terms live on ONE page, and "
                                 f"two answers to 'where' is how a licence read stops being a fact")


def _need_pattern(pattern, where: str, what: str) -> None:
    if not (isinstance(pattern, str) and pattern.strip()):
        raise MappingInvalid(f"{where}: {what} must be a non-empty regular expression")
    try:
        re.compile(pattern)
    except re.error as e:
        raise MappingInvalid(f"{where}: {what} {pattern!r} is not a regular expression: {e}") from e


def compiled_config(cfg: dict, where: str, defaults: dict | None = None) -> dict:
    """THE COMPILED CONFIG of a `kind: mapping` source: the config the loop is handed, made TOTAL
    over the keys `loop.run()` reads off a source BY SUBSCRIPT (contract.LOOP_KEYS).

    WHY (req-P1-A-COMPILED-MAPPING-MUST-CARRY-THE): `contract.py` validates `quarantined` with
    `cfg.get(...)` because the schema calls it OPTIONAL, while `loop.py` indexes `cfg["quarantined"]`
    — so a mapping whose entry did not spell the key died with `KeyError: 'quarantined'` at
    loop.py:1184 on its FIRST run. The code path only looked immune because every shipped entry
    spells `quarantined: null`. Here the compiler SUPPLIES the key with the same default the code
    path carries (`contract.LOOP_DEFAULTS`: absence of a quarantine IS "not quarantined"), and then
    CHECKS ITS OWN OUTPUT: a key the loop indexes that is still missing is REFUSED[mapping-invalid]
    NAMING IT, here, at the compiler's door.

    `defaults` is the table THIS compiler fills from (the loader passes contract.LOOP_DEFAULTS). A
    compiler handed no defaults — the planted mapping of symbiont/selftest_mapping, or any future
    one that has not learned a key the loop reads — refuses by name instead of shipping the hole."""
    return contract.loop_config(cfg, where, defaults=defaults)


class _Columns:
    """The declared columns RESOLVED against the real header: {our name: field index}. A declared
    column the header does not carry is REFUSED[mapping-mismatch] naming BOTH — because the source
    moved or the mapping is wrong, and either way a row read by position after that is a guess."""

    def __init__(self, declared: dict, header: list[str] | None, source: str):
        index: dict[str, int] = {}
        for our, theirs in declared.items():
            if isinstance(theirs, int):
                index[our] = theirs
                continue
            if header is None:
                raise MappingMismatch(
                    f"{source}: columns.{our} is the header name {theirs!r} but the mapping says "
                    f"`header: false` — a name can only be matched against a header row")
            if theirs not in header:
                raise MappingMismatch(
                    f"{source}: columns.{our} declares the header {theirs!r}, which the real header "
                    f"does not carry — the header reads {header!r}")
            index[our] = header.index(theirs)
        self.index = index
        self.width = (max(index.values()) + 1) if index else 0


class MappingSource:
    """ONE compiler for N dumps: the four calls above, filled in from a `mapping:` block.

    An INSTANCE per declared mapping, never a class per source — that is the whole point of the row
    ("one compiler, N mappings"): a new dump is a block of data next to the old one."""

    _from_mapping = True          # the loader's mark: compiled from data, not a file under adapters/

    def __init__(self, sid: str, block: dict):
        validate(block, f"mapping {sid!r}")
        self.name = sid
        self.block = block
        self.role = block["role"]
        # the config an UNCONFIGURED mapping carries: TOTAL over contract.LOOP_KEYS from the first
        # line, because a dict that is only *sometimes* whole is exactly the defect this row
        # removed. `budget`/`rate_limit` are the schema-REQUIRED keys the loop also indexes — an
        # unconfigured mapping declares NEITHER a cap nor a rate, and never fetches: configure()
        # replaces this dict with the real entry before discover() is reachable.
        self.cfg: dict = compiled_config(
            {"id": sid, "adapter": sid, "kind": "mapping", "enabled": False,
             "budget": {}, "rate_limit": {}}, f"mapping {sid!r}")
        self.urls: dict[str, str] = {}
        self.licence_url = ""
        self.licence_name = ""
        self._d: Discovery | None = None

    # -- 0 configure: the ONLY place a URL enters -------------------------------
    def configure(self, cfg: dict) -> None:
        """Called by the loop before discover(), exactly as for a code adapter. The mapping is read
        from the config THIS run loaded (so a fixture may repoint it), and every URL is resolved from
        `endpoints` — the mirror lever, unchanged."""
        validate(cfg.get("mapping"), f"source {cfg.get('id', self.name)!r}", cfg)
        self.cfg = compiled_config(cfg, f"source {cfg.get('id', self.name)!r}")
        self.block = cfg["mapping"]
        self.role = self.block["role"]
        self.urls = {ep: contract.resolve_url(cfg["base_url"], cfg["endpoints"][ep])
                     for ep in self.block["urls"]}
        self.licence_url = cfg["licence_url"]
        self.licence_name = cfg["licence_text"]

    # -- 1 discover -----------------------------------------------------------
    def discover(self, http, since: dict) -> Discovery:
        b = self.block
        notes = [f"mapping (declared in sources.yml, compiled here): urls={self.urls} "
                 f"archive={b['archive']} member={b.get('member', '-')!r} "
                 f"delimiter={b.get('delimiter', ',')!r} columns={sorted(b['columns'])}"]
        resources = []
        for ep, url in self.urls.items():
            h = http.head(url, f"{self.name} {ep} head")
            resources.append(dict(reskey=ep, url=url, ext=_ext(b), **h))
        release = self._release(http, resources)
        count, count_url, count_note = None, "", ""
        cb = b["count"]
        if cb["kind"] == "endpoint":
            count_url = self._count_url(cb["url"])
            body = http.get(count_url, f"{self.name} count endpoint").decode("utf-8", "replace")
            m = re.search(cb.get("pattern") or r"(\d[\d,._ ]*)", body)
            if not m:
                raise MappingMismatch(
                    f"{self.name}: count endpoint {count_url} states no number the mapping's pattern "
                    f"{cb.get('pattern')!r} can read ({len(body)} bytes read) — the declared count fact "
                    f"cannot be produced from the real answer")
            count = int(re.sub(r"[^\d]", "", m.group(1)))
            count_url_note = f"the publisher states {count} at {count_url}"
            count_note = count_url_note
            notes.append(count_url_note)
        licence = contract.licence_terms(http, since, release, self.licence_url, self.licence_name,
                                         b["licence_pattern"], notes)
        d = Discovery(source=self.name, release=release, resources=resources,
                      published_count=count, published_count_url=count_url or
                      (resources[0]["url"] if resources else ""),
                      licence=licence,
                      notes=notes + ([] if count_note else [
                          "count: file — the dump is its own count: the number published is the rows "
                          "parsed from the bytes whose sha256 this run records"]))
        self._d = d
        return d

    def _release(self, http, resources: list[dict]) -> str:
        """THE RELEASE FACT, in the three shapes a dump offers: the file's own Last-Modified, a
        literal the operator typed, or a stamp inside the file's header row. The stamp is QUOTED from
        the provider, prefixed by `label` (default: the source id) so a release names its source."""
        rel = self.block["release"]
        label = rel.get("label") or self.name
        kind = rel["kind"]
        if kind == "literal":
            return str(rel["value"])
        if kind == "last-modified":
            first = resources[0]
            when = None
            try:
                when = email.utils.parsedate_to_datetime(first.get("last_modified") or "")
            except (TypeError, ValueError):
                when = None
            if when is None:
                raise MappingMismatch(
                    f"{self.name}: release kind `last-modified`, but {first['url']} sent no usable "
                    f"Last-Modified header (got {first.get('last_modified')!r}) — the declared release "
                    f"fact cannot be read, so the release cannot be named")
            return f"{label}-{when.date().isoformat()}"
        # kind == "header": the stamp lives INSIDE the file, so it is read once here (the fetch below
        # reads it again through the fetcher, and both reads are charged to the source's budget).
        reskey = self.block["urls"][0]
        data = http.get(self.urls[reskey], f"{self.name} {reskey} header row (release stamp)")
        lines = self._text(data).splitlines()
        line = lines[0] if lines else ""
        m = re.search(rel["pattern"], line)
        if not m:
            raise MappingMismatch(
                f"{self.name}: release kind `header`, but the first line of {reskey} does not carry "
                f"the stamp the pattern {rel['pattern']!r} names — the line reads {line[:160]!r}")
        return f"{label}-{m.group(1)}"

    def _count_url(self, declared: str) -> str:
        """A count endpoint is named like a url: an endpoint KEY of this source, or an address."""
        eps = self.cfg.get("endpoints") or {}
        if declared in eps:
            return contract.resolve_url(self.cfg["base_url"], eps[declared])
        return contract.resolve_url(self.cfg["base_url"], declared)

    # -- 2 fetch(since) -------------------------------------------------------
    def fetch(self, http, since: dict) -> FetchPlan:
        d = self._d
        etags = {r["reskey"]: r.get("etag", "") for r in d.resources}
        if since and since.get("etags") == etags and since.get("release") == d.release:
            return FetchPlan(new=False, reason=(
                f"every url carries the ETag of run {since.get('run_id', '?')} "
                f"({', '.join(f'{k}={v or NONE}' for k, v in etags.items())}) and the release "
                f"stamp {d.release} is unchanged; zero new records"), resources={})
        out = {}
        for r in d.resources:
            body = http.get(r["url"], f"{self.name} {r['reskey']}")
            out[r["reskey"]] = {"bytes": body, "filename": f"{r['reskey']}.{r['ext']}"}
        return FetchPlan(new=True,
                         reason="ETag moved (or no prior snapshot): the dump re-fetched",
                         resources=out)

    # -- 3 normalise ----------------------------------------------------------
    def normalise(self, resources: dict) -> Normalised:
        b, cols, delim = self.block, self.block["columns"], self.block.get("delimiter", ",")
        records: list[dict] = []
        rejects: list[dict] = []
        accounting: list[dict] = []
        rows_in = 0
        for reskey in sorted(resources):
            rows = [r for r in csv.reader(io.StringIO(self._text(resources[reskey])), delimiter=delim)
                    if any(f.strip() for f in r)]
            header = rows[0] if b.get("header", True) else None
            data = rows[1:] if header is not None else rows
            index = _Columns(cols, header, self.name)
            nrec = 0
            for row in data:
                rec, why, detail = _row(row, index, b)
                if why:
                    rejects.append(dict(row=delim.join(row), reason=why, reskey=reskey, detail=detail))
                    continue
                records.append(rec)
                nrec += 1
            rows_in += len(data)
            accounting.append(dict(
                reskey=reskey, url=self.urls.get(reskey, ""), rows_in=len(data), records=nrec,
                rejects=len(data) - nrec, used=0,
                note=("every row either became a record or a reject, with the reason kept"
                      + ("" if header is not None else "; the file carries no header row, so every "
                                                     "line read is a data row"))))
        file_count = rows_in if b["count"]["kind"] == "file" else None
        return Normalised(
            rows_in=rows_in, records=records, rejects=rejects,
            published_count=file_count,
            published_count_url=(self._d.published_count_url if self._d else ""),
            extra=dict(accounting=accounting, count_kind=b["count"]["kind"]))

    # -- 4 verify -------------------------------------------------------------
    def verify(self, d: Discovery, n: Normalised) -> Verify:
        errs = contract.check_accounting(n)
        kind = (n.extra or {}).get("count_kind", "file")
        how = ("count: file — the dump is its own count, so the source's number IS the "
               f"{n.published_count} row(s) parsed from the bytes this run recorded"
               if kind == "file" else
               f"count: endpoint — the publisher states {n.published_count} at {d.published_count_url}")
        return Verify(
            ok=not errs and n.published_count is not None,
            ours=len(n.records), source_count=n.published_count,
            url=n.published_count_url or "",
            detail=(f"mapping {self.name}: {n.rows_in} row(s) read across "
                    f"{len((n.extra or {}).get('accounting') or [])} url(s); {len(n.records)} became "
                    f"records, {len(n.rejects)} were rejected with a reason; {how}"
                    + (f"; ACCOUNTING BROKEN: {errs}" if errs else "")))

    # -- the bytes ------------------------------------------------------------
    def _text(self, data: bytes) -> str:
        """The delimited text of a resource: the archive's one matching member, or the bytes as they
        came. A zip that does not hold exactly one match is REFUSED[mapping-mismatch] naming the
        members — silence here would read as 'an empty table'."""
        b = self.block
        if b["archive"] == "none":
            return data.decode("utf-8-sig", "replace")
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise MappingMismatch(
                f"{self.name}: mapping declares `archive: zip` but the bytes are not a readable "
                f"archive ({e}) — a truncated dump refuses by name, it never parses as empty") from e
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        hits = [n for n in names if _glob(b["member"], n)]
        if len(hits) != 1:
            raise MappingMismatch(
                f"{self.name}: mapping declares member {b['member']!r} but the archive holds "
                f"{len(hits)} match(es) — its members are {names}")
        return zf.read(hits[0]).decode("utf-8-sig", "replace")


def _ext(block: dict) -> str:
    return "zip" if block["archive"] == "zip" else "dump"


def _glob(pattern: str, name: str) -> bool:
    """A member glob matched against the BASENAME and the whole path (`*.csv` finds
    `<index>/genes.csv`) using fnmatch's own rules — one definition, no regex half-language."""
    import fnmatch
    base = name.rsplit("/", 1)[-1]
    return fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(base, pattern)


def _row(row: list[str], index: "_Columns", block: dict):
    """ONE row -> (record, why, detail). The record is the CONTRACT's canonical shape: key · id ·
    xrefs · coords. An empty cell asserts nothing, so it is not carried as an xref."""
    if len(row) < index.width:
        return None, "field_count", f"{len(row)} field(s), the mapping declares column {index.width - 1}"
    vals = {our: (row[i].strip() if i < len(row) else "") for our, i in index.index.items()}
    key = vals.pop(KEY_COLUMN, "")
    if not key:
        return None, "empty_key", "the join key column is empty"
    rid = vals.pop(ID_COLUMN, "")
    coords = {}
    if all(c in index.index for c in COORD_COLUMNS):
        chrom, start, end = (vals.pop(c) for c in COORD_COLUMNS)
        if not chrom or not start or not end:
            return None, "empty_coordinate", f"chromosome={chrom!r} start={start!r} end={end!r}"
        if not (start.isdigit() and end.isdigit()):
            return None, "non_integer_coordinate", f"{start!r}/{end!r} for {key}"
        coords = dict(chrom=chrom, start=int(start), end=int(end))
    xrefs = {our: v for our, v in vals.items() if v}
    return dict(key=key, id=rid, xrefs=xrefs, coords=coords), "", ""


def adapters_from_sources(sources: dict) -> dict:
    """Compile EVERY `kind: mapping` source in a loaded config into one adapter, keyed by its
    `adapter` name — the same key `load_adapters` uses for a module plug-in, so the loop, the
    registry check and the run record cannot tell the two apart. N mappings -> N instances of ONE
    class; nothing here is named after a publisher."""
    out: dict[str, MappingSource] = {}
    for sid in sorted(sources):
        cfg = sources[sid]
        if (cfg.get("kind") or "code") != "mapping":
            continue
        out[cfg["adapter"]] = MappingSource(cfg["adapter"], cfg["mapping"])
    return out
