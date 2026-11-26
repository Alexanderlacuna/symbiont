#!/bin/bash
# STATION-JOB — the ONE implementation of symbiont/docs/SCHEDULE.md (req-P1-THE-SCHEDULE-MODEL-
# when-the-station). One entry point, one cron table entry per job, every fire logged.
#
#   symbiont/station-job.sh fetch <source>   weekly, per source, staggered (SCHEDULE.md §1)
#   symbiont/station-job.sh digest           daily   (digest-<date>.md: health + ticks + findings)
#   symbiont/station-job.sh discovery        weekly  (tools/discover-sweep.sh)
#   symbiont/station-job.sh health           hourly  (health-digest.md + the 2x-overdue findings)
#   symbiont/station-job.sh manifest         daily, ON CHANGE ONLY (dry-run, compare, then write)
#
# EVERY fire appends ONE tick line to <store>/schedule/ticks.jsonl (SCHEDULE.md §3):
#   {"ts":…,"iso":"…","job":"…","source":"…","jobkey":"…","outcome":"ran|missed","reason":"…","detail":"…","pid":…}
# A missed tick ALWAYS carries its reason: held | locked | budget | down | cap. On the first
# successful pass after a miss (or a skipped interval) the tick says "I caught up: last pass <age>".
#
# Exit codes: 0 ran · 2 usage · 3 REFUSED[locked] (an overlapping fire holds the source) ·
# 4 MISSED (held|budget|cap|down — logged, never silent).
#
# Environment: FARM (default /home/kabui/app-farm) · STORE (default $FARM/symbiont/store) ·
# STATION_MAX_INFLIGHT (cap N sources in flight, default 2) · STATION_SOURCES (sources.yml path;
# default $APP_ROOT/symbiont/sources.yml) · STATION_EPOCH_OFFSET (fixtures: shift the clock, seconds) ·
# STATION_FETCH_CMD / STATION_DISCOVERY_CMD (fixtures: replace the provider-facing command; a
# fetch stub also skips the connectivity probe — the stub owns its own failures).
set -u

FARM="${FARM:-/home/kabui/app-farm}"                     # hb.sh + OWNER-HOLD live here
APP_ROOT=$(cd "$(dirname "$0")/.." && pwd)                # the code root (python3 -m symbiont runs from here)
STORE="${STORE:-$FARM/symbiont/store}"
export FARM                       # nurse/hb.sh reads FARM at source time (sandbox override)
JOB="${1:-}"
SOURCE="${2:-}"
SCHED="$STORE/schedule"
TICKS="$SCHED/ticks.jsonl"
LOCKS="$SCHED/locks"
LAST="$SCHED/last"
MAXIN="${STATION_MAX_INFLIGHT:-2}"
EPOCH_OFFSET="${STATION_EPOCH_OFFSET:-0}"

case "$JOB" in
  fetch)     [ -n "$SOURCE" ] || { echo "REFUSED[usage] station-job.sh fetch needs a source id"; exit 2; }
             HB="station-fetch-$SOURCE"; JOBKEY="fetch-$SOURCE"; INTERVAL=604800 ;;   # weekly
  digest)    HB="station-digest";    JOBKEY="digest";    INTERVAL=86400 ;;             # daily
  discovery) HB="station-discovery"; JOBKEY="discovery"; INTERVAL=604800 ;;           # weekly
  health)    HB="station-health";    JOBKEY="health";    INTERVAL=3600  ;;            # hourly
  manifest)  HB="station-manifest";  JOBKEY="manifest";  INTERVAL=86400 ;;             # daily
  *) echo "REFUSED[usage] station-job.sh: unknown job '${JOB:-}'"; exit 2 ;;
esac

# The heartbeat contract (one row per job, nurse/heartbeats/REGISTRY.tsv): run marker at start,
# exit= ts= iso= pid= dur= stamp on exit. FARM override lets a fixture stamp a sandbox.
. "$FARM/nurse/hb.sh" "$HB"

