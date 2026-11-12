"""THE ONE-WRITER RULE, ENFORCED BY A MACHINE — and the writer's four promises, quoted.

run: python3 -m symbiont.selftest_diary_writer

req-P1-THE-DIARY-WRITER-atomic-append-an asks for three things and four quotations. This file is
the proof of all of them, offline, at fixture scale, with the decisive line printed for each:

  W1 ONE WRITER BY NAME   a text grep of the WHOLE tree finds the diary's file name spelled in
                          exactly ONE module — symbiont/diary.py — and nothing else does.
  W2 ONE WRITER BY WRITE  an AST scan of the whole tree finds exactly ONE function that opens a
                          jsonl/diary path for writing: symbiont/diary.py:append. Any other
                          function doing it is named as a second writer. The only exempt files are
                          the three planting selftests, and no production module may be exempt.
  W3 A PLANTED WRITER     a second writer planted INSIDE the tree is CAUGHT and named (the
                          detector can fail — a check that cannot fail is not a check).
  W4 ATOMIC APPEND        `append()` writes ONE line: `tail -1` is a complete JSON object, the
                          file ends in a newline, and the last line's prev_event_sha256 EQUALS
                          sha256 of the previous line's bytes (`head -1 … | sha256sum`, quoted).
  W5 THE CLOSED TABLE     the row's seventeen classes are all in CLASSES (schema_changed included),
                          the table is data over every class, and a planted illegal transition is
                          REFUSED[illegal-transition] naming BOTH classes and the source — with the
                          file byte-identical afterwards (nothing was appended).
  W6 TRUNCATED TAIL       a planted partial line is DETECTED and REPORTED on read (line number,
                          "no terminating newline"), never skipped; and append() REFUSES onto the
                          damage rather than doubling over it.
  W7 APPEND-ONLY          every byte that was on disk before an append is still there, unchanged,
                          at the same offset (the prefix hash is equal on both sides).
  W8 THE DICT DOOR        `append(event)` — a nine-field event dict in, the ten-field line out,
                          seq/prev assigned by the writer; a missing/unknown field or a lying seq
                          is REFUSED[diary].
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

from . import diary, events

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# the name is ASSEMBLED here, so this file is not itself a hit for W1 (the grep is a real grep).
NAME = "source-" + "events.jsonl"

# THE ONLY EXEMPTION from W2, and it is the three PLANTING selftests: a test that PLANTS a second
# writer, a truncated tail or an altered line must write the diary file — that is the falsifier,
# not a writer (selftest_registry plants a truncated tail to prove `registry show` refuses to
# render over damaged history). The check below asserts the list is exactly these three AND that
# every entry is a `selftest_*.py` (so nobody can exempt a production module quietly).
EXEMPT = {os.path.join("symbiont", "selftest_diary.py"),
          os.path.join("symbiont", "selftest_diary_writer.py"),
          os.path.join("symbiont", "selftest_registry.py")}

SKIP_DIRS = {"__pycache__", ".git", "store", "build", "node_modules"}


def say(line: str = "") -> None:
    print(line, flush=True)


def _walk(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


def _text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


# ── W1 · the name may be spelled in one module ───────────────────────────────────────────────
def scan_names(root: str, name: str = NAME) -> list[str]:
    """Every .py file whose text contains the diary's file name, sorted, relative to root."""
    hits = [os.path.relpath(p, root) for p in _walk(root) if name in _text(p)]
    return sorted(hits)


# ── W2 · one function may open it for writing ────────────────────────────────────────────────
_WRITE_FLAGS = {"O_WRONLY", "O_RDWR", "O_APPEND", "O_CREAT", "O_TRUNC"}


def _write_opens(node: ast.AST) -> list[ast.Call]:
    """Every call in this subtree that opens a file for WRITING (mode a/w/x/+ or an O_* flag)."""
    out = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        fn = n.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None) or ""
        if name != "open":
            continue
        if isinstance(fn, ast.Attribute) and getattr(fn.value, "id", None) == "os":
            flags = " ".join(ast.unparse(a) for a in n.args[1:])
            if any(f in flags for f in _WRITE_FLAGS):
                out.append(n)
            continue
        mode = ""
        if len(n.args) > 1 and isinstance(n.args[1], ast.Constant):
            mode = str(n.args[1].value)
        for kw in n.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = str(kw.value.value)
        if any(c in mode for c in ("w", "a", "x", "+")):
            out.append(n)
    return out


