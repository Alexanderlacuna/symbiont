# ORTHOLOG JOIN NOTE — how a mouse aging gene reaches its human ortholog in THIS store

*Written by req-P1-E1-b-THE-HUMAN-AGING-LIST-AND-THE-ORTHOLOG (2026-11-21), revised by the same
row's re-fire (2026-11-22) after re-measuring every number below against the bytes on disk. One
page, in the store's own terms: the file names, column names, run ids and numbers below are the ones
on disk. Every number was computed by the script quoted in §3, against this store's own files.*

## 1 · The three tables

| view | what one row is | rows | release | run that published it |
|---|---|---|---|---|
| `view/genage_models.v1.tsv` | one row of HAGR's **model-organism** aging-gene list — `key` (the publisher's symbol), `id` (GenAge ID), `entrez_gene_id` | **2,205** | `genage_models-2023-10-02` | `20261120T214235Z` |
| `view/genage_human.v1.tsv` | one row of HAGR's **human** aging-gene list — `key`, `id`, `entrez_gene_id`, `uniprot` | **307** | `genage_human-2023-08-28` | `e1b-genage-human-3` |
| `view/ortholog.v1.tsv` | one line of MGI's `HOM_MouseHumanSequence.rpt` (a `DB Class Key` + one organism), carrying **both sides of its class** | **46,521** | `mgi-2026-11-16` | `e1b-ortholog-3` |

*Run ids updated by the row's re-fire (2026-11-22): both views were re-published a second time, from
the SAME raw snapshot, by runs `e1b-genage-human-3` and `e1b-ortholog-3` — each fetched **0 new
bytes** (`data_bytes=0`, one HEAD) and each `.tsv` came out **byte-identical** (`sha256
5810302c…9526ad` and `08b84079…5cdf2`, unchanged); every number below is unchanged because the
bytes are unchanged. The release stamps and the raw snapshots are unchanged.*

The ortholog file's own shape, measured: 46,521 data rows fold into **21,928 DB Class Keys** —
18,782 classes hold one mouse row and one human row, **1,745 hold a mouse row and NO human row**,
2 classes hold human rows and no mouse row (8 rows), 1,399 hold one mouse row and several human rows
(between 2 and 26 of them), and 3 hold TWO mouse rows for the SAME MGI id — duplicate lines in the
publisher's file, kept as they came because a row of the view is a row of the file. The class key IS
the relationship; a pair is a class with both sides.

**A class's human side may be SEVERAL genes, and then the two `class_human_*` columns hold a
comma-joined list, not one id.** Measured on this view: **7,205 of the 46,521 rows** carry more than
one `HGNC:…` in `class_human_hgnc_id` (class `52442686` is `HGNC:5217,HGNC:5218` on all three of its
rows), while `class_mouse_mgi_id` never carries a comma. So the join keys below hand you the
CLASS; when you need exactly one human identifier, read the row's own `id`/`symbol` on the **human**
row of that class, not the class column.

The ortholog view's column contract (`view/ortholog.v1.schema.json`, declared once in
`the mapping block for mgi_ortholog in symbiont/sources.yml::view_columns`):
`key` (DB Class Key) · `id` (this row's own id — `MGI:…` on a mouse row, `HGNC:…` on a human row) ·
`organism` · `taxon_id` · `symbol` · `entrez_gene_id` · `relationship` (mouse-human | mouse-only |
human-only) · `class_mouse_symbol` · `class_mouse_mgi_id` · `class_human_symbol` ·
`class_human_hgnc_id`.

## 2 · The join keys (three routes, in order of safety)

1. **Entrez id — the exact route.** `genage_models.entrez_gene_id` = `ortholog.entrez_gene_id`
   **restricted to `ortholog.organism = 'mouse, laboratory'`**. Both sides are the publisher's own
   integer id, so no name-normalisation is involved; the hit row carries `class_human_symbol` and
   `class_human_hgnc_id`.
2. **Mouse symbol — the readable route.** `genage_models.key` = `ortholog.symbol` on the mouse rows,
   compared **case-insensitively**. GenAge prints `Adcy5`, MGI prints its canonical form, and the
   difference is not cosmetic: a case-sensitive comparison matches 158 of the 2,205 rows where the
   case-insensitive one matches 312. A consumer must decide the case rule, not discover it.
3. **Mouse MGI id — the route the row names.** The ortholog table's mouse side names
   `class_mouse_mgi_id` (`MGI:1340024`), and the gene arm's view `view/mouse_gene.v1.tsv` carries
   `symbol → mgi_id` for 77,139 mouse markers, so a consumer holding only a symbol can resolve it
   first: `mouse_gene.symbol → mouse_gene.mgi_id → ortholog.class_mouse_mgi_id`. Measured against
   this store: **310 of 2,205** rows resolve that way (route 2 gives 312; the difference is symbols
   MGI files under another name).

`genage_models` carries **no MGI id and no organism column** — the view kept only `key`, `id`,
`entrez_gene_id` (FINDING F2 in the lane report). Its 2,205 rows are therefore not 2,205 mouse
genes: the archive's own `organism` column says 911+61 yeast, 889+1 worm, 202 fly, 5 other, and
**136 `Mus musculus`**. A symbol-only join with no organism filter matches mouse symbols against
yeast and worm genes — 178 of the 312 symbol hits are exactly that.

## 3 · The coverage fraction — computed, not guessed

```
$ python3 -m symbiont.examples.e1_aging.coverage      # the same arithmetic, shipped with the station
                                      # reads this store's own files only: the genage_models raw
                                      # archive + view/ortholog.v1.tsv, nothing else
genage_models rows            : 2205
  of which Mus musculus       : 136
  mouse rows WITH human ortho : 133 (symbol, case-insensitive) | 131 (entrez id)
  coverage of the mouse rows  : 97.79 % (symbol) | 96.32 % (entrez)
  coverage of all 2,205 rows  : 6.03 % (symbol) | 5.94 % (entrez)
ortholog mouse rows           : 21929 | naming a human side: 20184
one pair, verbatim            : Adcy5 -> ('ADCY5', 'HGNC:236')
```

**Answer to "how many of the 2,205 aging genes have a human ortholog": 133 of 2,205 — 6.03 % of the
list, which is 97.79 % of the 136 rows that are actually mouse.** Of the 2,072 rows left over,
2,069 are not mouse genes at all (178 of them still match a mouse symbol by name and would be
joined to the wrong species by a symbol-only join), and 3 are mouse rows: `Arntl` and `G6PD` resolve
to no ortholog row at all — MGI files those genes under other symbols, a name-resolution gap, not a
store gap — and one more mouse row lands in a class with no human side.

On the other side: of the ortholog table's **21,929 mouse rows**, **20,184 name a human side**
(92.0 %); the other 1,745 sit in classes with no human row — no human ortholog in MGI's table.

## 4 · What this note does NOT claim

It does not publish the join view (that is the next row). It says nothing about HPO/MONDO or the
literature. It does not filter `genage_models` to mouse: the published view has no `organism`
column, so the filter above is a step a consumer performs on the archive, never something the
published view already did.