mkdir -p "$SCHED" "$LOCKS" "$LAST" "$SCHED/manifests" 2>/dev/null || {
  echo "MISSED station-$JOBKEY reason=down: cannot create $SCHED"; exit 4; }
[ -e "$TICKS" ] || : > "$TICKS"        # the log exists from the first fire on — a reader never waits for it

# ── clock ────────────────────────────────────────────────────────────────────────────────
now()    { echo $(( $(date +%s) + EPOCH_OFFSET )); }
isots()  { date -u -d "@$1" '+%Y-%m-%dT%H:%M:%SZ'; }
human()  { local s=$1 d h m; d=$((s/86400)); h=$(((s%86400)/3600)); m=$(((s%3600)/60));
           [ $d -gt 0 ] && echo "${d}d${h}h" || { [ $h -gt 0 ] && echo "${h}h${m}m" || echo "${m}m"; }; }
esc()    { printf '%s' "$1" | tr '\n\r\t' '   ' | sed 's/\\/\\\\/g; s/"/\\"/g'; }

# ── the tick log: ONE line per fire, appended under a lock, never a silent gap (§3) ─────────
tick() { # outcome reason detail
  local out="${1:-ran}" reason="${2:-}" detail ts iso
  detail=$(esc "${3:-}"); reason=$(esc "$reason")
  ts=$(now); iso=$(isots "$ts")
  { flock 8
    printf '{"ts":%s,"iso":"%s","job":"%s","source":"%s","jobkey":"%s","outcome":"%s","reason":"%s","detail":"%s","pid":%s}\n' \
      "$ts" "$iso" "$JOB" "${SOURCE:-}" "$JOBKEY" "$out" "$reason" "$detail" "$$" >> "$TICKS"
  } 8>"$SCHED/.ticks.lock"
}
miss() { # reason detail [code] — a missed tick is LOGGED with its reason, then we go (§3)
  local reason="$1" detail="$2" code="${3:-4}"
  tick missed "$reason" "$detail"
  echo "MISSED station-$JOBKEY reason=$reason: $detail"
  exit "$code"
}

# ── the owner's HOLD switch: read only (tools/farm-hold.sh is the only writer) ─────────────
hold_reason() { sed -n 's/^REASON=//p' "$FARM/nurse/OWNER-HOLD" 2>/dev/null | head -1; }

# ── catch-up (§3): the first pass after a miss / a skipped interval says so ───────────────
# state = "<last_ran_ts> <missed_since>" for this jobkey, read from the tick log (the truth).
sched_state() {
  python3 - "$TICKS" "$JOBKEY" <<'PY' 2>/dev/null
import json, sys
last_ran, missed = 0, 0
for ln in open(sys.argv[1], encoding="utf-8", errors="replace"):
    try: r = json.loads(ln)
    except Exception: continue
    if r.get("jobkey") != sys.argv[2]: continue
    if r.get("outcome") == "ran": last_ran, missed = r.get("ts", 0), 0
    else: missed += 1
print(f"{last_ran} {missed}")
PY
}
lastpass_ts() { sed -n 's/.*"ts": *\([0-9]*\).*/\1/p' "$LAST/$JOBKEY.json" 2>/dev/null | head -1; }
stamp_lastpass() { local ts iso; ts=$(now); iso=$(isots "$ts")
  printf '{"jobkey":"%s","ts":%s,"iso":"%s"}\n' "$JOBKEY" "$ts" "$iso" > "$LAST/$JOBKEY.json.tmp.$$" \
    && mv -f "$LAST/$JOBKEY.json.tmp.$$" "$LAST/$JOBKEY.json"; }
# prints the catch-up line if this successful pass follows misses or a skipped interval
catchup() {
  local prev missed
  prev=$(lastpass_ts); [ -n "$prev" ] || return 0
  read -r _ missed <<<"$(sched_state)"
  local age=$(( $(now) - prev ))
  if [ "${missed:-0}" -gt 0 ] || [ "$age" -gt "$INTERVAL" ]; then
    CAUGHT="I caught up: last pass $(human "$age") ago (${missed} missed tick(s) since), interval $(human "$INTERVAL")"
    echo "$CAUGHT"
  fi
}

