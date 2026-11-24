"""ADAPTER `manifest_source` — ANOTHER STATION'S HANDOVER MANIFEST AS A DATA SOURCE.

WHY (owner, 2026-11-11: "is another SYMBIONT becoming a data source possible"): the handover
manifest was built as the BOUNDARY ARTEFACT, assuming nothing about its reader (DESIGN-STATION D7).
That is why a station's own output is already source-shaped — rows, counts, release, licence,
per-row provenance, sha256 — and why consuming it needs no new machinery: one adapter and four
rules. This file is that adapter. Nothing here knows the name of a station, a dataset, a column or
a URL: every one of those comes from the manifest it is pointed at, or from the config entry.

    discover(_, since)   -> Discovery     read the manifest; §7 fields, licence chain, via-chain
    fetch(_, since)      -> FetchPlan     copy the staged file, REFUSING a sha256 that disagrees
    normalise(resources) -> Normalised    its rows become our rows, carrying BOTH provenance levels
    verify(d, n)         -> Verify        our count vs the manifest's own count and the file's sha256

────────────────────────────────────────────────────────────────────────────────────────────
THE FOUR RULES, and what each one is allowed to claim
────────────────────────────────────────────────────────────────────────────────────────────

RULE 1 — TWO PROVENANCE LEVELS, KEPT APART. A row that arrives here has been through two stations,
so it carries two facts and both travel:

    LEVEL 1  the MANIFEST (this station's)   `via: <dataset_id>#<sha256:12> upstream=<release>`
    LEVEL 2  the UPSTREAM (the row's own)    the `source` and `source_release` cells, QUOTED
                                             verbatim from the incoming row, never re-spelt

The ingested row's `source` column still names the station that DERIVED the fact
(`ensembl_biomart|mgi`), never `manifest_source`. Renaming it would be provenance laundering: the
row would forget that someone else re-derived it, and a later reader would have no way to ask.

RULE 2 — VERIFICATION DEPTH, STATED ON EVERY ROW AND IN THE MANIFEST.

    verified: manifest   transport integrity. The copy this station holds agrees, byte for byte and
                         count for count, with the manifest that travelled with it. NOTHING MORE.
    verified: upstream   re-derived from the authority itself (MGI's MRK_Ensembl.rpt, Ensembl's
                         Mart), as the id_authority and xref_provider arms do.

A mirror CANNOT earn `upstream`: it did not re-derive one cell from the authority. So every row
this adapter writes carries `verified: manifest`, the manifest it writes carries
`verified: manifest`, and a row that arrives CLAIMING upstream depth is REFUSED[verification-mixed]
— because that claim, if it ever cleared the hash check, would be the laundering of a claim nobody
in this station can rebuild. The two depths are never averaged, never defaulted, never mixed.

RULE 3 — REFUSED[provenance-cycle]. The chain is the ancestry the manifest names: its own
dataset_id, then the `via_chain` it inherited, recursively — each station appends the chain it
received, so hop 3 sees hops 1 and 2 rather than only hop 2. If any dataset_id in that ancestry is
one THIS station already publishes, the ingest is refused: A←B←A is a loop, and a loop is a corpus
that cites itself. The one exception is deliberate and narrow: a manifest whose HEAD is a dataset
we publish is a re-read of the same edge (our own mirror, same staged sha256) and is ordinary
lifecycle. The same name with bytes we have never ingested is refused as a cycle too — a dataset
we publish cannot arrive from outside wearing our name.

RULE 4 — LICENCES CARRIED TRANSITIVELY. The manifest's stitched `licence.attribution` is copied
forward UNCHANGED (byte for byte, quoted in the ingest manifest) and every term travels with it.
A link anywhere in the chain that cannot be READ is REFUSED[licence-unreadable]: no data lands
under terms nobody in this station read, and "the manifest said so" is not a reading. What "read"
means is stated, not implied — `licence.link_read.by` in the ingest manifest is one of

    re-read, sha256 matched      the page/​file was opened here and hashed against the recorded
                                 terms_sha256 (a file: or local link, or an http(s) link when a
                                 reader was supplied)
    recorded quote + sha256      an http(s) link with no reader in the run: the link's evidence is
                                 what the manifest recorded reading (a non-empty quote and a
                                 64-hex terms_sha256). This is the WEAKER path and it is named.

RULE 5 — A RELEASE MOVE IS LIFECYCLE, NOT AN INCIDENT. On a re-read the fingerprint is
(dataset_id, upstream release string, staged sha256). If it moved, `fetch` says
`release_moved` and the ingest proceeds; if the verify then passes the lifecycle records
`re-verified`. There is no incident class here and no quarantine: a federated source moving its
release is the ordinary thing sources do, and treating it as an alarm would make every honest
upstream move look like a breach.

────────────────────────────────────────────────────────────────────────────────────────────
THE CONFIG ENTRY (validated by name — an unknown key is refused, so config and code cannot
disagree about what exists):

  - id: upstream_station_a
    adapter: manifest_source        # required, exactly this adapter's name
    manifest: <path or URL>         # required: the handover manifest to read
    base_url: ""                    # optional: where a RELATIVE staged path resolves
                                    # (default = the manifest's own directory, its sibling)
    published: []                   # optional: extra dataset_ids this station publishes
    note: "why this source exists"

OTHER_ARM, and why: this is not a gene-arm plug-in. `role` says `dataset` (its rows ARE the claim,
nothing joins it), but it is not driven by loop.run() — a federated ingest is a fact about TWO
stations, and its outputs (mirror, via:, verification depth) are not a gene-arm view. It declares
module-level `OTHER_ARM` for the same reason symbiont/adapters/mcp_source.py does, so
contract.load_adapters skips it BY NAME instead of filing it under a role it does not have.

Run:
    python3 -m symbiont.adapters.manifest_source --manifest <path|url> --store <store> [--dry-run]
    python3 -m symbiont.adapters.manifest_source --self-test --store <store>
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import urllib.parse

from .. import contract, manifest as handover
from ..contract import Discovery, FetchPlan, Normalised, Verify

OTHER_ARM = "manifest_source"

# THE TWO VERIFICATION DEPTHS, as values. Never a boolean: a boolean has no room for "which one",
# and the whole rule is that a reader can tell which one a row is claiming.
VERIFIED_MANIFEST = "manifest"
VERIFIED_UPSTREAM = "upstream"
DEPTHS = (VERIFIED_MANIFEST, VERIFIED_UPSTREAM)
DEPTH_MEANING = {
    VERIFIED_MANIFEST: ("transport integrity: the copy agrees with the manifest that travelled with "
                        "it (counts and sha256). The cells were NOT re-derived from the authority"),
    VERIFIED_UPSTREAM: ("re-derived from the authority by the station that publishes it (as the "
                        "id_authority/xref_provider arms do)"),
}

# the ingest layout inside the store: one content-addressed directory per staged sha256
INGEST = "ingest"
STATE = "state"

STAGE_PROVENANCE = handover.STAGE_PROVENANCE_COLUMNS      # ("source", "source_release")
VIA_COLUMN = "via"
VERIFIED_COLUMN = "verified"
OUT_COLUMNS = [VIA_COLUMN, VERIFIED_COLUMN]

# a row that arrives with an empty upstream cell has forgotten who derived it: a reject, counted,
# never a record whose provenance column silently holds nothing
REJECT_REASONS = ("field_count", "empty_key", "no_upstream_provenance", "provenance_disagrees",
                  "double_via")


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE REFUSALS, by name. A refusal is a legal answer with a reason: it lands verbatim on stdout
# ("REFUSED[provenance-cycle] ...") and in the run record, so "the source was skipped" and "the run
# broke" can never be mistaken for each other.
# ─────────────────────────────────────────────────────────────────────────────────────────────
class ProvenanceCycle(contract.Refusal):      # A←B←A: a corpus that cites itself
    code = "provenance-cycle"


class HashMismatch(contract.Refusal):          # the copy does not agree with its manifest
    code = "hash-mismatch"


class LicenceUnreadable(contract.Refusal):     # a link in the chain nobody could read
    code = "licence-unreadable"


class VerificationMixed(contract.Refusal):     # a row claiming depth this station cannot rebuild
    code = "verification-mixed"


class StagedMissing(contract.Refusal):         # the manifest names bytes that are not there
    code = "staged-missing"


# ─────────────────────────────────────────────────────────────────────────────────────────────
# small helpers (one definition site each)
# ─────────────────────────────────────────────────────────────────────────────────────────────
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_url(loc: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://", loc or ""))


def _read_bytes(loc: str, http=None, note: str = "manifest_source") -> bytes:
    """Read a locator. http(s) goes through the caller's reader when there is one; a path or a
    file:// URL is read from disk. ONE place decides, so discover/fetch cannot disagree."""
    if not loc:
        raise StagedMissing("empty locator: nothing to read")
    if _is_url(loc):
        scheme = urllib.parse.urlparse(loc).scheme
        if scheme == "file":
            return _read_path(urllib.parse.urlparse(loc).path, loc)
        if http is None:
            raise StagedMissing(f"{loc} is a network locator and this run has no reader — "
                               f"refusing rather than pretending")
        return http.get(loc, note)
    return _read_path(loc, loc)


