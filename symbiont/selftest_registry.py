"""THE INDEX IS A PROJECTION, NOT A SECOND AUTHORITY — the row's NSC, replayed by a machine.

run: python3 -m symbiont.selftest_registry

req-P1-THE-INDEX-IS-A-PROJECTION-NOT-A-S. `registry show` must rebuild the index from
sources.yml (intent) + the diary (facts) and NOTHING ELSE — which is only a claim until a
machine makes it fail. This file plants the failures the row names, offline, in a throwaway
store whose event clock is PINNED (so every sha below is reproducible on any day):

  I1 THE REPLAY TEST    `show` twice from the same diary: byte-identical stdout AND artefact,
                        and the header's render sha256 EQUALS the sha256 of the body.
  I2 DISPOSABLE         delete the artefact, rebuild → the same bytes, proving it is derived.
  I3 HAND-EDITED        a body byte moved by hand: REFUSED[stale-index] on build #1, REPORTED
                        AGAIN on build #2, the artefact NEVER overwritten; delete → rebuild
                        → the I1 bytes again.
  I4 THE GOVERNANCE LINES an override is NAMED (source, scope, day, actor); an enabled source
                        with no digest is named in the abandonment line; a source with a
                        digest inside the window is NOT.
  I5 THE PRECEDENCE, BOTH WAYS  the config flips `may fetch` while the diary keeps `status`;
                        an `intent_changed` event moves the last event, never the status; a
                        dataset named only in the diary keeps a row (`may fetch: no`).
  I6 DAMAGED HISTORY    over a diary with a truncated tail, show returns 3 and writes NOTHING
                        — an index rendered from damage would be the second authority.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

from . import contract, diary

PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources.yml")
MGI = "mgi:MRK_ENSEMBL"
ENS = "ensembl:mmusculus_gene_ensembl"
EXTRA = "who-glass:amr_profiles"          # named ONLY in the diary — the union's third row
NOW = "2026-11-12"                        # pinned: the render DATE is the only other input
CLOCK = lambda: "2026-11-12T04:00:00+00:00"  # the fixture's event clock, pinned the same way


def say(line: str = "") -> None:
    print(line, flush=True)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Tally:
    def __init__(self):
        self.n = self.total = 0

    def check(self, label: str, ok: bool, quote: str = ""):
        self.total += 1
        if ok:
            self.n += 1
            say(f"  PASS  {label}" + (f"  {quote}" if quote else ""))
        else:
            say(f"  FAIL  {label}" + (f"  {quote}" if quote else ""))


def run(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "symbiont.registry", *args],
                          capture_output=True, cwd=PKG_PARENT)


def show(store: str, cfg: str, index: str) -> subprocess.CompletedProcess:
    return run("show", "--store", store, "--sources-config", cfg, "--now", NOW,
               "--index", index)


def body_of(text: str) -> tuple[str, str]:
    """(render-sha claimed by the header, sha256 of the body below the first blank line)."""
    header, _, body = text.partition("\n\n")
    claimed = ""
    for line in header.split("\n"):
        if line.startswith("# render sha256="):
            claimed = line.split("=", 1)[1].split()[0]
    return claimed, sha(body.encode("utf-8"))


def row_of(text: str, dataset_id: str) -> str:
    for line in text.split("\n"):
        if line.startswith(dataset_id):
            return line
    return ""


def line_of(text: str, word: str) -> str:
    for line in text.split("\n"):
        if line.startswith(word):
            return line
    return ""


def build_store(store: str) -> None:
    """A five-event diary over three datasets, written by the ONE writer (diary.append)."""
    os.makedirs(store, exist_ok=True)
    diary.append(store, "admitted", source_id=MGI, why="fixture: mgi admitted",
                 actor="operator", origin="backfill", clock=CLOCK)
    diary.append(store, "acquired", source_id=MGI, why="fixture: mgi fetched, digest recorded",
                 release="mgi-2026-11-09", evidence={"run_id": "selftest_registry",
                 "resources": {"MRK_ENSEMBL.rpt": "0" * 64}}, clock=CLOCK)
    diary.append(store, "admitted", source_id=ENS, why="fixture: ensembl admitted and then "
                 "left alone — no digest ever recorded for it", actor="operator",
                 origin="backfill", clock=CLOCK)
    diary.append(store, "manual_override", source_id=ENS, why="fixture: one scoped exception",
                 actor="operator", evidence={"scope": "until:2099-12-31"}, clock=CLOCK)
    diary.append(store, "manual_add", source_id=EXTRA, why="fixture: a dataset named only in "
                 "the diary — the config never declared it", actor="operator",
                 origin="backfill", clock=CLOCK)
    diary.append(store, "intent_changed", source_id=MGI, why="fixture: the operator flipped "
                 "the switch — a permission fact, which must never move the status",
                 actor="operator", evidence={"from": True, "to": True}, clock=CLOCK)


def write_configs(t: str) -> tuple[str, str]:
    """The package's own sources.yml twice: as-is, and with mgi switched OFF (one field)."""
    with open(SOURCES, encoding="utf-8") as fh:
        text = fh.read()
    on = os.path.join(t, "sources-on.yml")
    with open(on, "w", encoding="utf-8") as fh:
        fh.write(text)
    sources = contract.load_sources(SOURCES)
    sid = next(s for s, c in sources.items()
               if contract.dataset_id(c) == MGI)
    sources[sid]["enabled"] = False
    off = os.path.join(t, "sources-off.yml")
    with open(off, "w", encoding="utf-8") as fh:
        fh.write(contract._header(text) + contract.dump_sources(sources))
    return on, off