# ── the 2x-overdue findings (§4): the health digest names every job silent too long ─────────
# prints one line per finding ("FINDING station-job <jobkey> …") or "none". Sources come from
# sources.yml (the ONE definition site); intervals are the model's (SCHEDULE.md §1).
station_findings() {
  STATION_SOURCES_DEF="${STATION_SOURCES:-$APP_ROOT/symbiont/sources.yml}" \
  STATION_SCHED_LAST="$LAST" python3 <<'PY' 2>/dev/null
import glob, json, os, re, time
INTERVALS = {"digest": 86400, "discovery": 604800, "health": 3600, "manifest": 86400}
def human(s):
    d, h, m = s // 86400, (s % 86400) // 3600, (s % 3600) // 60
    return f"{d}d{h}h" if d else (f"{h}h{m}m" if h else f"{m}m")
try:
    srcs = re.findall(r"^\s*- id:\s*(\S+)", open(os.environ["STATION_SOURCES_DEF"]).read(), re.M)
except Exception:
    srcs = []
keys = [(f"fetch-{s}", 604800) for s in srcs] + list(INTERVALS.items())
last_dir = os.environ["STATION_SCHED_LAST"]
nw = int(time.time())
out = []
for key, interval in keys:
    p = os.path.join(last_dir, f"{key}.json")
    try:
        ts = json.load(open(p))["ts"]
    except Exception:
        out.append(f"FINDING station-job {key} NEVER-RAN: no last-pass record in {last_dir} "
                   f"(fail closed — a job that has never run is a named finding, SCHEDULE.md §4)")
        continue
    age = nw - ts
    if age > 2 * interval:
        out.append(f"FINDING station-job {key} OVERDUE: last pass {human(age)} ago, "
                   f"interval {human(interval)} (2x overdue — SCHEDULE.md §4)")
print("\n".join(out) if out else "none: every station job ran within 2x its interval")
PY
}

# ── provider connectivity probe (§3 down): zero provider requests when the net is down ────
# prints 1 reachable / 0 not. base_url from sources.yml — the ONE definition site.
probe() { # $1 = source id
  STATION_SOURCES_DEF="${STATION_SOURCES:-$APP_ROOT/symbiont/sources.yml}" \
  python3 - "$1" <<'PY' 2>/dev/null
import os, socket, sys, urllib.parse
try:
    import yaml
    cfg = yaml.safe_load(open(os.environ["STATION_SOURCES_DEF"]))
    base = next(s["base_url"] for s in cfg["sources"] if s.get("id") == sys.argv[1])
    u = urllib.parse.urlparse(base)
    port = u.port or (443 if u.scheme == "https" else 80)
    socket.create_connection((u.hostname, port), timeout=3).close()
    print(1)
except Exception:
    print(0)
PY
}

# ── the jobs ─────────────────────────────────────────────────────────────────────────────

