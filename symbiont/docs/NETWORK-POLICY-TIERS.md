# NETWORK POLICY TIERS — who the policy protects, per deployment

req-P1-NETWORK-POLICY-TIERS. Owner, 2026-11-10: *"why do we care so much about 'this host'… could
be very clear on this host — if GeneNetwork was running it on their own, but someone could decide
to set up a training station on their machine"*. This page is that answer, and it exists because
the earlier text called a python-level guard a boundary.

## 0 · What was true before this page (measured, not remembered)

`symbiont/sandbox.py` wrapped `socket.getaddrinfo` / `create_connection` / `socket.socket.connect`
— a **PYTHON-LEVEL GUARD**. It catches urllib, http.client, requests, and a hand-rolled socket. It
does **not** catch a `ctypes` raw `connect()`, a static binary, or a subprocess with its own
resolver. `make check` proved the first half and nobody had tried the second. Run
`python3 -m symbiont.selftest_sandbox` and read **F5a**: a fixture that issues the connect as a
raw libc call to `127.0.0.1:8002` **succeeds** with the guard armed. So:

> The guard is the informative FIRST LINE (it names the host and the door that was attempted).
> The **kernel** is what refuses. A guard described as a boundary is a lie with good intentions.

## 1 · The two tiers (both generated from `sources.yml`)

| tier | allow | deny | used for |
|---|---|---|---|
| **`deny-private`** (TIER-DEFAULT) | the public internet | loopback `127.0.0.0/8`, `::1/128`; RFC1918 `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`; link-local `169.254.0.0/16` (incl. the cloud metadata address `169.254.169.254`), `fe80::/10` | any run that is not a drafted adapter |
| **`base-url`** (TIER-STRICT) | exactly the **resolved addresses** of the adapter's own `sources.yml` `base_url` host | everything else, including every range above | **drafted** adapters |

Print either one, as the run will install it:

```console
$ python3 -m symbiont.sandbox policy --tier deny-private
TIER      deny-private  enforced=yes
DENY      loopback 127.0.0.0/8 · RFC1918 10.0.0.0/8 · RFC1918 172.16.0.0/12 ·
          RFC1918 192.168.0.0/16 · link-local 169.254.0.0/16 · loopback ::1/128 ·
          link-local fe80::/10
RULESET
table inet symbiont_netpolicy {
  chain output {
    type filter hook output priority 0; policy accept;
    ip daddr 127.0.0.0/8 counter reject with icmpx type admin-prohibited comment "DENY loopback 127.0.0.0/8"
    ip daddr 10.0.0.0/8 counter reject with icmpx type admin-prohibited comment "DENY RFC1918 10.0.0.0/8"
    ip daddr 172.16.0.0/12 counter reject with icmpx type admin-prohibited comment "DENY RFC1918 172.16.0.0/12"
    ip daddr 192.168.0.0/16 counter reject with icmpx type admin-prohibited comment "DENY RFC1918 192.168.0.0/16"
    ip daddr 169.254.0.0/16 counter reject with icmpx type admin-prohibited comment "DENY link-local 169.254.0.0/16"
    ip6 daddr ::1/128 counter reject with icmpx type admin-prohibited comment "DENY loopback ::1/128"
    ip6 daddr fe80::/10 counter reject with icmpx type admin-prohibited comment "DENY link-local fe80::/10"
  }
}
```

```console
$ python3 -m symbiont.sandbox policy --tier base-url --source mgi
TIER      base-url  enforced=yes
ALLOW     base_url 34.102.200.12   hosts www.informatics.jax.org
DENY      (the same ranges) + "DENY outside-base_url everything"
DNS       8.8.8.8:53
RULESET   … policy drop; accept 34.102.200.12; accept replies; the seven DENY rules; catch-all
```

