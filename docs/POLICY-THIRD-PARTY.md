# POLICY — THIRD-PARTY CODE, DATA AND SERVICES (2026-11-10, one page, four buckets)

Written because tonight showed the gap: we read licences ad hoc, we found repositories by someone thinking of the right name, and we
depend on at least one paid service nowhere described in one place.

## 1 · CODE (a library, an MCP server, a script we did not write)
```
READ THE LICENCE ON DISK, never from a README claim or a search result (precedent: reports/INCIDENT-2026-10-22-FALSE-LICENSE.md).
LICENCE MUST ALLOW OUR USE (a station handed to GeneNetwork, executed on their hardware): MIT · Apache-2.0 · BSD · ISC are fine;
GPL/AGPL only if we never link it into the station; anything unclear = refused until read.
PIN IT: exact version + a hash (the PubMed MCP is pinned 2.12.0 with its licence read on disk).
NO VENDORING: we do not copy the code into our tree. We call it, or we write an adapter that calls it.
ONE DECISION RECORD before it is trusted: ADAPTER DECISION — what it is, its licence, its limits, its cost, what breaks if it vanishes.
SANDBOX FIRST: it runs in a throwaway store with a budget before it touches real data.
A DEPENDENCY THAT CANNOT BE PINNED IS NOT A DEPENDENCY, IT IS A RISK.
```

## 2 · DATA (records, files, dumps we store)
```
PER-SOURCE TERMS, recorded with the rows: MGI CC-BY 4.0 · Ensembl no restrictions · EuropePMC per its terms · Elicit's index is
theirs and is not a data source we may mirror.
ATTRIBUTION ON THE RECORD, not in a footnote: every row carries source + source_release + fetched_at.
STORE WHAT THE LICENCE ALLOWS (the STORE-IT-ALL rule): all fields where permitted; accession-only where not; never full text where
terms forbid it.
NO MIRRORING OF A PAID INDEX. If a provider sells access to a corpus, we read it through their API at their terms and store only what
they permit — we do not build a shadow copy.
```

## 3 · SERVICES (an API we call: Elicit, NCBI, Ensembl, an LLM gateway)
```
RECORD FOUR NUMBERS BEFORE DEPENDING ON IT: price per unit · rate limit · quota/allowance · what a refusal looks like.
   measured example (Elicit, 2026-11-10): maxResults per request by plan (Pro 300 · Scale 500 · Enterprise 10 000 · Basic/Plus none) ·
   100 req/min per IP across all endpoints, breach = 429 and a 5-minute IP block · exhausted allowance = 402 credits_exhausted or
   spend_limit_reached, and paused sessions do NOT auto-resume.
A SERVICE REFUSAL IS A NAMED CLASS, not a retry storm: 429 = backpressure (wait, same model); 402 = budget (stop, tell the operator);
5xx = upstream (one retry, then the fallback).
EVERY SERVICE HAS A REPLACEMENT PATH: if it vanishes tomorrow, say which adapter takes over or which part of the ship stops.
```

## 4 · WHAT WE REFUSE (regardless of convenience)
```
· an LLM as the acceptance gate (both scouts documented its failures)
· a mirror or shadow copy of a corpus we do not own
· a dependency we cannot pin or whose licence we cannot read
· a service whose refusals we cannot classify
· any "we accelerated science" claim without a receipt: counts, cost, release, hash
```

## THE ONE-LINE TEST BEFORE ADDING ANYTHING
```
"Can I read its licence on disk, pin its version, classify its refusals, and name what breaks when it disappears?"
If any answer is no, it does not go into the station.
