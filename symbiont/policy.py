"""GRANTS AND INHERITANCE — an expedition is a route under the station's policy, and the station's
policy is INHERITED, NEVER WIDENED (req-P2-GRANTS-AND-INHERITANCE-the-stati; owner + reviewer
2026-11-12, proposal §14).

THE PROBLEM THIS FILE EXISTS FOR. An expedition is a route: a named, owner-sanctioned shape of work
that runs for a while and then ends. The station is the floor it runs on. Before this module the only
policy vocabulary was a pair of booleans sprinkled through the config — `enabled` (may this source
run?) and `admit.by_policy` (may a source admit itself?) — read in one place and *implied* in every
other. `enabled: true` is a permission to fetch; `budget.requests` is a permission to spend;
`surfaces` is a permission to look. Once routes multiply, that pair becomes authority laundered by
convenience: a route says `enabled: true` and the station's own limits are silently re-derived by
whoever writes the route. There was no function that answered "what may this route do, and who
decided?".

WHAT REPLACES IT: three ORTHOGONAL grants and ONE inheritance function.

  WRITE      none | propose | acquire | publish
             How far this layer may go with material it does not own. `none` may not write at all,
             `propose` may draft, `acquire` may take a copy, `publish` may make it public. The
             levels are ORDERED, so intersecting is `min()` — but the grant is orthogonal to the
             other two: a route may be allowed to publish and forbidden to spend a single request.

  SPEND      requests / bytes / wall_s / per-host
             What it may cost. Numbers, each with its OWN owner (see `spend_by`), because a number
             without an owner is a rumour the moment two layers disagree.

  ATTENTION  which transitions need a human; DEFAULT ADMIT
             Every transition not named in the map is `admit`. A layer raises a transition to
             `human`; it can never lower another layer's `human` (approvals are UNIONED).

INHERITANCE (`meet(station, expedition)`):
    permissions  INTERSECTED   — the route may do what both allow
    prohibitions UNIONED       — either layer's "no" is the answer
    budgets      MINIMISED     — per key, and per host key
    approvals    UNIONED       — either layer's "ask a human" is the answer
so a route may NARROW what it may do and never widen it. A layer that declares a LARGER budget than
the layer beneath it is CAPPED to the floor's number, and the run record SAYS SO (`caps`, below) —
a silent cap is the same class of defect as no cap.

EVERY REFUSAL NAMES ITS LAYER. An operator who is refused must know whether to edit the route or
argue with the station, so the refusal string is `REFUSED[<reason> by=<layer>]`:
    REFUSED[admission-denied by=station]
    REFUSED[licence-denied by=expedition]
    REFUSED[budget by=station]
    REFUSED[preset-unversioned by=expedition]
`PolicyRefusal` carries `.reason`, `.by` and `.code` (the reason) so a caller can print the line and
write the fields into a run record. `http.py::Fetcher` takes the same attribution (`by`, `spend_by`,
`per_host`), because the ONE place that charges a request before bytes leave is where a budget
refusal has to be honest: a budget respected around the fetcher is a promise, and promises are what
the count check replaces.

NAMED PRESETS ARE VERSIONED AND SHA-PINNED. `supervised-survey.v1` is a NAME with a version and a
digest; `preset()` refuses a bare name (`supervised-survey`) and refuses a number with implied
authority (`level 3`) by name — `REFUSED[preset-unversioned by=...]` — and re-derives each preset's
sha256 against `PRESET_PINS`, so a hand-edited preset body is `REFUSED[preset-tampered]` instead of
silently becoming a grant. Every effective policy records the pins it was built from, so a run can
be explained a year later without reading this file.

FAIL CLOSED. A policy is validated key by key: an unknown key, a missing key, a write level outside
the vocabulary, a licence set that is not a list, a spend number that is not a positive integer, an
attention value outside {human, admit} — each is refused BY NAME with the offending key quoted, at
the layer that declared it. A policy this module cannot read grants NOTHING.

Run (read-only: this module never writes, fetches or spends):
    python3 -m symbiont.policy --station reports/station-policy.yml \
                               --expedition reports/expedition-policy.yml      # the effective policy
    python3 -m symbiont.policy --presets                                     # the pins
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

from . import contract

POLICY_VERSION = 1
LAYER_STATION = "station"
LAYER_EXPEDITION = "expedition"
LAYERS = (LAYER_STATION, LAYER_EXPEDITION)       # station FIRST: it is the floor, and it owns the ties

# ── THE THREE GRANTS, ORTHOGONAL ────────────────────────────────────────────────────────────────
# WRITE is ORDERED (intersecting is min), and each level names the actions it contains, so a caller
# never has to reason about the ordering itself.
WRITE_LEVELS = ("none", "propose", "acquire", "publish")
WRITE_ACTIONS = {
    "none": frozenset(),
    "propose": frozenset({"propose"}),
    "acquire": frozenset({"propose", "acquire"}),
    "publish": frozenset({"propose", "acquire", "publish"}),
}
SPEND_KEYS = ("requests", "bytes", "wall_s")      # the three global counters a fetcher charges
HOST_SPEND_KEYS = ("requests", "bytes")           # wall_s is a run clock, not a per-host one
ATTENTION_VALUES = ("human", "admit")             # `admit` is the DEFAULT for an unnamed transition
POLICY_KEYS = ("version", "layer", "name", "write", "permissions", "prohibitions", "licences",
               "spend", "attention")


# ── THE REFUSAL, WHICH ALWAYS NAMES ITS LAYER ──────────────────────────────────────────────────
class PolicyRefusal(contract.Refusal):
    """A refusal with a REASON and a LAYER. `str()` is the operator-facing line and always reads
    `REFUSED[<reason> by=<station|expedition>] <detail>` — never a bare 'denied'."""

    code = "policy"

    def __init__(self, reason: str, detail: str, by: str):
        self.reason = reason
        self.by = by if by in LAYERS else LAYER_STATION
        self.code = reason
        super().__init__(f"REFUSED[{reason} by={self.by}] {detail}")

    def line(self) -> str:
        return f"REFUSED[{self.reason} by={self.by}]"

    def as_record(self) -> dict:
        return dict(reason=self.reason, by=self.by, detail=str(self))


def _refuse(reason: str, detail: str, by: str):
    raise PolicyRefusal(reason, detail, by)


def canonical(pol: dict) -> bytes:
    """The BYTES a pin is taken over: sorted keys, no whitespace, so a reformat is not a tamper."""
    return json.dumps(pol, sort_keys=True, separators=(",", ":")).encode("utf-8")


def pin(pol: dict) -> str:
    """The sha256 a policy file (or preset body) is pinned by."""
    return hashlib.sha256(canonical(pol)).hexdigest()


# ═══════════════════════════════════════════════════════ 1 · VALIDATION (refuses, never guesses)
def _norm_licence(s: str) -> str:
    return str(s).strip().upper().replace("_", "-")


def _is_pos_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def validate(pol: dict, layer: str, where: str = "") -> dict:
    """Validate ONE layer's policy and return it normalised (sorted lists, normalised licences).

    `layer` is the layer that DECLARED the policy, so a malformed expedition policy is refused
    `by=expedition`: the operator is told whose file to open. Nothing is defaulted except the
    attention map's own declared default (`admit` for a transition the layer does not name).
    """
    if layer not in LAYERS:
        _refuse("policy", f"unknown layer {layer!r} — the layers are {list(LAYERS)}", LAYER_STATION)
    tag = f"{where}: " if where else ""
    if not isinstance(pol, dict):
        _refuse("policy", f"{tag}a policy must be a mapping with the keys {list(POLICY_KEYS)}, "
                          f"got {type(pol).__name__}", layer)
    # `_`-prefixed keys are THIS MODULE's own bookkeeping (the file path and its sha, a preset's
    # name and pin). They are not grants and a policy cannot declare them, so they are carried
    # through untouched rather than being mistaken for unknown keys.
    private = {k: v for k, v in pol.items() if isinstance(k, str) and k.startswith("_") and k not in POLICY_KEYS}
    unknown = [k for k in pol if k not in POLICY_KEYS and not k.startswith("_")]
    if unknown:
        _refuse("policy", f"{tag}unknown key(s) {sorted(unknown)} — the schema is "
                          f"{list(POLICY_KEYS)} (a policy the code does not read is not a grant)",
                layer)
    missing = [k for k in POLICY_KEYS if k not in pol]
    if missing:
        _refuse("policy", f"{tag}missing key(s) {missing} — a policy that does not state its "
                          f"grants grants nothing", layer)

    if pol["version"] != POLICY_VERSION:
        _refuse("policy", f"{tag}unsupported version {pol['version']!r} (this code reads "
                          f"{POLICY_VERSION})", layer)
    if pol["layer"] != layer:
        _refuse("policy", f"{tag}the file says layer={pol['layer']!r} but it is being read as the "
                          f"{layer} policy — a file may not claim another layer's authority", layer)
    if not (isinstance(pol["name"], str) and pol["name"]):
        _refuse("policy", f"{tag}name must be a non-empty string (a policy is NAMED, not anonymous)",
                layer)

    write = pol["write"]
    if write not in WRITE_LEVELS:
        _refuse("write-unknown", f"{tag}write={write!r} is outside the vocabulary "
                                 f"{list(WRITE_LEVELS)}", layer)

    for key in ("permissions", "prohibitions"):
        v = pol[key]
        if not (isinstance(v, list) and all(isinstance(a, str) and a for a in v)):
            _refuse("policy", f"{tag}{key} must be a list of action names, got {v!r}", layer)
        if len(set(v)) != len(v):
            _refuse("policy", f"{tag}{key} names an action twice: {v}", layer)

    lic = pol["licences"]
    if not (isinstance(lic, list) and all(isinstance(x, str) and x for x in lic)):
        _refuse("policy", f"{tag}licences must be a list of licence ids (an EMPTY list means 'none "
                          f"is allowed', never 'any is allowed'), got {lic!r}", layer)

    spend = pol["spend"]
    if not isinstance(spend, dict):
        _refuse("policy", f"{tag}spend must be a mapping with the keys {list(SPEND_KEYS)} and an "
                          f"optional per_host, got {spend!r}", layer)
    unk = [k for k in spend if k not in SPEND_KEYS + ("per_host",)]
    if unk:
        _refuse("policy", f"{tag}spend carries unknown key(s) {sorted(unk)} — the schema is "
                          f"{list(SPEND_KEYS + ('per_host',))}", layer)
    for k in SPEND_KEYS:
        if k not in spend:
            _refuse("policy", f"{tag}spend.{k} is missing — a budget states all three counters "
                              f"{list(SPEND_KEYS)}", layer)
        if spend[k] is not None and not _is_pos_int(spend[k]):
            _refuse("budget", f"{tag}spend.{k} must be null (this layer caps nothing) or a positive "
                              f"integer, got {spend[k]!r}", layer)
    hosts = spend.get("per_host") or {}
    if not isinstance(hosts, dict):
        _refuse("policy", f"{tag}spend.per_host must be a mapping host -> {list(HOST_SPEND_KEYS)}, "
                          f"got {hosts!r}", layer)
    for host, caps in hosts.items():
        if not (isinstance(host, str) and host):
            _refuse("policy", f"{tag}spend.per_host keys are host names, got {host!r}", layer)
        if not isinstance(caps, dict):
            _refuse("policy", f"{tag}spend.per_host[{host!r}] must be a mapping with the keys "
                              f"{list(HOST_SPEND_KEYS)}", layer)
        unk = [k for k in caps if k not in HOST_SPEND_KEYS]
        if unk:
            _refuse("policy", f"{tag}spend.per_host[{host!r}] carries unknown key(s) {sorted(unk)} "
                              f"— the schema is {list(HOST_SPEND_KEYS)}", layer)
        for k, v in caps.items():
            if v is not None and not _is_pos_int(v):
                _refuse("budget", f"{tag}spend.per_host[{host!r}].{k} must be null or a positive "
                                  f"integer, got {v!r}", layer)

    att = pol["attention"]
    if not isinstance(att, dict):
        _refuse("policy", f"{tag}attention must be a mapping transition -> "
                          f"{list(ATTENTION_VALUES)} (an unnamed transition defaults to `admit`)",
                layer)
    for trans, v in att.items():
        if not (isinstance(trans, str) and trans):
            _refuse("policy", f"{tag}attention keys are transition names, got {trans!r}", layer)
        if v not in ATTENTION_VALUES:
            _refuse("attention", f"{tag}attention.{trans}={v!r} is outside "
                                 f"{list(ATTENTION_VALUES)} — a transition either needs a human or "
                                 f"does not, and the default is `admit`", layer)

    out = dict(pol)
    out.update(private)
    out["permissions"] = sorted(pol["permissions"])
    out["prohibitions"] = sorted(pol["prohibitions"])
    out["licences"] = [_norm_licence(x) for x in pol["licences"]]
    out["spend"] = {k: spend[k] for k in SPEND_KEYS}
    out["spend"]["per_host"] = {h: dict(caps) for h, caps in hosts.items()}
    out["attention"] = dict(att)
    return out


def load(path: str, layer: str) -> dict:
    """Read a policy file (YAML) as `layer`'s. Fail closed: missing, unreadable or malformed is
    refused at the layer that owns the file, with the parser's own error NAMED."""
    import yaml
    if not os.path.isfile(path):
        _refuse("policy-missing", f"the {layer} policy file {path} is missing — fail closed: a "
                                  f"policy that is not on disk grants nothing", layer)
    try:
        with open(path, "rb") as fh:
            raw = yaml.safe_load(fh.read().decode("utf-8", "strict"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        _refuse("policy-malformed", f"the {layer} policy file {path} could not be read "
                                    f"({type(e).__name__}: {e}) — fail closed, the error named",
                layer)
    if isinstance(raw, dict) and "policy" in raw:      # a wrapper is allowed, and is then ignored
        raw = raw["policy"]
    out = validate(raw, layer, where=path)
    out["_path"] = os.path.abspath(path)
    with open(path, "rb") as fh:
        out["_file_sha256"] = hashlib.sha256(fh.read()).hexdigest()
    return out


# ═══════════════════════════════════════════════════════ 2 · THE PRESETS (versioned, sha-pinned)
# A preset is a NAMED policy body. Its name carries its VERSION (`supervised-survey.v1`) and its
# body's canonical sha256 is pinned below, so "level 3" is never a number with implied authority and
# a hand-edited preset is a refusal rather than a grant.
PRESETS = {
    "supervised-survey.v1": dict(
        version=1, layer=LAYER_EXPEDITION, name="supervised-survey.v1",
        write="propose",
        permissions=["fetch", "propose"],
        prohibitions=["publish"],          # a survey proposes; publishing is another act entirely
        licences=["CC0", "CC-BY"],
        spend=dict(requests=40, bytes=2_000_000, wall_s=600,
                   per_host={"api.github.com": {"requests": 10, "bytes": 500_000}}),
        attention={"admission": "human", "acquire": "human", "publish": "human"},
    ),
    "station-default.v1": dict(
        version=1, layer=LAYER_STATION, name="station-default.v1",
        write="acquire",
        permissions=["fetch", "admission", "propose", "acquire"],
        prohibitions=["publish"],
        licences=["CC0", "CC-BY", "CC-BY-NC"],
        spend=dict(requests=60, bytes=4_000_000, wall_s=900, per_host={}),
        attention={"admission": "human"},
    ),
    "narrow-reuse.v1": dict(
        version=1, layer=LAYER_EXPEDITION, name="narrow-reuse.v1",
        write="propose",
        permissions=["fetch", "propose"],
        prohibitions=["publish", "admission"],   # reuse only: this route may not admit anything
        licences=["CC0"],
        spend=dict(requests=12, bytes=600_000, wall_s=300,
                   per_host={"api.github.com": {"requests": 4, "bytes": 150_000}}),
        attention={"publish": "human"},
    ),
}

# THE PIN TABLE. Regenerate deliberately (`python3 -m symbiont.policy --presets`) and bump the
# version in the NAME when a body changes: a pin edited to match an edited body is how a preset
# quietly becomes a different grant.
PRESET_PINS = {
    "supervised-survey.v1": "71f183f78ee13f13f63f9eaccdca3736591227951c30b20dfdeaa057967015c5",
    "station-default.v1": "60ca20f9c9160465af2dd6ab3d3d038cddd06cc20f68df5b0d381bf4cc880905",
    "narrow-reuse.v1": "66f2fdae633200fe9745468de4b5bfc4f2150f77145013feed56d84453dd8082",
}

PRESET_VERSION_RE = re.compile(r"^[a-z][a-z0-9-]*\.v[0-9]+$")


def preset(name: str, layer: str = LAYER_EXPEDITION, as_layer: str | None = None) -> dict:
    """Resolve a named preset -> a validated policy body, WITH ITS PIN CHECKED.

    A name that carries no version (`supervised-survey`), a number (`level 3`, `3`) or a version we
    do not ship is refused by name at the layer that asked for it: an unversioned name is a number
    with implied authority.
    """
    asked_by = as_layer or layer
    if not (isinstance(name, str) and PRESET_VERSION_RE.match(name)):
        _refuse("preset-unversioned", f"{name!r} names no version — a preset is "
                                      f"'<name>.v<n>' (e.g. 'supervised-survey.v1'); a number or "
                                      f"a bare name is not a grant", asked_by)
    if name not in PRESETS:
        _refuse("preset-unknown", f"{name!r} is not a shipped preset — the pins on file are "
                                  f"{sorted(PRESETS)}", asked_by)
    body = validate(PRESETS[name], PRESETS[name]["layer"], where=f"preset {name}")
    got = pin(body)
    want = PRESET_PINS.get(name, "")
    if got != want:
        _refuse("preset-tampered", f"preset {name} hashes to {got[:16]}… but its pin is "
                                   f"{want[:16]}… — a preset body edited away from its pin is not "
                                   f"a grant; bump the version in its NAME and re-pin", asked_by)
    body["_preset"] = name
    body["_preset_sha256"] = got
    return body


def pin_table() -> dict:
    """The pins this code holds, with the sha freshly derived from each shipped body."""
    return {name: dict(pin=PRESET_PINS.get(name, ""), derived=pin(validate(PRESETS[name],
                                                                          PRESETS[name]["layer"])),
                       ok=PRESET_PINS.get(name, "") == pin(validate(PRESETS[name],
                                                                    PRESETS[name]["layer"])),
                       layer=PRESETS[name]["layer"])
            for name in sorted(PRESETS)}


# ═══════════════════════════════════════════════════════ 3 · MEET — INHERIT, NEVER WIDEN
def meet(station: dict, expedition: dict | None = None) -> dict:
    """THE INHERITANCE: the effective policy of a route under the station.

        permissions  INTERSECTED
        prohibitions UNIONED
        budgets      MINIMISED (per counter and per host counter)
        approvals    UNIONED (a transition is `human` if EITHER layer says so)

    Returns a JSON-able dict — the thing a run record stores — carrying the effective grants, the
    two layers it was built from (with their file shas), and `caps`: one entry per counter the
    expedition declared LARGER than the station and was therefore capped to the floor's number. A
    silent cap is a defect; `caps` is how the run record says so.
    """
    # THE POSITION DECIDES THE LAYER, never the file: a policy that says `layer: station` while
    # being read as the expedition's is refused (a file may not claim another layer's authority).
    st = validate(station, LAYER_STATION)
    ex = None
    if expedition is not None:
        ex = validate(expedition, LAYER_EXPEDITION)
    layers = [st] + ([ex] if ex else [])

    # PERMISSIONS: intersected. A grant neither layer names does not exist.
    permissions = sorted(set(layers[0]["permissions"]).intersection(*[l["permissions"] for l in layers]))
    # PROHIBITIONS: unioned. Either layer's "no" is the answer.
    prohibitions = sorted(set().union(*[set(l["prohibitions"]) for l in layers]))
    # LICENCES: intersected (a licence set is a permission, not a prohibition).
    licences = sorted(set(layers[0]["licences"]).intersection(*[l["licences"] for l in layers]))
    # WRITE: the lower level (the vocabulary is ordered).
    write = min((l["write"] for l in layers), key=WRITE_LEVELS.index)

    caps: list[dict] = []
    spend: dict = {}
    for k in SPEND_KEYS:
        a = st["spend"][k]
        b = ex["spend"][k] if ex else None
        if ex is None:
            spend[k], owner = a, LAYER_STATION
        elif a is None:
            spend[k], owner = b, LAYER_EXPEDITION
        elif b is None:
            spend[k], owner = a, LAYER_STATION
        elif b < a:
            spend[k], owner = b, LAYER_EXPEDITION
        else:
            spend[k], owner = a, LAYER_STATION
            if b > a:
                caps.append(dict(key=f"spend.{k}", station=a, expedition=b, effective=a,
                                 by=LAYER_STATION))
        spend.setdefault("_owner", {})[k] = owner
    # PER-HOST: the same minimisation, host by host, key by key.
    hosts = sorted(set(st["spend"]["per_host"]) | set((ex or {}).get("spend", {}).get("per_host", {})))
    per_host: dict = {}
    host_owner: dict = {}
    for h in hosts:
        per_host[h] = {}
        for k in HOST_SPEND_KEYS:
            a = (st["spend"]["per_host"].get(h) or {}).get(k)
            b = ((ex or {}).get("spend", {}).get("per_host", {}).get(h) or {}).get(k)
            if ex is None or a is None:
                per_host[h][k], o = (b if ex is not None and a is None else a), (
                    LAYER_EXPEDITION if ex is not None and a is None else LAYER_STATION)
            elif b is None:
                per_host[h][k], o = a, LAYER_STATION
            elif b < a:
                per_host[h][k], o = b, LAYER_EXPEDITION
            else:
                per_host[h][k], o = a, LAYER_STATION
                if b > a:
                    caps.append(dict(key=f"spend.per_host[{h}].{k}", station=a, expedition=b,
                                     effective=a, by=LAYER_STATION))
            host_owner[f"{k}@{h}"] = o
    spend["per_host"] = per_host

    # APPROVALS: unioned. A transition is `human` if EITHER layer says so; the owner of the demand
    # is recorded, because "who do I argue with" applies to a demand as much as to a refusal.
    attention: dict = {}
    attention_by: dict = {}
    for l in layers:
        for trans, v in l["attention"].items():
            if v == "human":
                attention.setdefault(trans, "human")
                attention_by.setdefault(trans, l["layer"])
            else:
                attention.setdefault(trans, "admit")
    for trans, by in attention_by.items():
        attention[trans] = "human"

    owner = dict(spend.pop("_owner"))
    eff = dict(
        version=POLICY_VERSION,
        write=write,
        permissions=permissions,
        prohibitions=prohibitions,
        licences=licences,
        spend=spend,
        attention=attention,
        caps=caps,
        owners=dict(spend_by=owner, per_host_by=host_owner, attention=attention_by,
                    write=next((l["layer"] for l in reversed(layers)
                                if l["write"] == write), LAYER_STATION)), 
        layers={l["layer"]: dict(name=l["name"], write=l["write"],
                                 permissions=list(l["permissions"]),
                                 prohibitions=list(l["prohibitions"]),
                                 licences=list(l["licences"]),
                                 spend=l["spend"], attention=l["attention"],
                                 preset=l.get("_preset", ""), preset_sha256=l.get("_preset_sha256", ""),
                                 path=l.get("_path", ""), file_sha256=l.get("_file_sha256", ""),
                                 policy_sha256=pin(l)) for l in layers},
        presets={l["layer"]: l.get("_preset", "") for l in layers if l.get("_preset")},
    )
    # AN EFFECTIVE POLICY THAT WIDENED SOMETHING IS NOT AN EFFECTIVE POLICY: this is the invariant
    # the module exists for, checked rather than asserted in prose.
    for l in layers:
        if not set(eff["permissions"]).issubset(l["permissions"]):
            _refuse("inherit-widened", f"the effective permissions {eff['permissions']} are not a "
                                       f"subset of {l['layer']}'s {l['permissions']}", l["layer"])
        if not set(eff["prohibitions"]).issuperset(l["prohibitions"]):
            _refuse("inherit-widened", f"the effective prohibitions {eff['prohibitions']} do not "
                                       f"carry {l['layer']}'s {l['prohibitions']}", l["layer"])
    return eff


def run_record(eff: dict, **extra) -> dict:
    """The piece of a RUN RECORD that says what the route was allowed to do, who decided, and which
    number was capped. Plain JSON: a run explained a year from now needs no reader of this file."""
    rec = dict(
        policy_version=eff["version"],
        write=eff["write"],
        permissions=list(eff["permissions"]),
        prohibitions=list(eff["prohibitions"]),
        licences=list(eff["licences"]),
        spend=dict(eff["spend"]),
        attention=dict(eff["attention"]),
        caps=[dict(c) for c in eff["caps"]],
        owners=dict(eff["owners"]),
        layers=dict(eff["layers"]),
        presets=dict(eff["presets"]),
        effective_sha256=pin({k: eff[k] for k in ("write", "permissions", "prohibitions",
                                                  "licences", "spend", "attention")}),
    )
    rec.update(extra)
    return rec


# ═══════════════════════════════════════════════════════ 4 · THE CHECKS — each names its layer
def _layer_denies_action(layer: dict, action: str) -> bool:
    return action in layer["prohibitions"] or action not in layer["permissions"]


def require_action(eff: dict, action: str) -> None:
    """May the effective policy DO <action>? Refused at the layer that denied it — the station when
    the floor forbids it (argue with the station), the expedition when only the route narrowed it
    (edit the route)."""
    for name in LAYERS:
        layer = eff["layers"].get(name)
        if layer and _layer_denies_action(layer, action):
            why = ("is PROHIBITED" if action in layer["prohibitions"]
                   else "is not in its permissions")
            _refuse(f"{action}-denied", f"{action} {why} at the {name} layer "
                                        f"(permissions={layer['permissions']}, "
                                        f"prohibitions={layer['prohibitions']}; the effective "
                                        f"policy allows {eff['permissions']})", name)


def require_write(eff: dict, action: str) -> None:
    """May this route perform the WRITE action (propose/acquire/publish)? An action above the
    effective level is refused at the layer whose level is the lower one."""
    if action in WRITE_ACTIONS[eff["write"]]:
        return
    by = next((name for name in LAYERS
               if eff["layers"].get(name) and action not in WRITE_ACTIONS[eff["layers"][name]["write"]]),
              eff["owners"]["write"])
    _refuse("write-denied", f"{action} needs a WRITE grant above "
                            f"write={eff['write']} (the effective level; "
                            f"{by} holds it at "
                            f"write={eff['layers'].get(by, {}).get('write', '?')})", by)


def require_licence(eff: dict, licence: str) -> None:
    """May this route ingest material under <licence>? Refused at the layer whose SET excludes it —
    which is how an expedition that narrows to CC0/CC-BY refuses a CC-BY-NC source the station alone
    would have allowed."""
    want = _norm_licence(licence)
    if want in eff["licences"]:
        return
    for name in LAYERS:
        layer = eff["layers"].get(name)
        if layer and want not in layer["licences"]:
            _refuse("licence-denied", f"{licence} is not in the {name} layer's licence set "
                                      f"{layer['licences']} (the effective set is "
                                      f"{eff['licences']})", name)
    _refuse("licence-denied", f"{licence} is not in the effective licence set {eff['licences']}",
            LAYER_STATION)


def needs_human(eff: dict, transition: str) -> bool:
    """ATTENTION, default ADMIT: only a transition a layer names as `human` needs one."""
    return eff["attention"].get(transition, "admit") == "human"


def require_attention(eff: dict, transition: str, *, human_present: bool = False) -> None:
    """A transition that needs a human and has none is REFUSED at the layer that demanded one."""
    if not needs_human(eff, transition) or human_present:
        return
    by = eff["owners"]["attention"].get(transition, LAYER_STATION)
    _refuse("attention", f"{transition} needs a human (the {by} layer sets attention.{transition}="
                         f"human; every unnamed transition defaults to `admit`) and none is "
                         f"present", by)


# ═══════════════════════════════════════════════════════ 5 · THE FETCHER BRIDGE (charge before bytes)
def fetcher_kwargs(eff: dict) -> dict:
    """The effective SPEND budget as symbiont/http.py::Fetcher's own arguments.

    The budget is handed to the ONE place that charges a request before bytes leave, together with
    the OWNER of each counter, so the refusal line names the layer whose number was spent:
    `REFUSED[budget by=station]`. A budget respected AROUND the fetcher is a promise; a promise is
    what the count check replaces.
    """
    return dict(budget={k: eff["spend"][k] for k in SPEND_KEYS},
                per_host={h: dict(c) for h, c in eff["spend"]["per_host"].items()},
                spend_by={**eff["owners"]["spend_by"], **eff["owners"].get("per_host_by", {})},
                by=eff["owners"]["spend_by"].get("requests", LAYER_STATION))


def fetcher(eff: dict, rate_limit: dict | None = None, deadline_s: int = 900):
    """A Fetcher that already obeys the effective policy (see `fetcher_kwargs`)."""
    from .http import Fetcher
    return Fetcher(rate_limit=rate_limit, deadline_s=deadline_s, **fetcher_kwargs(eff))


# ═══════════════════════════════════════════════════════ 6 · CLI (read-only)
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="symbiont.policy",
                                 description="grants and inheritance: meet(station, expedition) — "
                                             "permissions intersected, prohibitions unioned, "
                                             "budgets minimised, approvals unioned")
    ap.add_argument("--station", metavar="PATH", help="the station's policy file (YAML)")
    ap.add_argument("--expedition", metavar="PATH", default=None,
                    help="the expedition's policy file (YAML); omit for the station alone")
    ap.add_argument("--presets", action="store_true", help="print the versioned preset pin table")
    a = ap.parse_args(argv)
    try:
        if a.presets:
            for name, p in pin_table().items():
                print(f"PRESET {name:24s} {p['pin'][:16]}…  layer={p['layer']}  "
                      f"{'PINNED' if p['ok'] else 'PIN MISMATCH — ' + p['derived'][:16] + '…'}")
            return 0 if all(p["ok"] for p in pin_table().values()) else 1
        if not a.station:
            ap.error("--station is required (or --presets)")
        eff = meet(load(a.station, LAYER_STATION),
                   load(a.expedition, LAYER_EXPEDITION) if a.expedition else None)
    except PolicyRefusal as e:
        print(str(e))
        return 2
    print(json.dumps(run_record(eff), indent=2, sort_keys=True))
    for c in eff["caps"]:
        print(f"CAPPED {c['key']}: expedition {c['expedition']} > station {c['station']} — "
              f"effective {c['effective']} by={c['by']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
