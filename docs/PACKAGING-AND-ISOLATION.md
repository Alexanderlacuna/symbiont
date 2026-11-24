# PACKAGING AND ISOLATION — sandbox vs container vs Guix, and what SYMBIONT ships as (2026-11-10)

Written because the owner asked the right question: "why are we using a sandbox or a container, and what is the difference — let's discuss the
Guix thing and packaging."

## 1 · THE THREE WORDS, KEPT APART
```
SANDBOX        a POLICY on a running process: what it may write, what it may connect to, what it may see. Implemented with kernel features
               (namespaces · seccomp · read-only mounts). bubblewrap and `unshare -rn` are sandboxes. It answers: "what can this process do?"
CONTAINER      a SANDBOX plus a PACKAGED ROOT FILESYSTEM, usually an OCI image (Docker/Podman), with a daemon and cgroups. It answers two
               questions at once: "what can it do?" and "what is installed?" — at the cost of a daemon, an image registry, and bloat.
GUIX           a PACKAGE MANAGER WITH REPRODUCIBLE BUILDS and a content-addressed store. `guix shell -C` gives you a container-like sandbox
               WITHOUT a daemon (it is namespaces + /gnu/store bind mounts), and `guix pack` produces a relocatable tarball of an exact
               dependency closure. It answers: "what is installed, byte-for-byte, and can I rebuild it later?"
```
One-line separation:
```
sandbox   = what a process MAY DO        (prevention)
container = what is INSTALLED + a sandbox (packaging + prevention, heavier)
Guix      = what is installed, PROVABLY  (reproducibility; can also provide the sandbox)
```

## 2 · WHY SYMBIONT NEEDS BOTH KINDS OF GUARANTEE — AND THEY ARE DIFFERENT GUARANTEES
```
GUARANTEE A · "this running code cannot harm the store or the network"   → SANDBOX. Needed for the two untrusted things: a research ROLE and a
   DRAFTED ADAPTER. The station already implements this with bubblewrap: read-only root, only the allowlisted paths bound read-write, a
   connect-time host allowlist, and a SANDBOX-BREACH finding when something tries. (17/17 assertions in its own suite.)

GUARANTEE B · "this exact software, on someone else's machine, in a year"  → PACKAGING. This is what a lab or GeneNetwork needs to trust that the
   station runs the same way there as here. That is where Guix earns its place — and it is the one thing pip cannot give us.

⇒ THE TWO ARE NOT SUBSTITUTES. A sandbox does not make your dependencies reproducible; a pinned environment does not stop a bug from writing
  the store.
```

## 3 · WHAT THE BOXES ACTUALLY HAVE (measured 2026-11-10)
```
THIS FARM     bwrap 0.6.1 ✓ · unshare ✓ · docker 29.6.1 ✓ · guix ✓
VESUVIO       bwrap ✗      · unshare ✓ · docker ✗         · guix ✓      ← the box we know on the GN side
```
```
CONSEQUENCE: a handover that assumes bubblewrap or Docker would fail on vesuvio. Guix and unshare are the two things present on BOTH sides.
That alone decides the packaging conversation.
```

## 4 · WHAT I RECOMMEND FOR THE STATION (three layers, cheapest first)
```
LAYER 1 · STANDARD PYTHON PACKAGING — always    pyproject.toml + pinned constraints (a lock file), `python3 -m symbiont.…` works from a clean
                                                clone with no install (already true today: 25/25, 15/15, 13/13, 20/20 in a clone). This is the floor:
                                                if everything else fails, `git clone && python3 -m symbiont.selftest_sources` must still work.
LAYER 2 · A PINNED ENVIRONMENT FILE — now         requirements.lock (exact versions + hashes) for pip users AND a `guix.scm` for Guix users.
                                                  Two recipes, one truth: the versions match, and a test asserts it (parsing both files).
LAYER 3 · REPRODUCIBLE ARTEFACT — at handover    `guix pack -m guix.scm` → one tarball, no daemon needed to RUN it beyond the store; and for
                                                non-Guix recipients, the same closure as a venv-based tarball built from the lock file.
SANDBOX stays where it is                        bubblewrap on our box; on a box without it, `unshare -rn` + read-only bind mounts (same effect,
                                                fewer features); and the station REFUSES to run untrusted code if neither is available — named,
                                                never silently unsandboxed.
```

