# THE SHIP TEMPLATE — what every domain gets, and what every domain must supply (2026-11-10)

SYMBIONT was not built as a biology tool; it was built as a **ship**, and biology was the cargo. This page is the template, written so the next
ship (trading, cyber, app discovery, model-market) does not rediscover it.

## THE INVARIANT CORE — identical in every domain (already built and tested)
```
1 SURFACE REGISTRY     a data file: name · kind · query syntax · licence field · auth · rate · enabled. Adding a domain = adding rows.
2 QUERY RUNNER         runs LISTED queries only, logging each verbatim with its result count ("a count without its query is not a number")
3 CANDIDATE            one normalised row: name · surface · social proof · last activity · LICENCE READ FROM THE API · archived? · why it matched
4 POLICY GATE          refuse by RULE, never by mood: licence unreadable · archived/dead · licence not ours to use · terms we cannot classify
5 DEDUPE               a persistent seen-set across surfaces AND across weeks — week 2 reports only NEW
6 SANDBOX              the only place a stranger's code runs: throwaway store, pinned toolchain, network allowlist (DESIGN §4b–§4d)
7 CARD + ADMISSION     probe → evaluate → draft → sandbox → card → ADMISSION (operator, then policy: N stable cycles + every check green)
8 THE LOOP             fetch · VERIFY · snapshot · publish; quarantine on 404 or two count mismatches; budget refuses; skip by name, requests=0
9 RECEIPTS             every row carries source · release · fetched_at · hash; every run writes runs/<id>.json; the manifest is the only export
10 HONESTY RULES       a zero-result run prints "I looked: N surfaces, M queries, 0 new" · no silent skip · no refusal without its rule ·
                       and (our addition) NO ROW CLOSES WITHOUT EVIDENCE NEWER THAN THE FIRE (the FALSE-DONE rule)
```

## THE DOMAIN MUST SUPPLY — the four things that cannot be shared
```
A VERIFIER      what "true" means here, and how it is measured.
                bio      our count == the source's own count (+ a frozen gold set: BioASQ, T10)
                cyber    detonation in an isolated sandbox, behaviour observed, advisory cited
                trading  out-of-sample, walk-forward, NET of costs (Desktop/AI-TRADING-RESEARCH-PROTOCOL.md E1/E2, F1)
B GOLD SET      the frozen reference the verifier scores against. No gold set, no ship — a ship without one is a rumour generator.
C LEGAL GATE    the domain's hard NOs, decided before the first sweep:
                bio      source terms (CC-BY attribution, EuropePMC terms, no full-text storage where forbidden)
                cyber    public defensive sources only (NVD, GitHub advisories, CISA KEV, ExploitDB METADATA). NO dark-web acquisition, NO
                         malware handling, NO honeypot interaction, NO payload weaponisation, NO self-replicating code, ever
                trading  market-data licences, venue terms, no personal/insider data, no unlicensed redistribution of quotes
D REFUSAL CLASSES  the named ways this domain says no, so a failure is a fact rather than a shrug.
```

## THE SPECIES CHOICE — a POLICY question, not a technical one
```
MUTUALISTIC (ours by default)   when the sandbox finds a broken upstream tool, we may file the fix / open the PR / report the bug.
                                Slower, but it is the difference between harvesting a commons and strip-mining it — and it is what makes a
                                station welcome rather than resented.
COMMENSAL                       read-only, invisible, no contribution. Acceptable for a closed internal tool; weak for anything public.
PARASITIC                       take, strip, never credit. Our POLICY-THIRD-PARTY forbids it (attribution is a field on every row), and a
                                business built on it cannot survive a reviewer.
⇒ RECORDED CHOICE: mutualistic. Attribution on every row; upstream fixes when cheap; never claim their work as ours.
```

## WHAT AN OUTSIDE ANALYSIS GOT RIGHT, AND WHAT IT OVERREACHED ON
```
RIGHT      that the core is domain-agnostic · that licence gating inside a discovery loop is rare · that "0-result honesty" is rare ·
           that paper-first abandonware is the real disease in scientific software · that MCP-hub ingestion is frontier
OVERREACH  "REFUSE if latency > 5ms" — that is a STRATEGY PARAMETER, not a policy gate; a gate refuses unlawful/unusable inputs, not
           unprofitable ones (the backtest decides that)
           dark-web forums / Telegram leak channels / "weaponised payload for red team" — out of scope and, in most jurisdictions, out of law
           grant figures quoted from memory — every number in a proposal must come from the funder's own page, dated, not from a chat model
```

