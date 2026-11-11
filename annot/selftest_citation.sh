#!/bin/bash
# annot/selftest_citation.sh — the citation gate, three cases, nothing else.
#   req-P1-CITATION-REQUIRED-AT-THE-DOSSIER-DOOR
#
#   bash annot/selftest_citation.sh        (from anywhere)
#
# The answer mode takes {tool, params} plus the composed answer, and returns — beside `items` — a
# `sentences` list of {text, accessions, grounded, reason}. These three cases prove the door:
#   (a) an answer citing a REAL accession                    -> grounded:true
#   (b) the same answer with an INVENTED accession           -> grounded:false, "accession not in items"
#   (c) a real accession with a WRONG value named for it     -> grounded:false, "stored value differs"
# Exit 0 only when all three behave exactly as stated.
set -u
export PYTHONDONTWRITEBYTECODE=1
FARM=$(cd "$(dirname "$0")/.." && pwd)
cd "$FARM" || exit 2
PASS=0; FAIL=0
ok() { PASS=$((PASS+1)); echo "  PASS $*"; }
no() { FAIL=$((FAIL+1)); echo "  FAIL $*"; }

# the real accession pair for Trp53, read from the store rather than typed in
TRP53=$(python3 -m annot call gene_dossier --json '{"gene":"Trp53"}')
MGI=$(printf '%s' "$TRP53" | python3 -c "import json,sys;print(json.load(sys.stdin)['subject']['mgi_accession'])")
ENS=$(printf '%s' "$TRP53" | python3 -c "import json,sys;print(json.load(sys.stdin)['subject']['ensembl_gene_id'])")
echo "== citation gate: dossier gene Trp53, stored accessions $MGI / $ENS =="

answer() {   # answer <json> -> the response on stdout, its body written to a temp file
  python3 -m annot call answer --json "$1"
}

echo "== A · a REAL accession, correctly named → grounded:true =="
A=$(answer "{\"tool\":\"gene_dossier\",\"params\":{\"gene\":\"Trp53\"},\"sentences\":[\"Trp53 is the mouse gene with MGI accession $MGI and Ensembl gene id $ENS.\"]}")
printf '%s\n' "$A" | python3 -c "
import json,sys
d=json.load(sys.stdin); s=d['sentences'][0]
print(json.dumps({k:s[k] for k in ('text','accessions','grounded','reason')}, indent=2))
assert d['status']=='ok' and s['grounded'] is True and s['reason'] is None, 'not grounded'
assert s['accessions']==['$MGI','$ENS'], s['accessions']
" && ok "(a) real accession: grounded:true, both accessions verified against the store" \
  || no "(a) a real accession was not grounded"

echo "== B · the SAME answer plus an INVENTED accession → grounded:false 'accession not in items' =="
B=$(answer "{\"tool\":\"gene_dossier\",\"params\":{\"gene\":\"Trp53\"},\"sentences\":[\"Trp53 is the mouse gene with MGI accession $MGI.\",\"The moon is made of cheese, as recorded for MGI:99999999.\"]}")
printf '%s\n' "$B" | python3 -c "
import json,sys
d=json.load(sys.stdin); s=d['sentences'][1]
print(json.dumps({k:s[k] for k in ('text','accessions','grounded','reason')}, indent=2))
assert d['status']=='refused', d['status']
assert d['sentences'][0]['grounded'] is True, 'the real half should still be grounded'
assert s['grounded'] is False and s['reason']=='accession not in items', s['reason']
assert s['accession']=='MGI:99999999', s.get('accession')
" && ok "(b) invented accession: grounded:false, reason 'accession not in items' — sentence returned, not dropped" \
  || no "(b) the invented accession was not refused with 'accession not in items'"

echo "== C · a REAL accession with a WRONG value named → grounded:false 'stored value differs' =="
C=$(answer "{\"tool\":\"gene_dossier\",\"params\":{\"gene\":\"Trp53\"},\"sentences\":[\"$MGI is the accession of Myc.\"]}")
printf '%s\n' "$C" | python3 -c "
import json,sys
d=json.load(sys.stdin); s=d['sentences'][0]
print(json.dumps({k:s[k] for k in ('text','accessions','grounded','reason')}, indent=2))
assert d['status']=='refused', d['status']
assert s['grounded'] is False, 'should not be grounded'
assert s['reason']=='stored value differs: Myc vs Trp53', s['reason']
assert s['sentence_value']=='Myc' and s['stored_value']=='Trp53', (s.get('sentence_value'), s.get('stored_value'))
" && ok "(c) real accession, wrong value: grounded:false, reason 'stored value differs: Myc vs Trp53'" \
  || no "(c) the contradicted value was not refused with 'stored value differs'"

echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ] || exit 1
exit 0
