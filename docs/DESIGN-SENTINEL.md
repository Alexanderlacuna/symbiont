# DESIGN — THE SENTINEL AND THE CYCLE (the station's own watch, and the station's own growth)

Status: DESIGN + one governance question filed to an owner review. No code shipped yet. Written 2026-11-10 after the owner asked how the GeneNetwork
station could "keep itself sentient" without carrying our brain.

## 1 · TWO BEHAVIOURS, ONE FILE
```
sentinel   WATCH     health · staleness · release-watch · gaps — reads, never adds. The station's eyes.
discovery  GROW      find candidate sources → evaluate → draft → sandbox → card → admit-by-policy. The station's hands.
cycle      RUNS EITHER, weekly, bounded by its own budget:   symbiont/cycle.py --mode watch|discover|both
```
The distinction matters because they carry different risk: `watch` can only say things, `discover` can change the corpus. A deployment may
enable the first and never the second.

## 2 · WHAT SHIPS ON, AND WHAT SHIPS OFF (owner, 2026-11-11: "why not have only the discovery switched off, with the watch mode over the
## corpus on?")

The two modes carry different risk, so they get different defaults:

```
cycle.yml
   watch:    enabled: true      READ-ONLY: health · staleness · release-watch · gaps. It only looks and reports. Ships ON, because a station
                                that cannot tell you your corpus is stale is not a station.
   discover: enabled: false     CHANGES THE CORPUS: finds candidates, drafts adapters, sandboxes, cards, proposes. Ships OFF — a deployment
                                turns it on deliberately.
   admit:    by_policy: false   the gated step stays with a human until an owner review rules (see §7).
   every: weekly · budget: {requests, bytes, wall_s} · surfaces: <the deployment's own registry> · draft.provider: (empty unless configured)
```
WHY THIS SPLIT IS RIGHT: the two modes are not "the same thing at different budgets" — one reads, one writes. A default that switches off the
reading too would hide the station's most useful property (it notices) in order to be cautious about its riskiest one (it changes things).

WHAT WATCH MODE MAY TOUCH: it reads the upstream metadata endpoints and the local store, and writes only `store/digest/*` and its own run record.
It never writes `sources.yml`, never creates an adapter, never fetches a payload. An unreadable `cycle.yml` still makes it refuse to start, naming
the parse error; `watch.enabled: false` is honoured (some deployments want silence).

## 3 · THE CYCLE, STEP BY STEP (each step's refusal class named)
```
1 READ TELEMETRY          digests: gaps · staleness · release-watch. Refuses if a digest has no numbers (digest-no-numbers).
2 DISCOVER                over the deployment's OWN surface registry, listed queries only, each logged with its count.
                          Refuses to run with no registry (REFUSED[no-surfaces]) — it does not invent where to look.
3 EVALUATE                per candidate: licence read from the API · there is a count endpoint · accessions are stable · rate limit fits budget.
                          Refusals: licence-unreadable · no-count-endpoint · rate-too-low. Each candidate carries the rule that refused it.
4 DRAFT (optional)        a model writes the adapter. No model ⇒ skipped, and the candidate is reported as PROPOSED (not built).
5 SANDBOX                 the draft runs in a throwaway store with the cycle's budget; its count must equal the source's own count.
                          Refusals: count-mismatch · budget · source-gone · schema-drift.
6 CARD                    the admission card: licence · counts · sample rows · xref rate · cost · the question it unlocks.
7 ADMIT — THE ONE GATED STEP
      by_policy: false   the cycle proposes; a human admits (today's behaviour)
      by_policy: true    admit ONLY if every required card check is green AND the source has been stable for N cycles; the admission record
                         must quote the policy rule and N ("by policy <rule>, N=<n>") — a record that cannot quote the rule is not an admission
8 CYCLE REPORT            found · refused (rule named) · drafted · sandboxed · admitted · cost — and "I looked: <surfaces> <queries> 0 new"
                          on a quiet week. A cycle that changes nothing still prints, every time.
```

## 4 · WHAT IT MAY NEVER DO
```
· invent a surface (it reads the deployment's registry file, and refuses without one)
· admit anything whose licence it could not read
· admit anything that failed its count check, ever, regardless of policy
· spend past its budget, or continue after a refusal of class budget/source-gone without recording it
· edit the law, fire lanes, or touch the farm's queue — it is a station process, not a hand
· call a model provider that the deployment has not configured (no key of ours, ever)
```

## 5 · ITS MCP SURFACE — MACHINE DOOR ONLY
```
cycle_status       enabled? · mode · last run · last report · budget left · next due            (read-only, safe for an operator's agent)
cycle_run_once     one bounded run of the configured mode, budgeted, returning the report hash (operator-only; it is a write door)
NOT ON THE READ DOORS: a page or a casual agent must never be able to trigger growth. Asking and admitting stay separate (§8b).
```

## 6 · WHY IT IS ONE FILE AND NOT A BRAIN TRANSPLANT
```
every piece already exists and is tested: digests (41,285 b) · health (25,728 b) · admission policy (57,323 b, 20/20 fixtures) · sandbox
(28,513 b, 17/17) · the discovery sweep (12,448 b, first run: 8 queries · 123 candidates · 62 refused) · the MCP machine doors.
The cycle is the ORCHESTRATOR that runs them in one order, on one budget, and writes one report. No new organs; one new file and one config.
```

## 7 · THE GOVERNANCE QUESTION (filed to an owner review, not decided here)
```
DESIGN §4 says the station's research layer has "senses and a mouth, NEVER A WILL". admit-by-policy is a narrow will: it moves exactly one
decision — which already-passing source goes live — from a person to a policy file the operator writes.
An owner review was asked: "May the SHIPPED station admit a data source by policy with no human in the runtime loop?" with options
A (off by default, admit-by-policy inside a deployment — recommended) · B (spec only) · C (on by default) · D (signed approval every time).
UNTIL IT RULES, `admit.by_policy` stays false and this document is a design, not a licence.
```

## 8 · ACCEPTANCE (what would prove it, when built)
```
A1 a quiet week: cycle runs, prints "I looked: … 0 new", exits 0, spends nothing beyond a few API calls
A2 one new candidate: found → refused by its own rule → the refusal appears in the report with the rule named
A3 one draft: sandbox count mismatched → REFUSED[count-mismatch], nothing admitted
A4 admit.by_policy false: a fully green card is PROPOSED, and no enabled:true appears in sources.yml
A5 admit.by_policy true with N unmet: refused, naming N;  with N met: admitted, and the record quotes the rule and N
A6 budget exhausted mid-cycle: the cycle stops, records why, and the next cycle says how much it did
A7 the whole cycle run with the network DOWN: named refusals, no hang, "I looked" still printed
```

## 9 · THE NAME

The sentinel is called **Sophie**.