def main() -> int:
    t = tempfile.mkdtemp(prefix="selftest-registry-")
    store = os.path.join(t, "store")
    idx = os.path.join(t, "store", "source-index.txt")
    try:
        build_store(store)
        on, off = write_configs(t)
        say(f"FIXTURE  store={store}  config-on={on}  config-off={off}  now pinned={NOW}")
        tm = Tally()

        # I1 — THE REPLAY TEST: twice from the same diary, byte-identical.
        say("")
        say("I1 THE REPLAY TEST — `show` twice from the same diary is byte-identical")
        a1 = show(store, on, idx)
        a2 = show(store, on, idx)
        tm.check("I1 both builds exit 0", a1.returncode == 0 and a2.returncode == 0,
                 f"rc={a1.returncode},{a2.returncode}")
        tm.check("I1 the two stdouts are byte-identical", a1.stdout == a2.stdout,
                 f"sha256={sha(a1.stdout)[:16]}… == {sha(a2.stdout)[:16]}…")
        tm.check("I1 the artefact IS the stdout (what you saw is what is on disk)",
                 open(idx, "rb").read() == a1.stdout, f"sha256={sha(open(idx, 'rb').read())[:16]}…")
        claimed, actual = body_of(a1.stdout.decode("utf-8"))
        tm.check("I1 the header's render sha256 EQUALS the sha256 of the body",
                 claimed == actual, f"header={claimed[:16]}… body={actual[:16]}…")

        # I2 — DISPOSABLE: delete the artefact, rebuild → the same bytes.
        say("")
        say("I2 DELETE THE ARTEFACT AND REBUILD — identical, so it is disposable")
        before = sha(open(idx, "rb").read())
        os.remove(idx)
        tm.check("I2 the artefact is gone before the rebuild", not os.path.exists(idx))
        a3 = show(store, on, idx)
        after = sha(open(idx, "rb").read())
        tm.check("I2 the rebuild writes the same bytes", a3.returncode == 0 and before == after,
                 f"{before[:16]}… == {after[:16]}…")

        # I3 — HAND-EDITED: REFUSED[stale-index], reported, never overwritten.
        say("")
        say("I3 A HAND-EDITED INDEX IS REFUSED[stale-index], REPORTED, NEVER OVERWRITTEN")
        good = open(idx, "rb").read()
        bad = good.decode("utf-8").replace("yes        acquired", "yes        admitted")
        with open(idx, "w", encoding="utf-8") as fh:
            fh.write(bad)
        edited = sha(open(idx, "rb").read())
        r1 = show(store, on, idx)
        r2 = show(store, on, idx)
        tm.check("I3 build #1 REFUSES[stale-index] and names the file",
                 r1.returncode == 2 and b"REFUSED[stale-index]" in r1.stdout
                 and idx.encode() in r1.stdout, f"rc={r1.returncode}")
        tm.check("I3 build #2 REPORTS IT AGAIN (not silently forgotten)",
                 r2.returncode == 2 and b"REFUSED[stale-index]" in r2.stdout, f"rc={r2.returncode}")
        tm.check("I3 the hand-edited bytes are UNTOUCHED by both refusals",
                 sha(open(idx, "rb").read()) == edited, f"sha256={edited[:16]}… unchanged")
        os.remove(idx)
        a4 = show(store, on, idx)
        tm.check("I3 delete + rebuild restores the I1 bytes (the remedy the refusal names)",
                 a4.returncode == 0 and open(idx, "rb").read() == good,
                 f"sha256={sha(open(idx, 'rb').read())[:16]}…")

        # I4 — the governance lines: the override named, the abandonment alarm rings.
        say("")
        say("I4 THE GOVERNANCE LINES — overrides named, abandonment rung")
        text = a1.stdout.decode("utf-8")
        ov = line_of(text, "OVERRIDES")
        ab = line_of(text, "ABANDONED")
        tm.check("I4 the OVERRIDES line names the source, its scope, the day and the actor",
                 ENS in ov and "scope=until:2099-12-31" in ov and "2026-11-12" in ov
                 and "operator" in ov, ov)
        tm.check("I4 the ABANDONMENT line names the enabled source with no digest",
                 ENS in ab and "no digest recorded" in ab, ab)
        tm.check("I4 the source with a digest inside the window is NOT abandoned",
                 MGI not in ab, ab)

        # I5 — the precedence, both ways, and the union with a diary-only dataset.
        say("")
        say("I5 THE PRECEDENCE — intent moves `may fetch`, facts move `status`, never each other")
        a5 = show(store, off, os.path.join(t, "idx-off.txt"))
        off_text = a5.stdout.decode("utf-8")
        mgi_row = row_of(off_text, MGI)
        tm.check("I5 the config flips `may fetch` (mgi switched OFF, one field)",
                 a5.returncode == 0 and " no " in mgi_row, mgi_row)
        tm.check("I5 the diary keeps `status` (still the last fact, not the intent)",
                 "acquired" in mgi_row, mgi_row)
        a6 = show(store, on, os.path.join(t, "idx-on.txt"))
        on_text = a6.stdout.decode("utf-8")
        mgi_on = row_of(on_text, MGI)
        tm.check("I5 an `intent_changed` event moved the LAST EVENT, never the status",
                 "acquired" in mgi_on and "seq 6 " in mgi_on, mgi_on)
        extra_row = row_of(on_text, EXTRA)
        tm.check("I5 a dataset named only in the diary keeps its row, `may fetch: no`",
                 extra_row.startswith(EXTRA) and "  no " in extra_row + " ", extra_row)

        # I6 — damaged history: exit 3 and NOTHING written.
        say("")
        say("I6 DAMAGED HISTORY — show refuses to render, writes nothing")
        broken = os.path.join(t, "store-broken")          # the diary ONLY — no old artefact
        os.makedirs(broken)
        shutil.copy(diary.diary_path(store), diary.diary_path(broken))
        with open(diary.diary_path(broken), "ab") as fh:      # plant a truncated tail
            fh.write(b'{"seq":99,"class":"acquired"')
        bidx = os.path.join(broken, "source-index.txt")
        a7 = show(broken, on, bidx)
        tm.check("I6 a truncated diary makes show exit 3 (a finding, not a render)",
                 a7.returncode == 3, f"rc={a7.returncode}")
        tm.check("I6 NO artefact was written over the damage",
                 not os.path.exists(bidx), f"{bidx}: absent")

        say("")
        if tm.n == tm.total:
            say(f"SELFTEST PASS: registry {tm.n}/{tm.total} held — the index is a projection: "
                f"replayed byte-identical, disposable, REFUSED[stale-index] when hand-edited, "
                f"its governance lines ring, and its precedence lives in exactly one rule")
            return 0
        say(f"SELFTEST FAIL: registry {tm.n}/{tm.total} — a projection that cannot be proved "
            f"disposable is a second authority")
        return 1
    finally:
        shutil.rmtree(t, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
