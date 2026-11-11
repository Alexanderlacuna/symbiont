"""PROOF of the research layer's sandbox (req-P1-THE-RESEARCH-LAYERS-SANDBOX-roles-and).

Four fixtures, each FAILING ON PURPOSE, each one refusal named:

  F1 WRITE ALLOWLIST (lane)     a research lane that writes outside its allowlist is REFUSED
                                naming the path, and THE FILE IS NOT CREATED (kernel: read-only
                                root, only the allowlist bound read-write). The same lane's own
                                report IS written — the positive control that proves the runner
                                is not simply refusing everything.
  F2 NETWORK ALLOWLIST (adapter) a drafted adapter that calls a host outside its entry's
                                base_url is refused AT CONNECT, the host named, and the refusal
                                is in the run record. The allowed host is reached in F3.
  F3 THROWAWAY STORE            a sandbox run leaves the REAL store byte-identical (dir hash
                                before/after quoted in the receipt), the throwaway store's hash
                                is recorded and the store is deleted on exit.
  F4 ROLES READ-ONLY            a role that tries to write the queue is refused BY THE NAME OF
                                THE CLASS (queue), and the queue is byte-identical afterwards.
  F5 THE HOSTILE FIXTURE        an adapter that calls ctypes raw connect() to 127.0.0.1:8002 is
                                CONNECTED with the python guard armed and the kernel rule off (the
                                guard does not see it) and REFUSED with the rule on — twice, once
                                with the guard armed and once with it off, so "the kernel refused
                                it" is not riding on the guard. (req-P1-NETWORK-POLICY-TIERS)
  F6 THE RANGE IS NAMED         the same fixture reaching 192.168.7.7 is refused and the record
                                names 192.168.0.0/16, read from the kernel's own rule counters;
                                with no ruleset the SAME target connects — the rule is the reason.
  F7 TIER-STRICT base_url       generated from a real sources.yml entry: the adapter reaches its
                                own base_url host and its NEIGHBOUR does not.
  F8 THE THREE DEPLOYMENTS      the docs state, per deployment, WHO the policy protects, and each
                                of the three quotes the tested policy (the ranges it denies).

Nothing here touches the network: a local HTTP server on 127.0.0.1 serves the fixture source, and
every path lives under one temp directory. The real store is only ever READ (hashed).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORBIDDEN = os.path.join(FARM, "reports", "SANDBOX-FIXTURE-BREACH.md")
# THE QUEUE THIS SUITE MAY AIM AT. The farm's own queue is farm-only by DESIGN §1b, so a clone
# has none; the shipped fixture queue stands in for it (the kernel does not care which file it
# refuses, and F4a still names the class by the REAL path). See symbiont/fixtures/queue/.
FIXTURE_QUEUE = os.path.join(FARM, "symbiont", "fixtures", "queue", "STANDING-JOBS.tsv")

GENES = """symbol\tMGI id\tchrom\tstart\tend
Trp53\tMGI:98834\t11\t69580359\t69581786
Brca1\tMGI:104537\t11\t101471872\t101543608
Epcam\tMGI:106653\t17\t87652551\t87659529
"""
LICENCE = ("<html><body>CC0 1.0 Universal — public domain dedication. You are free to store and "
           "redistribute this data.</body></html>")

results: list[tuple[str, bool]] = []
skipped: list[str] = []


def check(name: str, good: bool, detail: str = "") -> None:
    results.append((name, good))
    print(f"  {'PASS' if good else 'FAIL'} {name}" + (f"  {detail}" if detail else ""), flush=True)


def skip(name: str, missing: str, path: str) -> None:
    """An ABSENT input is a SKIP that names itself — never a red line. A PRESENT-but-wrong input
    stays a FAIL (that is what `check` is for)."""
    skipped.append(name)
    print(f"  SKIP {name}: {missing} at {path}", flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self._send(GENES.encode(), "text/tab-separated-values")

    def do_GET(self):
        if self.path.startswith("/genes"):
            self._send(GENES.encode(), "text/tab-separated-values")
        elif self.path.startswith("/licence"):
            self._send(LICENCE.encode(), "text/html")
        else:
            self._send(b"not found", "text/plain", 404)


def admission(tmp: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "symbiont.admission", *args,
                           "--admission-dir", os.path.join(tmp, "adm"),
                           "--drafts-dir", os.path.join(tmp, "drafts"),
                           "--adapters-dir", os.path.join(tmp, "adapters"),
                           "--sources-config", os.path.join(tmp, "sources.yml"),
                           "--store", os.path.join(tmp, "store")],
                          capture_output=True, text=True, cwd=FARM)


def sha_file(p: str) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.isfile(p) else "MISSING"


# THE HOSTILE FIXTURE — the adapter that "today would pass". It binds a listener on the target
# FIRST (so "nothing was listening" can never be mistaken for a refusal), then issues the connect
# as a raw libc call on a bare fd: socket.getaddrinfo / create_connection / socket.connect — the
# three doors the python guard wraps — are never entered. Nothing here is hostile beyond that.
RAW_CONNECT = '''import ctypes, errno as E, os, select, socket, struct, sys, threading, time
HOST, PORT, BUDGET = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])


class SA(ctypes.Structure):
    _fields_ = [("family", ctypes.c_ushort), ("port", ctypes.c_ushort),
                ("addr", ctypes.c_uint32), ("zero", ctypes.c_ubyte * 8)]


srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    srv.bind((HOST, PORT))
    srv.listen(8)
    threading.Thread(target=lambda: srv.accept(), daemon=True).start()
    print(f"listener {HOST}:{PORT} bound", flush=True)
except OSError as e:
    print(f"listener {HOST}:{PORT} NOT bound: {e}", flush=True)

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)      # only .connect is on the guard's list
s.setblocking(False)
sa = SA(socket.AF_INET, socket.htons(PORT), struct.unpack("=I", socket.inet_aton(HOST))[0])
libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.connect.argtypes = [ctypes.c_int, ctypes.POINTER(SA), ctypes.c_uint]
libc.connect.restype = ctypes.c_int
ctypes.set_errno(0)
t0 = time.time()
rc = libc.connect(s.fileno(), ctypes.byref(sa), ctypes.sizeof(sa))
err = ctypes.get_errno()
if rc != 0 and err not in (E.EINPROGRESS, E.EALREADY, E.EAGAIN):
    state = f"refused errno={err} {E.errorcode.get(err, '?')}"
else:
    poller = select.poll()
    poller.register(s, select.POLLOUT)
    if poller.poll(int(BUDGET * 1000)):
        so = s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        state = "CONNECTED" if so == 0 else f"refused errno={so} {E.errorcode.get(so, '?')}"
    else:
        state = "NO ANSWER (the connection never completed)"
print(f"raw connect() to {HOST}:{PORT} -> {state} after {time.time() - t0:.2f}s", flush=True)
sys.exit(0 if state == "CONNECTED" else 7)
'''

# THE CARELESS FIXTURE — the same attempt through urllib, i.e. through the doors the python guard
# wraps. This is what the guard is FOR: it names the host and the door before the kernel is asked.
CARELESS_CONNECT = '''import sys, urllib.request
url = sys.argv[1]
try:
    urllib.request.urlopen(url, timeout=1.0).read()
    print(f"FETCHED {url} — nothing refused it", flush=True)
    sys.exit(0)
except Exception as e:
    print(f"REFUSED {url} by {type(e).__name__}: {e}", flush=True)
    sys.exit(7)
'''


def run_fixture(sandbox, tmp: str, body: str, argv: list, *, network: dict,
                guard: bool, name: str) -> dict:
    """One confined run of one fixture. The ONLY thing that differs between poles is the
    ruleset and whether the guard is armed — never the fixture, never the target."""
    path = os.path.join(tmp, f"{name}.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return sandbox.run([sys.executable, path, *[str(a) for a in argv]], class_name="adapter",
                       cwd=FARM, runs_dir=os.path.join(tmp, "runs"),
                       capture_stdout=True, network=network, network_guard_armed=guard)


def main() -> int:
    import symbiont.sandbox as sandbox

    print("== selftest_sandbox: the research layer's sandbox (fixtures only) ==", flush=True)
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}/genes.tsv"
    licence = f"http://127.0.0.1:{port}/licence.html"
    tmp = tempfile.mkdtemp(prefix="symbiont-sandbox-test-")
    try:
        # ══ F1 · THE WRITE ALLOWLIST (lane) ═══════════════════════════════════════════════
        print("\nF1 write allowlist — a lane that writes outside it is refused, the path named, "
              "the file NOT created", flush=True)
        if os.path.exists(FORBIDDEN):
            os.unlink(FORBIDDEN)                       # a stale probe from an earlier run
        lane = os.path.join(tmp, "lane.sh")
        report = os.path.join(tmp, "own-report.md")
        with open(lane, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/bash\n"
                     "echo 'my own page' > \"$1\"\n"
                     f"echo 'breach' > {FORBIDDEN}\n"
                     "echo 'after the attempt'\n")
        os.chmod(lane, 0o755)
        rec = sandbox.run([lane, report], class_name="research", report=report,
                          runs_dir=os.path.join(tmp, "runs"))
        named = [f for f in rec["findings"] if f.get("path") == os.path.realpath(FORBIDDEN)]
        check("F1a a research lane writing outside its allowlist -> REFUSED[sandbox-breach], "
              "exit 126", rec["exit"] == sandbox.BREACH_EXIT and bool(rec["findings"]),
              f"exit={rec['exit']} findings={len(rec['findings'])}")
        check("F1b the refusal NAMES the path", bool(named),
              f"named={[f.get('path') for f in rec['findings']]}")
        check("F1c the file is NOT created (the kernel refused, nothing repaired)", not os.path.exists(FORBIDDEN),
              f"test -e {FORBIDDEN} -> {'exists' if os.path.exists(FORBIDDEN) else 'absent'}")
        check("F1d the run record carries the finding, class sandbox-breach",
              bool(rec.get("record")) and os.path.isfile(rec["record"] or "")
              and json.load(open(rec["record"], encoding="utf-8"))["breach"] is True,
              f"record={rec.get('record')}")
        check("F1e POSITIVE CONTROL: the lane's OWN report IS written",
              os.path.isfile(report) and open(report, encoding="utf-8").read().strip() == "my own page",
              f"report={sha_file(report)[:16]}…")

        py = os.path.join(tmp, "pylane.py")
        with open(py, "w", encoding="utf-8") as fh:
            fh.write("import sys\n"
                     "try:\n"
                     f"    open({FORBIDDEN!r}, 'w').write('x')\n"
                     "    print('WROTE (BAD)')\n"
                     "except Exception as e:\n"
                     "    print('refused:', type(e).__name__)\n")
        rec_py = sandbox.run([sys.executable, py], class_name="role",
                             report=os.path.join(tmp, "role-page.md"),
                             runs_dir=os.path.join(tmp, "runs"))
        named_py = [f for f in rec_py["findings"] if f.get("path") == os.path.realpath(FORBIDDEN)]
        check("F1f a PYTHON child is refused by the audit hook, which names the exact path "
              "(the kernel alone says only EROFS)",
              rec_py["exit"] == sandbox.BREACH_EXIT and named_py and named_py[0]["event"] == "open",
              f"exit={rec_py['exit']} event={named_py[0]['event'] if named_py else '—'}")

        # ══ F4 · ROLES ARE READ-ONLY: the queue ════════════════════════════════════════════
        print("\nF4 roles read-only — a role that tries to edit the queue is refused BY NAME",
              flush=True)
        queue = sandbox.QUEUE if os.path.exists(sandbox.QUEUE) else FIXTURE_QUEUE
        if queue is not sandbox.QUEUE:
            print(f"  (the farm's queue is not in this tree — F4b/F4c aim at the shipped FIXTURE "
                  f"queue {queue}; F4a still names the class by the real path "
                  f"{sandbox.QUEUE})", flush=True)
        q_before = sha_file(queue)
        try:
            sandbox.check_write("role", sandbox.QUEUE, report=os.path.join(tmp, "role-page.md"))
            check("F4a role -> queue is REFUSED by the class name", False, "no refusal raised")
        except sandbox.SandboxBreach as e:
            check("F4a role -> queue is REFUSED by the class name",
                  "queue" in e.detail and e.target == os.path.realpath(sandbox.QUEUE),
                  f"detail={e.detail[:70]}")
        rl = os.path.join(tmp, "role.sh")
        with open(rl, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/bash\n"
                     f"echo 'role-edit' >> {queue}\n"
                     "echo 'after the attempt'\n")
        os.chmod(rl, 0o755)
        rec_r = sandbox.run([rl], class_name="role", report=os.path.join(tmp, "role-page.md"),
                            runs_dir=os.path.join(tmp, "runs"))
        check("F4b a role LANE appending to the queue -> REFUSED, exit 126",
              rec_r["exit"] == sandbox.BREACH_EXIT
              and any(f.get("path") == os.path.realpath(queue) for f in rec_r["findings"]),
              f"exit={rec_r['exit']} findings={[f.get('path') for f in rec_r['findings']]}")
        check("F4c the queue is byte-identical afterwards (sha256 unchanged)",
              sha_file(queue) == q_before and q_before != "MISSING",
              f"{q_before[:16]}… -> {sha_file(queue)[:16]}…  queue={queue}")
        if q_before == "MISSING":
            skip("F4c the queue is byte-identical afterwards",
                 "the queue this tree can see (neither the farm's nor the shipped fixture)",
                 queue)
        try:
            sandbox.allowlist("role", os.path.join(tmp, "role-page.md"),
                              [os.path.join(FARM, "reports", "planning", "x.md")])
            check("F4d a role handed the row's declared paths is REFUSED (it writes one page)",
                  False, "no refusal raised")
        except sandbox.SandboxRefused as e:
            check("F4d a role handed the row's declared paths is REFUSED (it writes one page)",
                  "nothing else" in str(e), str(e)[:88])

        # ══ the real pipeline against the local source: F3 (throwaway store) and F2 ═════════
        print("\nF3 throwaway store — a sandbox run leaves the real store byte-identical",
              flush=True)
        for cmd in (("probe", base, "--id", "fixture_src", "--licence-url", licence),
                    ("evaluate", "fixture_src"), ("draft", "fixture_src")):
            p = admission(tmp, *cmd)
            if p.returncode != 0:
                print(f"  (setup {cmd[0]} failed: {p.stdout[-300:]} {p.stderr[-300:]})")
        before = sandbox.dirhash(sandbox.STORE_DIR)
        p = admission(tmp, "sandbox", "fixture_src")
        after = sandbox.dirhash(sandbox.STORE_DIR)
        receipts = [os.path.join(tmp, "adm", "fixture_src", f)
                    for f in os.listdir(os.path.join(tmp, "adm", "fixture_src"))
                    if f.startswith("sandbox-") and f.endswith(".receipt.json")]
        rc = json.load(open(receipts[-1], encoding="utf-8")) if receipts else {}
        check("F3a source_sandbox of the drafted adapter -> exit 0, records parsed from the "
              "throwaway store", p.returncode == 0 and rc.get("records") == 3,
              f"exit={p.returncode} records={rc.get('records')} rejects={rc.get('rejects')}")
        check("F3b the REAL store dir hash is identical before and after",
              before == after and rc.get("real_store_unchanged") is True,
              f"{before[:16]}… -> {after[:16]}…")
        check("F3c the throwaway store's hash is RECORDED and the store is DELETED on exit",
              bool(rc.get("throwaway_store_sha256")) and rc.get("throwaway_deleted") is True
              and not os.path.exists(rc.get("throwaway_store", "/nonexistent")))
        check("F3d the receipt quotes the allowlist and the exact store hashes",
              rc.get("network_allowlist") == ["127.0.0.1"]
              and rc.get("real_store_sha256_before") == rc.get("real_store_sha256_after") == before,
              f"allowlist={rc.get('network_allowlist')}")

        # ══ F2 · THE NETWORK ALLOWLIST (a drafted adapter reaching a foreign host) ═════════
        print("\nF2 network allowlist — a drafted adapter calling a foreign host is refused at "
              "connect, the host named", flush=True)
        for cmd in (("probe", base, "--id", "fixture_badhost", "--licence-url", licence),
                    ("evaluate", "fixture_badhost"), ("draft", "fixture_badhost")):
            admission(tmp, *cmd)
        draft = os.path.join(tmp, "drafts", "fixture_badhost.py")
        src = open(draft, encoding="utf-8").read()
        src = src.replace(
            "        body = http.get(self.data_url, note=\"draft data\")",
            f"        http.get_text('http://localhost:{port}/genes.tsv', note='foreign host')\n"
            "        body = http.get(self.data_url, note=\"draft data\")")
        open(draft, "w", encoding="utf-8").write(src)
        p2 = admission(tmp, "sandbox", "fixture_badhost")
        recs2 = sorted(f for f in os.listdir(os.path.join(tmp, "adm", "fixture_badhost"))
                       if f.endswith(".receipt.json"))
        rc2 = json.load(open(os.path.join(tmp, "adm", "fixture_badhost", recs2[-1]),
                             encoding="utf-8")) if recs2 else {}
        nr = (rc2.get("network_refusals") or [{}])[0]
        check("F2a a foreign host is REFUSED at connect, exit 3, the host NAMED",
              p2.returncode == 3 and "tried to connect to localhost" in p2.stdout,
              f"exit={p2.returncode} out={(p2.stdout.strip().splitlines() or [''])[-3][:88]}")
        check("F2b the run record NAMES the host and the allowlist it broke",
              nr.get("host") == "localhost" and nr.get("kind") == "network"
              and nr.get("allowed") == ["127.0.0.1"],
              f"host={nr.get('host')} allowed={nr.get('allowed')}")
        check("F2c a refusal leaves the real store untouched as well",
              rc2.get("real_store_unchanged") is True, f"{str(rc2.get('real_store_sha256_after'))[:16]}…")

        # ══ F5 · THE HOSTILE FIXTURE (ctypes raw connect to 127.0.0.1:8002) ══════════════════
        # req-P1-NETWORK-POLICY-TIERS. The claim under test is NOT "the sandbox refuses bad
        # connections" — the claim is WHICH LAYER refuses them, and the honest answer is the
        # kernel, because the python guard does not see a raw libc connect() at all.
        print("\nF5 the hostile fixture — ctypes raw connect() to 127.0.0.1:8002, guard on/off, "
              "rule on/off", flush=True)
        deny_private = sandbox.netpolicy("deny-private")
        off = sandbox.netpolicy("off")                      # the fixture's pole: no ruleset
        check("F5-pre TIER-DEFAULT names every range it denies, in the ruleset itself",
              all(d["range"] in deny_private["ruleset"] for d in deny_private["deny"])
              and "127.0.0.0/8" in deny_private["ruleset"]
              and "192.168.0.0/16" in deny_private["ruleset"]
              and deny_private["enforced"] is True,
              f"{len(deny_private['deny'])} ranges: "
              + ", ".join(f"{d['label']} {d['range']}" for d in deny_private["deny"]))
        check("F5-pre the fixture's 'off' pole is recorded as NOT enforced (it is a pole, "
              "not a policy)", off["enforced"] is False and "reject" not in off["ruleset"],
              'tier "off" -> enforced=False, ruleset has no rule')

        r_bypass = run_fixture(sandbox, tmp, RAW_CONNECT, ["127.0.0.1", 8002, 0.7],
                               network=off, guard=True, name="hostile_guard_on_rule_off")
        check("F5a GUARD ON, RULE OFF: the ctypes connect SUCCEEDS — the python guard does not "
              "see it (a guard is not a boundary)",
              r_bypass["exit"] == 0 and "-> CONNECTED" in (r_bypass.get("stdout") or ""),
              f"exit={r_bypass['exit']} "
              f"{(r_bypass.get('stdout') or '').strip().splitlines()[-1][:74]}")

        r_guard_on = run_fixture(sandbox, tmp, RAW_CONNECT, ["127.0.0.1", 8002, 0.7],
                                 network=deny_private, guard=True, name="hostile_guard_on")
        kf = [f for f in r_guard_on["findings"] if f.get("via") == "kernel-nftables"]
        check("F5b GUARD ON, RULE ON: REFUSED by the KERNEL rule — non-zero exit, the range "
              "127.0.0.0/8 named and its counter non-zero",
              r_guard_on["exit"] == sandbox.BREACH_EXIT and kf
              and kf[0]["range"] == "127.0.0.0/8" and kf[0]["packets"] > 0,
              f"exit={r_guard_on['exit']} range={kf[0]['range'] if kf else '—'} "
              f"packets={kf[0]['packets'] if kf else 0} host={kf[0].get('host') if kf else '—'}")
        check("F5b' the record NAMES the host the child reported (127.0.0.1) as well as the range",
              bool(kf) and kf[0].get("host") == "127.0.0.1",
              f"host={kf[0].get('host') if kf else '—'}")
        check("F5b'' the guard logged NOTHING: the refusal was not the python guard's",
              not [f for f in r_guard_on["findings"] if f.get("via") != "kernel-nftables"],
              f"guard findings="
              f"{[f.get('via') for f in r_guard_on['findings'] if f.get('via') != 'kernel-nftables']}")

        r_guard_off = run_fixture(sandbox, tmp, RAW_CONNECT, ["127.0.0.1", 8002, 0.7],
                                  network=deny_private, guard=False, name="hostile_guard_off")
        kf2 = [f for f in r_guard_off["findings"] if f.get("via") == "kernel-nftables"]
        check("F5c GUARD OFF, RULE ON: REFUSED identically — the kernel alone is sufficient",
              r_guard_off["exit"] == sandbox.BREACH_EXIT and kf2
              and kf2[0]["range"] == "127.0.0.0/8",
              f"exit={r_guard_off['exit']} range={kf2[0]['range'] if kf2 else '—'}")

        # ══ F6 · the RANGE is named (192.168.x), and the rule is provably the reason ════════
        print("\nF6 a deny-private fixture reaching 192.168.7.7 is refused WITH THE RANGE NAMED, "
              "and the same target connects when the rule is absent", flush=True)
        pol_lo = sandbox.netpolicy("deny-private", lo_addrs=["192.168.7.7/32"])
        off_lo = sandbox.netpolicy("off", lo_addrs=["192.168.7.7/32"])
        r_priv = run_fixture(sandbox, tmp, RAW_CONNECT, ["192.168.7.7", 8002, 0.7],
                             network=pol_lo, guard=True, name="private_192_168")
        pf = [f for f in r_priv["findings"] if f.get("via") == "kernel-nftables"]
        check("F6a refused, and the RECORD names 192.168.0.0/16 (RFC1918) with a non-zero counter",
              r_priv["exit"] == sandbox.BREACH_EXIT and pf and pf[0]["range"] == "192.168.0.0/16"
              and pf[0]["range_label"] == "RFC1918" and pf[0]["packets"] > 0,
              f"exit={r_priv['exit']} range={pf[0]['range'] if pf else '—'} "
              f"label={pf[0]['range_label'] if pf else '—'} host={pf[0].get('host') if pf else '—'}")
        r_priv_off = run_fixture(sandbox, tmp, RAW_CONNECT, ["192.168.7.7", 8002, 0.7],
                                 network=off_lo, guard=True, name="private_192_168_rule_off")
        check("F6b RULE OFF: the SAME target on the SAME namespace CONNECTS — so the refusal was "
              "the rule, not the absence of a route or a listener",
              r_priv_off["exit"] == 0 and "-> CONNECTED" in (r_priv_off.get("stdout") or ""),
              f"exit={r_priv_off['exit']} "
              f"{(r_priv_off.get('stdout') or '').strip().splitlines()[-1][:74]}")

        # ══ F5d · the CARELESS fixture is refused by the GUARD (the informative first line) ══
        print("\nF5d the guard's own job — a careless (urllib) client is refused by the guard, "
              "which NAMES the host and the door", flush=True)
        r_careless = run_fixture(sandbox, tmp, CARELESS_CONNECT,
                                 [f"http://127.0.0.1:{port}/genes.tsv"], network=deny_private,
                                 guard=True, name="careless_urllib")
        gf = [f for f in r_careless["findings"] if f.get("via") not in ("kernel-nftables", None)]
        check("F5d-a refused with the host named by the GUARD (not the kernel), exit 126",
              r_careless["exit"] == sandbox.BREACH_EXIT and gf and gf[0]["host"] == "127.0.0.1",
              f"exit={r_careless['exit']} via={gf[0]['via'] if gf else '—'} "
              f"range={gf[0].get('range') if gf else '—'}")
        check("F5d-b the guard names the RANGE too — the first line is informative, not vague",
              bool(gf) and gf[0].get("range") == "127.0.0.0/8",
              f"range={gf[0].get('range') if gf else '—'}")

        # ══ F7 · TIER-STRICT, generated from a real sources.yml entry ════════════════════════
        print("\nF7 tier base-url — generated from sources.yml: own host reaches, neighbour does "
              "not", flush=True)
        import yaml
        shipped = yaml.safe_load(open(os.path.join(FARM, "symbiont", "sources.yml"),
                                      encoding="utf-8"))
        entry = [e for e in shipped["sources"] if e["id"] == "mgi"][0]
        own = "203.0.113.10"
        neighbour = "203.0.113.11"
        entry = dict(entry, base_url=f"http://{own}:8002/downloads/reports/")
        strict_cfg = os.path.join(tmp, "sources-strict.yml")
        with open(strict_cfg, "w", encoding="utf-8") as fh:
            yaml.safe_dump({"version": 1, "sources": [entry]}, fh)
        strict = sandbox.policy_for_source(strict_cfg, "mgi", "base-url",
                                           lo_addrs=[f"{own}/32", f"{neighbour}/32"])
        check("F7-pre the tier is GENERATED from sources.yml: the entry's base_url host resolves "
              "to exactly one allowed address",
              strict["allow_ips"] == [own] and strict["allow_hosts"] == [own]
              and f"ALLOW base_url {own}" in strict["ruleset"]
              and "policy drop" in strict["ruleset"],
              f"allow_ips={strict['allow_ips']} hosts={strict['allow_hosts']}")
        r_own = run_fixture(sandbox, tmp, RAW_CONNECT, [own, 8002, 0.7], network=strict,
                            guard=True, name="strict_own_host")
        check("F7a the adapter REACHES its own base_url host (positive control, exit 0)",
              r_own["exit"] == 0 and "-> CONNECTED" in (r_own.get("stdout") or "")
              and not r_own["findings"],
              f"exit={r_own['exit']} "
              f"{(r_own.get('stdout') or '').strip().splitlines()[-1][:74]}")
        r_nb = run_fixture(sandbox, tmp, RAW_CONNECT, [neighbour, 8002, 0.7], network=strict,
                           guard=True, name="strict_neighbour")
        nf = [f for f in r_nb["findings"] if f.get("via") == "kernel-nftables"]
        check("F7b its NEIGHBOUR does not — refused, the catch-all named ('everything that is "
              "not the base_url'), host named",
              r_nb["exit"] == sandbox.BREACH_EXIT and nf
              and nf[0]["range"] == "everything" and nf[0].get("host") == neighbour,
              f"exit={r_nb['exit']} rule={nf[0]['range'] if nf else '—'} "
              f"host={nf[0].get('host') if nf else '—'}")

        # ══ F8 · THE THREE DEPLOYMENTS, each quoting the policy it was tested with ═══════════
        print("\nF8 the docs state, per deployment, WHO the policy protects — each quoting the "
              "tested policy", flush=True)
        doc = os.path.join(FARM, "symbiont", "docs", "NETWORK-POLICY-TIERS.md")
        text = open(doc, encoding="utf-8").read() if os.path.isfile(doc) else ""
        ranges = [d["range"] for d in deny_private["deny"] if d["family"] == "ip"]
        check("F8-pre the doc exists", bool(text), f"{doc} ({len(text)} bytes)")
        for key, who in (("(a) WE HOST", "tenants against EACH OTHER"),
                         ("(b) GN HOSTS", "their own services and keys"),
                         ("(c) A TRAINING STATION", "the USER's machine and keys")):
            i = text.find(key)
            sect = text[i:i + 2200] if i >= 0 else ""
            check(f"F8 {key} quotes deny-private and the ranges it was tested with ({who})",
                  i >= 0 and "deny-private" in sect
                  and all(r in sect for r in ranges[:4])
                  and ("base-url" in sect or "base_url" in sect),
                  f"found={i >= 0} ranges={[r for r in ranges[:4] if r in sect]} "
                  f"base-url={'base-url' in sect or 'base_url' in sect}")
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [r for r in results if not r[1]]
    npass = len(results) - len(bad)
    print(f"RESULT: {npass} passed, {len(bad)} failed — the research layer's "
          f"two untrusted things run inside their allowlists, and the network tier is refused by "
          f"the KERNEL (the python guard only names what was attempted)", flush=True)
    print(f"RESULT: {npass} passed, {len(skipped)} skipped, {len(bad)} failed "
          + ("(clone mode)" if skipped else "(every check ran — no skip)"), flush=True)
    if skipped:
        print(f"SKIPPED: {len(skipped)} check(s) whose input is not in this tree: "
              + "; ".join(skipped), flush=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
