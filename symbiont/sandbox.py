"""THE RESEARCH LAYER'S SANDBOX — two untrusted things, two allowlists, ENFORCED.

This is layers 2 and 3 of reports/DESIGN-STATION.md §4c — the matrix that says, per layer, what
could go wrong, what the control is, and whether it is enforced or merely printed:

  LAYER 2 · RESEARCH-LAYER ROLES (scout/analyst · triage · explore · exploit · adversary)
            a role reads the whole farm and writes ONE page. It may not write the store, the
            queue or the law. The prohibition is enforced here, not printed in the brief.

  LAYER 3 · DRAFTED ADAPTERS
            an adapter nobody reviewed runs against its source in a THROWAWAY store, and may
            connect only to the base_url of its own sources.yml entry (network allowlist).

WHY THIS FILE EXISTS (measured 2026-11-10): a lane edited nurse/fire-lane.sh at 03:36 and shut
the door for every fire. The lane ran as the user with full filesystem write access. "The brief
says do not" is not a control; the farm's own rule of thumb is PREVENT WHERE THE KERNEL CAN,
DETECT WHERE IT CANNOT. So:

  WRITE allowlist   PREVENTED by the kernel: the whole tree is mounted read-only and only the
                    allowlisted paths are bind-mounted read-write (bubblewrap, a user-namespace
                    mount namespace — no root needed). A write outside the list cannot create a
                    file. It is also NAMED and RECORDED: the child's refusal is parsed and, for
                    python children, an audit hook names the exact path; the run record carries a
                    finding of class `sandbox-breach` (a named class, never a silent repair).

  NETWORK policy    PREVENTED by the kernel: the payload runs in a network namespace with one
                    nftables ruleset (`network_tier` on run(), tiers generated from
                    sources.yml), so a ctypes raw connect(), a static binary or a subprocess
                    with its own resolver is refused by the same rule as everything else. The
                    python guard (`network_guard()`, wrapping socket.getaddrinfo /
                    create_connection / connect) is the informative FIRST LINE: it names the
                    host and the door that was attempted in the run record. MEASURED
                    2026-11-10: the guard alone does NOT catch a ctypes raw connect() —
                    selftest_sandbox F5a proves the connection SUCCEEDS with the guard armed,
                    F5b proves the kernel rule refuses it. A guard is not a boundary; that is
                    why it is no longer described as one.

                    TIER-DEFAULT 'deny-private'  allow the public internet; DENY loopback
                        (127/8, ::1), RFC1918 (10/8, 172.16/12, 192.168/16) and link-local
                        (169.254/16 — which is also the cloud metadata address
                        169.254.169.254, fe80:/10). One ruleset; it stops the three things
                        this farm owns: the model budget on 127.0.0.1:8002, our loopback
                        services (8099 lens, the matrix bridge, mariadb), and most
                        exfiltration.
                    TIER-STRICT   'base-url'     allow exactly the RESOLVED addresses of the
                        adapter's own sources.yml base_url host and deny everything else —
                        generated from the entry, used for DRAFTED adapters.
                    TIER 'off' is the fixture's pole (a namespace with NO ruleset, so that
                    'the rule refused it' is proved against 'nothing refused it'). run()
                    records it as enforced=False and the CLI refuses it: it is not a policy.

  THROWAWAY STORE the drafted adapter is run inside the same confinement with only the
                    throwaway store writable; the store's dir hash is recorded and the store is
                    deleted on exit, so admission never touches the real store.

WHAT THIS FILE DOES NOT DO: it does not wrap every farm lane (that is layer 1, DETECTION, and a
different row). It confines the things the research layer does not trust by construction.

CLI (one definition per operation; the tests and an operator run the same words):
  python3 -m symbiont.sandbox allowlist --class research --report R [--row-path P]...
  python3 -m symbiont.sandbox check-write --class role --report R <path>     # 0 allowed | 126 refused
  python3 -m symbiont.sandbox run --class research --report R [--row-path P]... -- cmd...
  python3 -m symbiont.sandbox run --class adapter --network-tier base-url \
          --sources-config symbiont/sources.yml --source mgi -- cmd...
  python3 -m symbiont.sandbox policy --tier deny-private            # the ruleset, printed
  python3 -m symbiont.sandbox policy --tier base-url --sources-config S --source ID
  python3 -m symbiont.sandbox dirhash <dir>

Exit codes: 0 ok · 2 REFUSED before the work (unknown class, no bwrap, bad allowlist)
            126 REFUSED[sandbox-breach] — the write or the connect was outside the allowlist
            otherwise the child's own exit code, untouched.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
FARM = os.path.dirname(ROOT)

STORE_DIR = os.path.join(ROOT, "store")
QUEUE = os.path.join(FARM, "reports", "STANDING-JOBS.tsv")
DROP_LEDGER = os.path.join(FARM, "reports", "DROP-REASONS.tsv")
ROWS_JOURNAL = os.path.join(FARM, "reports", "rows-journal.log")
LAW = (
    os.path.join(FARM, "CONSTITUTION.md"),
    os.path.join(FARM, "ORCHESTRATION-RULES.md"),
    os.path.join(FARM, "RESEARCH-LAYER.md"),
)
INSTRUMENTS = (os.path.join(FARM, "tools"), os.path.join(FARM, "nurse"))

BREACH_EXIT = 126

# ── THE WRITE-ALLOWLIST, PER LANE CLASS ──────────────────────────────────────────────────────
# A class names the ONLY paths it may write. `row_paths` = what the lane's own row declared.
# `deny` is a hard floor: these are never writable, whatever the class or the row says. The
# research layer's own guardrail (RESEARCH-LAYER.md "RECURSION FLOOR") already says the research
# arm may never edit its governance, the store or the doors — this makes it a control.
LANE_CLASSES = {
    # a RESEARCH LANE writes: its own report · its own row's declared paths · /tmp
    "research": dict(row_paths=True, deny=("store", "queue", "law", "instrument")),
    # a ROLE writes: its own report page · /tmp — nothing else (read-only everywhere else)
    "role": dict(row_paths=False, deny=("store", "queue", "law", "instrument")),
    # a DRAFTED ADAPTER writes: the THROWAWAY store (passed as its declared path) · /tmp —
    # nothing else. The real store cannot be declared: classify() refuses it as `store`.
    "adapter": dict(row_paths=True, deny=("store", "queue", "law", "instrument")),
}


class SandboxRefused(RuntimeError):
    """A refusal BEFORE the work — unknown class, no confinement available, unusable allowlist."""


class SandboxBreach(RuntimeError):
    """A write or a connect outside the allowlist. A named class, never a silent repair."""

    def __init__(self, kind: str, target: str, detail: str):
        super().__init__(f"{kind}: {target} — {detail}")
        self.kind = kind          # "write" | "network"
        self.target = target      # the path or the host
        self.detail = detail


class NetworkRefused(SandboxBreach):
    def __init__(self, host: str, url: str = ""):
        super().__init__("network", host,
                         f"host not in the adapter's network allowlist (url {url or host})")
        self.host = host
        self.url = url


# ── path classification ─────────────────────────────────────────────────────────────────────
def _norm(p: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(p)))


def _under(path: str, root: str) -> bool:
    path, root = _norm(path), _norm(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def classify(path: str) -> str | None:
    """Which protected class does this path belong to (None = ordinary farm path)?

    The names are the refusal vocabulary: a role refused a queue write is refused BY THE NAME
    OF THE CLASS, so the finding can be counted and the farm can see what was attempted."""
    if _under(path, STORE_DIR):
        return "store"
    if any(_norm(path) == _norm(q) for q in (QUEUE, DROP_LEDGER, ROWS_JOURNAL)):
        return "queue"
    if any(_norm(path) == _norm(q) for q in LAW):
        return "law"
    if any(_under(path, d) for d in INSTRUMENTS):
        return "instrument"
    return None


def allowlist(class_name: str, report: str | None = None, row_paths=()) -> list[str]:
    """The resolved write allowlist for a class: report · (row paths) · /tmp.

    Raises SandboxRefused when the class is unknown, when a role is handed row paths (a role
    writes its own page, nothing else), or when a declared path is itself protected — a row may
    not declare the store as its own path, and refusing that HERE keeps the error before work."""
    if class_name not in LANE_CLASSES:
        raise SandboxRefused(f"unknown lane class {class_name!r} — classes: "
                             f"{', '.join(sorted(LANE_CLASSES))}")
    spec = LANE_CLASSES[class_name]
    out: list[str] = []
    if report:
        c = classify(report)
        if c:
            raise SandboxRefused(f"{class_name} lane: its own report {report} is in the protected "
                                 f"class {c!r} — a report is not a queue/store/law write")
        out.append(_norm(report))
    if row_paths:
        if not spec["row_paths"]:
            raise SandboxRefused(
                f"{class_name} lane: a role writes its own report and nothing else — the row's "
                f"declared paths are refused by name ({', '.join(sorted(row_paths))})")
        for p in row_paths:
            c = classify(p)
            if c:
                raise SandboxRefused(f"{class_name} lane: declared path {p} is in the protected "
                                     f"class {c!r} — a row may not declare it as its own")
            out.append(_norm(p))
    out.append("/tmp")
    # dedupe, keep order, drop nested duplicates (a bind of a parent already covers the child)
    seen: list[str] = []
    for p in out:
        if not any(_under(p, s) for s in seen):
            seen.append(p)
    return seen


def check_write(class_name: str, path: str, report: str | None = None, row_paths=()) -> None:
    """Raise SandboxBreach (naming the path AND its class) if `path` is not writable.

    This is the NAME-LEVEL half of the control: it is what a role harness calls before it acts,
    and what the tests assert. The kernel half (run()) is what makes the file absent."""
    allow = allowlist(class_name, report, row_paths)
    c = classify(path)
    if c and c in LANE_CLASSES[class_name]["deny"]:
        raise SandboxBreach("write", path,
                            f"a {class_name} lane may not write the {c} — it is read-only for this "
                            f"class (allowlist: {', '.join(allow)})")
    if any(_under(path, a) for a in allow):
        return
    raise SandboxBreach("write", path,
                        f"outside the {class_name} lane's write allowlist "
                        f"({', '.join(allow)})")


# ── kernel enforcement: the read-only root + the rw bind list ───────────────────────────────
def bwrap_path() -> str | None:
    return shutil.which("bwrap")


def confined_argv(allow: list[str], argv: list[str], *, cwd: str | None = None,
                  extra_env: dict | None = None, scratch_env: str = "/tmp",
                  extra_binds: list[tuple[str, str]] | None = None) -> list[str]:
    """The bubblewrap argv: ro-bind the whole tree, then bind ONLY the allowlist read-write.

    Order matters: `--ro-bind / /` first makes everything read-only; `--tmpfs /tmp` then gives a
    private writable /tmp; a bind AFTER that re-exposes an allowlisted path that lives under
    /tmp. `--die-with-parent` means a killed lane cannot leave an orphan writing anywhere."""
    bwrap = bwrap_path()
    if not bwrap:
        raise SandboxRefused("bwrap (bubblewrap) is not installed — the sandbox cannot confine a "
                             "lane without it, and it will not run one unconﬁned")
    out = [bwrap, "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev", "--proc", "/proc",
           "--tmpfs", scratch_env, "--die-with-parent"]
    for p in allow:
        if not os.path.exists(p):
            raise SandboxRefused(f"allowlisted path {p} does not exist — the sandbox binds only "
                                 f"paths that are there (call _ensure_declared first)")
    # A BLANKET /tmp BIND RE-EXPOSES A TREE THAT LIVES UNDER /tmp (measured 2026-11-11): /tmp is
    # always on the allowlist, so a clone checked out at /tmp/x had every one of its files
    # writable again — the read-only root was undone by the scratch bind (selftest_sandbox F1a
    # wrote reports/SANDBOX-FIXTURE-BREACH.md inside the "sandbox", F4c let a role append to the
    # queue). So /tmp is bound FIRST, the tree is made read-only again AFTER it, and only then the
    # remaining allowlisted paths (a report, a row's declared paths — which may live inside the
    # tree) are bound read-write on top.
    allow_norm = [_norm(p) for p in allow]
    blanket = [p for p, n in zip(allow, allow_norm) if n == "/tmp"]
    rest = [p for p, n in zip(allow, allow_norm) if n != "/tmp"]
    for p in blanket:
        out += ["--bind", p, p]
    if blanket and _under(FARM, "/tmp"):
        out += ["--ro-bind", FARM, FARM]
    for p in rest:
        out += ["--bind", p, p]
    for src, dst in (extra_binds or []):
        out += ["--bind", src, dst]
    for k, v in (extra_env or {}).items():
        out += ["--setenv", k, str(v)]
    out += ["--chdir", cwd or os.getcwd()]
    out += ["--"] + argv
    return out


# ── network allowlist (refused AT CONNECT, naming the host) ─────────────────────────────────
def host_of(url: str) -> str:
    from urllib.parse import urlsplit
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def allowed_hosts_for_entry(entry: dict) -> tuple[set[str], list[str]]:
    """The network allowlist of a sources.yml entry: its base_url, nothing else.

    Returns (allowed, foreign) where `foreign` names endpoint hosts the entry would reach that
    its own base_url does not cover — the sandbox refuses the run rather than silently widening
    the list (the allowlist is the entry's claim, not a wish)."""
    allowed = {h for h in (host_of(entry.get("base_url", "")),) if h}
    foreign: list[str] = []
    for url in (entry.get("endpoints") or {}).values():
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            h = host_of(url)
            if h and h not in allowed:
                foreign.append(h)
    return allowed, foreign


NETPOL_TIERS = ("deny-private", "base-url", "off")
DEFAULT_NETWORK_TIER = "deny-private"

# THE DENY LIST OF TIER-DEFAULT, each range with the name it is refused BY. The label is the
# refusal vocabulary the run record and the docs quote, so a reader never has to guess which
# range a denial came from.
DENY_RANGES = (
    ("ip", "127.0.0.0/8", "loopback"),
    ("ip", "10.0.0.0/8", "RFC1918"),
    ("ip", "172.16.0.0/12", "RFC1918"),
    ("ip", "192.168.0.0/16", "RFC1918"),
    ("ip", "169.254.0.0/16", "link-local"),          # incl. 169.254.169.254 (cloud metadata)
    ("ip6", "::1/128", "loopback"),
    ("ip6", "fe80::/10", "link-local"),
)
NETPOL_TABLE = "symbiont_netpolicy"

# THE EGRESS NOTE. A fresh network namespace has ONE interface — lo — so a run inside it can
# reach nothing public until a deployment gives the namespace a way out (slirp4netns, pasta, or
# a veth from a parent that holds CAP_NET_ADMIN). That is a DEPLOYMENT decision, and the honest
# name for today's state on this box is 'none': stricter than deny-private (nothing public is
# reachable either), never weaker. The record carries it so nobody reads 'enforced' as 'reachable'.
EGRESS_NOTE = ("none — the namespace holds only lo; giving it a way out is the deployment's "
               "decision (see symbiont/docs/NETWORK-POLICY-TIERS.md)")


def ip_in_denied_ranges(addr: str) -> str | None:
    """The DENY range that contains `addr`, or None. A refusal NAMES its range."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(str(addr).split("%")[0])
    except ValueError:
        return None
    for fam, net, _label in DENY_RANGES:
        if ip.version != (6 if fam == "ip6" else 4):
            continue
        try:
            if ip in ipaddress.ip_network(net, strict=False):
                return net
        except ValueError:
            continue
    return None


def label_of_range(net: str) -> str:
    for _fam, n, label in DENY_RANGES:
        if n == net:
            return label
    return "denied"


def _family_of(ip: str) -> str:
    return "ip6" if ":" in str(ip) else "ip"


def resolver_addresses() -> list[str]:
    """The box's configured resolvers (/etc/resolv.conf). Used for ONE narrow exception — see
    nft_ruleset(): DNS to the resolver, port 53 only, because a strict-tier adapter whose
    base_url is a NAME must still be able to resolve it inside the namespace."""
    out: list[str] = []
    try:
        with open("/etc/resolv.conf", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0].lower() == "nameserver":
                    out.append(parts[1])
    except OSError:
        pass
    return out


def _is_ip_literal(h: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(str(h).split("%")[0])
        return True
    except ValueError:
        return False


def resolve_host(host: str, port: int | None = None) -> list[str]:
    """The address set of a host, resolved in the PARENT (the confined run never resolves)."""
    h = (host or "").strip().lower()
    if not h:
        return []
    if _is_ip_literal(h):
        return [h]
    try:
        out = socket.getaddrinfo(h, port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return []
    ips: list[str] = []
    for sa in out:
        try:
            ip = str(sa[4][0]).lower()
        except Exception:
            continue
        if ip not in ips:
            ips.append(ip)
    return ips


def netpolicy(tier: str = DEFAULT_NETWORK_TIER, *, allowed_hosts=(), allowed_ips=(),
              lo_addrs=()) -> dict:
    """Generate one policy tier. The RESULT is the definition; the ruleset is derived from it.

    deny-private   allow the public internet; deny loopback + RFC1918 + link-local (DENY_RANGES).
    base-url       allow exactly the resolved addresses of `allowed_hosts` (the adapter's own
                   sources.yml base_url) and deny everything else.
    off            NO RULESET — the fixture's pole only (proves 'the rule refused it' against
                   'nothing refused it'); run() records it enforced=False. Not a run policy.

    DNS: exactly one narrow exception (resolver address, port 53) and only when the tier has to
    resolve a NAME at run time. The addresses it returns are still subject to the deny rules at
    connect time — the exception widens the DNS door, never the data door. It IS a residual
    channel (DNS exfiltration); a gatekeeper proxy with no other route closes it, and that is
    what a deployment with real tenants should run."""
    if tier not in NETPOL_TIERS:
        raise SandboxRefused(f"unknown network tier {tier!r} — tiers: {', '.join(NETPOL_TIERS)}")
    allowed_hosts = {str(h).lower() for h in allowed_hosts if h}
    ips: list[str] = [str(i) for i in allowed_ips if i]
    dns: list[str] = []
    deny = [dict(family=f, **{"range": n}, label=l) for f, n, l in DENY_RANGES]
    if tier == "off":
        deny = []
        allow_all = True
    elif tier == "deny-private":
        allow_all = True                       # everything not on the deny list is allowed
        names = [h for h in allowed_hosts if not _is_ip_literal(h)]
        if names:
            dns = resolver_addresses()
    else:                                      # base-url
        allow_all = False
        for h in sorted(allowed_hosts):
            got = resolve_host(h)
            if not got:
                raise SandboxRefused(
                    f"base-url tier: the host {h!r} did not resolve — a tier that cannot name "
                    f"the addresses it allows would allow NOTHING, so it refuses before the run")
            for ip in got:
                if ip not in ips:
                    ips.append(ip)
        if not ips:
            raise SandboxRefused("base-url tier needs at least one address to allow — "
                                 "allowed_hosts/allowed_ips were both empty")
        if [h for h in allowed_hosts if not _is_ip_literal(h)]:
            dns = resolver_addresses()
    pol = dict(tier=tier, enforced=(tier != "off"), deny=deny, allow_ips=ips,
               allow_hosts=sorted(allowed_hosts), dns_exception=dns,
               lo_addrs=[str(a) for a in lo_addrs], allow_all=allow_all, egress=EGRESS_NOTE)
    pol["ruleset"] = nft_ruleset(pol)
    return pol


def nft_ruleset(pol: dict) -> str:
    """ONE table, ONE output chain. Every rule carries a machine-readable comment — that comment
    is how a refusal names the range that refused it, read back from the counters after exit."""
    lines = [f"table inet {NETPOL_TABLE} {{", "  chain output {",
             f"    type filter hook output priority 0; policy "
             f"{'accept' if pol.get('allow_all', True) else 'drop'};"]
    for ip in pol.get("dns_exception") or []:
        fam = _family_of(ip)
        for proto in ("udp", "tcp"):
            lines.append(f'    {fam} daddr {ip} {proto} dport 53 counter accept '
                         f'comment "ALLOW dns {ip}:53"')
    for ip in pol.get("allow_ips") or []:
        fam = _family_of(ip)
        lines.append(f'    {fam} daddr {ip} counter accept comment "ALLOW base_url {ip}"')
    if not pol.get("allow_all", True):
        lines.append('    ct state established,related counter accept comment "ALLOW replies"')
    for d in pol.get("deny") or []:
        lines.append(f'    {d["family"]} daddr {d["range"]} counter reject with icmpx type '
                     f'admin-prohibited comment "DENY {d["label"]} {d["range"]}"')
    if not pol.get("allow_all", True):
        lines.append('    counter reject with icmpx type admin-prohibited '
                     'comment "DENY outside-base_url everything"')
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def policy_for_entry(entry: dict, tier: str = "base-url", **kw) -> dict:
    """TIER-STRICT generated from a sources.yml entry: its base_url host, nothing else — the
    `foreign` check is the same one allowed_hosts_for_entry() applies at admission."""
    allowed, foreign = allowed_hosts_for_entry(entry)
    if foreign:
        raise SandboxRefused(
            f"the entry would reach {', '.join(sorted(set(foreign)))} but its base_url is "
            f"{', '.join(sorted(allowed)) or 'nothing'} — a policy is the entry's claim, never "
            f"widened to cover an endpoint it does not name")
    return netpolicy(tier, allowed_hosts=allowed, **kw)


def policy_for_source(config_path: str, source_id: str, tier: str = "base-url", **kw) -> dict:
    """The same, from ACTUAL sources.yml on disk — the one definition site per source."""
    from . import contract
    sources = contract.load_sources(config_path)          # {id: cfg}, validated by the contract
    entry = sources.get(source_id)
    if not entry:
        raise SandboxRefused(f"no source {source_id!r} in {config_path} — a tier is generated "
                             f"from an entry that exists, never from a name that does not")
    return policy_for_entry(entry, tier, **kw)


_COUNTER_RE = re.compile(r'\bcounter packets (?P<packets>\d+) bytes (?P<bytes>\d+) '
                         r'(?:accept|reject|drop)\b[^\n"]*comment "(?P<comment>[^"]*)"')


def read_counters(path: str) -> tuple[list[dict], list[dict]]:
    """(all rules with their packet counts, the DENY rules that fired). The second list is the
    proof that a refusal was the RULE's, not the absence of a listener."""
    text = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return [], []
    rules: list[dict] = []
    for m in _COUNTER_RE.finditer(text):
        comment = m.group("comment")
        parts = comment.split(" ", 2)
        rules.append(dict(rule=comment, kind=parts[0].lower(),
                          label=parts[1] if len(parts) > 1 else "",
                          target=parts[2] if len(parts) > 2 else "",
                          packets=int(m.group("packets"))))
    fired = [r for r in rules if r["kind"] == "deny" and r["packets"] > 0]
    return rules, fired


