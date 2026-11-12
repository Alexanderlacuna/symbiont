#!/bin/bash
# annot/selftest.sh — the NSC harness for req-P1-EXPOSURE. Exit 0 only when every check passes.
#
#   bash annot/selftest.sh          (from a checkout of this tree, or any cwd)
#
#
# The six checks, in the order the brief names them:
#   1 WRITES      no door can write: grep for write-mode opens / outbound clients / write verbs
#   2 ONE-DEF     every tool: CLI bytes == MCP tools/call text, byte for byte (cmp)
#   3 ACCESSION   every item in every response carries an accession, in all tools
#   4 EMPTY       a gene with no record and a tool with no source return NAMED empty results
#   5 OFFLINE     with the network namespace torn away the same query answers identically
#   6 NO-STORE-WRITE  the store under test (live or fixture) is byte- and mtime-identical before/after
# plus (req-P1-CITATION-REQUIRED-AT-THE-DOSSIER-DOOR):
#   G ANSWER      the opt-in answer mode: three fixtures + one real answer from tonight's store
# plus (req-P0-A-CLONE-DOOR-MUST-SAY-STORE-MISSING):
#   H NO-STORE    a store that is not there is REFUSED[store-missing] (path + the two creating
#                 commands, exit 3), never a traceback — run with ANNOT_STORE UNSET (a clone with
#                 only the committed fixture store, and a tree with no store at all) and with it
#                 pointed at a path that does not exist; plus a fault injected into the core, which
#                 must come back reason=core_fault even when it breaks provenance()'s own read
# plus (req-P1-THE-PUBLISHED-DATASET-TRAVELS-WITH-IT):
#   H6 RECEIPT    a view stripped of its sidecars (both, or the schema alone) is REFUSED
#                 [view-unreceipted] — 0 items, exit 3, the missing sidecar and the regenerating
#                 command named, the .tsv untouched — while the same store WITH its sidecars answers;
#                 and harvester-view/ (when present) carries a receipt that agrees with its tsv
# plus (req-P1-WHAT-CHANGED-CANNOT-COMPARE-A-NAIVE):
#   I WHAT_CHANGED  one normaliser for every timestamp: an ordinary date answers (it used to raise
#                 TypeError out of the `since` filter and reach every door as -32603), a naive
#                 finished_at is handled identically to an aware one, and an unplaceable instant is
#                 REPORTED on the item as uncomparable_timestamp instead of dropped or raised
set -u
export PYTHONDONTWRITEBYTECODE=1   # the core writes no .pyc into the farm either
FARM=$(cd "$(dirname "$0")/.." && pwd)
cd "$FARM" || exit 2
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  PASS $*"; }
no()   { FAIL=$((FAIL+1)); echo "  FAIL $*"; }

# The store this run reads: the tree's live store (symbiont/store) when one exists, else the
# committed fixture store (symbiont/fixtures/store-doors — raw + records + view + xref, a few
# hundred rows) so the whole suite runs offline in a clean clone. With NEITHER on disk the
# suite SKIPS, naming the missing path — a skip that says why, never 12 red lines.
LIVE_STORE="$FARM/symbiont/store"
FIXTURE_STORE="$FARM/symbiont/fixtures/store-doors"
if [ -d "$LIVE_STORE/view" ]; then
  STORE="$LIVE_STORE"
elif [ -d "$FIXTURE_STORE/view" ]; then
  STORE="$FIXTURE_STORE"
  echo "  (no live store at $LIVE_STORE — running against the committed fixture store $STORE)"
else
  echo "SKIP: no store to read: none at $LIVE_STORE and no committed fixture store at $FIXTURE_STORE."
  echo "SKIP: fetch a store (see the README's 'The real fetch') or restore the committed fixture"
  echo "SKIP: store from the repository, then re-run — the doors never guess at a missing store."
  exit 0
fi
export ANNOT_STORE="$STORE"
manifest() { find "$STORE" -type f -printf '%p\t%s\t%T@\n' 2>/dev/null | sort | sha256sum | cut -d' ' -f1; }