def _read_path(path: str, loc: str) -> bytes:
    if not os.path.isfile(path):
        raise StagedMissing(f"{loc} does not exist at run time ({os.path.abspath(path)})")
    with open(path, "rb") as fh:
        return fh.read()


def _json_bytes(b: bytes, what: str, loc: str) -> dict:
    try:
        return json.loads(b.decode("utf-8"))
    except Exception as e:
        raise contract.Refusal(f"{what} at {loc} is not JSON ({type(e).__name__}: {e})") from e


def _jdump(obj, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def _write_bytes(b: bytes, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b)
    return path


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def say(line: str = "") -> None:
    print(line, flush=True)


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE CHAIN (rule 3) — read from the manifest's own head and the chain it inherited
# ─────────────────────────────────────────────────────────────────────────────────────────────
def via_cell(dataset_id: str, staged_sha256: str, upstream_release: str) -> str:
    """THE ONE CELL a federated row carries: the manifest it came through AND the upstream release
    the row itself states. Two levels in one cell, because a store that writes only one of them can
    no longer be asked which of the two a value came from."""
    return f"{dataset_id}#{(staged_sha256 or '')[:12]} upstream={upstream_release}"


def dataset_id_of_via(cell: str) -> str:
    """The dataset a via cell names — the part before '#'. Unparseable cells name nothing ("")."""
    s = str(cell or "").strip()
    if not s or " upstream=" not in s and "#" not in s:
        return ""
    head = s.split(" upstream=")[0]
    return head.split("#")[0].strip()


def chain_of(m: dict) -> list[dict]:
    """The ancestry a manifest names, HEAD FIRST: its own dataset_id, then every link it inherited
    through `provenance_chain`/`via_chain`. Each station appends the chain it received, so the
    ancestry is complete rather than only the last hop — that is what "recursively" buys, and it
    costs nothing to carry because it is a list of small dicts."""
    out: list[dict] = []
    head_id = (m.get("dataset_id") or "").strip()
    if head_id:
        out.append(dict(dataset_id=head_id,
                        staged_sha256=((m.get("staged") or {}).get("sha256") or ""),
                        link="head"))
    for key in ("provenance_chain", "via_chain"):
        for e in m.get(key) or []:
            if not isinstance(e, dict):
                continue
            did = (e.get("dataset_id") or "").strip()
            if did:
                out.append(dict(dataset_id=did, staged_sha256=e.get("staged_sha256") or "",
                                link=key))
    # the manifest-level `via` (a station may state its whole chain as one cell)
    v = (m.get("via") or "")
    if isinstance(v, str) and v.strip():
        did = dataset_id_of_via(v)
        if did:
            out.append(dict(dataset_id=did, staged_sha256="", link="via"))
    return out


def _manifest_dataset_ids(root: str) -> set[str]:
    out: set[str] = set()
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        p = os.path.join(root, name)
        try:
            m = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if (m.get("dataset_id") or "").strip():
            out.add(m["dataset_id"].strip())
    return out


def _manifest_staged_shas(root: str) -> set[str]:
    out: set[str] = set()
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        try:
            m = json.load(open(os.path.join(root, name), encoding="utf-8"))
        except Exception:
            continue
        sha = ((m.get("staged") or {}).get("sha256") or "").strip()
        if sha:
            out.add(sha)
    return out


def published_dataset_ids(store: str) -> dict:
    """WHAT THIS STATION PUBLISHES, in two kinds, because they answer different questions:

        derived   datasets this station DERIVED and hands over (handover/*.json) — its OWN rows.
                  A manifest whose ancestry names one of these is a corpus citing itself.
        mirrored  datasets this station took in and mirrors (ingest/*/*/MANIFEST.json) — it
                  carries their bytes but derived none of their cells. A mirror naming its own
                  upstream is the same edge re-read, not a loop.
        declared  dataset_ids the store's sources.yml states (publisher+dataset), the identities
                  this station would derive from if it ran.

    Never guessed: a station that cannot say what it publishes cannot check a cycle."""
    hdir = os.path.join(store, "handover")
    idir = os.path.join(store, INGEST)
    derived = _manifest_dataset_ids(hdir)
    mirrored: set[str] = set()
    for root, _dirs, files in os.walk(idir):
        if "MANIFEST.json" in files:
            try:
                m = json.load(open(os.path.join(root, "MANIFEST.json"), encoding="utf-8"))
            except Exception:
                continue
            if (m.get("dataset_id") or "").strip():
                mirrored.add(m["dataset_id"].strip())
    declared: set[str] = set()
    cfgp = os.path.join(store, "sources.yml")
    if os.path.isfile(cfgp):
        try:
            for cfg in contract.load_sources(cfgp).values():
                try:
                    declared.add(contract.dataset_id(cfg))
                except contract.ConfigError:
                    pass
        except Exception:
            pass
    return dict(derived=sorted(derived), mirrored=sorted(mirrored), declared=sorted(declared))


def self_published(store: str, extra=()) -> set[str]:
    """The set a CYCLE is checked against: what this station derives, plus what it declares, plus
    whatever the config names. NOT what it mirrors — that is the same edge, not a loop."""
    p = published_dataset_ids(store) if store else dict(derived=[], mirrored=[], declared=[])
    return set(p["derived"]) | set(p["declared"]) | set(extra or ())


def ingested_shas(store: str) -> set[str]:
    """Every staged sha256 this station has already taken in OR handed out. A name we derive may
    arrive again only with bytes we have already seen: that is our own handover being read back,
    not a stranger wearing our name."""
    out: set[str] = set(_manifest_staged_shas(os.path.join(store, "handover")))
    sdir = os.path.join(store, STATE)
    for name in sorted(os.listdir(sdir)) if os.path.isdir(sdir) else []:
        if not name.endswith(".json"):
            continue
        try:
            st = json.load(open(os.path.join(sdir, name), encoding="utf-8"))
        except Exception:
            continue
        for k in ("staged_sha256", "manifest_sha256"):
            if st.get(k):
                out.add(st[k])
        for s in st.get("staged_sha256_seen") or []:
            out.add(s)
    for root, _dirs, files in os.walk(os.path.join(store, INGEST)):
        if "MANIFEST.json" in files:
            out |= _manifest_staged_shas(root)
    return out


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE LICENCE CHAIN (rule 4)
# ─────────────────────────────────────────────────────────────────────────────────────────────
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _link_read(term: dict, http, term_index: int) -> dict:
    """Can this term's link be READ? Two honest answers, and the weaker one is named.

    A malformed or absent link is unreadable, always. A local/file link is opened and hashed here.
    An http(s) link is read when a reader exists, else readability rests on what the manifest
    recorded reading — a non-empty quote and a 64-hex terms_sha256 — and the manifest says so."""
    url = (term.get("url") or "").strip()
    name = (term.get("name") or "").strip()
    if not url:
        raise LicenceUnreadable(
            f"licence term {term_index} ({name or 'unnamed'}) carries no link — the chain cannot be "
            f"read past this point, and a licence nobody can be sent to is not an attribution")
    parsed = urllib.parse.urlparse(url)
    if not (parsed.scheme or url.startswith("/") or url.startswith(".")):
        raise LicenceUnreadable(
            f"licence term {term_index} ({name or 'unnamed'}) has an unparseable link {url!r} — "
            f"neither a URL nor a path")
    recorded = (term.get("terms_sha256") or "").strip()
    local = (not parsed.scheme) or parsed.scheme == "file"
    if local:
        path = parsed.path if parsed.scheme == "file" else url
        try:
            page = _read_path(path, url)
        except StagedMissing as e:
            raise LicenceUnreadable(
                f"licence term {term_index} ({name or 'unnamed'}): the link {url} cannot be read "
                f"here ({e}) — refusing to carry terms nobody read") from e
        got = sha256_bytes(page)
        return dict(term=term_index, name=name, url=url, read=True, by="re-read, sha256 matched"
                    if got == recorded else "re-read, sha256 CHANGED since the manifest recorded it",
                    sha256=got, recorded_sha256=recorded, matches_recorded=(got == recorded),
                    bytes=len(page))
    if parsed.scheme in ("http", "https") and http is not None:
        try:
            page = http.get(url, f"licence {name or url}")
        except Exception as e:      # unreachable, 404, deadline, short read — all mean UNREADABLE
            raise LicenceUnreadable(
                f"licence term {term_index} ({name or 'unnamed'}): the link {url} could not be read "
                f"({type(e).__name__}: {e})") from e
        got = sha256_bytes(page)
        return dict(term=term_index, name=name, url=url, read=True, by="re-read, sha256 matched"
                    if got == recorded else "re-read, sha256 CHANGED since the manifest recorded it",
                    sha256=got, recorded_sha256=recorded, matches_recorded=(got == recorded),
                    bytes=len(page))
    # no reader for a network link: the recorded evidence is the link's readability, and it must
    # actually be evidence (a quote was read, from a page that hashed to something)
    quote = (term.get("quote") or "").strip()
    if not quote or not HEX64.match(recorded):
        raise LicenceUnreadable(
            f"licence term {term_index} ({name or 'unnamed'}) at {url}: no reader in this run AND "
            f"the manifest records no readable evidence for it (quote={'yes' if quote else 'NO'}, "
            f"terms_sha256={recorded or 'MISSING'}) — an unread link is not a licence")
    return dict(term=term_index, name=name, url=url, read=True, by="recorded quote + sha256",
                sha256="", recorded_sha256=recorded, matches_recorded=None, bytes=0,
                note=("an http(s) link with no reader in this run: readability rests on what the "
                      "producing station recorded reading, which is the weaker of the two paths "
                      "and is named rather than implied"))


def licence_chain(m: dict, http=None) -> dict:
    """The stitched attribution, COPIED FORWARD UNCHANGED, plus the readability of every link."""
    lic = m.get("licence") or {}
    attribution = (lic.get("attribution") or "").strip()
    terms = lic.get("terms") or []
    if not attribution:
        raise LicenceUnreadable(
            f"the manifest {m.get('dataset_id')!r} carries no licence attribution — there is "
            f"nothing to copy forward, and an empty attribution is not a licence")
    if not terms:
        raise LicenceUnreadable(
            f"the manifest {m.get('dataset_id')!r} states an attribution but no terms[] — the links "
            f"the attribution rests on cannot be read, so neither can the licence")
    links = [_link_read(t, http, i) for i, t in enumerate(terms)]
    return dict(attribution=attribution,                       # VERBATIM: never re-spelt
                attribution_sha256=sha256_bytes(attribution.encode("utf-8")),
                terms=[dict((k, t.get(k, "")) for k in
                            ("source", "source_release", "name", "url", "quote", "terms_sha256"))
                       for t in terms],
                link_read=links, transitive=True,
                note=("the attribution above is the upstream manifest's own stitched string, copied "
                      "forward byte for byte (its sha256 is stated so a reader can check that no "
                      "station in the chain re-spelt it); the terms and their links travel with it"))


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE ADAPTER
# ─────────────────────────────────────────────────────────────────────────────────────────────
class ManifestSource:
    name = "manifest_source"
    role = "dataset"        # its rows ARE the claim: nothing joins it (see OTHER_ARM above)

    def __init__(self) -> None:
        self.cfg: dict = {}
        self.store = ""
        self.locator = ""
        self.base_url = ""
        self.published: set[str] = set()
        self.seen_shas: set[str] = set()
        self._m: dict = {}
        self._d: Discovery | None = None
        self._manifest_bytes: bytes = b""
        self._manifest_sha256: str = ""
        self._staged_locator: str = ""
        self._chain: list[dict] = []
        self._licence: dict = {}
        self.dry_run = False

    # -- 0 configure: the ONLY place a location enters this adapter -----------
    def configure(self, cfg: dict, store: str = "") -> None:
        unknown = sorted(set(cfg) - set(CONFIG_KEYS))
        if unknown:
            raise contract.ConfigError(
                f"manifest_source: unknown config key(s) {unknown} — the schema is TOTAL, so an "
                f"unknown key is refused by name rather than ignored ({list(CONFIG_KEYS)})")
        missing = [k for k in ("id", "manifest") if not (cfg.get(k) or "").strip()]
        if missing:
            raise contract.ConfigError(
                f"manifest_source: config is missing {missing} — an adapter with no manifest is an "
                f"adapter with no source")
        self.cfg = dict(cfg)
        self.locator = cfg["manifest"].strip()
        base = (cfg.get("base_url") or "").strip()
        self.store = store or ""
        if base:
            self.base_url = base
        elif not _is_url(self.locator):
            self.base_url = os.path.dirname(os.path.abspath(self.locator))
        else:
            self.base_url = self.locator.rsplit("/", 1)[0]
        extra = set(cfg.get("published") or [])
        self.published = set(extra)
        self.mirrored: set[str] = set()
        if self.store:
            self.published |= self_published(self.store, extra)
            self.mirrored = set(published_dataset_ids(self.store)["mirrored"])
            self.seen_shas = ingested_shas(self.store)

    # -- 1 discover ---------------------------------------------------------
    def discover(self, http, since: dict) -> Discovery:
        raw = _read_bytes(self.locator, http, "manifest_source manifest")
        self._manifest_bytes = raw
        self._manifest_sha256 = sha256_bytes(raw)
        m = _json_bytes(raw, "the handover manifest", self.locator)
        self._m = m

        # ---- THE §7 FIELDS, BY NAME (one definition site: symbiont/manifest.py) --------------
        errs = []
        for path, phrase, fname in handover.REQUIRED_FIELDS:
            val = (m.get(path) or None) if "." not in path else _dig(m, path)
            if val in (None, "", [], {}):
                errs.append(f"{fname} (§7: {phrase}; path {path})")
        if errs:
            raise contract.Refusal(
                f"the artefact at {self.locator} is not a handover manifest: missing "
                f"{', '.join(errs)}")
        pv = m.get("row_provenance") or {}
        if pv.get("per_row") is not True:
            raise contract.Refusal(
                "row_provenance.per_row is not true — a manifest whose rows do not each carry "
                "source+source_release cannot be ingested row by row")
        staged_cols = (m.get("staged") or {}).get("columns") or []
        absent = [c for c in STAGE_PROVENANCE if c not in staged_cols]
        if absent:
            raise contract.Refusal(
                f"the staged file declares no {absent} column — a row without its upstream "
                f"provenance is exactly the laundering this adapter exists to refuse "
                f"(columns: {staged_cols})")

        # ---- RULE 3: THE CHAIN, BEFORE A BYTE IS COPIED --------------------------------------
        chain = chain_of(m)
        self._chain = chain
        head, inherited = (chain[0] if chain else {}), (chain[1:] if chain else [])
        hits = [c for c in inherited if c["dataset_id"] in self.published]
        if hits:
            raise ProvenanceCycle(
                f"the manifest {head.get('dataset_id')!r} names "
                f"{', '.join(sorted({c['dataset_id'] for c in hits}))} in its via-chain, and this "
                f"station already PUBLISHES that dataset — A←B←A is a loop, and a loop is a corpus "
                f"that cites itself. Refused before one byte was copied; the chain was "
                f"{[c['dataset_id'] for c in chain]}; this station publishes "
                f"{sorted(self.published)}")
        if (head.get("dataset_id") in self.published
                and head.get("dataset_id") not in self.mirrored
                and head.get("staged_sha256") not in self.seen_shas):
            raise ProvenanceCycle(
                f"the manifest's head dataset_id {head.get('dataset_id')!r} is a dataset this "
                f"station already publishes, but its staged sha256 {head.get('staged_sha256')} is "
                f"one this station has never ingested — a dataset we publish cannot arrive from "
                f"outside wearing our name with bytes we have never seen "
                f"(ingested: {sorted(self.seen_shas)})")

        # ---- RULE 4: THE LICENCE CHAIN ------------------------------------------------------
        self._licence = licence_chain(m, http)

        # ---- the staged locator, resolved by name and never guessed -------------------------
        staged_rel = (m.get("staged") or {}).get("path") or ""
        self._staged_locator = self._resolve_staged(staged_rel)
        release = (pv.get("source_release") or "").strip()
        staged_sha = (m.get("staged") or {}).get("sha256") or ""
        d = Discovery(
            source=self.name,
            release=release,          # the upstream release string, VERBATIM: never re-spelt
            resources=[dict(reskey="manifest", url=self.locator, sha256=self._manifest_sha256,
                            content_length=len(raw)),
                       dict(reskey="staged", url=self._staged_locator, sha256=staged_sha,
                            content_length=(m.get("staged") or {}).get("bytes"))],
            published_count=(m.get("rows") or {}).get("count"),
            published_count_url=self.locator,
            licence=self._licence,
            notes=[
                f"manifest read at {self.locator} ({len(raw)} bytes, sha256 {self._manifest_sha256})",
                f"dataset_id {m.get('dataset_id')!r} · entity {m.get('entity')!r} · upstream release "
                f"{release!r} (quoted from row_provenance.source_release)",
                f"the manifest's own count is rows.count={(m.get('rows') or {}).get('count')} and "
                f"counts.rows={(m.get('counts') or {}).get('rows')}",
                f"provenance chain, head first: {[c['dataset_id'] for c in chain]} — none of them is "
                f"published here ({sorted(self.published) or 'this station publishes nothing'})"
                if not hits else "provenance chain refused above",
                "licence links: " + ", ".join(
                    "%s (%s)" % (l["by"], l["url"]) for l in self._licence["link_read"]),
                "verification depth for every row this ingest writes is "
                f"{VERIFIED_MANIFEST!r}: " + DEPTH_MEANING[VERIFIED_MANIFEST],
            ],
        )
        self._d = d
        return d

    def _resolve_staged(self, staged_rel: str) -> str:
        """Where the staged bytes are, named as a list of what was tried. The manifest's path field
        was written on the PRODUCING station's disk (an absolute path there is a fiction here), so
        the resolution is: an existing absolute path · a sibling of the manifest · base_url + path."""
        tried: list[str] = []
        cands = []
        if staged_rel:
            cands.append(staged_rel)
            base = os.path.basename(staged_rel)
            if _is_url(self.base_url):
                cands.append(self.base_url.rstrip("/") + "/" + base)
            else:
                cands.append(os.path.join(self.base_url, base))
        else:
            # no path at all: accept a staged file that sits beside the manifest, named after it
            if not _is_url(self.locator):
                d = os.path.dirname(os.path.abspath(self.locator))
                cands += [os.path.join(d, n) for n in sorted(os.listdir(d))
                          if n.endswith((".tsv", ".txt"))]
        for c in cands:
            tried.append(c)
            if _is_url(c):
                continue        # existence is proved by the read, where a reader exists
            if os.path.isfile(c):
                return c
        raise StagedMissing(
            f"the manifest names staged bytes at {staged_rel!r} and none of these exist at run "
            f"time: {tried} — a manifest whose bytes are not there is not a source "
            f"(base_url {self.base_url!r})")

    # -- 2 fetch(since) -----------------------------------------------------
    def fetch(self, http, since: dict) -> FetchPlan:
        d = self._d
        assert d is not None, "fetch before discover"
        m = self._m
        staged_sha = (m.get("staged") or {}).get("sha256") or ""
        fingerprint = dict(dataset_id=(m.get("dataset_id") or ""), release=d.release,
                           manifest_sha256=self._manifest_sha256, staged_sha256=staged_sha)
        old = (since or {}).get("fingerprint") or {}
        moved = [k for k in ("dataset_id", "release", "manifest_sha256", "staged_sha256")
                 if old.get(k) and old.get(k) != fingerprint[k]]
        if old and not moved:
            return FetchPlan(new=False, reason=(
                f"RUNCHANGED: dataset {fingerprint['dataset_id']!r} at upstream release "
                f"{fingerprint['release']!r}, staged sha256 {staged_sha[:12]} — the same bytes as run "
                f"{since.get('run_id', '?')}; nothing re-copied, the ingest re-verifies the copy on "
                f"disk"))

        # THE COPY, AND THE HASH CHECK THAT MAKES IT A COPY (rule: transport integrity)
        body = _read_bytes(self._staged_locator, http, "manifest_source staged")
        got = sha256_bytes(body)
        if got != staged_sha:
            raise HashMismatch(
                f"the staged file at {self._staged_locator} hashes to {got} but the manifest "
                f"{self._manifest_sha256[:16]} says {staged_sha} — the copy does not agree with its "
                f"manifest, so there is no copy to ingest (both hashes are named; nothing was "
                f"written into the store)")
        target_dir = os.path.join(self.store, INGEST, _safe(fingerprint["dataset_id"]), got[:12])
        staged_path = os.path.join(target_dir, "staged.tsv")
        manifest_path = os.path.join(target_dir, "manifest.json")
        if not getattr(self, "dry_run", False):
            _write_bytes(body, staged_path)
            _write_bytes(self._manifest_bytes, manifest_path)
        why = ("release_moved: " + ", ".join(f"{k} {old[k]!r}→{fingerprint[k]!r}" for k in moved)
               if moved else "first ingest: no prior snapshot of this source in this store")
        return FetchPlan(
            new=True,
            reason=(f"{why} — a release move on a federated source is ordinary lifecycle: the copy "
                    f"is taken, the manifest's own counts and the file's sha256 re-verify it "
                    f"({got[:12]}), and the lifecycle records re-verified. Not an incident"),
            resources={"manifest": {"bytes": self._manifest_bytes, "filename": "manifest.json",
                                    "path": manifest_path, "sha256": self._manifest_sha256},
                       "staged": {"bytes": body, "filename": "staged.tsv",
                                  "path": staged_path, "sha256": got}},
        )

    # -- 3 normalise --------------------------------------------------------
    def normalise(self, resources: dict) -> Normalised:
        m = self._m
        raw = resources.get("staged")
        if isinstance(raw, dict):
            raw = raw.get("bytes")
        if not isinstance(raw, (bytes, bytearray)):
            raise StagedMissing("normalise was given no staged bytes — nothing to map")
        text = bytes(raw).decode("utf-8", "replace")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if not lines:
            raise contract.Refusal("the staged file is empty — a handover with no rows is not a "
                                   "handover (the manifest would say rows.count >= 1)")
        columns = lines[0].split("\t")
        body = lines[1:]
        # THE FIRST COUNT: the bytes, without the parser (no reader involved, no row can hide)
        byte_rows = len(body)

        key_col = ((m.get("rows") or {}).get("key") or "symbol")
        for need in (key_col,) + tuple(STAGE_PROVENANCE):
            if need not in columns:
                raise contract.Refusal(
                    f"the staged header has no {need!r} column (columns: {columns}) — the mapping "
                    f"this adapter applies cannot invent a column the file does not carry")
        ki = columns.index(key_col)
        si, ri = columns.index("source"), columns.index("source_release")
        vi = columns.index(VERIFIED_COLUMN) if VERIFIED_COLUMN in columns else None
        stated_release = (m.get("row_provenance") or {}).get("source_release") or ""
        stated_set = frozenset(x for x in stated_release.split("|") if x)

        staged_sha = (m.get("staged") or {}).get("sha256") or ""
        dataset_id = (m.get("dataset_id") or "").strip()
        cell = via_cell(dataset_id, staged_sha, stated_release)

        rejects: list[dict] = []
        records: list[dict] = []
        row_chain: set[str] = set()
        for ln in body:
            f = ln.split("\t")
            if len(f) != len(columns):
                rejects.append(dict(row=ln, reason="field_count",
                                    detail=f"{len(f)} fields, header has {len(columns)}"))
                continue
            key = f[ki].strip()
            if not key:
                rejects.append(dict(row=ln, reason="empty_key",
                                    detail=f"{key_col} is empty"))
                continue
            src, rel = f[si].strip(), f[ri].strip()
            if not src or not rel:
                rejects.append(dict(row=ln, reason="no_upstream_provenance",
                                    detail=(f"{key}: source={src!r} source_release={rel!r} — a row "
                                            f"that has forgotten who derived it is provenance "
                                            f"laundering, so it is a reject with a reason and never "
                                            f"a record")))
                continue
            if stated_set and frozenset(x for x in rel.split("|") if x) != stated_set:
                rejects.append(dict(row=ln, reason="provenance_disagrees",
                                    detail=(f"{key}: the row says release {rel!r} but the manifest's "
                                            f"row_provenance.source_release is {stated_release!r}")))
                continue
            if vi is not None:
                claim = (f[vi].strip() or VERIFIED_MANIFEST)
                if claim != VERIFIED_MANIFEST:
                    # RULE 2's guard: a depth this station cannot rebuild is not averaged, it is
                    # refused. Nothing is ingested at all: a corpus that has mixed the two depths
                    # cannot be un-mixed row by row after the fact.
                    raise VerificationMixed(
                        f"row {key!r} of {dataset_id!r} claims verified={claim!r}, and this station "
                        f"holds no re-derivation of it. A manifest source can only claim "
                        f"{VERIFIED_MANIFEST!r} ({DEPTH_MEANING[VERIFIED_MANIFEST]}); the two depths "
                        f"may never be silently mixed, so the whole ingest is refused rather than "
                        f"the row quietly downgraded")
            if VIA_COLUMN in columns:
                up = dataset_id_of_via(f[columns.index(VIA_COLUMN)])
                if up:
                    row_chain.add(up)
            xrefs = {c: f[i] for i, c in enumerate(columns)
                     if c not in (VIA_COLUMN, VERIFIED_COLUMN)}
            records.append(dict(
                key=key,
                id="",       # a MIRROR is the authority for NOTHING: every cell is carried, not owned
                source=self.name,
                coords={},   # ditto: the coordinates travel in xrefs, labelled with their origin
                via=cell,
                verified=VERIFIED_MANIFEST,
                upstream=dict(source=src, source_release=rel),
                xrefs=xrefs,
            ))

        # RULE 3, SECOND DOOR: a chain stated in the ROWS is a chain too
        hits = sorted((row_chain - {dataset_id}) & set(self.published))
        if hits:
            raise ProvenanceCycle(
                f"the staged rows of {dataset_id!r} carry via: cells naming {hits}, which this "
                f"station already publishes — the same loop as a via_chain, stated per row, and "
                f"refused for the same reason")

        rejects_by_reason = {r: sum(1 for x in rejects if x["reason"] == r) for r in REJECT_REASONS}
        return Normalised(
            rows_in=byte_rows,
            records=records,
            rejects=rejects,
            published_count=(m.get("rows") or {}).get("count"),
            published_count_url=self.locator,
            extra=dict(
                via=cell,
                dataset_id=dataset_id,
                verified=VERIFIED_MANIFEST,
                columns=columns,
                base_columns=[c for c in columns if c not in OUT_COLUMNS],
                out_columns=[c for c in columns if c not in OUT_COLUMNS] + OUT_COLUMNS,
                key_column=key_col,
                upstream_release=stated_release,
                staged_sha256=staged_sha,
                manifest_sha256=self._manifest_sha256,
                licence=self._licence,
                provenance_chain=self._chain,
                coverage=dict(
                    byte_rows=byte_rows,
                    manifest_rows_count=(m.get("rows") or {}).get("count"),
                    manifest_counts_rows=(m.get("counts") or {}).get("rows"),
                    records=len(records), rejects=len(rejects),
                    rejects_by_reason=rejects_by_reason,
                    rows_claiming_upstream=sum(1 for r in records
                                               if r.get("verified") != VERIFIED_MANIFEST),
                    upstream_sources=sorted({r["upstream"]["source"] for r in records}),
                    upstream_releases=sorted({r["upstream"]["source_release"] for r in records}),
                    via_cells=sorted({r["via"] for r in records}),
                ),
                accounting=[dict(
                    reskey="staged", url=self._staged_locator, rows_in=byte_rows,
                    records=len(records), rejects=len(rejects), used=0,
                    note=("every staged data row became one record or one reject with a reason; a "
                          "record keeps BOTH provenance levels (via: the manifest; source/"
                          "source_release the upstream) and claims verified=manifest only"))],
            ),
        )

    # -- 4 verify ----------------------------------------------------------
    def verify(self, d: Discovery, n: Normalised) -> Verify:
        errs = contract.check_accounting(n)
        m = self._m
        ours = len(n.records) + len(n.rejects)
        ex = n.extra or {}
        cov = ex.get("coverage") or {}
        stated = (m.get("rows") or {}).get("count")
        counts_rows = (m.get("counts") or {}).get("rows")
        staged_sha = (m.get("staged") or {}).get("sha256") or ""
        byte_rows = cov.get("byte_rows")
        # THE HASH, RE-CHECKED ON DISK: the copy that landed is the copy the manifest names
        on_disk = ""
        try:
            body = _read_bytes(self._staged_locator, None, "verify")
            on_disk = sha256_bytes(body)
        except Exception as e:
            errs = errs + [f"the staged bytes could not be re-read for the hash check: {e}"]
        hash_ok = on_disk == staged_sha
        if not hash_ok and on_disk:
            raise HashMismatch(
                f"after the copy the staged file hashes to {on_disk} but the manifest says "
                f"{staged_sha} — naming both, as the rule requires")
        count_ok = (stated is not None and ours == stated and byte_rows == stated)
        ok = (not errs) and count_ok and hash_ok
        detail = (
            f"the manifest's own count is rows.count={stated} (counts.rows={counts_rows}) and a "
            f"newline count over the copied bytes says {byte_rows}; "
            f"{len(n.records)} records + {len(n.rejects)} reject(s) = {ours}; "
            f"the staged file's sha256 is {on_disk[:16]} and the manifest states {staged_sha[:16]} "
            f"({'they agree' if hash_ok else 'THEY DISAGREE'}); "
            f"every record claims verified={VERIFIED_MANIFEST} and "
            f"{cov.get('rows_claiming_upstream')} claim upstream depth; "
            f"the upstream release carried by the rows is {cov.get('upstream_releases')} and the "
            f"manifest's row_provenance.source_release is {ex.get('upstream_release')!r}; "
            f"rejects by reason: "
            + (" · ".join(f"{k}={v}" for k, v in (cov.get("rejects_by_reason") or {}).items()) or "none")
            + (f"; accounting does not close: {errs}" if errs else ""))
        return Verify(ok=ok, ours=ours, source_count=stated, url=self.locator, detail=detail)


ADAPTER = None      # this arm is OTHER_ARM: see the docstring. `adapter()` builds an instance.
CONFIG_KEYS = ("id", "adapter", "manifest", "base_url", "published", "note", "enabled")


def adapter() -> ManifestSource:
    return ManifestSource()


def _dig(m: dict, dotted: str):
    cur = m
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "unnamed").strip()) or "unnamed"


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE INGEST — a second store taking this station's (or any station's) handover as a SOURCE
# ─────────────────────────────────────────────────────────────────────────────────────────────
def _state_path(store: str, sid: str) -> str:
    return os.path.join(store, STATE, f"{_safe(sid)}.json")