def _attempted_addresses(text: str) -> list[str]:
    """The addresses a refused child itself reported (its own words name the host). Lines that
    talk about a connect come first: a bind/listen line is evidence too, but it is not the
    attempt, and the record must name the ATTEMPT."""
    lines = (text or "").splitlines()
    ordered = [ln for ln in lines if "connect" in ln.lower()] + lines
    seen: list[str] = []
    for ln in ordered:
        for m in re.finditer(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", ln):
            a = m.group(1)
            if a not in seen:
                seen.append(a)
    return seen


def netns_argv(pol: dict, inner: list[str]) -> list[str]:
    """The kernel wrapper: a user+net namespace (CAP_NET_ADMIN comes free inside it), lo up, the
    tier's ruleset installed, the payload run INSIDE it, the counters written out, its exit
    returned untouched. `inner` is the write-confined argv, so one process tree carries both."""
    unshare, nft, ip = shutil.which("unshare"), shutil.which("nft"), shutil.which("ip")
    missing = [n for n, p in (("unshare", unshare), ("nft", nft), ("ip", ip)) if not p]
    if missing:
        raise SandboxRefused(
            f"the kernel network policy needs {', '.join(missing)} and cannot be enforced "
            f"without it — refusing to run the tier that was asked for (never a silent downgrade "
            f"to the python guard)")
    setup = ["set -u", f"{ip} link set lo up"]
    for a in pol.get("lo_addrs") or []:
        setup.append(f"{ip} addr add {a} dev lo 2>/dev/null || true")
    if pol.get("enforced"):
        setup.append(f'printf "%s\\n" "$SYMBIONT_NETPOL" | {nft} -f -')
    setup.append('"$@"')
    setup.append("rc=$?")
    setup.append(f'[ -n "${{SYMBIONT_NETPOL_COUNTERS:-}}" ] && {nft} list ruleset '
                 f'> "$SYMBIONT_NETPOL_COUNTERS" 2>&1')
    setup.append("exit $rc")
    return [unshare, "--user", "--map-root-user", "--net", "--fork", "--",
            "bash", "-c", "\n".join(setup), "symbiont-netns", *inner]


# ── the python guard: the informative FIRST LINE (it names; the kernel refuses) ─────────────
class _NetworkGuard:
    """Refuse any connect to a host outside `allowed_hosts` — BEFORE the socket leaves the box.

    Wraps socket.getaddrinfo / create_connection / socket.socket.connect, which is the set of
    doors every python client (urllib, http.client, requests, a hand-rolled socket) goes
    through, so a redirect to another host is caught on its own hop, not after the body arrived.
    It does NOT see a ctypes raw connect(), a static binary or a subprocess: that is the kernel's
    job, and this class says so in its own refusal record (via=<the door>)."""

    def __init__(self, allowed_hosts, *, allowed_ips=(), deny_private: bool = False,
                 refusals: list | None = None, log: list | None = None,
                 breach_log: str | None = None, class_name: str = ""):
        self.allowed_hosts = {str(h).lower() for h in allowed_hosts}
        self.allowed_ips = {str(i).lower() for i in allowed_ips}
        self.deny_private = deny_private
        self.refusals = refusals if refusals is not None else []
        self.log = log if log is not None else []
        self.breach_log = breach_log
        self.class_name = class_name
        self.resolved: set[str] = set()
        self._orig = None

    def _refuse(self, host: str, what: str, rng: str | None):
        rec = dict(at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   finding_class="sandbox-breach", kind="network", host=host, via=what,
                   allowed=sorted(self.allowed_hosts))
        if rng:
            rec["range"] = rng
            rec["range_label"] = label_of_range(rng)
        self.refusals.append(rec)
        self.log.append(rec)
        if self.breach_log:
            try:
                with open(self.breach_log, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec) + "\n")
            except Exception:
                pass
        detail = (f"inside the denied range {rng} ({label_of_range(rng)})" if rng
                  else f"host not in the network allowlist "
                       f"({', '.join(sorted(self.allowed_hosts)) or 'nothing'})")
        raise _NetRefused(host, detail, rng)

    def check(self, host, what: str, ip_ok: bool = False) -> None:
        h = str(host or "").lower()
        if not h:
            return
        if ip_ok and h in self.resolved:
            return
        if self.deny_private:
            rng = ip_in_denied_ranges(h)
            if rng:
                self._refuse(h, what, rng)         # named range, always, tier-default
                return
        if h in self.allowed_hosts or (ip_ok and h in self.allowed_ips):
            return
        self._refuse(h, what, None)

    def install(self):
        orig_gai = socket.getaddrinfo
        orig_cc = socket.create_connection
        orig_c = socket.socket.connect
        orig_ce = socket.socket.connect_ex
        self._orig = (orig_gai, orig_cc, orig_c, orig_ce)

        def gai(host, *a, **k):
            self.check(host, "getaddrinfo")
            out = orig_gai(host, *a, **k)
            for sa in out:
                try:
                    ip = str(sa[4][0]).lower()
                except Exception:
                    continue
                if self.deny_private:
                    rng = ip_in_denied_ranges(ip)
                    if rng:                        # a NAME that RESOLVES into the denied space
                        self._refuse(ip, "resolve", rng)
                self.resolved.add(ip)
            return out

        def cc(address, *a, **k):
            if isinstance(address, tuple) and address:
                self.check(address[0], "create_connection")
            return orig_cc(address, *a, **k)

        def c_(self_, address, *a, **k):
            # the socket layer sees the RESOLVED IP, not the name — an IP is acceptable only
            # when it came out of an allowlisted host's own resolution (a bare IP is refused)
            if isinstance(address, tuple) and address:
                self.check(address[0], "connect", ip_ok=True)
            return orig_c(self_, address, *a, **k)

        def ce(self_, address, *a, **k):
            if isinstance(address, tuple) and address:
                self.check(address[0], "connect_ex", ip_ok=True)
            return orig_ce(self_, address, *a, **k)

        socket.getaddrinfo, socket.create_connection = gai, cc
        socket.socket.connect, socket.socket.connect_ex = c_, ce
        return self

    def uninstall(self):
        if self._orig:
            (socket.getaddrinfo, socket.create_connection,
             socket.socket.connect, socket.socket.connect_ex) = self._orig
            self._orig = None