echo "== A · NO WRITES ANYWHERE (the doors are readers by construction) =="
HITS=$(grep -nE "open\([^)]*,\s*[\"'](w|a|x|r\+|w\+|a\+)" annot/*.py 2>/dev/null)
[ -z "$HITS" ] && ok "no write-mode open() in annot/*.py" || no "write-mode open() found: $HITS"
HITS=$(grep -nE "^[^#]*(import|from) (socket|urllib|http\.client|requests|subprocess|shutil|ftplib|telnetlib)" \
        annot/core.py annot/mcp_server.py annot/page.py 2>/dev/null)
[ -z "$HITS" ] && ok "core, MCP and page import no network/process module" || no "network module imported: $HITS"
# (the doors DO write to stdout/stderr and to the socket — that is their output, not the store)
HITS=$(grep -nE "os\.(remove|unlink|rename|replace|mkdir|makedirs|rmdir)|shutil\.|json\.dump\(|write_text|write_bytes|NamedTemporaryFile|mkstemp|rmtree" annot/*.py 2>/dev/null)
[ -z "$HITS" ] && ok "no path-write / temp-file / json.dump-to-handle call in annot/*.py" || no "path-write call: $HITS"
# rest.py is a server: only GET/HEAD/OPTIONS are implemented; every write verb is a refusal alias
HITS=$(grep -nE "def do_(POST|PUT|PATCH|DELETE)" annot/rest.py)
[ -z "$HITS" ] && ok "rest.py implements no write verb" || no "rest.py implements a write verb: $HITS"
grep -q "do_POST = do_PUT = do_PATCH = do_DELETE = _refuse" annot/rest.py \
  && ok "rest.py write verbs are aliased to _refuse (405 read_only)" || no "rest.py write verbs are not refused"
HITS=$(grep -nE "jsonrpc|tools/call" annot/mcp_server.py | grep -cE "write|create|update|upload|delete" || true)
[ "$HITS" = "0" ] && ok "mcp_server.py has no write/create/update/upload/delete tool path" \
  || no "mcp_server.py names a write operation ($HITS line(s))"

echo "== B · ONE DEFINITION: CLI bytes == MCP bytes, for all eight tools (six read + opt-in answer + research_check) =="
CLI_JSON="$T/mcp-requests.jsonl"
python3 - "$CLI_JSON" <<'PY'
import json, sys
calls = [
    ("gene_dossier",      {"gene": "Trp53"}),
    ("gene_xref",         {"gene": "Gstm7"}),
    ("phenotype_evidence",{"gene": "Trp53"}),
    ("publications_for",  {"gene": "Trp53"}),
    ("traits_containing", {"term": "body weight", "limit": 5}),
    ("what_changed",      {"source": "mgi", "since": "2026-11-09T20:00:00Z"}),
    ("answer",            {"tool": "gene_dossier", "params": {"gene": "Trp53"},
                           "sentences": ["Trp53 is the mouse gene with MGI accession MGI:98834."]}),
    # (req-P1-RESEARCH-CHECK-a-read-door-that-says): the coverage door is a tool like the rest —
    # it must answer the same BYTES at the CLI and at the MCP door, so it is pinned here too.
    ("research_check",    {"spec": "symbiont/research/mouse-gene-identity.yml"}),
]
with open(sys.argv[1], "w") as fh:
    fh.write(json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                    "clientInfo": {"name": "annot-selftest", "version": "1"}}}) + "\n")
    fh.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    for i, (tool, args) in enumerate(calls, 1):
        fh.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                             "params": {"name": tool, "arguments": args}}) + "\n")
    fh.write(json.dumps({"jsonrpc": "2.0", "id": 99, "method": "tools/list"}) + "\n")
json.dump(calls, open(sys.argv[1] + ".calls", "w"))
PY
python3 -m annot.mcp_server < "$CLI_JSON" > "$T/mcp-out.jsonl" 2>"$T/mcp-err.txt"
python3 - "$T/mcp-out.jsonl" "$T" <<'PY'
import json, sys
out, tmp = sys.argv[1], sys.argv[2]
calls = json.load(open(sys.argv[1] + ".calls")) if False else json.load(open(out.replace("mcp-out", "mcp-requests") + ".calls"))
msgs = [json.loads(l) for l in open(out)]
by_id = {m.get("id"): m for m in msgs}
assert by_id[0]["result"]["protocolVersion"] == "2024-11-05", "initialize failed"
tools = [t["name"] for t in by_id[99]["result"]["tools"]]
assert tools == [c[0] for c in calls], "tools/list mismatch: %s" % tools
for i, (tool, args) in enumerate(calls, 1):
    text = by_id[i]["result"]["content"][0]["text"]
    open("%s/mcp-%d.json" % (tmp, i), "w").write(text)
    open("%s/args-%d.json" % (tmp, i), "w").write(json.dumps(args))
print("  PASS mcp session: initialize ok, tools/list = the tools in SPECS order, 8 calls answered")
PY
i=0
while [ $i -lt 8 ]; do
  i=$((i+1))
  TOOL=$(python3 -c "import json;print(json.load(open('$T/mcp-requests.jsonl.calls'))[$((i-1))][0])")
  ARGS=$(python3 -c "import json;a=json.load(open('$T/args-$i.json'));print(' '.join('--%s %s'%(k,repr(str(v)) if ' ' in str(v) else v) for k,v in a.items()))")
  # shellcheck disable=SC2086
  python3 -m annot call "$TOOL" --json "$(cat "$T/args-$i.json")" > "$T/cli-$i.json"
  if cmp -s "$T/cli-$i.json" "$T/mcp-$i.json"; then
    ok "$TOOL: CLI and MCP returned the same $(wc -c <"$T/cli-$i.json") bytes (sha256 $(sha256sum "$T/cli-$i.json" | cut -c1-16)…)"
  else
    no "$TOOL: CLI and MCP differ"
  fi
done

echo "== C · EVERY ITEM CARRIES AN ACCESSION (all eight responses) =="
python3 - "$T" <<'PY'
import json, sys, glob
tmp = sys.argv[1]
bad, items = [], 0
def walk(o, path):
    global items
    if isinstance(o, dict):
        if "value" in o and "accession" in o:
            items += 1
            if not o["accession"] or not o.get("source") or not o.get("release") \
               or not o.get("fetched_at") or not o.get("sha256"):
                bad.append((path, o))
        for k, v in o.items():
            walk(v, path + "." + str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            walk(v, "%s[%d]" % (path, i))
for f in sorted(glob.glob(tmp + "/cli-*.json")):
    walk(json.load(open(f)), f.split("/")[-1])
print("  %s %d item(s) across eight responses, every one with value+accession+source+release+fetched_at+sha256"
      % ("PASS" if not bad else "FAIL", items))
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo "== D · NAMED EMPTY RESULTS (never a blank) =="
python3 -m annot gene_dossier --gene NotAGene > "$T/empty-gene.json"
python3 -m annot phenotype_evidence --gene Trp53 > "$T/empty-pheno.json"
python3 -m annot gene_xref --gene NotAGene > "$T/empty-xref.json"
python3 -m annot traits_containing --term zzzznotatrait > "$T/empty-trait.json"
python3 - "$T" <<'PY'
import json, sys
tmp = sys.argv[1]
checks = [("gene_dossier", "empty-gene.json", "no_record_for_query"),
          ("gene_xref", "empty-xref.json", "no_record_for_query"),
          # req-P1-THE-DOOR-READS-THE-PHENOTYPE-ARM — UPDATED DELIBERATELY: the store HOLDS
          # phenotypes/view/gene_phenotype.v1.tsv (699 annotations), so Trp53's empty result is no
          # longer "the source is missing" — it is "the arm is here, this gene has no row in it".
          # Keeping source_not_in_store here would re-tell the lie this row removed.
          ("phenotype_evidence", "empty-pheno.json", "no_phenotype_for_gene"),
          ("traits_containing", "empty-trait.json", "no_marker_name_contains_term")]
rc = 0
for tool, name, reason in checks:
    d = json.load(open("%s/%s" % (tmp, name)))
    good = (d.get("status") == "empty" and d.get("reason") == reason
            and isinstance(d.get("items"), list) and len(d["items"]) == 0
            and isinstance(d.get("message"), str) and len(d["message"]) > 20
            and isinstance(d.get("provenance"), dict))
    print("  %s %s: status=%s reason=%s items=%d message=%r"
          % ("PASS" if good else "FAIL", tool, d.get("status"), d.get("reason"),
             len(d.get("items") or []), (d.get("message") or "")[:58] + "…"))
    rc = rc or (0 if good else 1)
sys.exit(rc)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
# req-P1-THE-DOOR-READS-THE-PHENOTYPE-ARM — UPDATED DELIBERATELY: this assertion used to demand
# 'mgi_phenotype_annotations', a name NO row, receipt or run in the store ever carried — it pinned the
# door's guess instead of the view's truth, which is exactly the lie this row exists to remove. What it
# asserts now is the rule that replaces it: the door's lookup name IS the view's own `source` column.
SRC_IN_VIEW=$(python3 - "$STORE" <<'PY'
import glob, sys
f = sorted(glob.glob(sys.argv[1] + "/phenotypes/view/gene_phenotype.v*.tsv"))[-1]
with open(f) as fh:
    rows = [l.split("\t") for l in fh.read().splitlines()[1:] if l.strip()]
print(sorted({r[9] for r in rows if len(r) > 9}))
PY
)
SRC_IN_DOOR=$(python3 -c "import annot.core as c;print(sorted({c.PHENOTYPE_SOURCE}))")
SRC_IN_MISSING=$(python3 -c "import annot.core as c;print(sorted({s for s in c.MISSING_SOURCES if s == c.PHENOTYPE_SOURCE}))")
if [ "$SRC_IN_VIEW" = "$SRC_IN_DOOR" ] && [ "$SRC_IN_VIEW" = "$SRC_IN_MISSING" ]; then
  ok "the door's phenotype source lookup IS the view's own 'source' column: $SRC_IN_DOOR (one definition, not two)"
else
  no "source-name lookup differs from the view's source column: view=$SRC_IN_VIEW door=$SRC_IN_DOOR missing=$SRC_IN_MISSING"
fi
# THE OTHER HALF OF THE SAME RULE, new here because an arm-present store can no longer show it: strip
# the arm from a symlinked copy of the same store and the door must STILL say source_not_in_store and
# name that same source — 'no arm in this store' must survive as a distinguishable answer.
NOARM="$T/store-no-phenotype-arm"
cp -as "$STORE" "$NOARM" 2>/dev/null; rm -rf "$NOARM/phenotypes"
ANNOT_STORE="$NOARM" python3 -m annot phenotype_evidence --gene Brca1 > "$T/noarm-pheno.json" 2>"$T/noarm.err"
NORE=$(python3 - "$T/noarm-pheno.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
un = [u["source"] for u in (d.get("provenance") or {}).get("unreachable", [])]
print("%s|%s|%s|%d|%s" % (d.get("status"), d.get("reason"), ",".join(un),
                           len(d.get("items") or []), (d.get("message") or "")[:70]))
PY
)
IFS='|' read -r NR_ST NR_RE NR_UN NR_N __ <<<"$NORE"
# The reason depends on whether the GENE resolves in the store being read: a live store knows Brca1 and answers
# source_not_in_store; a small fixture may not know it and answers no_record_for_query_and_no_source_in_store.
# Both are a NAMED ABSENCE and both name the missing source, so the check asserts THAT, not one spelling.
if [ "$NR_ST" = "empty" ] \
   && { [ "$NR_RE" = "source_not_in_store" ] || [ "$NR_RE" = "no_record_for_query_and_no_source_in_store" ]; } \
   && [ "$NR_UN" = "mgi_genepheno" ] \
   && [ "$NR_N" = "0" ] && ! grep -qE 'Traceback \(most recent call last\)' "$T/noarm.err"; then
  ok "a store with NO phenotype view names the absence (reason=$NR_RE) and names $NR_UN — whichever gene resolution the store allows"
else
  no "arm-stripped store did not keep the named absence: $NORE"
fi
rm -rf "$NOARM"

echo "== E · OFFLINE: the store answers with every external source torn away =="
BEFORE=$(manifest)
unshare -rn true 2>/dev/null && NETNS=yes || NETNS=no
if [ "$NETNS" = yes ]; then
  unshare -rn python3 -c "import socket;socket.create_connection(('1.1.1.1',80),2)" 2>"$T/net.txt" \
    && no "the network was still reachable inside the namespace" \
    || ok "inside 'unshare -rn' the network is unreachable ($(tail -1 "$T/net.txt" | sed 's/.*\[Errno/[Errno/'))"
  unshare -rn python3 -m annot gene_dossier --gene Trp53 > "$T/offline.json" 2>"$T/offline-err.txt"
  RC=$?
  [ "$RC" = 0 ] && ok "offline dossier exit=0 ($(wc -c <"$T/offline.json") bytes)" || no "offline exit=$RC"
  cmp -s "$T/offline.json" "$T/cli-1.json" \
    && ok "offline response is byte-identical to the networked one (cmp == 0)" \
    || no "offline response differs from the networked one"
else
  no "unshare -rn unavailable: the offline proof could not be run on this box"
fi

echo "== G · ANSWER MODE: citation-required at the dossier door (three cases) =="
# (runs before F so the store-manifest check below covers it too)
python3 - "$FARM" <<'PY'
import json, subprocess, sys
farm = sys.argv[1]
rc = 0
def call(args):
    out = subprocess.run(["python3", "-m", "annot", "call", "answer", "--json", json.dumps(args)],
                         capture_output=True)
    return json.loads(out.stdout)
def report(name, good, detail):
    global rc
    rc |= 0 if good else 1
    print("  %s %s" % ("PASS" if good else "FAIL", name), detail)

# case (a) — an answer citing a real accession: grounded, with the accessions it was verified against
real = call({"tool": "gene_dossier", "params": {"gene": "Trp53"},
             "sentences": ["Trp53 is the mouse gene with MGI accession MGI:98834."]})
s = (real.get("sentences") or [{}])[0]
report("(a) real accession: grounded:true", real["status"] == "ok" and s.get("grounded") is True
       and s.get("reason") is None and s.get("accessions") == ["MGI:98834"],
       "-> grounded=%r accessions=%s" % (s.get("grounded"), s.get("accessions")))

# case (b) — fixture 1: the same answer plus an invented accession, refused as 'accession not in items'
d = call(json.load(open(farm + "/annot/fixtures/answer-mode/1-invented-claim.json")))
bad = d["sentences"][1]
report("(b) fixture 1 (invented accession): grounded:false 'accession not in items'",
       d["status"] == "refused" and d["sentences"][0]["grounded"] is True
       and bad["grounded"] is False and bad["reason"] == "accession not in items"
       and bad.get("accession") == "MGI:99999999",
       "-> reason=%r accession=%r text=%r" % (bad.get("reason"), bad.get("accession"), bad.get("text")))

# case (b') — fixture 2: the same answer with the invented claim removed: accepted
d = call(json.load(open(farm + "/annot/fixtures/answer-mode/2-claim-removed.json")))
report("(b') fixture 2 (claim removed): accepted, everything grounded",
       d["status"] == "ok" and all(x["grounded"] for x in d["sentences"]),
       "-> status=%r grounded=%d/%d" % (d["status"],
        d["provenance"]["answer_mode"]["sentences_grounded"],
        d["provenance"]["answer_mode"]["sentences_total"]))

# case (c) — fixture 3: a real accession whose stored value contradicts the sentence: both values quoted
d = call(json.load(open(farm + "/annot/fixtures/answer-mode/3-contradicted-value.json")))
r = d["sentences"][0]
report("(c) fixture 3 (contradicted value): 'stored value differs: Myc vs Trp53'",
       d["status"] == "refused" and r["grounded"] is False
       and r["reason"] == "stored value differs: Myc vs Trp53"
       and r.get("sentence_value") == "Myc" and r.get("stored_value") == "Trp53",
       "-> reason=%r sentence_value=%r stored_value=%r"
       % (r.get("reason"), r.get("sentence_value"), r.get("stored_value")))

# a sentence without an accession is refused as 'no accession', returned not dropped
d = call({"tool": "gene_dossier", "params": {"gene": "Trp53"},
          "sentences": ["Trp53 is a tumour suppressor."]})
r = d["sentences"][0]
report("no-accession sentence: grounded:false 'no accession', returned not dropped",
       d["status"] == "refused" and r["grounded"] is False and r["reason"] == "no accession"
       and r["text"] == "Trp53 is a tumour suppressor.",
       "-> reason=%r text=%r" % (r.get("reason"), r.get("text")))

# one real answer composed from tonight's store, verified against records RE-READ from disk
sub = json.loads(subprocess.run(["python3", "-m", "annot", "call", "gene_dossier", "--json",
                                 '{"gene": "Trp53"}'], capture_output=True).stdout)["subject"]
sym, mgi, ens = sub["matched_symbol"], sub["mgi_accession"], sub["ensembl_gene_id"]
sentence = ("%s is a gene in the published view, with MGI accession %s and Ensembl gene id %s."
            % (sym, mgi, ens))
d = call({"tool": "gene_dossier", "params": {"gene": "Trp53"}, "sentences": [sentence]})
am = d["provenance"]["answer_mode"]
good = (d["status"] == "ok" and d["sentences"][0]["grounded"] is True
        and mgi in am["accessions_checked"] and ens in am["accessions_checked"]
        and am["sentences_refused"] == 0)
report("real answer from tonight's store: grounded, provenance names the accessions checked", good,
       "-> status=%r checked=%s refused=%d"
       % (d["status"], am["accessions_checked"], am["sentences_refused"]))

# opt-in proof: no default (non-answer) response carries a `mode` key
for tool, args in (("gene_dossier", '{"gene": "Trp53"}'), ("gene_xref", '{"gene": "Gstm7"}')):
    body = json.loads(subprocess.run(["python3", "-m", "annot", "call", tool, "--json", args],
                                     capture_output=True).stdout)
    good = "mode" not in body
    report("opt-in: %s (default mode) carries no 'mode' key" % tool, good, "-> keys have mode: %s"
           % ("mode" in body))
sys.exit(rc)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo "== H · A MISSING STORE IS NAMED, NEVER A TRACEBACK (the clone's first call) =="
# req-P0-A-CLONE-DOOR-MUST-SAY-STORE-MISSING (measured 2026-11-11 by an external critique of the
# public tree): the harvested store symbiont/store is WITHHELD from the public tree, so a fresh clone
# has none where the docs point — and `tools/call gene_dossier` answered
# `-32603 FileNotFoundError: … symbiont/store/view/mouse_gene.v1.provenance.json`. This suite could not
# see it: every section above runs with ANNOT_STORE exported to a store that exists, and the EMPTY
# check tests a GENE with no record, never a STORE with no files. The five cases below all run with
# ANNOT_STORE unset or pointed at a path that does not exist.
CLONE="$T/clone"
mkdir -p "$CLONE/annot" "$CLONE/symbiont/fixtures" \
  && cp "$FARM"/annot/*.py "$CLONE/annot/" \
  && cp -a "$FIXTURE_STORE" "$CLONE/symbiont/fixtures/store-doors" \
  && ok "H0 built a clone fixture: the shipped package + the committed fixture store, no live store" \
  || no "H0 cannot build the clone fixture under $CLONE"
has_tr(){ grep -qE 'Traceback \(most recent call last\)' "$1"; }   # a traceback on a door's stderr?

# H1 — ANNOT_STORE UNSET in that clone: a REAL DOSSIER from the committed fixture store.
# req-P1-A-CLONE-ANSWERS-OUT-OF-THE-BOX-THE-DEF (measured 2026-11-11): every section above exports
# ANNOT_STORE to a store this script chose, so the DEFAULT path — the one a stranger's first call
# takes — was never exercised; the suite's 21/21 said nothing about it. This case runs with the
# variable UNSET and asserts four things: status ok, a non-empty dossier whose every item carries
# source/release/accession (+ fetched_at/sha256), provenance intact against the view's own hash, and
# the ONE line saying which store answered: 'store: fixtures/store-doors — no live store found'.
FIXTURE_LINE="store: fixtures/store-doors — no live store found"
LIVE_LINE="store: symbiont/store — the live store"
( cd "$CLONE" && env -u ANNOT_STORE python3 -m annot call gene_dossier --json '{"gene":"Trp53"}' ) \
  >"$T/h1.json" 2>"$T/h1.err"
H1_RC=$?
H1=$(python3 - "$T/h1.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
p = d.get("provenance") or {}
v = p.get("view") or {}
items = []
def walk(o):
    if isinstance(o, dict):
        if "value" in o and "accession" in o:
            items.append(o)
        for x in o.values():
            walk(x)
    elif isinstance(o, list):
        for x in o:
            walk(x)
walk(d.get("sections") or {})
bad = [i for i in items if not (i.get("source") and i.get("release") and i.get("accession")
                                and i.get("fetched_at") and i.get("sha256"))]
print("%s|%s|%s|%s|%s|%d|%d" % (d.get("status"), p.get("store"), v.get("rows"), v.get("intact"),
                                 p.get("store_line"), len(items), len(bad)))
PY
)
IFS='|' read -r H1_ST H1_STORE H1_ROWS H1_INTACT H1_LINE H1_N H1_BAD <<<"$H1"
if [ "$H1_RC" = 0 ] && [ "$H1_ST" = "ok" ] \
   && [ "$H1_STORE" = "$CLONE/symbiont/fixtures/store-doors" ] && [ "$H1_INTACT" = "True" ] \
   && [ "$H1_LINE" = "$FIXTURE_LINE" ] && [ -n "$H1_N" ] && [ "$H1_N" -gt 0 ] && [ "$H1_BAD" = "0" ] \
   && ! has_tr "$T/h1.err"; then
  ok "H1 ANNOT_STORE unset, no live store: a REAL dossier from the committed fixture — $H1_N items each with source/release/accession, view $H1_ROWS rows intact, and the line '$H1_LINE'"
else
  no "H1 a clone's first call did not answer from the fixture with provenance (exit=$H1_RC, got '$H1', stderr $(head -c 160 "$T/h1.err"))"
fi

# H1b — THE SAME CALL WITH A REAL STORE PRESENT PREFERS IT (quoted): the clone is given the store the
# docs point at, ANNOT_STORE stays unset, and the answer must come from symbiont/store — never from the
# fixture, which is still on disk next to it.
CLONE_LIVE="$T/clone-live"
mkdir -p "$CLONE_LIVE/annot" "$CLONE_LIVE/symbiont" \
  && cp "$FARM"/annot/*.py "$CLONE_LIVE/annot/" \
  && cp -a "$FIXTURE_STORE" "$CLONE_LIVE/symbiont/store" \
  && mkdir -p "$CLONE_LIVE/symbiont/fixtures" \
  && cp -a "$FIXTURE_STORE" "$CLONE_LIVE/symbiont/fixtures/store-doors" \
  || no "H1b cannot build the clone-with-a-live-store fixture under $CLONE_LIVE"
( cd "$CLONE_LIVE" && env -u ANNOT_STORE python3 -m annot call gene_dossier --json '{"gene":"Trp53"}' ) \
  >"$T/h1b.json" 2>"$T/h1b.err"
H1B_RC=$?
H1B=$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1]));p=d.get("provenance") or {}
print("%s|%s|%s" % (d.get("status"), p.get("store"), p.get("store_line")))' "$T/h1b.json" 2>/dev/null)
[ "$H1B_RC" = 0 ] && [ "$H1B" = "ok|$CLONE_LIVE/symbiont/store|$LIVE_LINE" ] && ! has_tr "$T/h1b.err" \
  && ok "H1b ANNOT_STORE unset with a live store present: the same call PREFERS it — store $CLONE_LIVE/symbiont/store, line '$LIVE_LINE' (the fixture is still on disk and was not read)" \
  || no "H1b a live store present was not preferred (exit=$H1B_RC, got '$H1B', expected ok|$CLONE_LIVE/symbiont/store|$LIVE_LINE)"

# H2 — the SAME clone at the MCP door: `tools/call gene_dossier` is the call the row measured
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"gene_dossier","arguments":{"gene":"Trp53"}}}' \
  | ( cd "$CLONE" && env -u ANNOT_STORE python3 -m annot.mcp_server ) >"$T/h2.jsonl" 2>"$T/h2.err"
H2=$(python3 -c 'import json,sys
msgs=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
r=[m for m in msgs if m.get("id")==2][0]
if "error" in r: print("JSONRPC-ERROR|%s" % (r.get("error") or {}).get("code")); raise SystemExit
b=json.loads(r["result"]["content"][0]["text"])
print("%s|%s|%s" % (b.get("status"), r["result"].get("isError"),
                    (b.get("provenance") or {}).get("store_line")))' \
  "$T/h2.jsonl" 2>/dev/null)
[ "$H2" = "ok|False|$FIXTURE_LINE" ] && ! has_tr "$T/h2.err" \
  && ok "H2 tools/call gene_dossier in the same clone: no JSON-RPC error, status=ok (isError=False), provenance.store_line='$FIXTURE_LINE' — the call that used to be -32603 FileNotFoundError" \
  || no "H2 the MCP door at a clone answered '$H2' (expected ok|False|$FIXTURE_LINE); stderr $(head -c 160 "$T/h2.err")"

# H3 — ANNOT_STORE POINTED AT A PATH THAT DOES NOT EXIST: REFUSED[store-missing], that path named, exit 3
MISSING="$T/no-store-here"
env ANNOT_STORE="$MISSING" python3 -m annot call gene_dossier --json '{"gene":"Trp53"}' \
  >"$T/h3.json" 2>"$T/h3.err"
H3_RC=$?
H3=$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
m=d.get("message") or ""
print("%s|%s|%s|%s|%s" % (d.get("status"), d.get("reason"), "REFUSED[store-missing]" in m,
                          sys.argv[2] in m, ("symbiont.loop" in m and "symbiont.manifest" in m)))' \
  "$T/h3.json" "$MISSING" 2>/dev/null)
[ "$H3_RC" = 3 ] && [ "$H3" = "refused|store-missing|True|True|True" ] && ! has_tr "$T/h3.err" \
  && ok "H3 ANNOT_STORE=$MISSING: status=refused reason=store-missing, exit 3, the path and the two creating commands (loop, then manifest) named, no traceback" \
  || no "H3 a pointed-but-absent store gave exit=$H3_RC, '$H3' (expected refused|store-missing|True|True|True); stderr $(head -c 160 "$T/h3.err")"

# H4 — ANNOT_STORE UNSET WITH **NO STORE ANYWHERE** (no live store, no committed fixture): the same
# refusal, naming the DEFAULT path the docs tell a stranger to create. This is the case the row asked
# for — the suite finally exercises the default path rather than a store this script exported.
BARE="$T/bare"
mkdir -p "$BARE/annot" && cp "$FARM"/annot/*.py "$BARE/annot/"
( cd "$BARE" && env -u ANNOT_STORE python3 -m annot call gene_dossier --json '{"gene":"Trp53"}' ) \
  >"$T/h4.json" 2>"$T/h4.err"
H4_RC=$?
H4=$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
m=d.get("message") or ""
print("%s|%s|%s|%s" % (d.get("status"), d.get("reason"), "REFUSED[store-missing]" in m,
                       sys.argv[2] in m))' "$T/h4.json" "$BARE/symbiont/store" 2>/dev/null)
[ "$H4_RC" = 3 ] && [ "$H4" = "refused|store-missing|True|True" ] && ! has_tr "$T/h4.err" \
  && ok "H4 ANNOT_STORE unset and NO store anywhere: REFUSED[store-missing] naming the default $BARE/symbiont/store, exit 3, no traceback" \
  || no "H4 the unset-store case gave exit=$H4_RC, '$H4' (expected refused|store-missing|True|True); stderr $(head -c 160 "$T/h4.err")"

# H5 — A FAULT INJECTED INTO THE CORE: reason=core_fault and NOTHING ESCAPES, including when the fault
# also breaks the store read provenance() performs (that is the 2026-11-11 double traceback: the handler
# called provenance() from inside its own except block and re-raised from there).
( cd "$CLONE" && env -u ANNOT_STORE python3 - "$CLONE" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1])
import annot.core as core
rc = 0
def report(name, good, detail):
    global rc
    rc |= 0 if good else 1
    print("  %s %s %s" % ("PASS" if good else "FAIL", name, detail))
core.QUERIES["gene_dossier"] = lambda st, a: (_ for _ in ()).throw(RuntimeError("injected query fault"))
out = json.loads(core.dispatch("gene_dossier", {"gene": "Trp53"}))
report("(a) fault in the query: reason=core_fault, no traceback",
       out.get("status") == "error" and out.get("reason") == "core_fault"
       and "RuntimeError: injected query fault" in (out.get("message") or ""),
       "-> status=%r reason=%r" % (out.get("status"), out.get("reason")))
core.QUERIES["gene_dossier"] = core.q_gene_dossier
core._sha256_file = lambda p: (_ for _ in ()).throw(OSError("injected store fault"))
out = json.loads(core.dispatch("gene_dossier", {"gene": "Trp53"}))
report("(b) fault that also breaks provenance()'s own store read: still core_fault, still no traceback",
       out.get("status") == "error" and out.get("reason") == "core_fault"
       and "injected store fault" in ((out.get("provenance") or {}).get("unreadable") or ""),
       "-> status=%r reason=%r provenance.read=%r" % (out.get("status"), out.get("reason"),
                                                      (out.get("provenance") or {}).get("read")))
sys.exit(rc)
PY
) >"$T/h5.txt" 2>"$T/h5.err"
H5_RC=$?
sed 's/^  //' "$T/h5.txt"
[ "$H5_RC" = 0 ] && ! has_tr "$T/h5.err" \
  && ok "H5 a fault injected into the core returns reason=core_fault twice, including for a fault in the store read the handler itself performs" \
  || no "H5 an injected fault escaped or was not named (exit=$H5_RC); stderr $(head -c 200 "$T/h5.err")"

echo "== H6 · A VIEW STRIPPED OF ITS SIDECARS IS REFUSED BY NAME, NEVER READ (its own rule) =="
# req-P1-THE-PUBLISHED-DATASET-TRAVELS-WITH-IT (measured 2026-11-11): a view travels with
# *.provenance.json and *.schema.json — source · release · fetched_at · licence · rows · the view's
# sha256, and the columns. The .tsv alone is a number nobody can trace, so the door must REFUSE it
# with a class of its own (view-unreceipted), naming the sidecar that is missing and the one command
# that regenerates it. (a) is the control — the SAME store answers while both sidecars are there, so
# the refusal below cannot be the store, the query, or the tsv.
STRIPPED="$T/stripped"
cp -a "$FIXTURE_STORE" "$STRIPPED" || true
STRIP_TSV_SHA=$(sha256sum "$STRIPPED/view/mouse_gene.v1.tsv" | cut -d' ' -f1)
ctlp=$(env ANNOT_STORE="$STRIPPED" python3 -m annot call gene_dossier --json '{"gene":"Trp53"}' 2>"$T/h6c.err" \
  | python3 -c 'import json,sys
d=json.load(sys.stdin)
v=(d.get("provenance") or {}).get("view") or {}
print("%s|%s|%s|%s|%s" % (d.get("status"), v.get("rows"), v.get("intact"),
                        v.get("sha256") == sys.argv[1], (d.get("subject") or {}).get("matched_by")))' "$STRIP_TSV_SHA" \
  2>/dev/null)
[ "$ctlp" = "ok|241|True|True|symbol_casefold" ] && ! has_tr "$T/h6c.err" \
  && ok "H6 (a) control: the fixture store WITH both sidecars answers — status=ok, the door READ the view (241 rows, view.sha256 == sha256sum of the tsv, intact:true), Trp53 matched by symbol_casefold" \
  || no "H6 (a) the control store did not answer '$ctlp' (expected 'ok|241|True|True|symbol_casefold'); stderr $(head -c 120 "$T/h6c.err")"

# (b) both sidecars gone; (c) only the schema sidecar gone — each must be the SAME named refusal
for case in "both:$STRIPPED" "schema-only:$STRIPPED/schema"; do
  kind=${case%%:*}
  if [ "$kind" = "schema-only" ]; then
    rm -rf "$STRIPPED/schema" && cp -a "$FIXTURE_STORE" "$STRIPPED/schema" \
      && rm -f "$STRIPPED/schema/view/mouse_gene.v1.schema.json"
    S="$STRIPPED/schema"
  else
    rm -f "$STRIPPED/view/mouse_gene.v1.provenance.json" "$STRIPPED/view/mouse_gene.v1.schema.json"
    S="$STRIPPED"
  fi
  env ANNOT_STORE="$S" python3 -m annot call gene_dossier --json '{"gene":"Trp53"}' \
    >"$T/h6.$kind.json" 2>"$T/h6.$kind.err"
  RC=$?
  GOT=$(python3 -c 'import json,sys
s=sys.argv[1]
d=json.load(open(sys.argv[2]))
m=d.get("message") or ""
miss=" ".join(x.rsplit("/",1)[-1] for x in ((d.get("refusal") or {}).get("missing") or []))
print("%s|%s|%s|%s|%s|%s" % (d.get("status"), d.get("reason"), len(d.get("items") or []),
                            "REFUSED[view-unreceipted]" in m, miss,
                            "--rebuild-from-raw" in m))' "$S" "$T/h6.$kind.json" 2>/dev/null)
  want="refused|view-unreceipted|0|True|$([ "$kind" = schema-only ] && echo mouse_gene.v1.schema.json || echo "mouse_gene.v1.provenance.json mouse_gene.v1.schema.json")|True"
  [ "$RC" = 3 ] && [ "$GOT" = "$want" ] && ! has_tr "$T/h6.$kind.err" \
    && ok "H6 $kind stripped: status=refused reason=view-unreceipted, 0 items, exit 3, missing=$([ "$kind" = schema-only ] && echo 'schema only' || echo 'both sidecars') named, the regenerating command named, no traceback" \
    || no "H6 $kind stripped: exit=$RC '$GOT' (expected 3 '$want'); stderr $(head -c 160 "$T/h6.$kind.err")"
  [ "$(sha256sum "$S/view/mouse_gene.v1.tsv" | cut -d' ' -f1)" = "$STRIP_TSV_SHA" ] \
    && ok "H6 $kind stripped: the .tsv itself is byte-identical before/after — the door read nothing and wrote nothing" \
    || no "H6 $kind stripped: the .tsv changed during the refusal"
done

# (d) THE PUBLISHED VIEW TRAVELS WITH ITS RECEIPT — checked where it is, skipped by name where it is not.
# harvester-view/ is the directory a reviewer opens; its provenance sidecar must name the tsv's own
# sha256, so the number in the file and the number of the bytes cannot drift apart.
HV="$FARM/harvester-view"
if [ -f "$HV/mouse_gene.v1.tsv" ]; then
  HV_GOT=$(python3 - "$HV" <<'PY'
import hashlib, json, os, sys
hv = sys.argv[1]
tsv = os.path.join(hv, "mouse_gene.v1.tsv")
prov = os.path.join(hv, "mouse_gene.v1.provenance.json")
sch = os.path.join(hv, "mouse_gene.v1.schema.json")
h = hashlib.sha256(open(tsv, "rb").read()).hexdigest()
rows = sum(1 for _ in open(tsv, "rb")) - 1
try:
    p = json.load(open(prov))
    s = json.load(open(sch))
except Exception as e:
    print("unreadable|%s" % e); raise SystemExit
srcs = p.get("sources") or {}
named = sorted(k for k, v in srcs.items()
               if v.get("release") and v.get("fetched_at") and (v.get("licence") or {}).get("name"))
print("%s|%s|%s|%s|%s|%s|%s" % (h == p.get("view_sha256"), rows, p.get("rows"),
                                hashlib.sha256(open(sch, "rb").read()).hexdigest() == p.get("schema_sha256"),
                                len(s.get("columns") or []), ",".join(named), rows == 77144))
PY
)
  [ "$HV_GOT" = "True|77144|77144|True|6|ensembl_biomart,mgi|True" ] \
    && ok "H6 (d) harvester-view/: the sidecars are next to the tsv and agree with it — provenance sha256 == sha256sum of the tsv, rows=77144 in the file AND in the receipt, schema sha256 == sha256 of the .schema.json, 6 columns, and both sources name source·release·fetched_at·licence" \
    || no "H6 (d) harvester-view/ does not travel with a receipt that agrees with it: '$HV_GOT' (expected 'True|77144|77144|True|6|ensembl_biomart,mgi|True')"
else
  ok "H6 (d) no harvester-view/ in this tree (looked for $HV/mouse_gene.v1.tsv) — the published-view check does not apply here, and says so"
fi

echo "== I · WHAT_CHANGED PLACES EVERY TIMESTAMP AND NEVER MIXES NAIVE WITH AWARE =="
# (req-P1-WHAT-CHANGED-CANNOT-COMPARE-A-NAIVE) The fault this section pins: `what_changed(source=mgi,
# since=2026-11-01)` — an ordinary question, date only — parsed NAIVE while the run records parsed
# AWARE, so the `since` filter raised `TypeError: can't compare offset-naive and offset-aware
# datetimes` and every door above it answered JSON-RPC -32603. The fixture is the real store with a
# REPLACED runs/ directory: five records identical in every field except their instant, so the
# comparison itself is what is under test, not the store's data.
WCS="$T/wc-store"
cp -as "$STORE" "$WCS" 2>/dev/null
rm -rf "$WCS/runs"
mkdir -p "$WCS/runs"
python3 - "$WCS/runs" <<'PY'
import json, os, sys
d = sys.argv[1]
def rec(run_id, stamp, has=True):
    r = {"run_id": run_id, "sources": {"mgi": {
        "discover": {"release": "mgi-2026-11-02", "published_count": 241},
        "normalise": {"rows_in": 241, "records": 241, "rejects": 0},
        "fetch": {"new": True}, "verify": {"ok": True}}}}
    if has:
        r["finished_at"] = stamp
    with open(os.path.join(d, run_id + ".json"), "w") as fh:
        json.dump(r, fh, sort_keys=True, indent=2)
rec("wc-aware",  "2026-11-02T00:00:00+00:00")
rec("wc-naive",  "2026-11-02T00:00:00")
rec("wc-early",  "2026-10-01T00:00:00")
rec("wc-bad",    "eleven-ish")
rec("wc-absent", None, has=False)
PY

ANNOT_STORE="$WCS" python3 -m annot call what_changed --json '{"source":"mgi","since":"2026-11-01"}' \
  > "$T/wc-date-since.json" 2>"$T/wc-date-since.err"
WCRC=$?
ANNOT_STORE="$WCS" python3 -m annot call what_changed \
  --json '{"source":"mgi","since":"2026-11-01T00:00:00+00:00"}' > "$T/wc-aware-since.json" 2>&1

printf '%s\n' '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"annot-selftest","version":"1"}}}' \
              '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
              '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"what_changed","arguments":{"source":"mgi","since":"2026-11-01"}}}' \
  | ANNOT_STORE="$WCS" python3 -m annot.mcp_server > "$T/wc-mcp.jsonl" 2>"$T/wc-mcp.err"
python3 - "$T/wc-mcp.jsonl" <<'PY' > "$T/wc-mcp.json"
import json, sys
for line in open(sys.argv[1]):
    m = json.loads(line)
    if m.get("id") == 1:
        sys.stdout.write(m["result"]["content"][0]["text"])
        rpc = {"error": m.get("error"), "isError": m["result"].get("isError")}
        open(sys.argv[1] + ".rpc", "w").write(json.dumps(rpc))
PY

WCOUT=$(python3 - "$T" "$WCRC" <<'PY'
import datetime, json, os, sys
from annot.core import _iso
T, rc = sys.argv[1], sys.argv[2]
def p(pass_, msg):
    print("%s|%s" % ("PASS" if pass_ else "FAIL", msg))
err = open(os.path.join(T, "wc-date-since.err")).read()
raw = open(os.path.join(T, "wc-date-since.json")).read()
d = json.loads(raw)
items = {i["run_id"]: i for i in d["items"]}
p(rc == "0" and d["status"] == "ok" and not items == {} and "Traceback" not in err,
  "the row's own call what_changed(source=mgi, since=2026-11-01) answers (exit %s, status=%s, %d items, no traceback)"
  % (rc, d["status"], len(d["items"])))
p(d.get("reason") is None and (d.get("counts") or {}).get("uncomparable_timestamp") == 2,
  "no refusal and the two unplaceable records are COUNTED: status=%s reason=%r counts.uncomparable_timestamp=%r"
  % (d.get("status"), d.get("reason"), (d.get("counts") or {}).get("uncomparable_timestamp")))
def iso(s):
    s = s.strip()
    if s[-1:] in ("Z", "z"):
        s = s[:-1] + "+00:00"
    x = datetime.datetime.fromisoformat(s)
    return x if x.tzinfo else x.replace(tzinfo=datetime.timezone.utc)
a, n = items.get("wc-aware"), items.get("wc-naive")
proj = lambda i: (i["value"], i["source"], i["release"], i["accession"], i["verify_ok"], iso(i["fetched_at"]))
p(a is not None and n is not None and proj(a) == proj(n),
  "a NAIVE finished_at is handled IDENTICALLY to an AWARE one: wc-naive %r and wc-aware %r give the same item (same instant %r, same value, same release)"
  % (n and n["fetched_at"], a and a["fetched_at"], a and iso(a["fetched_at"])))
aware = json.load(open(os.path.join(T, "wc-aware-since.json")))
p(aware.get("items") == d["items"] and bool(d["items"]),
  "a naive `since` date and its aware twin select the SAME items: since=2026-11-01 == since=2026-11-01T00:00:00+00:00 (%d items, byte-identical list)" % len(d["items"]))
p("wc-early" not in items,
  "a naive instant BEFORE `since` is still excluded: wc-early (finished_at=2026-10-01T00:00:00) is NOT in the answer (%d items)" % len(items))
b, m = items.get("wc-bad"), items.get("wc-absent")
p(b is not None and b["reason"] == "uncomparable_timestamp" and b["fetched_at"] == "eleven-ish",
  "an UNPARSEABLE timestamp is reported ON the item: wc-bad reason=%r fetched_at=%r, and the rest of the answer returns (%d items)"
  % (b and b.get("reason"), b and b.get("fetched_at"), len(items)))
p(m is not None and m["reason"] == "uncomparable_timestamp" and "fetched_at" in m and m["fetched_at"] is None,
  "a MISSING timestamp is reported ON the item too: wc-absent reason=%r fetched_at key present=%s value=%r"
  % (m and m.get("reason"), m is not None and "fetched_at" in m, m and m.get("fetched_at")))
rpc = json.load(open(os.path.join(T, "wc-mcp.jsonl.rpc")))
mcp_text = open(os.path.join(T, "wc-mcp.json")).read()
p(rpc["error"] is None and rpc["isError"] is False and mcp_text == raw,
  "the MCP door has no JSON-RPC error for it: error=%r isError=%r, and its text is the CLI's bytes (cmp == 0, %d bytes)"
  % (rpc["error"], rpc["isError"], len(raw)))
probes = [("2026-11-01", True), ("2026-11-02T00:00:00", True), ("2026-11-02T00:00:00Z", True),
          ("2026-11-02T00:00:00+02:00", True), ("eleven-ish", False), ("", False), (None, False)]
got = [(v, _iso(v)) for v, _ in probes]
p(all((c is not None and c.tzinfo is not None) if want_instant else c is None
      for (v, want_instant), (_, c) in zip(probes, got)),
  "_iso() is the ONE normaliser and every instant it returns is AWARE (tzinfo set), every unparseable one is None: %s"
  % "; ".join("%r->%s" % (v, "None" if c is None else ("aware " + c.isoformat())
                            if c.tzinfo is not None else ("NAIVE " + c.isoformat())) for v, c in got))
PY
)
echo "$WCOUT" | grep -v '^$' | sed 's/^PASS|/  PASS /; s/^FAIL|/  FAIL /'
PASS=$((PASS+$(echo "$WCOUT" | grep -c '^PASS')))
FAIL=$((FAIL+$(echo "$WCOUT" | grep -c '^FAIL')))

echo "== F · THE STORE WAS NOT TOUCHED BY ANY OF THE ABOVE =="
AFTER=$(manifest)
[ "$BEFORE" = "$AFTER" ] \
  && ok "$STORE manifest identical before/after ($(echo "$AFTER" | cut -c1-16)…) — 0 bytes written" \
  || no "the store CHANGED during the run"

echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ] || exit 1
exit 0