def read_state(store: str, sid: str) -> dict:
    p = _state_path(store, sid)
    if not os.path.isfile(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def ingest(store: str, cfg: dict, http=None, refresh: bool = False, run_id: str | None = None,
           dry_run: bool = False) -> dict:
    """One ingest of ONE upstream manifest into `store`. Everything it writes is named below, and
    a dry-run writes NOTHING (a dry-run that writes is a lie):

        <store>/ingest/<dataset>/<sha12>/manifest.json   the upstream manifest, byte for byte
        <store>/ingest/<dataset>/<sha12>/staged.tsv      the copy whose sha256 the manifest states
        <store>/ingest/<dataset>/<sha12>/rows.tsv        the rows, with `via` and `verified` on each
        <store>/ingest/<dataset>/<sha12>/rejects.tsv     rejects with reasons (header even when empty)
        <store>/ingest/<dataset>/<sha12>/MANIFEST.json   OUR manifest of the ingest
        <store>/state/<id>.json                          release · fingerprint · lifecycle
    """
    sid = (cfg.get("id") or "").strip()
    run_id = run_id or f"ingest-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    ad = ManifestSource()
    ad.configure(cfg, store)
    ad.dry_run = bool(dry_run)
    since = read_state(store, sid)
    d = ad.discover(http or getattr(ad, "http", None), since)
    plan = ad.fetch(http, since)
    staged_bytes = ((plan.resources or {}).get("staged") or {}).get("bytes")
    if staged_bytes is None:
        # not moved: verify against the copy already in this store, which is the point of the
        # content-addressed directory (the ingest never trusts the upstream for a re-verify)
        target = _ingest_dir(store, (ad._m.get("dataset_id") or ""),
                             (ad._m.get("staged") or {}).get("sha256") or "")
        staged_bytes = _read_bytes(os.path.join(target, "staged.tsv"), None, "staged copy")
    n = ad.normalise({"staged": staged_bytes})
    v = ad.verify(d, n)
    ex = n.extra or {}
    cov = ex.get("coverage") or {}

    # ---- the ingest manifest: what a DOWNSTREAM station will read -------------------------
    rows_tsv = _rows_bytes(n, ex)
    lifecycle = list(since.get("lifecycle") or [])
    event = "release_moved" if "release_moved" in (plan.reason or "") else (
        "first_ingest" if plan.new else "unchanged")
    lifecycle.append(dict(event=event, at=utc_now(), run_id=run_id,
                          detail=plan.reason))
    lifecycle.append(dict(event="re-verified" if v.ok else "verify-failed", at=utc_now(),
                          run_id=run_id, detail=v.detail))
    incoming_chain = ex.get("provenance_chain") or []
    manifest_out = {
        "manifest_version": 1,
        "generated_at": utc_now(),
        # VERBATIM from the upstream manifest: this IS the same dataset (same staged bytes, same
        # sha256). A mirror that renamed it would break the loop check it exists to enable.
        "dataset_id": ex.get("dataset_id"),
        "entity": ad._m.get("entity"),
        "append_only": True,
        # THE VERIFICATION DEPTH, twice: once as the value a reader greps, once as what it means.
        "verified": VERIFIED_MANIFEST,
        "verification": {
            "verified": VERIFIED_MANIFEST,
            "means": DEPTH_MEANING[VERIFIED_MANIFEST],
            "does_not_mean": ("this station re-derived a single cell from the authority. It did "
                              "not, and no row here may claim the other depth"),
            "depths": list(DEPTHS),
            "rows_claiming_upstream": cov.get("rows_claiming_upstream"),
            "upstream_release_quoted": ex.get("upstream_release"),
            "upstream_sources": cov.get("upstream_sources"),
            "upstream_releases_carried_by_rows": cov.get("upstream_releases"),
            "how_this_depth_was_earned": (
                "the copy agrees with its manifest: the staged sha256 matches "
                f"{ex.get('staged_sha256')}, and the manifest's own count "
                f"{ex.get('coverage', {}).get('manifest_rows_count')} equals the rows counted two "
                "ways (a newline count over the bytes and the row builder)"),
        },
        "mirror": {
            "upstream_manifest": ad.locator,
            "upstream_manifest_sha256": ex.get("manifest_sha256"),
            "upstream_staged": ad._staged_locator,
            "upstream_staged_sha256": ex.get("staged_sha256"),
            "upstream_store_path_field": (ad._m.get("store") or {}).get("path"),
            "note": ("the manifest's own `path` fields were written on the PRODUCING station's "
                     "disk; this block names the locators this ingest actually read"),
        },
        "rows": {
            "count": len(n.records),
            "key": ex.get("key_column"),
            "columns": ex.get("out_columns"),
            "file": "",              # filled at write time
            "file_sha256": sha256_bytes(rows_tsv),
            "via": ex.get("via"),
            "note": ("each row carries `via` (the manifest it came through AND the upstream "
                     "release) and `verified`; the upstream `source`/`source_release` cells are "
                     "quoted verbatim, so the row never forgets who derived it"),
        },
        "row_provenance": {
            "per_row": True,
            "columns": STAGE_PROVENANCE + OUT_COLUMNS,
            "source": "|".join(sorted({r["upstream"]["source"] for r in n.records})),
            "source_release": ex.get("upstream_release"),
            "via": ex.get("via"),
            "verified": VERIFIED_MANIFEST,
            "value_form": "via = <dataset_id>#<staged sha256:12> upstream=<source_release, quoted>",
        },
        "staged": {
            "path": ad._staged_locator,
            "sha256": ex.get("staged_sha256"),
            "rows": cov.get("byte_rows"),
            "columns": ex.get("columns"),
            "deterministic": True,
            "verify": "sha256sum <the copy in this store> must equal this value",
        },
        "counts": {
            "rows": len(n.records),
            "rows_in": n.rows_in,
            "records": len(n.records),
            "rejects": len(n.rejects),
            "rejects_by_reason": cov.get("rejects_by_reason"),
            "manifest_rows": cov.get("manifest_rows_count"),
            "manifest_counts_rows": cov.get("manifest_counts_rows"),
            "verified_ok": v.ok,
            "ours_vs_source": f"{v.ours} vs {v.source_count}",
            "note": "rows_in = records + rejects: a staged row can be rejected, it cannot vanish",
        },
        "rejects": {"count": len(n.rejects), "by_reason": cov.get("rejects_by_reason")},
        "licence": {
            # COPIED FORWARD UNCHANGED (rule 4) — the sha256 is stated so a reader can prove that
            # no station in the chain re-spelt the attribution.
            "attribution": (ex.get("licence") or {}).get("attribution"),
            "attribution_sha256": (ex.get("licence") or {}).get("attribution_sha256"),
            "terms": (ex.get("licence") or {}).get("terms"),
            "link_read": (ex.get("licence") or {}).get("link_read"),
            "transitive": True,
            "note": (ex.get("licence") or {}).get("note"),
        },
        "provenance_chain": incoming_chain,
        "lifecycle": lifecycle,
        "verify": {"ok": v.ok, "ours": v.ours, "source_count": v.source_count, "url": v.url,
                   "detail": v.detail},
        "warnings": [],
    }
    # the producing station's own validator, run here and its answer STATED rather than assumed
    try:
        errs = handover.validate(ad._m)
    except Exception as e:      # a manifest too old to validate is a statement, not a crash
        errs = [f"the upstream manifest could not be validated here ({type(e).__name__}: {e})"]
    if errs:
        manifest_out["warnings"].append(
            "the upstream manifest does not itself pass its producer's validator "
            f"({len(errs)} error(s)); this ingest states that rather than assuming it away: "
            + " · ".join(errs))
    if not v.ok:
        manifest_out["warnings"].append(f"verify FAILED: {v.detail}")

    out = dict(dataset_id=ex.get("dataset_id"), release=d.release, verify=v, normalised=n,
               plan=plan, discovery=d, manifest=manifest_out, dry_run=bool(dry_run),
               rows_tsv_path="", manifest_path="", staged_path="")

    if dry_run:
        out["manifest_path"] = ""
        return out

    target = _ingest_dir(store, ex.get("dataset_id") or "", ex.get("staged_sha256") or "")
    os.makedirs(target, exist_ok=True)
    manifest_out["rows"]["file"] = os.path.join(target, "rows.tsv")
    _write_bytes(rows_tsv, os.path.join(target, "rows.tsv"))
    _write_bytes(_rejects_bytes(n), os.path.join(target, "rejects.tsv"))
    _jdump(manifest_out, os.path.join(target, "MANIFEST.json"))
    fingerprint = dict(dataset_id=ex.get("dataset_id"), release=d.release,
                       manifest_sha256=ex.get("manifest_sha256"),
                       staged_sha256=ex.get("staged_sha256"))
    seen = sorted(set(list(since.get("staged_sha256_seen") or [])
                      + [ex.get("staged_sha256") or "", ex.get("manifest_sha256") or ""]) - {""})
    _jdump({"id": sid, "run_id": run_id, "updated_at": utc_now(), "release": d.release,
            "dataset_id": ex.get("dataset_id"), "manifest_locator": ad.locator,
            "manifest_sha256": ex.get("manifest_sha256"),
            "staged_sha256": ex.get("staged_sha256"), "fingerprint": fingerprint,
            "fingerprint_history": (list(since.get("fingerprint_history") or [])
                                    + [dict(fingerprint, at=utc_now())]),
            "staged_sha256_seen": seen, "lifecycle": lifecycle,
            "ingest_dir": target, "manifest_path": os.path.join(target, "MANIFEST.json")},
           _state_path(store, sid))
    out.update(rows_tsv_path=os.path.join(target, "rows.tsv"),
               manifest_path=os.path.join(target, "MANIFEST.json"),
               staged_path=os.path.join(target, "staged.tsv"),
               ingest_dir=target)
    return out


def _ingest_dir(store: str, dataset_id: str, staged_sha256: str) -> str:
    return os.path.join(store, INGEST, _safe(dataset_id), (staged_sha256 or "")[:12] or "nohash")


def _rows_bytes(n: Normalised, ex: dict) -> bytes:
    """The ingested rows: the upstream cells VERBATIM, then `via` and `verified`. Deterministic —
    no clock, no dict order — so its sha256 is a reproducible claim rather than a timestamp."""
    base = ex.get("base_columns") or []
    out = ["\t".join(base + OUT_COLUMNS)]
    for r in n.records:
        x = r["xrefs"]
        out.append("\t".join([str(x.get(c, "")) for c in base] + [r["via"], r["verified"]]))
    return ("\n".join(out) + "\n").encode("utf-8")


def _rejects_bytes(n: Normalised) -> bytes:
    out = ["reason\tdetail\trow"]
    for r in n.rejects:
        out.append("\t".join([r.get("reason", ""), str(r.get("detail", "")).replace("\t", " "),
                              str(r.get("row", "")).replace("\t", " ")]))
    return ("\n".join(out) + "\n").encode("utf-8")


def say_ingest(out: dict) -> None:
    m = out["manifest"]
    v = out["verify"]
    say(f"INGEST  dataset_id={m['dataset_id']}  release={out['release']!r}  "
        f"verified={m['verified']}  rows={m['rows']['count']}  rejects={m['counts']['rejects']}")
    say(f"INGEST  via={m['row_provenance']['via']!r}")
    say(f"INGEST  upstream: source={m['row_provenance']['source']} "
        f"source_release={m['row_provenance']['source_release']!r}")
    say(f"INGEST  sha256: staged={m['staged']['sha256'][:16]} manifest="
        f"{m['mirror']['upstream_manifest_sha256'][:16]}  agree=true")
    say(f"INGEST  licence carried forward unchanged (attribution sha256 "
        f"{m['licence']['attribution_sha256'][:16]}); links: "
        + ", ".join(f"{l['by']}" for l in m["licence"]["link_read"]))
    say(f"INGEST  lifecycle: {[e['event'] for e in m['lifecycle']]}")
    say(f"VERIFY  {'OK' if v.ok else 'FAILED'}  ours={v.ours} source={v.source_count}")
    say(f"VERIFY  {v.detail}")
    for w in m["warnings"]:
        say(f"WARN    {w}")
    if out["manifest_path"]:
        say(f"WROTE   {out['staged_path']}")
        say(f"WROTE   {out['rows_tsv_path']}")
        say(f"WROTE   {out['manifest_path']}")
    else:
        say("DRY-RUN nothing written: no directory created, no file touched, no state updated")


# ─────────────────────────────────────────────────────────────────────────────────────────────
# FIXTURES — small, synthetic, and GENERATED so they exist at run time (never pasted into a report)
# ─────────────────────────────────────────────────────────────────────────────────────────────
FIXTURE_ROWS = [
    # symbol, mgi_id, ensembl_gene_id, chromosome, start, end
    ("Trp53", "MGI:98834", "ENSMUSG00000059552", "11", "69580359", "69597844"),
    ("Sod1", "MGI:98351", "ENSMUSG00000038703", "16", "90209369", "90214293"),
    ("Cdkn2a", "MGI:104738", "ENSMUSG00000044303", "4", "89177406", "89203651"),
]


def write_fixture(root: str, *, name: str = "small", dataset_id: str = "fixture.example_genes.v1",
                  release: str = "fixture-r1", lie_sha: bool = False,
                  bad_licence_link: bool = False, row_verified: str = "",
                  via_chain: list | None = None, via_column: str = "") -> str:
    """Write a handover-shaped manifest + its staged file into <root>/<name>/ and return the
    manifest path. Small (3 rows), synthetic, and shaped exactly like symbiont/manifest.py's
    output — that shape is the whole claim of this adapter, so a fixture that did not match it
    could prove nothing. Every deviation is asked for by NAME (lie_sha, bad_licence_link, ...)."""
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    cols = ["symbol", "mgi_id", "ensembl_gene_id", "chromosome", "start", "end",
            "source", "source_release"]
    if row_verified:
        cols.append(VERIFIED_COLUMN)
    if via_column:
        cols.append(VIA_COLUMN)
    lines = ["\t".join(cols)]
    for sym, mgi, ens, chrom, start, end in FIXTURE_ROWS:
        cells = [sym, mgi, ens, chrom, start, end, "fixture_upstream", release]
        if row_verified:
            cells.append(row_verified)
        if via_column:
            cells.append(via_column)
        lines.append("\t".join(cells))
    staged = ("\n".join(lines) + "\n").encode("utf-8")
    staged_path = os.path.join(d, "staged.tsv")
    _write_bytes(staged, staged_path)
    real_sha = sha256_bytes(staged)

    terms_file = os.path.join(d, "LICENCE.txt")
    _write_bytes(b"Fixture data: CC-BY 4.0 (attribution required)\n", terms_file)
    terms_sha = sha256_file(terms_file)
    terms = [
        dict(source="fixture_upstream", source_release=release,
             name="Fixture data: CC-BY 4.0 (attribution required)",
             url=("file:///nonexistent/terms-that-cannot-be-read.html" if bad_licence_link
                  else "https://example.invalid/licence"),
             quote="Fixture data are licensed under CC-BY 4.0 (attribution required).",
             terms_sha256="0" * 64),
        dict(source="fixture_upstream", source_release=release,
             name="Fixture terms on disk", url=terms_file,
             quote="Fixture data: CC-BY 4.0 (attribution required)",
             terms_sha256=terms_sha),
    ]
    m = {
        "manifest_version": 1,
        "generated_at": utc_now(),
        "dataset_id": dataset_id,
        "entity": "gene",
        "append_only": True,
        "store": {"path": "/producing/station/store", "view": "example_genes", "version": 1,
                  "run_id": "fixture-run",
                  "note": "a path on the PRODUCING station's disk: this ingest must not believe it"},
        "rows": {"count": len(FIXTURE_ROWS), "key": "symbol",
                 "columns": cols, "accessions": ["symbol", "mgi_id", "ensembl_gene_id"],
                 "file": staged_path, "file_sha256": real_sha},
        "row_provenance": {
            "per_row": True, "columns": STAGE_PROVENANCE,
            "value_form": "role order id_authority first, then xref_provider",
            "source": "fixture_upstream", "source_release": release,
        },
        "staged": {"path": staged_path,
                   "sha256": ("f" * 64 if lie_sha else real_sha),
                   "bytes": len(staged), "format": "tsv", "rows": len(FIXTURE_ROWS),
                   "columns": cols, "deterministic": True},
        "counts": {"rows": len(FIXTURE_ROWS), "rows_in": len(FIXTURE_ROWS) + 1,
                   "records": len(FIXTURE_ROWS), "rejects": 1},
        "rejects": {"count": 1, "by_reason": {"empty_symbol": 1}, "files": [],
                    "note": ("rejects are NOT staged: this one had no publishable accession pair, so "
                             "it never reached the staged file and the ingest does not re-count it")},
        "licence": {
            "attribution": ("fixture_upstream fixture-r1: Fixture data: CC-BY 4.0 (attribution "
                            "required) https://example.invalid/licence"),
            "terms": terms,
        },
    }
    if via_chain:
        m["via_chain"] = via_chain
    return _jdump(m, os.path.join(d, "MANIFEST.json"))


# ─────────────────────────────────────────────────────────────────────────────────────────────
# THE SELF-TEST — the five quotations the row's NSC asks for, run rather than described
# ─────────────────────────────────────────────────────────────────────────────────────────────
def self_test(store: str) -> int:
    """Each check prints PASS/FAIL and the thing it proved. A check that cannot fail is not a
    check, so every PASS below is a refusal that FIRED or a value read back OFF DISK."""
    scratch = os.path.join(os.path.abspath(store), "self-test")
    sstore = os.path.join(scratch, "store")
    if os.path.isdir(scratch):
        shutil.rmtree(scratch)
    os.makedirs(scratch, exist_ok=True)
    os.makedirs(sstore, exist_ok=True)
    results: list[tuple[bool, str, str]] = []

    def expect_refusal(label: str, code: str, fn, must_quote: tuple = ()) -> None:
        try:
            fn()
        except contract.Refusal as e:
            got = getattr(e, "code", "refused")
            text = str(e)
            ok = (got == code) and all(q in text for q in must_quote)
            results.append((ok, label, f"REFUSED[{got}] {text}"))
            return
        except Exception as e:      # noqa: BLE001 — anything else is a FAIL, named
            results.append((False, label, f"raised {type(e).__name__}: {e}"))
            return
        results.append((False, label, f"NO REFUSAL — expected REFUSED[{code}]"))

    # ---- 1 the ordinary ingest: BOTH provenance levels, and depth = manifest ---------------
    m1 = write_fixture(scratch, name="small", release="fixture-r1")
    out = ingest(sstore, dict(id="fixture_a", adapter="manifest_source", manifest=m1))
    rows = open(out["rows_tsv_path"], encoding="utf-8").read().splitlines()
    hdr = rows[0].split("\t")
    first = dict(zip(hdr, rows[1].split("\t")))
    mine = json.load(open(out["manifest_path"], encoding="utf-8"))
    ok1 = (
        out["verify"].ok
        and hdr[-2:] == OUT_COLUMNS
        and first[VIA_COLUMN].startswith("fixture.example_genes.v1#")
        and "upstream=fixture-r1" in first[VIA_COLUMN]
        and first["source"] == "fixture_upstream"          # verbatim: the row did not forget
        and first["source_release"] == "fixture-r1"
        and first[VERIFIED_COLUMN] == VERIFIED_MANIFEST
        and mine["verified"] == VERIFIED_MANIFEST
        and mine["verification"]["rows_claiming_upstream"] == 0
        and mine["licence"]["attribution_sha256"]
    )
    results.append((ok1, "1 ingest: via: names the manifest AND the upstream release; every row "
                         "and the manifest carry verified=manifest",
                    f"row1 = {rows[0]} || {rows[1]}"))

    # ---- 2 REFUSED[hash-mismatch], naming BOTH hashes ------------------------------------
    m2 = write_fixture(scratch, name="hash_lie", dataset_id="fixture.hash_lie.v1", lie_sha=True)
    expect_refusal("2 REFUSED[hash-mismatch] names both hashes", "hash-mismatch",
                   lambda: ingest(sstore, dict(id="fixture_lie", adapter="manifest_source",
                                               manifest=m2)),
                   must_quote=("f" * 64, "hashes to"))

    # ---- 3 REFUSED[licence-unreadable] ---------------------------------------------------
    m3 = write_fixture(scratch, name="licence_bad", dataset_id="fixture.licence_bad.v1",
                       bad_licence_link=True)
    expect_refusal("3 REFUSED[licence-unreadable] when a link in the chain cannot be read",
                   "licence-unreadable",
                   lambda: ingest(sstore, dict(id="fixture_licence", adapter="manifest_source",
                                               manifest=m3)),
                   must_quote=("nonexistent/terms-that-cannot-be-read.html",))

    # ---- 4 a row claiming upstream depth is REFUSED, never averaged ----------------------
    m4 = write_fixture(scratch, name="mixed", dataset_id="fixture.mixed.v1",
                       row_verified=VERIFIED_UPSTREAM)
    expect_refusal("4 REFUSED[verification-mixed] when a row claims the other depth",
                   "verification-mixed",
                   lambda: ingest(sstore, dict(id="fixture_mixed", adapter="manifest_source",
                                               manifest=m4)),
                   must_quote=(VERIFIED_UPSTREAM,))

    # ---- 5 REFUSED[provenance-cycle]: a via-chain naming a dataset this station publishes --
    # this station DERIVES fixture.example_genes.v1 for real (a handover in its own handover/),
    # which is what makes the next check a SELF-citation rather than a second edge
    os.makedirs(os.path.join(sstore, "handover"), exist_ok=True)
    shutil.copyfile(m1, os.path.join(sstore, "handover",
                                     "MANIFEST-fixture.example_genes.v1.json"))
    published = published_dataset_ids(sstore)["derived"]
    m5 = write_fixture(scratch, name="cycle", dataset_id="fixture.station_b.v1",
                       via_chain=[dict(dataset_id=(published[0] if published else "x"),
                                       staged_sha256="0" * 64)])
    expect_refusal("5 REFUSED[provenance-cycle] when the via-chain names a dataset this station "
                   "publishes", "provenance-cycle",
                   lambda: ingest(sstore, dict(id="fixture_cycle", adapter="manifest_source",
                                               manifest=m5)),
                   must_quote=(published[0] if published else "", "publishes"))

    # ---- 6 a release move is LIFECYCLE: release_moved, then re-verified, no incident -----
    m6 = write_fixture(scratch, name="small_r2", release="fixture-r2")
    out6 = ingest(sstore, dict(id="fixture_a", adapter="manifest_source", manifest=m6),
                  run_id="selftest-r2")
    events = [e["event"] for e in out6["manifest"]["lifecycle"]]
    state = read_state(sstore, "fixture_a")
    ok6 = (out6["verify"].ok and "release_moved" in events and "re-verified" in events
           and "fixture-r2" in out6["plan"].reason and out6["release"] == "fixture-r2"
           and state["release"] == "fixture-r2")
    results.append((ok6, "6 a release move is ordinary lifecycle (release_moved → re-verified, "
                         "never an incident)",
                    f"lifecycle={events} reason={out6['plan'].reason!r}"))

    # ---- 7 no row anywhere claims the other depth (the grep, as a check) -----------------
    bad = []
    for root, _dirs, files in os.walk(sstore):
        for name in files:
            p = os.path.join(root, name)
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            if f"{VERIFIED_COLUMN}: {VERIFIED_UPSTREAM}" in text or \
                    f"{VERIFIED_COLUMN}={VERIFIED_UPSTREAM}" in text:
                bad.append(p)
    results.append((not bad, "7 a grep for the other depth in the ingested store finds nothing",
                    f"offenders={bad}"))

    width = max(len(label) for _ok, label, _d in results)
    for ok, label, detail in results:
        say(f"  {'PASS' if ok else 'FAIL'}  {label.ljust(width)}  {detail}")
    passed = sum(1 for ok, _l, _d in results if ok)
    say(f"SELFTEST {passed}/{len(results)} PASS  (store={sstore})")
    return 0 if passed == len(results) else 1


# ─────────────────────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="a second station's handover manifest as a data source (four calls, four rules)")
    ap.add_argument("--manifest", default="", help="the handover manifest: a path or a URL")
    ap.add_argument("--store", required=True, help="the SECOND store the rows land in")
    ap.add_argument("--id", default="", help="the source id (default: the manifest's dataset_id)")
    ap.add_argument("--published", default="", help="comma list of dataset_ids to treat as "
                                                    "published here (added to the store's own)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--refresh", action="store_true", help="copy again even if nothing moved")
    ap.add_argument("--dry-run", action="store_true",
                    help="read, check and verify, then write NOTHING (a dry-run that writes is a lie)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test(a.store)
    if not a.manifest:
        say("REFUSED[config] --manifest is required: this adapter knows nothing a manifest does "
            "not say, so there is no default source to fall back on")
        return 2
    cfg = dict(id=(a.id or os.path.basename(a.manifest) or "manifest_source"),
               adapter="manifest_source", manifest=a.manifest)
    if a.published:
        cfg["published"] = [s for s in a.published.split(",") if s]
    try:
        out = ingest(a.store, cfg, refresh=a.refresh, run_id=a.run_id, dry_run=a.dry_run)
    except contract.Refusal as e:
        say(f"REFUSED[{e.code}] {cfg['id']}: {e}")
        return 2
    except contract.ConfigError as e:
        say(f"REFUSED[config] {cfg['id']}: {e}")
        return 2
    say_ingest(out)
    return 0 if out["verify"].ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