do_fetch() {
  [ -f "$FARM/nurse/OWNER-HOLD" ] && miss held "owner HOLD is set ($(hold_reason)): the fetch stands down (SCHEDULE.md §3)"
  # global cap: count OTHER sources currently in flight (probe each lock with flock -n)
  local -a holders=()
  local lf
  for lf in "$LOCKS"/*.lock; do
    [ -e "$lf" ] || continue
    [ "$(basename "$lf")" = "$SOURCE.lock" ] && continue
    flock -n "$lf" true 2>/dev/null || holders+=("$(head -1 "$lf" 2>/dev/null)")
  done
  [ "${#holders[@]}" -ge "$MAXIN" ] && miss cap "no more than $MAXIN source(s) in flight; held now: ${holders[*]}"
  # per-source lock: ONE run per source at a time — the second fire REFUSES, quoting the holder
  local lock="$LOCKS/$SOURCE.lock" holder
  exec 9>>"$lock"
  flock -n 9 || { holder=$(head -1 "$lock" 2>/dev/null)
                  tick missed locked "held by: $holder"
                  echo "REFUSED[locked] $SOURCE: held by $holder"; exit 3; }
  local ts; ts=$(now)
  printf 'pid=%s job=fetch ts=%s iso=%s\n' "$$" "$ts" "$(isots "$ts")" > "$lock"
  # network down is a LOGGED miss that spends zero provider requests (§3, §4)
  if [ -z "${STATION_FETCH_CMD:-}" ]; then
    [ "$(probe "$SOURCE")" = 1 ] || miss down "I looked: $SOURCE did not answer (pre-flight connect refused) — logged, not silent; zero provider requests spent"
  fi
  local out rc
  if [ -n "${STATION_FETCH_CMD:-}" ]; then
    out=$(bash -c "$STATION_FETCH_CMD" 2>&1); rc=$?
  else
    out=$(cd "$APP_ROOT" && python3 -m symbiont.loop --store "$STORE" --sources "$SOURCE" \
          ${STATION_SOURCES:+--sources-config "$STATION_SOURCES"} 2>&1); rc=$?
  fi
  if [ $rc -eq 0 ]; then
    local caught=""; caught=$(catchup); stamp_lastpass
    tick ran "" "exit=0${caught:+; $caught}"
    [ -n "$caught" ] && echo "$caught"
    exit 0
  fi
  if grep -q 'REFUSED\[budget\]' <<<"$out"; then
    miss budget "$(grep 'REFUSED\[budget\]' <<<"$out" | head -1)"
  fi
  if grep -q 'source-unreachable' <<<"$out"; then
    miss down "I looked: $SOURCE refused source-unreachable — logged, not silent: $(grep 'source-unreachable' <<<"$out" | head -1)"
  fi
  tick ran "" "refused exit=$rc: $(head -1 <<<"$out")"
  exit "$rc"
}

do_health() {   # disk only, zero requests: the watchman keeps looking even under HOLD and offline
  local f="$SCHED/health-digest.md"
  local tmp="$f.tmp.$$" ts iso findings
  ts=$(now); iso=$(isots "$ts")
  findings=$(station_findings)
  { echo "# STATION HEALTH DIGEST — $iso (hourly probe, schedule model §4)"
    echo
    ( cd "$APP_ROOT" && python3 -m symbiont.health --store "$STORE" 2>&1 )
    echo
    echo "## schedule findings (a job 2x its interval silent is a NAMED finding)"
    printf '%s\n' "$findings"
    echo
    echo "## ticks, last 24h (from schedule/ticks.jsonl)"
    awk -v since=$((ts-86400)) -F'"ts":' 'NF>1 {split($2,a,/[^0-9]/); t=a[1]+0; if (t>=since) c++} END {print c+0 " tick(s)"}' "$TICKS" 2>/dev/null || true
  } > "$tmp" && mv -f "$tmp" "$f"
  grep '^FINDING' <<<"$findings" || true
  tick ran "" "health-digest.md written; findings: $(head -1 <<<"$findings")"
  local caught=""; caught=$(catchup); stamp_lastpass
  [ -n "$caught" ] && echo "$caught"
  exit 0
}

do_digest() {   # the daily digest page (§1): health, the day's ticks, the findings
  [ -f "$FARM/nurse/OWNER-HOLD" ] && miss held "owner HOLD is set ($(hold_reason)): the digest pass stands down (SCHEDULE.md §3)"
  local ts iso day f tmp findings caught dg
  ts=$(now); iso=$(isots "$ts"); day=${iso%%T*}
  f="$SCHED/digest-$day.md"; tmp="$f.tmp.$$"
  findings=$(station_findings)
  # the four digests' daily refresh (symbiont/docs/DIGESTS.md): gaps + suggest are re-measured
  # from the store disk-only (zero requests); a store with no harvest run has nothing to
  # re-measure and the page says so — the pass never fails on it.
  dg=$( cd "$APP_ROOT" && python3 -m symbiont.digests write --store "$STORE" 2>&1 ) \
    && dg="gaps+suggest refreshed:$(printf '%s' "$dg" | tr '\n' ' ')" \
    || dg="gaps+suggest NOT refreshed: no harvest run record in $STORE - nothing to re-measure"
  { echo "# STATION DIGEST — $day (daily pass, schedule model §1)"
    echo
    echo "## the four digests, daily refresh (DIGESTS.md)"
    echo "    $dg"
    echo
    echo "## per-source health (symbiont health --store — disk only, zero requests)"
    ( cd "$APP_ROOT" && python3 -m symbiont.health --store "$STORE" 2>&1 | sed 's/^/    /' )
    echo
    echo "## today's ticks (from schedule/ticks.jsonl)"
    grep "\"iso\":\"$day" "$TICKS" 2>/dev/null | sed 's/^/    /' || echo "    (none yet)"
    echo
    echo "## schedule findings (2x overdue = a NAMED finding, SCHEDULE.md §4)"
    printf '%s\n' "$findings"
  } > "$tmp" && mv -f "$tmp" "$f"
  tick ran "" "digest page $f"
  caught=$(catchup); stamp_lastpass
  [ -n "$caught" ] && echo "$caught"
  exit 0
}

do_discovery() {
  [ -f "$FARM/nurse/OWNER-HOLD" ] && miss held "owner HOLD is set ($(hold_reason)): the discovery sweep stands down (SCHEDULE.md §3)"
  local out rc
  if [ -n "${STATION_DISCOVERY_CMD:-}" ]; then out=$(bash -c "$STATION_DISCOVERY_CMD" 2>&1); rc=$?
  else out=$(cd "$APP_ROOT" && bash tools/discover-sweep.sh 2>&1); rc=$?; fi
  [ $rc -eq 0 ] || miss down "I looked: discovery sweep exit=$rc — logged, not silent: $(head -1 <<<"$out")"
  local caught=""; caught=$(catchup); stamp_lastpass
  tick ran "" "$(grep 'DISCOVERY-SWEEP' <<<"$out" | tail -1)"
  [ -n "$caught" ] && echo "$caught"
  exit 0
}

do_manifest() {   # ON CHANGE ONLY: dry-run, compare the staged sha256, write only if changed
  [ -f "$FARM/nurse/OWNER-HOLD" ] && miss held "owner HOLD is set ($(hold_reason)): the manifest pass stands down (SCHEDULE.md §3)"
  local out rc sha prev ts iso caught
  out=$(cd "$APP_ROOT" && python3 -m symbiont.manifest --store "$STORE" --entity gene --dry-run 2>&1); rc=$?
  [ $rc -eq 0 ] || { tick ran refused "dry-run exit=$rc: $(head -1 <<<"$out")"; exit "$rc"; }
  sha=$(grep -o 'sha256=[0-9a-f]*' <<<"$out" | head -1 | cut -d= -f2)
  prev=$(cat "$LAST/manifest.sha" 2>/dev/null)
  if [ "$sha" = "$prev" ] && [ -n "$prev" ]; then
    caught=$(catchup); stamp_lastpass
    tick ran "" "no change: staged sha256 $sha — nothing regenerated (on change only, §1)"
    [ -n "$caught" ] && echo "$caught"
    exit 0
  fi
  ts=$(now); iso=$(isots "$ts")
  out=$(cd "$APP_ROOT" && python3 -m symbiont.manifest --store "$STORE" --entity gene \
        --out "$SCHED/manifests/manifest-$iso.json" 2>&1); rc=$?
  [ $rc -eq 0 ] || { tick ran refused "manifest write exit=$rc: $(head -1 <<<"$out")"; exit "$rc"; }
  echo "$sha" > "$LAST/manifest.sha"
  caught=$(catchup); stamp_lastpass
  tick ran "" "regenerated: staged sha256 $sha (was ${prev:-none}) -> manifests/manifest-$iso.json"
  [ -n "$caught" ] && echo "$caught"
  exit 0
}

case "$JOB" in
  fetch)     do_fetch ;;
  digest)    do_digest ;;
  discovery) do_discovery ;;
  health)    do_health ;;
  manifest)  do_manifest ;;
esac