## THE ONE-LINE TEMPLATE
```
A ship = (the invariant core, unchanged) + (a verifier, a gold set, a legal gate, and refusal classes chosen for that domain).
SYMBIONT is that template instantiated for bioinformatics mining; the next ship is the same body with a different organ.

## THE RESEARCH LAYER HAS DEPARTMENTS — SYMBIONT IS ONE OF THEM (owner, 2026-11-10)
```
THE CORRECTION THAT MATTERS: SYMBIONT is not the name of a station or of the research layer. SYMBIONT is a DEPARTMENT of the research layer
— a mini-ship that lives inside it — and the department exists for BIOINFORMATICS MINING. Another department would have another name
(naming is OWNER-CLASS per an owner review's ruling) for trading, for cyber, for the model market, for app scouting.
WHAT IS SHARED (the layer)          the brain: scouts · triage · explore · exploit · adversary · discovery sweep · expeditions · role summons
WHAT IS REPLACEABLE (the department) the four ORGANS, and only these:
    1 THE SURFACE REGISTRY ROWS     where to look for THIS domain
    2 THE VERIFIER                  what "true" means here, and how it is measured
    3 THE GOLD SET                  the frozen reference the verifier scores against
    4 THE LEGAL GATE + REFUSAL CLASSES   the domain's hard NOs, named
EVERYTHING ELSE IS INHERITED       the runner · dedupe · candidate row · sandbox · card · admission · loop · quarantine · receipts ·
                                   honesty rules · budget · the manifest contract
```
```
                 RESEARCH LAYER  (the brain; domain-agnostic; owns the machinery above)
                 │
   ┌─────────────┼──────────────────┬───────────────────────┬────────────────────────┐
   ▼             ▼                  ▼                       ▼                        ▼
 DEPARTMENT   DEPARTMENT          DEPARTMENT             DEPARTMENT               DEPARTMENT
 BIO MINING   TRADING            CYBER (defensive)      MODEL MARKET             APP SCOUTING
 =SYMBIONT    (owner to name)    (owner to name)        (owner to name)          (owner to name)
 organs:      organs:            organs:                organs:                  organs:
 bio registry trading registry   advisory registry      hub/registry rows        app-store + Exa surfaces
 count-match  walk-forward       detonation/advisory    latency + terms          capture + parity
 BioASQ gold  cost-net gold      advisory-id gold       measured ladder          licence + live origin
 source terms venue/data terms   public-defensive only  no-training-on-data      store/publisher terms
```
```
WHY THIS SHAPE IS BETTER THAN "ONE STATION": a department can be extracted, renamed, handed over, or retired without touching the brain.
That is the "replaceable part": you swap four organs and the same layer hunts in a different forest — and a department is small enough to
carry its own budget and kill switch.
THE TEST OF THE CLAIM: build ONE more department (the model market, already filed as an expedition) using only the four organs, and show it
runs on the inherited machinery unchanged. If it needs new machinery, the blueprint is wrong and we say so.
```

## NAMING, SETTLED (owner, 2026-11-10): SYMBIONT IS A MINI-SHIP INSIDE THE RESEARCH LAYER
```
SYMBIONT is not the station, not the layer, and not a department in the bureaucratic sense. It is a MINI-SHIP that sails inside the research
layer: it has a route (the discovery→admission→loop checkpoints), a crew (its lanes), cargo (a dataset + manifest), and four organs (surface
registry · verifier · gold set · legal gate). Those four organs are the MINI-SHIP's own; everything else — runner, dedupe, sandbox, card,
admission, receipts, honesty rules — is the research layer's hull, shared by every mini-ship.

                 GRID                      the sea: providers + nodes, caps, keys, price, breakers
                  │
                 MACHINE                   the engine: door · lanes · caps · preflight · receipts · reconcile  (executes, never chooses)
                  │
                 RESEARCH LAYER            the brain: scouts · expeditions · role summons · the hull every mini-ship is built on
                  ├── MINI-SHIP  SYMBIONT           bioinformatics mining        (cargo: datasets + manifests)
                  ├── MINI-SHIP  (owner to name)    trading research            (cargo: tested claims with cost-net evidence)
                  ├── MINI-SHIP  (owner to name)    defensive cyber intel       (cargo: advisory-bound watches, public sources only)
                  ├── MINI-SHIP  (owner to name)    model-market scouting       (cargo: measured ladders, price/terms tables)
                  └── MINI-SHIP  (owner to name)    app scouting                (cargo: captures, parity pairs, licence rows)
                  │
                 SHIPS                     what the farm delivers to the world: an app, a dataset, a strategy, a station
```
```
THE TEST OF THE NAMING: if a mini-ship needs something the hull does not have, either the hull is incomplete (fix the layer once) or the
mini-ship is trying to be a ship (send it out on its own). SYMBIONT stays a mini-ship as long as it lives inside the layer and returns cargo;
the moment it is handed to GeneNetwork to run independently, it has graduated to a SHIP — which is exactly what D2 describes.
```

## THE HULL'S SHARED INSTRUMENTS — including the "taste" ones (owner, 2026-11-10: "the research layer could have shared departments/items like
## taste, MCPs like Exa")

A mini-ship supplies four organs. Everything it NEEDS BEYOND THOSE comes from the hull, and the hull's instruments are SHARED — one definition,
many readers. A mini-ship may not write its own copy of any of these.

```
SENSES (outward)        EXA  search + fetch        the canonical outward sense: prose, docs, changelogs, competitor pages. It is what found
                                                   BioFlow and Thoa in a single query, and what the app ships used before SYMBIONT existed
                        SURFACE ADAPTERS           GitHub/GitLab API · PyPI/npm/conda · arXiv/bioRxiv/EuropePMC/PubMed · Zenodo/Figshare ·
                                                   MCP hubs · standards registries. Each adapter is one file, called by every mini-ship
                        MCP GATEWAY                the tool surface: one place that knows which servers exist, their licences and their limits
GATE (inward)           POLICY GATE                licence read from the API · terms classified · refuse by rule, never by mood
                        LEGAL GATE (per domain)    supplied by the mini-ship, but ENFORCED by the shared gate machinery
SANDBOX                 the only place a stranger's code runs: throwaway store · pinned toolchain · network allowlist (Guix/unshare)
MEMORY                  SEEN-SET                   dedupe across surfaces and across weeks (week 2 reports only new)
                        STORE                      content-addressed raw + records + views (tier rules: store · point-at · derive)
TASTE (the part that is judgement, and that we made a shared instrument too)
                        ADVERSARY ROLE             reads a claim and tries to refute it — the same prompt, every domain
                        REVIEW LANE                a different family from the worker (P-076); reads a report and re-runs its gate
                        AN OWNER REVIEW                  three living distinct families; rules on what a mini-ship may ADOPT, not on what it found
                        EVIDENCE RULE              no row closes without a path newer than the fire; no claim without a receipt
ACCOUNTING              GRID METER                 lanes · minutes · time-to-first-byte · estimated cost per grid per day
                        BUDGET + NO-PROGRESS STOP  a loop that cannot be stopped cannot be priced
                        EXPEDITION LEDGER           what each mini-ship spent and what cargo it returned
```

```
THE RULE THAT MAKES "SHARED" REAL: a mini-ship DECLARES which instruments it uses in its registry (a list of names), and the loader REFUSES
a mini-ship that ships its own copy of a shared instrument ("one definition, N readers"). That is how five mini-ships stay cheap: the hull is
paid for once.
```

## LINEAGE — the research layer was DISCOVERED by building the mining station (owner, 2026-11-10)
```
THE ORDER OF EVENTS MATTERS. We did not design a research layer and then look for work for it. We were asked to mine (datasets + literature for
GeneNetwork, for them OR FOR ANYONE ELSE who wants to run it — D2), and the nature of THAT work forced the layer into existence:
   a mining question needs SOURCES           → a surface registry (data, not code)
   sources need TERMS                        → a policy gate with named refusals
   fetched data must be TRUSTED              → a verifier, a gold set, a count-check, a manifest
   new sources must be FOUND, not guessed    → discovery, dedupe, admission cards, quarantine
   and the whole thing must be EXPLAINED     → receipts, digests, refusals, "I looked: 0 new"
Every one of those is a research-layer instrument. None of them was invented for "AI research" in the abstract; each was forced by a real
mining requirement that had to be defensible in front of a reviewer.
⇒ THE RECIPROCAL IS THE GOOD NEWS: because the mining station was built honestly, the layer it needed turns out to be reusable. SYMBIONT is
  the mining station for GeneNetwork or anyone else; the hull it required can carry the next mini-ship into another domain without re-inventing
  the parts that took a week to get right.
```