class _NetRefused(NetworkRefused):
    """A guard refusal that carries WHY (the range, or the allowlist it broke) as its detail."""

    def __init__(self, host: str, detail: str, rng: str | None = None):
        super().__init__(host, host)
        self.detail = detail
        self.range = rng


@contextlib.contextmanager
def network_guard(allowed: set[str], refusals: list | None = None, log: list | None = None,
                  *, allowed_ips=(), deny_private: bool = False):
    """Arm the guard for a block (admission's in-process path) — same words as before, plus the
    tier's deny list and the address set, so one definition serves both callers."""
    g = _NetworkGuard(allowed, allowed_ips=allowed_ips, deny_private=deny_private,
                      refusals=refusals, log=log).install()
    try:
        yield g.refusals
    finally:
        g.uninstall()


def install_network_guard(allowed, *, allowed_ips=(), deny_private: bool = False,
                          breach_log: str | None = None, class_name: str = "") -> _NetworkGuard:
    """Arm the guard for the LIFE of a confined child (sitecustomize calls this), so a named
    refusal lands in the run record before the kernel has to refuse anything."""
    return _NetworkGuard(allowed, allowed_ips=allowed_ips, deny_private=deny_private,
                         breach_log=breach_log, class_name=class_name).install()


# ── the python write guard (NAMES the path; the kernel is the enforcement) ──────────────────
_GUARD_SKIP = re.compile(r"(^|/)(__pycache__|\.git)/|\.pyc$")
_GUARD_ALLOW_PREFIX = ("/dev/", "/proc/", "/sys/", "/run/")


