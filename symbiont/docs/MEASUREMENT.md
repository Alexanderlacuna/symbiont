# A MEASUREMENT ROW — what it is, and what it is NOT

(req-P2-E5-A-MEASUREMENT-ENTITY-PROVEN-FROM-A-REAL-SERIES)

The station already held two kinds of row: the gene-keyed JOIN (an authority id + a provider id for
one symbol) and the STANDALONE DATASET (one source's own table). This note is about the third
thing the `dataset` role now carries: a **measurement** — a value of one trait, in the publisher's
unit, at one time, at one place, for one subject.

`view/measurement.v1.tsv` is written from ONE real series: Eurostat `nama_10_gdp` — GDP at market
prices (`na_item` B1G), current prices in million euro (`unit` CP_MEUR), annual 2020–2024, every
`geo` the publisher carries. The source is declared in `symbiont/sources.yml` as `eurostat_gdp`,
`kind: mapping`, `role: dataset`, `enabled: false`, and the export URL is in `endpoints.gdp`.

## What a measurement row IS

Eight columns, every one of them the publisher's own text (nothing is typed in by hand, nothing is
converted):

| column | from | meaning |
|---|---|---|
| `key` | `geo` | the subject's identity — the compiler's column contract allows ONE key column, so the key is the subject's place |
| `subject` | `geo` | the entity measured: the national economy at that place |
| `trait` | `na_item` | which aggregate is measured (`B1G` = GDP at market prices) |
| `value` | `OBS_VALUE` | the number, exactly as published |
| `unit` | `unit` | `CP_MEUR` — the publisher's own unit code: current prices, million euro |
| `period` | `TIME_PERIOD` | the year the value belongs to |
| `place` | `geo` | the place the value is for |
| `source_release` | `LAST UPDATE` | the publisher's own release stamp, carried PER ROW, so the view can be dated without asking anyone |

## What a measurement row is NOT

1. **It is NOT a claim about causation.** A value beside a year and a place asserts exactly one
   thing: the publisher published that number for that trait, unit, period and place. Nothing in a
   measurement row says why the number moved, what caused it, or what it predicts. Any such reading
   is the reader's, not the row's.
2. **Its unit is the SOURCE'S, not ours.** `CP_MEUR` is carried verbatim as `CP_MEUR`. We do not
   convert currencies, rescale to units, re-base years, or restate the unit in words of our own; a
   reader who wants euros rather than million euros does that arithmetic, and owns it. A row whose
   unit cell is empty would be a row that lost its unit — there is no fallback and no default.
3. **It is NOT a gene-keyed row, and it joins nothing.** `role: dataset` means these rows ARE the
   claim: no xref join runs, and the run that publishes this view may not also publish the gene
   arm's join (the loop refuses that mixture by name, `view-mixture`).
4. **It is not a second series.** One source, one dataflow, one indicator, one unit. Two series in
   one view would be two claims in one table, which is what `standalone-multiple` refuses.

## Two honest limits of THIS v1 (stated, not hidden)

* `subject`, `place` and `key` all resolve to the publisher's `geo`. Eurostat's export has four
  dimensions (`freq`, `unit`, `na_item`, `geo`) and no separate "subject" column: the entity that
  is measured IS the economy at that place. The view states that fact rather than inventing a value.
* Because the key is one column, `key` alone is NOT a row identity: the identity is the tuple
  (`key` · `trait` · `period`), and the view carries `trait` and `period` as their own columns. A
  composite key is a change to the mapping compiler's column contract, not to this mapping.
* The SDMX endpoint sends no `ETag` and no `Last-Modified`, so the mapping's `release` is a LITERAL
  quoted from the file's own `LAST UPDATE` column. Until that literal is bumped, a second run sees
  the same release and re-fetches nothing (`new=0`).

Activating it (it ships `enabled: false`): copy `symbiont/sources.yml`, flip `enabled: false -> true`
on `eurostat_gdp`, and run

    python3 -m symbiont.loop --store <store> --sources eurostat_gdp --sources-config <copy>