**How it is enforced.** The payload runs inside a network namespace
(`unshare --user --map-root-user --net`, where `CAP_NET_ADMIN` comes free) with the ruleset above
installed by `nft`. There is no second route out of that namespace. The python guard is armed
inside the same child when the child is python; the nft `counter` on each rule is what proves a
refusal was *the rule's* and not a missing listener — the run record reads those counters back and
names the range (`"range": "192.168.0.0/16", "range_label": "RFC1918", "packets": 2`).

**One named hole: DNS.** When the tier has to resolve a *name* at run time (`base-url` on a
hostname), exactly the resolver's address is allowed on port **53** — nothing else. The addresses
it returns are still subject to the deny rules at connect time. This is a residual channel (DNS
exfiltration); a deployment with real tenants should run the gatekeeper proxy below instead.

**What a namespace does NOT have: a way out.** A fresh namespace holds only `lo`. Handing it the
public internet is a *deployment* decision (slirp4netns, pasta, or a veth from a parent holding
`CAP_NET_ADMIN`), and until a deployment makes one, a sandboxed run has **no egress at all** —
stricter than `deny-private`, never weaker. Every run record says so:
`network.egress = "none — the namespace holds only lo…"`. Do not read `enforced: yes` as
`reachable: yes`.

## 2 · The three deployments — WHO this protects

Each case names the policy it is tested with. The ranges quoted are the ones the fixture battery
asserts (`selftest_sandbox` F5/F6/F7 and F8, which reads this page and refuses if a case stops
quoting its policy).

### (a) WE HOST — tenants' adapters are untrusted relative to EACH OTHER

Policy: **`deny-private` is mandatory, plus per-tenant egress** (`base-url` for the tenant's own
declared sources, and a per-tenant namespace or gatekeeper proxy beyond that). Denied ranges, as
tested: `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` (+ `169.254.0.0/16`,
`::1/128`, `fe80::/10`).

Who is protected: **the farm's own services and the other tenants.** Without `deny-private`, one
tenant's adapter reaches `127.0.0.1:8002` (our model budget), `127.0.0.1:8099` (lens), the matrix
bridge, mariadb — and any other tenant's store on the same box. The threat is not malice; it is a
tenant's adapter with a config typo and a loopback URL. Per-tenant egress is what stops tenant A
reading tenant B's *remote* data through the shared farm identity; `deny-private` is what stops
them reading the box.

### (b) GN HOSTS — the adapter author is them, but their keys are still on that box

Policy: **`deny-private` is the sane default** — allow the public internet, deny loopback
`127.0.0.0/8` and `::1/128`, RFC1918 `10.0.0.0/8` / `172.16.0.0/12` / `192.168.0.0/16`, and
link-local `169.254.0.0/16` / `fe80::/10`. `base-url` is available for adapters they have not
reviewed.

Who is protected: **their own services and keys.** The author is trusted, the *code path* is not:
a draft that follows a redirect, or that was written against a mirror on their laptop
(`192.168.x`), becomes a scanner of their internal network the moment it runs unattended. Their
API keys and their database are on the same machine as the adapter, and loopback is where both
listen. Same default as (a) for a different reason — and (b) is the reason the default cannot be
"allow everything and hope": the person who would be harmed is the one who said yes to running it.

### (c) A TRAINING STATION ON A LAPTOP — the thing protected is the USER's machine and keys

Policy: **`deny-private`**, identical to (b): deny loopback `127.0.0.0/8` and `::1/128`, RFC1918
`10.0.0.0/8` / `172.16.0.0/12` / `192.168.0.0/16`, link-local `169.254.0.0/16` / `fe80::/10`; allow
the public internet; `base-url` for anything unreviewed.

Who is protected: **the USER's machine and keys.** On a laptop the numbers change and the stakes
do not: loopback is their password manager, their agent, their local model; `192.168.x` is their
home router's admin page and the NAS with the backups; `169.254.169.254` is a cloud metadata
endpoint whenever the "laptop" is a cloud notebook. This is the owner's own case — *"someone could
decide to set up a training station on their machine"* — and it is why the default is
`deny-private` rather than `base-url`: a new user's first source is not yet a vetted `sources.yml`
entry, and asking a stranger to fill one in before the sandbox protects them is how the sandbox
ends up switched off.