def _diary_aliases(fn_node) -> set[str]:
    """Variables in this function that hold the diary's path (`p = diary_path(store)`)."""
    tokens = (diary.diary_path.__name__, diary.DIARY_NAME)
    alias: set[str] = set()
    for n in ast.walk(fn_node):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            src = ast.unparse(n.value)
            if any(t in src for t in tokens):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        alias.add(t.id)
    return alias


def scan_writers(root: str, exempt: set[str] | None = None) -> list[str]:
    """`file:function` for every function that opens the DIARY for writing — and nothing else.

    A WRITE SITE is a function that opens a file for writing whose PATH is the diary's: the path
    argument (or a variable holding it) names `diary_path()` or the diary's file name. That is the
    shape of a writer. A function that writes a RUN RECORD (a different jsonl, in a different
    place) is not a diary writer and is not named — precision matters: a check that cries wolf is
    switched off within a week.
    """
    exempt = EXEMPT if exempt is None else exempt
    hits: list[str] = []
    tokens = (diary.diary_path.__name__, diary.DIARY_NAME)
    for path in _walk(root):
        rel = os.path.relpath(path, root)
        if rel in exempt:
            continue
        txt = _text(path)
        if diary.DIARY_NAME not in txt and diary.diary_path.__name__ not in txt:
            continue
        try:
            tree = ast.parse(txt, filename=path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = _write_opens(node)
            if not calls:
                continue
            alias = _diary_aliases(node)
            for call in calls:
                arg_src = " ".join(ast.unparse(a) for a in call.args) + " " + " ".join(
                    ast.unparse(k.value) for k in call.keywords)
                names_it = any(t in arg_src for t in tokens) or any(
                    isinstance(a, ast.Name) and a.id in alias for a in call.args)
                if names_it:
                    hits.append(f"{rel}:{node.name}")
                    break
    return sorted(hits)


# ── small local tools ────────────────────────────────────────────────────────────────────────
def sha256(text: bytes | str) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def sh(cmd: str) -> str:
    p = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    return (p.stdout or p.stderr).strip()


def lines_of(path: str) -> list[bytes]:
    with open(path, "rb") as fh:
        return fh.read().split(b"\n")


def refuse_of(fn) -> str:
    try:
        fn()
    except Exception as e:                        # noqa: BLE001 — the refusal IS the result
        code = getattr(e, "code", type(e).__name__)
        return f"REFUSED[{code}] {e}"
    return "(NO REFUSAL — it was accepted)"


def main(argv=None) -> int:
    results: list[tuple[str, bool]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok)))
        print(f"  {'PASS' if ok else 'FAIL'}  {name:56} {detail}", flush=True)

    tmp = tempfile.mkdtemp(prefix="diary-writer-")
    try:
        # ══ W1 · ONE WRITER BY NAME ═══════════════════════════════════════════════════════════
        say("\nW1 ONE WRITER BY NAME — the file name is spelled in exactly one module of the tree")
        named = scan_names(FARM)
        check("W1 the name lives in exactly one .py file — symbiont/diary.py",
              named == [os.path.join("symbiont", "diary.py")], f"offenders={named}")
        check("W1 events.py does NOT spell it (the performer is reachable only through the writer)",
              os.path.join("symbiont", "events.py") not in named,
              f"grep({NAME!r}) -> {len(named)} file(s)")

        # ══ W2 · ONE WRITER BY WRITE ══════════════════════════════════════════════════════════
        say("\nW2 ONE WRITER BY WRITE — an AST scan: one function opens a diary/jsonl for writing")
        writers = scan_writers(FARM)
        check("W2 exactly one write site in the tree — symbiont/diary.py:append",
              writers == [os.path.join("symbiont", "diary.py") + ":append"],
              f"write sites={writers}")
        check("W2 the exemption list is exactly the three planting selftests — and nothing else",
              EXEMPT == {os.path.join("symbiont", "selftest_diary.py"),
                         os.path.join("symbiont", "selftest_diary_writer.py"),
                         os.path.join("symbiont", "selftest_registry.py")}
              and all(os.path.basename(e).startswith("selftest_") for e in EXEMPT),
              f"exempt={sorted(EXEMPT)} (a production module may never be exempt)")

        # ══ W3 · A PLANTED SECOND WRITER IS CAUGHT BY NAME ═══════════════════════════════════
        say("\nW3 A PLANTED WRITER — put a second writer inside the tree and let the scan find it")
        probe_dir = os.path.join(FARM, ".planted-writer-probe")
        os.makedirs(probe_dir, exist_ok=True)
        probe = os.path.join(probe_dir, "second_writer.py")
        rel_probe = os.path.join(".planted-writer-probe", "second_writer.py")
        try:
            plant = ("import os\n\n\ndef second_writer(store):\n"
                     "    with open(os.path.join(store, @NAME@), 'a',\n"
                     "              encoding='utf-8') as fh:\n"
                     "        fh.write(@LINE@)\n")
            plant = plant.replace("@NAME@", repr(NAME)).replace("@LINE@", repr("{}\n"))
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write(plant)
            ast.parse(plant)          # the detector is handed REAL python, not a broken plant
            planted = scan_writers(FARM, exempt=set())
            check("W3 the planted second writer is CAUGHT and named by file AND function",
                  rel_probe + ":second_writer" in planted,
                  f"caught: {[w for w in planted if 'planted' in w]}")
            check("W3 the planted writer is ALSO caught by the name grep (it spells the file)",
                  rel_probe in scan_names(FARM), f"by name: {scan_names(FARM)}")
        finally:
            shutil.rmtree(probe_dir, ignore_errors=True)
        one = os.path.join("symbiont", "diary.py") + ":append"
        check("W3 the probe is gone and the tree is back to ONE write site and ONE name",
              scan_writers(FARM) == [one] and scan_names(FARM) == [os.path.join("symbiont", "diary.py")]
              and not os.path.exists(probe_dir),
              f"write sites={scan_writers(FARM)}")

        # ══ W4 · ATOMIC APPEND (the row's first quotation) ═══════════════════════════════════
        say("\nW4 ATOMIC APPEND — one line, one write, and `tail -1` is a complete JSON object")
        store = os.path.join(tmp, "w4.store")
        first = diary.append(store, "discovered", source_id="fixture:genes",
                             why="the fixture lists a dataset for the first time",
                             evidence=dict(surface="listed"), actor="machine")
        second = diary.append(store, "evaluated", source_id="fixture:genes",
                              why="the licence was read and the count matched",
                              evidence=dict(card="cards/fixture.md"), actor="machine")
        path = diary.diary_path(store)
        blob = open(path, "rb").read()
        tail1 = blob.rstrip(b"\n").split(b"\n")[-1]
        tail_ev = json.loads(tail1.decode())
        check("W4 the file is newline-terminated and `tail -1` parses as ONE event",
              blob.endswith(b"\n") and isinstance(tail_ev, dict) and tail_ev.get("seq") == 2,
              f"tail -1 -> {tail1.decode()[:120]}…")
        check("W4 the line carries exactly the row's ten fields",
              set(tail_ev) == set(diary.FIELDS), f"fields={sorted(tail_ev)}")
        prev_line = lines_of(path)[0]
        prev_hash = sha256(prev_line)
        check("W4 `tail -1`'s prev_event_sha256 EQUALS the previous line's sha256",
              tail_ev["prev_event_sha256"] == prev_hash,
              f"prev_event_sha256={tail_ev['prev_event_sha256'][:32]}… == "
              f"sha256(line 1)={prev_hash[:32]}…")
        quoted = sh(f"printf '%s' \"$(head -1 {path})\" | sha256sum")
        check("W4 …and `head -1 <diary> | sha256sum` says the same thing, outside python",
              quoted.split()[0] == tail_ev["prev_event_sha256"], f"sha256sum says {quoted[:80]}")
        check("W4 seq is the line order (1, 2) and ts is ISO-8601 UTC",
              [first["seq"], second["seq"]] == [1, 2]
              and second["ts"].endswith("+00:00") and len(second["ts"]) == 25,
              f"seqs=[1, 2] ts={second['ts']}")

        # ══ W5 · THE CLOSED TRANSITION TABLE ═════════════════════════════════════════════════
        say("\nW5 THE CLOSED TABLE — the row's seventeen classes, every class a row, and a refusal")
        row_classes = ("discovered evaluated sandboxed admitted intent_changed acquired "
                       "release_moved count_ok count_mismatch schema_changed stale unreadable "
                       "vanished retired manual_add manual_override override_expired").split()
        missing = [c for c in row_classes if c not in diary.CLASSES]
        check("W5 every class the row names is in the closed set (schema_changed included)",
              not missing, f"missing={missing}" if missing else f"{len(row_classes)}/17 named")
        norow = sorted(c for c in diary.CLASSES
                       if c not in diary.TRANSITIONS and c not in diary.INTENT_CLASSES)
        pairs = sum(len(v) for v in diary.TRANSITIONS.values())
        check("W5 the table is DATA over every class: a row per class, N legal (from -> to) pairs",
              not norow and pairs > 0,
              f"rows={len(diary.TRANSITIONS)} pairs={pairs} classes_without_a_row={norow}")
        legal = [t for t in diary.transitions_from("admitted") if t in row_classes]
        check("W5 `admitted` admits a first count and a schema change (the table says so)",
              {"count_ok", "schema_changed", "acquired"} <= set(legal), f"admitted -> {sorted(legal)}")

        s5 = os.path.join(tmp, "w5.store")
        for cls in ("discovered", "refused"):
            diary.append(s5, cls, source_id="fixture:markers",
                         why=f"fixture plants a {cls} before the illegal step")
        before = sha256(open(diary.diary_path(s5), "rb").read())
        refusal = refuse_of(lambda: diary.append(s5, "admitted", source_id="fixture:markers",
                                                why="the fixture tries to skip `reconsidered`"))
        after = sha256(open(diary.diary_path(s5), "rb").read())
        check("W5 a planted illegal transition is REFUSED[illegal-transition], naming BOTH classes",
              "REFUSED[illegal-transition]" in refusal and "'admitted'" in refusal
              and "'refused'" in refusal, refusal.split(" — ")[0])
        check("W5 …and it NAMES the source it was about", "fixture:markers" in refusal,
              refusal[:150] + "…")
        check("W5 nothing was appended: the diary's bytes are identical before and after",
              before == after, f"sha256 {before[:16]}… == {after[:16]}… ({len(lines_of(diary.diary_path(s5))) - 1} lines)")
        check("W5 with `reconsidered` between them the SAME event is legal (the door is not shut)",
              refuse_of(lambda: diary.append(s5, "reconsidered", source_id="fixture:markers",
                                            why="the licence page changed")).startswith("(NO REFUSAL")
              and refuse_of(lambda: diary.append(s5, "admitted", source_id="fixture:markers",
                                                why="the gate said yes after all")
                            ).startswith("(NO REFUSAL"), f"classes={[e['class'] for e in events.read(s5).events]}")

        # ══ W6 · THE TRUNCATED TAIL (the row's third quotation) ══════════════════════════════
        say("\nW6 TRUNCATED TAIL — planted, DETECTED, REPORTED, and never appended onto")
        s6 = os.path.join(tmp, "w6.store")
        shutil.copytree(store, s6)
        p6 = diary.diary_path(s6)
        with open(p6, "ab") as fh:
            fh.write(b'{"seq":3,"class":"acquired","source_id":"fixture:ge')
        r6 = events.read(s6)
        reported = r6.report() if r6.truncated else "(read CLEAN — the damage was skipped!)"
        check("W6 the damaged tail is DETECTED on read and REPORTED (never skipped)",
              r6.truncated and "TRUNCATED" in reported and "no terminating newline" in reported,
              [ln for ln in reported.splitlines() if "no terminating newline" in ln][0][:130])
        check("W6 history is read ONLY up to the last COMPLETE line (2 events, seq 1..2)",
              len(r6.events) == 2 and r6.seq == 2, f"events={len(r6.events)} head_seq={r6.seq}")
        dirty = sha256(open(p6, "rb").read())
        refused6 = refuse_of(lambda: diary.append(s6, "count_ok", source_id="fixture:genes",
                                                  why="must be refused, not doubled over"))
        check("W6 appending onto the damage is REFUSED[truncated-diary] (stated, not attempted)",
              "REFUSED[truncated-diary]" in refused6 and "no terminating newline" in refused6,
              refused6.split("\n  ")[0][:130])
        check("W6 the damaged bytes are LEFT ON DISK UNTOUCHED by the refusal",
              sha256(open(p6, "rb").read()) == dirty, f"sha256 {dirty[:16]}… unchanged")

        # ══ W7 · APPEND-ONLY ════════════════════════════════════════════════════════════════
        say("\nW7 APPEND-ONLY — an append never rewrites one byte that was already there")
        s7 = os.path.join(tmp, "w7.store")
        diary.append(s7, "discovered", source_id="fixture:genes", why="first")
        p7 = diary.diary_path(s7)
        prefix = open(p7, "rb").read()
        diary.append(s7, "evaluated", source_id="fixture:genes", why="second")
        grown = open(p7, "rb").read()
        check("W7 the old bytes are still the first bytes (prefix hash equal on both sides)",
              grown.startswith(prefix) and sha256(grown[:len(prefix)]) == sha256(prefix),
              f"prefix {len(prefix)} B sha256={sha256(prefix)[:16]}… == {sha256(grown[:len(prefix)])[:16]}…")
        check("W7 the file grew by exactly one line's bytes",
              grown[len(prefix):].count(b"\n") == 1 and grown.endswith(b"\n"),
              f"grew {len(grown) - len(prefix)} byte(s)")

        # ══ W8 · THE DICT DOOR (the row's `append(event)`) ═══════════════════════════════════
        say("\nW8 `append(event)` — a nine-field dict in, the ten-field line out")
        s8 = os.path.join(tmp, "w8.store")
        ev8 = diary.append_event(dict(source_id="fixture:genes", release="r1",
                                      **{"class": "discovered"}, actor="machine",
                                      why="the fixture hands the writer a dict, as the row says",
                                      evidence=dict(how="dict"), origin="live"), store=s8)
        tail8 = json.loads(lines_of(diary.diary_path(s8))[-2].decode())
        check("W8 append(event) wrote ONE complete line with all ten fields",
              set(tail8) == set(diary.FIELDS) and tail8 == ev8, f"seq={ev8['seq']} class={ev8['class']}")
        miss = refuse_of(lambda: diary.append_event(dict(source_id="x", why="no class"), store=s8))
        extra = refuse_of(lambda: diary.append_event(
            dict(source_id="x", **{"class": "evaluated"}, why="w", colour="blue"), store=s8))
        lying = refuse_of(lambda: diary.append_event(
            dict(source_id="x", **{"class": "evaluated"}, why="w", actor="machine",
                 release=None, evidence={}, origin="live", seq=99), store=s8))
        check("W8 a missing field is REFUSED[diary] and the schema is named",
              "REFUSED[diary]" in miss and "missing field" in miss, miss[:120] + "…")
        check("W8 an unknown field is REFUSED[diary] (one schema, not many)",
              "REFUSED[diary]" in extra and "unknown field" in extra, extra[:110] + "…")
        check("W8 a seq the writer did not assign is REFUSED[diary] (seq is the line order)",
              "REFUSED[diary]" in lying and "not a claim" in lying, lying[:120] + "…")

        # ── the verdict ─────────────────────────────────────────────────────────────────────
        bad = [n for n, ok in results if not ok]
        say("")
        if bad:
            for n in bad:
                say(f"  FAILING: {n}")
            say(f"SELFTEST FAIL: {len(bad)} of {len(results)} checks failed")
            return 1
        say(f"SELFTEST PASS: one-writer {len(results)}/{len(results)} held — one writer by name "
            f"(symbiont/diary.py) and by write (append), a planted second writer is caught by "
            f"name, an append is atomic and chained, an illegal transition is refused naming both "
            f"classes, and a truncated tail is reported and never written over")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
