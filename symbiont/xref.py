"""xref — join the sources on a stable key and keep every disagreement on the record.

Generic over roles, not over sources:
  * an adapter with role "id_authority" owns the primary identifiers and coordinates;
  * an adapter with role "xref_provider" asserts symbols and cross-references.
Nothing here knows the words "ensembl" or "MGI". Add a source with the same roles and the
join runs on the new pair unchanged.

Resolution rules (stated once, applied everywhere; a rule that is not written down is a
conflict waiting to happen):
  R1 one authority id + one provider id on a key        -> PAIR (ok)
  R2 the two sources disagree on a shared namespace id  -> PAIR, authority id wins, CONFLICT logged
  R3 a key with >1 authority id or >1 provider id       -> NO PAIR, CONFLICT logged (many-to-one)
  R4 one provider id claimed by >1 key                  -> NO PAIR, CONFLICT logged (many-to-one, reverse)
  R5 a key the authority publishes that the provider    -> NO PAIR, CONFLICT logged (retired symbol)
     has withdrawn (and folded into another symbol)
  R6 a key the authority publishes that the provider    -> NO PAIR, counted unresolved (no xref)
     does not publish at all
"""
from __future__ import annotations

from dataclasses import dataclass, field

# The five classes this slice can emit. A class that fires zero times is still reported,
# so "no conflicts" is a measurement and not an absence of instrumentation.
CLASSES = (
    "xref_disagreement",
    "many_to_one_authority",
    "many_to_one_provider",
    "many_to_one_reverse",
    "retired_symbol_on_authority_side",
)
UNRESOLVED_REASONS = ("no_provider_match",) + CLASSES


@dataclass
class XrefResult:
    rows: list[dict] = field(default_factory=list)        # the pairs that may be published
    failures: list[dict] = field(default_factory=list)     # every non-published key, with a reason
    unresolved_by_reason: dict = field(default_factory=dict)
    conflict_counts: dict = field(default_factory=dict)
    retired: list[dict] = field(default_factory=list)
    authority_rows: int = 0
    provider_rows: int = 0

    @property
    def unresolved(self) -> int:
        return sum(self.unresolved_by_reason.values())


def _idx(records):
    out: dict[str, list[dict]] = {}
    for r in records:
        out.setdefault(r["key"], []).append(r)
    return out


def join(parts: dict, roles: dict) -> XrefResult:
    """parts: {source_name: Normalised}; roles: {source_name: 'id_authority'|'xref_provider'}."""
    auth_names = [s for s, r in roles.items() if r == "id_authority"]
    prov_names = [s for s, r in roles.items() if r == "xref_provider"]
    if len(auth_names) != 1 or len(prov_names) != 1:
        raise RuntimeError(f"this join needs exactly one authority and one provider, got {roles}")
    an, pn = auth_names[0], prov_names[0]
    auth = _idx(parts[an].records)
    prov = _idx(parts[pn].records)

    retired_map: dict[str, dict] = {}
    for r in (parts[pn].extra or {}).get("retired", []):
        retired_map.setdefault(r["key"], r)

    res = XrefResult(authority_rows=sum(len(v) for v in auth.values()),
                     provider_rows=sum(len(v) for v in prov.values()),
                     unresolved_by_reason={k: 0 for k in UNRESOLVED_REASONS},
                     conflict_counts={k: 0 for k in CLASSES},
                     retired=(parts[pn].extra or {}).get("retired", []))

    prov_id_keys: dict[str, set] = {}
    for key, recs in prov.items():
        for r in recs:
            prov_id_keys.setdefault(r["id"], set()).add(key)

    def fail(key, reason, detail, *recs):
        res.failures.append(dict(key=key, reason=reason, detail=detail,
                                 authority_ids=[r["id"] for r in recs[0]] if recs else [],
                                 provider_ids=[r["id"] for r in recs[1]] if len(recs) > 1 else []))
        if reason in res.unresolved_by_reason:
            res.unresolved_by_reason[reason] += 1
        if reason in res.conflict_counts:
            res.conflict_counts[reason] += 1

    for key, arecs in sorted(auth.items()):
        precs = prov.get(key, [])
        if not precs:
            if key in retired_map:
                r = retired_map[key]
                fail(key, "retired_symbol_on_authority_side",
                     f"{an} still publishes {key!r}; {pn} withdrew it and folded it into "
                     f"{r['current_symbol']!r} ({r['current_id']})", arecs)
            else:
                fail(key, "no_provider_match", f"{pn} publishes no marker under {key!r}", arecs)
            continue
        if len(arecs) > 1:
            fail(key, "many_to_one_authority",
                 f"{an} gives {len(arecs)} ids for one symbol: "
                 f"{', '.join(r['id'] for r in arecs)}", arecs, precs)
            continue
        if len(precs) > 1:
            fail(key, "many_to_one_provider",
                 f"{pn} gives {len(precs)} ids for one symbol: "
                 f"{', '.join(r['id'] for r in precs)}", arecs, precs)
            continue
        a, p = arecs[0], precs[0]
        owners = prov_id_keys.get(p["id"], set())
        if len(owners) > 1:
            fail(key, "many_to_one_reverse",
                 f"{pn} id {p['id']} is claimed by {len(owners)} symbols: "
                 f"{', '.join(sorted(owners))}", arecs, precs)
            continue
        disagree = [(ns, a["xrefs"][ns], p["xrefs"][ns])
                    for ns in a["xrefs"] if ns in p.get("xrefs", {}) and p["xrefs"][ns] != a["xrefs"][ns]]
        row = dict(key=key, authority_id=a["id"], provider_id=p["id"],
                   coords=a.get("coords") or {}, conflict="")
        if disagree:
            res.conflict_counts["xref_disagreement"] += 1
            res.failures.append(dict(key=key, reason="xref_disagreement",
                                     detail="; ".join(f"{ns}: {an}={av} vs {pn}={pv}" for ns, av, pv in disagree),
                                     authority_ids=[a["id"]], provider_ids=[p["id"]]))
            row["conflict"] = "xref_disagreement"   # published anyway: R2, authority wins
        res.rows.append(row)
    return res