## 3 · What is NOT built (named, so nobody assumes it)

- **Admission's station path still runs the guard only.** `source_sandbox` calls
  `sandbox.run()` without a `network` policy, because a namespace with no egress would break every
  real source fetch. Wiring the tiers into `admission.py` needs the egress plumbing in §1 first.
  Until then the station's drafted-adapter runs are guarded, not kernel-refused.
- **The gatekeeper proxy with no other route** (the row's alternative to the netns) is not built.
  It is what closes the DNS hole and gives per-tenant egress; it is the recommended next row.
- **A bare namespace has no internet.** Stated in §1; it is a limitation, not a design goal.

## THE ALLOWLIST IS DECLARED ENDPOINTS — AND REDIRECTS ARE PINNED AT ADMISSION
(Learned by a real end-to-end run, 2026-11-12. Two refusals in one source.)

```
WHAT HAPPENED   base_url was github.com; the data 302'd to release-assets.githubusercontent.com; the licence page was on raw.githubusercontent.com.
                The sandbox armed github.com alone, so it could neither fetch the data nor read the terms of the artifact it was told to evaluate —
                REFUSED[licence-unknown], host not in the allowlist. A licence gate that cannot read the licence is an unreachable gate, not a safety
                property.
THE RULE        the allowlist is built ONLY from DECLARED, validated endpoints — data base_url · licence_url · metadata_url — one host per declaration,
                never a wildcard, never a host discovered by execution.
REDIRECTS       resolved ONCE at admission and PINNED, like DNS: follow the chain, record the final host(s), and REFUSED[redirect-undeclared] when a 302
                points at a host the entry did not declare (naming both hosts). A redirect followed at run time is a hole; a redirect pinned at admission
                is a declaration the operator can see.
VISIBILITY      the sandbox banner prints every pinned host WITH ITS REASON — `base_url (declared data)` · `release-assets.githubusercontent.com
                (302 from the release asset)` · `raw.githubusercontent.com (declared licence page)` — and the run record carries the pinned list per fetch.
THE PRINCIPLE   the sandbox must be able to reach the evidence required to evaluate a source, WITHOUT widening beyond what the entry declared.
```

**BUILT TODAY (req-P1-THE-LICENCE-HOST-IS-IN-THE-ALLOWL).** The first half of that rule is now in
code, in ONE definition site — `symbiont/sandbox.py::allowlist_declarations()` — and it is the
entry's declarations, one host per declaration, never a wildcard: `base_url` (data) plus
`licence_url` (licence page) whenever that URL names a **different** host, collapsed back to
exactly ONE entry when both name the same host. `symbiont/admission.py` prints it as
`allowlist: github.com (data) · raw.githubusercontent.com (licence page)`, carries it into the run
record (`allowlist`, `licence_host`, `network_allowlist`), and records the licence fetch as its own
request named `licence` with its own sha256 — so a reader can see *why* the second door was opened
and *what* was read through it (measured 2026-11-12: `n=1 note='licence'
url=https://raw.githubusercontent.com/monarch-initiative/mondo/master/LICENSE status=200
bytes=18658`). The second half — pinning a 302 chain at admission — is **not** built: a licence page
that is *served* from a host the entry does not declare (a CDN in front of it, an alias, or the 302
above) is refused by name, `REFUSED[licence-host-undeclared]`, naming that host and the
declarations it broke, so the operator's next act is to declare the host (or point `licence_url` at
it) instead of reading a `licence-unknown` mystery; the terms are never followed onto a host nobody
named. The kernel TIER keeps its narrower claim for the same reason: `base-url` is generated from
the DATA host (a tier confines the payload's egress to the host the data comes from), while the
licence host is a declaration of the *sandbox allowlist* — the artifact the banner and the run
record name, and the one under which the licence read actually happens.