def _guard_meta(path: str) -> bool:
    """Things a confined process must still be able to touch to run at all."""
    return (path.startswith(_GUARD_ALLOW_PREFIX) or bool(_GUARD_SKIP.search(path))
            or path in ("/dev/null",))


WRITE_EVENTS = {
    "open": (0, 1),                 # (path, mode/flags)
    "os.mkdir": (0,), "os.rmdir": (0,), "os.remove": (0,), "os.unlink": (0,),
    "os.rename": (0, 1), "os.replace": (0, 1), "os.link": (0, 1), "os.symlink": (0, 1),
    "os.truncate": (0,), "os.chmod": (0,), "os.chown": (0,), "os.utime": (0,),
    "shutil.copyfile": (0, 1), "shutil.copymode": (0, 1),
}


def _is_write_call(event: str, args) -> str | None:
    """Return the target path if this audit event is a WRITE, else None."""
    if event not in WRITE_EVENTS:
        return None
    idx = WRITE_EVENTS[event]
    path = args[idx[0]] if idx and len(args) > idx[0] else None
    if not isinstance(path, (str, bytes, os.PathLike)):
        return None
    path = os.fsdecode(path)
    if event == "open":
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        writing = False
        if isinstance(mode, str):
            writing = any(c in mode for c in "wax+")
        if isinstance(flags, int) and flags:
            writing = writing or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT |
                                               os.O_TRUNC | os.O_APPEND))
        if not writing:
            return None
    return path


