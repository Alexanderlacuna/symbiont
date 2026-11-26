# Example E1 — a cross-species aging corpus, step by step

This directory is the **reader's copy** of a worked example: three datasets, three licences, then a join. Every number below was produced by
running the commands here, in this order. Nothing is projected.

## What you need
```
python3 (3.10+) · pyyaml · network access to genomics.senescence.info, informatics.jax.org and raw.githubusercontent.com
```
No account, no key, no GPU. A laptop is enough: the whole example fetches under 40 MB.

## Step 0 — start a store
```
mkdir -p ~/symbiont-example && cd ~/symbiont-example
export PYTHONPATH=/path/to/symbiont          # the directory that contains symbiont/ and this examples/ tree
```

## Step 1 — give the station the mapping blocks
Mappings are compiled only from the shipped `symbiont/sources.yml` (one definition site, by design). Paste the three blocks
(`genage_models`, `genage_human`, `mgi_ortholog`) from `docs/EXAMPLES.md` → **Appendix · the mapping blocks** into your copy of
`symbiont/sources.yml`, set each `enabled: true`, and record the admission:
```
python3 -m symbiont.registry add-event --source hagr:genage_models --class admitted --why "E1, my own run"
python3 -m symbiont.registry add-event --source hagr:genage_human  --class admitted --why "E1, my own run"
python3 -m symbiont.registry add-event --source mgi:HOM_MouseHumanSequence --class admitted --why "E1, my own run"
```

## Step 2 — run each dataset (one dataset per run: one run, one kind of view)
```
python3 -m symbiont.loop --sources genage_models --since auto --store ./store
python3 -m symbiont.loop --sources genage_human  --since auto --store ./store
python3 -m symbiont.loop --sources mgi_ortholog  --since auto --store ./store
```
What you should see, from our run:
```
genage_models  NORMALISE rows_in=2205  records=2205  rejects=0    VERIFY ours=2205  source=2205  OK
genage_human   NORMALISE rows_in=307   records=307   rejects=0    VERIFY ours=307   source=307   OK
mgi_ortholog   NORMALISE rows_in=46521 records=46521 rejects=0    VERIFY ours=46521 source=46521 OK
each one ends: PUBLISH view/<entity>.v1.tsv rows=N sha256=…
```
If `VERIFY` disagrees, the run refuses and publishes nothing — that is the rule working, not a failure of your setup.

## Step 3 — check the three sidecars
```
ls store/view/            # <entity>.v1.tsv · .schema.json · .provenance.json
cat store/view/genage_models.v1.provenance.json | head -20   # release, licence, per-row source
```

## Step 4 — prove it is reproducible
```
python3 -m symbiont.loop --sources genage_models --since auto --store ./store   # second run: FETCH new=0, same sha256
python3 -m symbiont.loop --rebuild-from-raw --store ./store                     # rebuild view/ from raw/ alone
```

## Step 5 — the join (the finding)
```
# the join is a declared transform: your own script, joining on the keys the sidecars name
#   aging gene (symbol, MGI id)  ×  ortholog (mouse MGI id → human Ensembl/HGNC)
# our result, for comparison: input_rows=2205  joined=127  one_to_many=4  none=2074  sum=2205  partition_holds=true
```
Report the four counts, and keep the absent set as a file — the genes with no human ortholog are evidence too.

## What this example does NOT claim
An ortholog is not evidence of ageing in humans. The literature per gene is the first, thin evidence; the join only says which human gene to ask about.