## 5 · THE DECISION THE OWNER HAS TO MAKE (and it is a real trade-off)
```
OPTION A  Guix-first handover       they run `guix pack` / `guix shell -C`. Bit-reproducible; both boxes already have Guix; nobody needs
                                    Docker. Cost: Guix is unfamiliar to most bio groups, and its first build is slow.
OPTION B  pip-first handover        a lock file and a venv. Familiar. Cost: "works on our box" comes back the moment a system library differs.
OPTION C  BOTH (my recommendation)  the lock file is the contract, `guix.scm` is the audit trail, and the README says which to use when.
                                    A recipient who has Guix gets bit-reproducibility for free; one who does not still has an exact version list.
```
```
WHAT I WOULD NOT DO: ship Docker images as the primary artefact. Neither box has a Docker daemon, the images are large, and the daemon adds a
privilege surface that our sandbox design deliberately avoids.
```

## 6 · WHERE THIS LEAVES THE GUix QUESTION WE STARTED WITH
```
Guix is NOT the sandbox (bubblewrap is, today) — Guix is the TOOLCHAIN and the HANDOVER. The six places in DESIGN §4d remain the plan:
1 source sandbox (bubblewrap now; Guix when the layout stops moving) · 2 roles · 3 station runs · 4 tests · 5 handover (`guix pack`) ·
6 reproducing an old report (`guix time-machine`). Phases, not a flag day.
```

## 7 · DECISION (owner, 2026-11-10): GUIX IS PARKED — "forgot about it"
```
The owner's word: forget Guix. So the shipping answer is the cheap one, and it is already true in the repo:
   ISOLATION   bubblewrap here · `unshare -rn` + read-only mounts where bwrap is absent · REFUSE untrusted code if neither exists, by name
   PACKAGING   pyproject + a pinned lock file; `python3 -m symbiont.selftest_sources` works from a clean clone with no install
   HANDOVER    the repo + the lock file + the quickstart; a venv tarball if they want one
GUIX IS NOT DELETED FROM THE DESIGN — DESIGN §4d keeps the six places and the phasing — but it is NOT on the path to finishing the ship, and
nobody should spend another minute on it unless a recipient asks for bit-reproducibility by name. If that day comes, `guix.scm` is a day's work,
not a migration.
```

## 8 · WHY THE STATION KEEPS ITS OWN SANDBOX EVEN WHEN GUIX IS AVAILABLE (design reason, recorded 2026-11-10)
```
Guix `guix shell -C` and the station's own sandbox are NOT rivals; they are different policies, and the station needs the finer one:

  Guix `guix shell -C` (measured on this box, 2026-11-10)
     filesystem  confined — only what you --expose / --share exists; the home directory is NOT visible by default; the store is read-only
     network     BINARY — off by default (connect to 1.1.1.1 → ENETUNREACH), on with -N/--network. It cannot say "only this host".
     toolchain   exact and rebuildable          (its real strength)
  the station's sandbox (symbiont/sandbox.py, bubblewrap + a connect-time check)
     filesystem  read-only root + allowlisted bind mounts per lane class
     network     PER-HOST ALLOWLIST — a drafted adapter may reach only the host of its own sources.yml base_url; anything else is REFUSED AT
                 CONNECT and the host is NAMED in the run record (fixture: allowed ['127.0.0.1'], a call to 'localhost' refused, exit 3)
     secrets     keys are not mounted at all
     breach      a write or connect outside the policy is a named finding of class sandbox-breach — never a silent repair

⇒ THE DESIGN REASON: a drafted adapter is code we did not write, pointed at ONE source we did choose. "The network is on" is not an adequate
  policy for that; "this one host, and nothing else" is. Guix cannot express it, so the station expresses it itself — and Guix keeps the job it
  is actually better at: pinning what is installed, and handing over an environment someone else can rebuild.
```