def install_write_guard(allow: list[str], breach_log: str, class_name: str) -> None:
    """Install a sys.audit hook that REFUSES (naming the path) any write outside `allow`.

    The kernel already prevents the write (the root is read-only); this makes the refusal
    NAMEABLE and RECORDABLE instead of a bare EROFS the caller has to parse. It raises, so the
    child's operation fails; the caller records a `sandbox-breach` finding."""
    allow = [_norm(p) for p in allow]
    # The guard must speak the KERNEL's rule, not a looser one: a blanket /tmp does not re-open
    # the tree under test (confined_argv re-binds it read-only after the scratch bind), so a
    # clone checked out at /tmp/x is refused there too — and NAMED (measured 2026-11-11).
    explicit = [a for a in allow if a != "/tmp"]
    blanket = "/tmp" in allow
    tree = _norm(FARM)

    def _allowed(full: str) -> bool:
        if _guard_meta(full):
            return True
        if any(_under(full, a) for a in explicit):
            return True
        return bool(blanket and _under(full, "/tmp") and not _under(full, tree))

    def hook(event, args):
        path = _is_write_call(event, args)
        if path is None:
            return
        try:
            full = _norm(path) if os.path.isabs(path) else _norm(os.path.join(os.getcwd(), path))
        except Exception:
            full = path
        if _allowed(full):
            return
        rec = dict(at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   finding_class="sandbox-breach", kind="write", class_name=class_name,
                   path=full, event=event, allowed=allow)
        try:
            with open(breach_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        except Exception:
            pass
        raise SandboxBreach("write", full,
                            f"REFUSED by the {class_name} lane's write allowlist")

    sys.addaudithook(hook)


SITECUSTOMIZE = '''# written by symbiont.sandbox — installs the guards in a confined python child
import os, sys
if os.environ.get("SYMBIONT_SANDBOX_BREACH_LOG"):
    from symbiont.sandbox import install_write_guard
    install_write_guard(os.environ["SYMBIONT_SANDBOX_ALLOW"].split(os.pathsep),
                        os.environ["SYMBIONT_SANDBOX_BREACH_LOG"],
                        os.environ.get("SYMBIONT_SANDBOX_CLASS", "lane"))
if (os.environ.get("SYMBIONT_NETPOL_GUARD", "0") == "1"
        and os.environ.get("SYMBIONT_SANDBOX_BREACH_LOG")):
    # the informative FIRST line: it NAMES the host and the door; the kernel is what refuses
    from symbiont.sandbox import install_network_guard
    install_network_guard(os.environ.get("SYMBIONT_NETPOL_ALLOWED", "").split(os.pathsep),
                          allowed_ips=os.environ.get("SYMBIONT_NETPOL_ALLOWED_IPS", "").split(os.pathsep),
                          deny_private=os.environ.get("SYMBIONT_NETPOL_DENY_PRIVATE") == "1",
                          breach_log=os.environ["SYMBIONT_SANDBOX_BREACH_LOG"],
                          class_name=os.environ.get("SYMBIONT_SANDBOX_CLASS", "lane"))
'''


# ── the run ────────────────────────────────────────────────────────────────────────────────
def dirhash(root: str) -> str:
    """A deterministic hash of a directory tree: relpath + file sha256, sorted. No mtimes, so
    two runs that leave the same BYTES hash the same."""
    h = hashlib.sha256()
    if not os.path.isdir(root):
        return "MISSING"
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for name in sorted(fns):
            p = os.path.join(dp, name)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            h.update(rel.encode("utf-8", "surrogateescape"))
            h.update(b"\0")
            if os.path.islink(p):
                h.update(b"L" + os.readlink(p).encode("utf-8", "surrogateescape"))
            else:
                with open(p, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
            h.update(b"\0")
    return h.hexdigest()


def _ensure_declared(p: str) -> None:
    """A declared write path that does not exist yet is created as an EMPTY FILE (its parent
    directory made if needed) — the bind then covers exactly that path, never its siblings.
    /tmp is never created: it is the kernel's own scratch path."""
    if p == "/tmp" or os.path.exists(p):
        return
    parent = os.path.dirname(p) or "."
    os.makedirs(parent, exist_ok=True)
    with open(p, "a", encoding="utf-8"):
        pass


def _parse_erofs(stderr: str) -> list[str]:
    """For non-python children the kernel's message is the only name we get: pull the path out."""
    out = []
    for m in re.finditer(r"(['\"]?)(/[^'\"\n:]+)\1: Read-only file system", stderr):
        p = m.group(2).strip()
        if p and p not in out:
            out.append(p)
    return out


def run(argv: list[str], *, class_name: str, report: str | None = None, row_paths=(),
        cwd: str | None = None, env: dict | None = None, runs_dir: str | None = None,
        label: str | None = None, extra_env: dict | None = None,
        capture_stdout: bool = False, network: dict | None = None,
        network_guard_armed: bool = True) -> dict:
    """Run `argv` inside the write allowlist of `class_name`. Returns the run record.

    Refuses before the work if the class is unknown or bwrap is unavailable (never runs a lane
    unconﬁned and calls it sandboxed). A detected write refusal returns exit 126 with a
    `sandbox-breach` finding naming the path — the file is not there, because the kernel said no.

    `network` is a POLICY TIER (netpolicy() / policy_for_entry() / policy_for_source()). When it
    is given the payload runs inside a network namespace with that nftables ruleset, and a
    connection the ruleset denied comes back as a `sandbox-breach` finding that NAMES THE RANGE
    (read from the kernel's own counters, not guessed). The python guard is armed inside the
    child as the first line when `network_guard_armed` — turning it off is how the fixture
    proves the kernel refuses a ctypes raw connect() the guard never sees."""
    allow = allowlist(class_name, report, row_paths)
    for p in allow:                       # a declared path must exist before it can be bound
        _ensure_declared(p)
    meta = tempfile.mkdtemp(prefix="symbiont-sandbox-meta-")
    breach_log = os.path.join(meta, "breach.jsonl")
    guard_dir = os.path.join(meta, "guard")
    os.makedirs(guard_dir, exist_ok=True)
    with open(os.path.join(guard_dir, "sitecustomize.py"), "w", encoding="utf-8") as fh:
        fh.write(SITECUSTOMIZE)
    child_env = dict(env if env is not None else os.environ)
    child_env.update(
        PYTHONPATH=os.pathsep.join([FARM, guard_dir] + [p for p in
                                                        (child_env.get("PYTHONPATH") or "").split(os.pathsep) if p]),
        SYMBIONT_SANDBOX_ALLOW=os.pathsep.join(allow),
        SYMBIONT_SANDBOX_BREACH_LOG=os.path.join("/tmp/sbx-meta", "breach.jsonl"),
        SYMBIONT_SANDBOX_CLASS=class_name,
    )
    pass_env = {
        "SYMBIONT_SANDBOX_ALLOW": child_env["SYMBIONT_SANDBOX_ALLOW"],
        "SYMBIONT_SANDBOX_BREACH_LOG": child_env["SYMBIONT_SANDBOX_BREACH_LOG"],
        "SYMBIONT_SANDBOX_CLASS": class_name,
        "PYTHONPATH": child_env["PYTHONPATH"],
    }
    pass_env.update({k: str(v) for k, v in (extra_env or {}).items()})
    argv_full = confined_argv(allow, argv, cwd=cwd, extra_env=pass_env)
    # expose the meta dir (breach log) inside the namespace, read-write, at /sbx-meta
    argv_full = argv_full[:argv_full.index("--")] + ["--bind", meta, "/tmp/sbx-meta"] + \
        argv_full[argv_full.index("--"):]
    # ── THE NETWORK TIER: the kernel wrapper goes OUTSIDE the write confinement, so one process
    # tree carries both controls (a net namespace holds CAP_NET_ADMIN inside itself, and bwrap
    # keeps it — measured). The counter file lives in the PARENT's /tmp: same mount namespace,
    # different network namespace, so it is readable after the namespace is gone.
    counters_path = ""
    parent_env = dict(os.environ)
    if network:
        pass_env.update({
            "SYMBIONT_NETPOL_GUARD": "1" if network_guard_armed else "0",
            "SYMBIONT_NETPOL_ALLOWED": os.pathsep.join(network.get("allow_hosts") or []),
            "SYMBIONT_NETPOL_ALLOWED_IPS": os.pathsep.join(network.get("allow_ips") or []),
            "SYMBIONT_NETPOL_DENY_PRIVATE": "1" if network.get("tier") == "deny-private"
                                            else "0",
        })
        argv_full = confined_argv(allow, argv, cwd=cwd, extra_env=pass_env)
        argv_full = argv_full[:argv_full.index("--")] + ["--bind", meta, "/tmp/sbx-meta"] + \
            argv_full[argv_full.index("--"):]
        counters_path = os.path.join(meta, "netpol.counters")
        parent_env.update(SYMBIONT_NETPOL=network.get("ruleset", ""),
                          SYMBIONT_NETPOL_COUNTERS=counters_path)
        argv_full = netns_argv(network, argv_full)
    t0 = time.time()
    proc = subprocess.run(argv_full, capture_output=True, text=True, env=parent_env)
    wall = round(time.time() - t0, 1)

    findings: list[dict] = []
    for line in (open(breach_log, encoding="utf-8").read().splitlines()
                 if os.path.isfile(breach_log) else []):
        try:
            findings.append(json.loads(line))
        except Exception:
            pass
    if not findings:                       # a bash child: the kernel's own words name the path
        for p in _parse_erofs(proc.stderr):
            findings.append(dict(at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 finding_class="sandbox-breach", kind="write",
                                 class_name=class_name, path=p, event="erofs", allowed=allow))
    net = None
    if network:
        rules, fired = read_counters(counters_path)
        attempted = _attempted_addresses((proc.stderr or "") + "\n" + 
                                          (proc.stdout or ""))
        for r in fired:
            host = ""
            for a in attempted:
                if r["target"] == "everything" or ip_in_denied_ranges(a) == r["target"]:
                    host = a
                    break
            f = dict(at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     finding_class="sandbox-breach", kind="network", via="kernel-nftables",
                     tier=network.get("tier"), range=r["target"], range_label=r["label"],
                     packets=r["packets"], allowed=sorted(network.get("allow_ips") or []))
            if host:
                f["host"] = host
            findings.append(f)
        net = dict(tier=network.get("tier"), enforced=bool(network.get("enforced")),
                   deny=network.get("deny") or [], allow_ips=network.get("allow_ips") or [],
                   allow_hosts=network.get("allow_hosts") or [],
                   dns_exception=network.get("dns_exception") or [],
                   ruleset=network.get("ruleset", ""), egress=network.get("egress"),
                   guard=("armed" if (network.get("enforced") and network_guard_armed)
                          else "off"),
                   counters=rules, denied=fired)
    rec = dict(run_id=f"{label or class_name}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-"
                      f"{os.getpid()}-{uuid.uuid4().hex[:6]}",
               at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), lane_class=class_name,
               argv=argv, cwd=cwd or os.getcwd(), allowlist=allow, wall_s=wall,
               exit=proc.returncode, stderr_tail=proc.stderr.strip().splitlines()[-3:],
               findings=findings, breach=bool(findings), confinement=bwrap_path(),
               network=net)
    if capture_stdout:
        rec["stdout"] = proc.stdout
        rec["stderr"] = proc.stderr
    if findings:
        rec["exit"] = BREACH_EXIT
    rec["record"] = _write_record(rec, runs_dir)
    shutil.rmtree(meta, ignore_errors=True)
    return rec


def _write_record(rec: dict, runs_dir: str | None) -> str:
    d = runs_dir or os.environ.get("SYMBIONT_SANDBOX_RUNS_DIR")
    if not d:
        return ""
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{rec['run_id']}.json")
    tmp = p + ".tmp"
    kept = {k: v for k, v in rec.items() if k not in ("stdout", "stderr")}
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(kept, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, p)
    return p


# ── CLI ────────────────────────────────────────────────────────────────────────────────────
def _print_allow(allow: list[str]) -> None:
    for p in allow:
        print(f"ALLOW     {p}")


def _cli_policy(a) -> dict:
    """The tier the CLI was asked for, generated the same way the API generates it."""
    tier = a.tier if a.sub == "policy" else a.network_tier
    if tier == "off":
        raise SandboxRefused("'off' is the fixture's pole (a namespace with NO ruleset) and is "
                             "never a run policy — it exists so that 'the rule refused it' can "
                             "be proved against 'nothing refused it'")
    if getattr(a, "source", None):
        cfg = a.sources_config or os.path.join(ROOT, "sources.yml")
        return policy_for_source(cfg, a.source, tier)
    if tier == "base-url":
        hosts = [host_of(h) if h.startswith(("http://", "https://")) else h
                 for h in (a.allow_host or [])]
        if not hosts:
            raise SandboxRefused("the base-url tier needs --source ID (generated from "
                                 "sources.yml) or --allow-host HOST — an allowlist of nothing "
                                 "allows nothing")
        return netpolicy(tier, allowed_hosts=hosts)
    return netpolicy(tier)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="symbiont.sandbox",
                                 description="the research layer's sandbox: write allowlist, "
                                             "network allowlist, throwaway stores, role pages")
    sub = ap.add_subparsers(dest="sub", required=True)

    p = sub.add_parser("allowlist")
    p.add_argument("--class", dest="class_name", required=True)
    p.add_argument("--report", default=None)
    p.add_argument("--row-path", action="append", default=[])

    p = sub.add_parser("check-write")
    p.add_argument("--class", dest="class_name", required=True)
    p.add_argument("--report", default=None)
    p.add_argument("--row-path", action="append", default=[])
    p.add_argument("paths", nargs="+")

    p = sub.add_parser("run")
    p.add_argument("--class", dest="class_name", required=True)
    p.add_argument("--report", default=None)
    p.add_argument("--row-path", action="append", default=[])
    p.add_argument("--runs-dir", default=None)
    p.add_argument("--cwd", default=None)
    p.add_argument("--network-tier", dest="network_tier", default=None,
                   choices=[t for t in NETPOL_TIERS if t != "off"] + ["none"])
    p.add_argument("--sources-config", default=None)
    p.add_argument("--source", default=None)
    p.add_argument("--allow-host", action="append", default=[])
    p.add_argument("--guard", dest="guard_armed", default="on", choices=["on", "off"])
    p.add_argument("cmd", nargs=argparse.REMAINDER)

    p = sub.add_parser("policy")
    p.add_argument("--tier", required=True, choices=list(NETPOL_TIERS))
    p.add_argument("--sources-config", default=None)
    p.add_argument("--source", default=None)
    p.add_argument("--allow-host", action="append", default=[])

    p = sub.add_parser("dirhash")
    p.add_argument("dir")

    a = ap.parse_args(argv)
    if a.sub == "allowlist":
        try:
            _print_allow(allowlist(a.class_name, a.report, a.row_path))
        except SandboxRefused as e:
            print(f"REFUSED[class] {e}")
            return 2
        return 0
    if a.sub == "check-write":
        rc = 0
        for path in a.paths:
            try:
                check_write(a.class_name, path, a.report, a.row_path)
                print(f"ALLOWED   {path}")
            except SandboxBreach as e:
                print(f"REFUSED[sandbox-breach] {e.target} — {e.detail}")
                rc = BREACH_EXIT
            except SandboxRefused as e:
                print(f"REFUSED[class] {e}")
                return 2
        return rc
    if a.sub == "dirhash":
        print(dirhash(a.dir))
        return 0
    if a.sub == "policy":
        try:
            pol = _cli_policy(a)
        except SandboxRefused as e:
            print(f"REFUSED[policy] {e}")
            return 2
        verdict = "yes" if pol["enforced"] else "NO (the fixture's pole, not a run policy)"
        print(f"TIER      {pol['tier']}  enforced={verdict}")
        print(f"ALLOW     base_url {', '.join(pol['allow_ips']) or '—'}"
              f"   hosts {', '.join(pol['allow_hosts']) or '—'}")
        print(f"DENY      " + (" · ".join(f"{d['label']} {d['range']}" for d in pol['deny'])
                             or "—"))
        print(f"DNS       " + (", ".join(f"{d}:53" for d in pol['dns_exception'])
                             or "no exception"))
        print(f"EGRESS    {pol['egress']}")
        print("RULESET")
        sys.stdout.write(pol["ruleset"])
        return 0
    if a.sub == "run":
        cmd = list(a.cmd)
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        if not cmd:
            print("REFUSED[bad-input] run: no command after --")
            return 2
        try:
            network = _cli_policy(a) if a.network_tier else None
        except SandboxRefused as e:
            print(f"REFUSED[policy] {e}")
            return 2
        try:
            rec = run(cmd, class_name=a.class_name, report=a.report, row_paths=a.row_path,
                      cwd=a.cwd, runs_dir=a.runs_dir, network=network,
                      network_guard_armed=(a.guard_armed != "off"))
        except SandboxRefused as e:
            print(f"REFUSED[unconfined] {e}")
            return 2
        for f in rec["findings"]:
            print(f"REFUSED[sandbox-breach] {f.get('kind')} {f.get('path') or f.get('host')} — "
                  f"outside the {a.class_name} lane's allowlist"
                  + (f" [range {f['range']} {f.get('range_label')}, {f.get('via')}]"
                     if f.get("range") else ""))
        if network:
            print(f"POLICY    tier {network['tier']} enforced="
                  f"{'yes' if network['enforced'] else 'NO'} guard="
                  f"{'on' if a.guard_armed != 'off' else 'off'} egress: {network['egress']}")
        if rec.get("record"):
            print(f"RECORD    {rec['record']}")
        return rec["exit"]
    return 2


if __name__ == "__main__":
    sys.exit(main())
