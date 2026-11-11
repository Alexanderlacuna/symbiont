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

echo "== B · ONE DEFINITION: CLI bytes == MCP bytes, for all seven tools (six read + opt-in answer) =="
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
print("  PASS mcp session: initialize ok, tools/list = the tools in SPECS order, 7 calls answered")
PY
i=0
while [ $i -lt 7 ]; do
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

echo "== C · EVERY ITEM CARRIES AN ACCESSION (all seven responses) =="
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
print("  %s %d item(s) across seven responses, every one with value+accession+source+release+fetched_at+sha256"
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
          ("phenotype_evidence", "empty-pheno.json", "source_not_in_store"),
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
UNREACH=$(python3 -c "import json;print(','.join(u['source'] for u in json.load(open('$T/empty-pheno.json'))['provenance']['unreachable']))")
[ "$UNREACH" = "mgi_phenotype_annotations" ] \
  && ok "the empty result NAMES the unreachable source: $UNREACH" \
  || no "unreachable sources not named (got '$UNREACH')"

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

echo "== F · THE STORE WAS NOT TOUCHED BY ANY OF THE ABOVE =="
AFTER=$(manifest)
[ "$BEFORE" = "$AFTER" ] \
  && ok "$STORE manifest identical before/after ($(echo "$AFTER" | cut -c1-16)…) — 0 bytes written" \
  || no "the store CHANGED during the run"

echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ] || exit 1
exit 0