## 9 · GUIX AS A SANDBOX ALTERNATIVE — the fallback TIER (owner, 2026-11-10: "maybe Guix is a sandbox alternative lol")
```
Agreed, with one caveat that decides how it is used: `guix shell -C` IS a sandbox (measured here: separate namespaces, read-only store, home not
visible, network off unless -N). So the station's isolation becomes a TIER LIST, weakest requirement met first:

   TIER 1  bubblewrap            read-only root · allowlisted bind mounts · PER-HOST connect allowlist · keys not mounted      (today, this farm)
   TIER 2  guix shell -C         namespaces · read-only store · only exposed paths · network off unless asked                   (fallback; present on vesuvio, absent bwrap)
   TIER 3  unshare -rn + mounts  same kernel features by hand                                                                  (fallback; present on both boxes)
   TIER 4  REFUSE                no isolation available → refuse to run untrusted code, naming why                              (never run unsandboxed)

THE CAVEAT, STATED ONCE: tier 2's network control is all-or-nothing, so a DRAFTED ADAPTER (which needs "this host and nothing else") cannot rely on
Guix alone. Two honest options, both fine:
   (a) inside `guix shell -C -N`, the station still applies its own connect-time host allowlist — Guix confines the filesystem, the station
       confines the host. Compose, don't choose.
   (b) on a box where that composition is impossible, the adapter step is REFUSED by name rather than run with open network.
⇒ SO: Guix is a legitimate sandbox alternative for everything whose policy is "filesystem confinement + network off", and it is the natural
  fallback on GeneNetwork's own machines (they have Guix and no bwrap). It does not replace the per-host check; it sits under it.
```

## 10 · "THIS HOST" — WHAT IT MEANS IN EACH DEPLOYMENT (the owner asked for this to be very clear)
```
"only this host" is not paranoia about the internet. It is a rule about the three things we actually own on whatever machine the station runs on:
   (i) the model gateway bound to 127.0.0.1:8002   — an adapter reaching it spends OUR key and quota (and 429s cost us hours)
  (ii) services bound to loopback                  — the lens on :8099, the matrix bridge, a local mariadb: none of them are firewalled
 (iii) the data in the run directory               — whatever the adapter read can be sent anywhere
```
```
CASE A · WE HOST THE STATION (many users, many adapters)
   the untrusted party is not only the outside world — it is ANOTHER USER'S ADAPTER. Without a policy, one tenant's adapter can reach the farm's
   own services and, worse, read another tenant's runs. So here the policy is not optional: deny-private AND per-tenant egress, and the docs must
   say so, because "the network is on" is an invitation to lateral movement.
CASE B · GENENETWORK HOSTS IT (one institution, their own adapters)
   the adapter author is them, so the threat model is different — but the same box holds their keys, their database and their services. deny-private
   remains the sane default; base_url-only for anything machine-drafted. The point is not distrust of GN; it is that a bug should not be able to
   reach a database.
CASE C · A TRAINING STATION ON A LAPTOP (one person, learning)
   the asset being protected is the USER's machine and keys. deny-private by default means an experimental adapter cannot wander onto their home
   LAN or into their wallet. This is the case where a per-host rule would feel like overkill — and deny-private is exactly the cheap 90% answer.
```
```
THE RULE IN ONE SENTENCE: allow what the adapter is FOR (its base_url), deny what it is not for (loopback and private ranges first, everything
else second). The first half of that sentence is the job; the second half is what stops a mistake becoming an incident.
NOTE ON HONESTY: today's implementation is a Python-level guard (it wraps socket.getaddrinfo / create_connection / connect). It stops careless
code, not hostile code — a ctypes raw connect() would slip through. Moving the policy into the kernel (netns + nftables, or a single allowlisting
gatekeeper) is filed as req-P1-NETWORK-POLICY-TIERS, with that exact hostile fixture.
```
