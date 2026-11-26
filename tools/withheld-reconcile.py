#!/usr/bin/env python3
"""WITHHELD-RECONCILE — reconcile the paths THIS TREE CITES against the paths THIS TREE HAS.

WHY (req-P1-SAY-WHAT-IS-WITHHELD-ONE-FILE-AND, 2026-11-11): about twenty paths cited by this tree do
not exist in it. A reviewer's first hypothesis about a dead link is sloppiness, not governance — so
the tree states, in one file (docs/WITHHELD.md), why each cited-but-absent path is absent. This
script is what makes that statement checkable instead of a promise:

    every path cited anywhere in the tree is either (a) present here, (b) resolvable relative to the
    file that cites it, or (c) a row in docs/WITHHELD.md. Anything else is a defect and this exits 1.
    and: every citation of a WITHHELD row in a reader-facing document carries '†' -> docs/WITHHELD.md.
    (a reader-facing document is *.md, the Makefile, and symbiont/cycle.yml — the files a stranger
    reads. A name in a .py/.sh source comment is a provenance annotation, not navigation; it is
    covered by rule (c) and counted in the output so nothing is hidden.)

USAGE
    python3 tools/withheld-reconcile.py [TREE]      # default: the current directory
    python3 tools/withheld-reconcile.py --quiet     # one RESULT line only (what CI prints)
EXIT
    0  reconciled — every citation is present, resolvable, or declared
    1  UNDECLARED: a citation is neither present, nor resolvable, nor in docs/WITHHELD.md
    2  a withheld citation in a reader-facing document has no '†'
    3  docs/WITHHELD.md is missing (nothing can be reconciled against nothing)
"""
import fnmatch
import os
import re
import sys

LEDGER = "docs/WITHHELD.md"
READER_SUFFIX = (".md",)
READER_NAMES = ("Makefile", "symbiont/cycle.yml")
SCAN_SUFFIX = (".md", ".py", ".sh", ".yml", ".yaml", ".txt", ".toml", ".scm")
SCAN_NAMES = ("Makefile",)
# docs/reference/ holds ARCHIVED descriptions of this station: their citations are resolved as of their own date, and rewriting them to add a
# '†' would falsify the record — so they are skipped by the reconciliation on purpose (2026-11-12).
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}
SKIP_PATHS = {"docs/reference"}   # relative prefixes, checked against the walker's relative dir
SKIP_FILES = {LEDGER}

# a path with a directory part, as this tree writes them
TOP = "annot|bench|data|docs|harness|nurse|reports|symbiont|tools"
PATH_RE = re.compile(r"(?<![\w./:-])(?:\.\./)*(?:" + TOP + r")/[A-Za-z0-9_.*/-]+\.[A-Za-z0-9]{1,5}\b")
# a bare file name a withheld row may also be cited by (e.g. "SCOUT-AI-MINING-STATIONS.md §10")
BARE_RE = re.compile(r"\b(?:DESIGN-STATION|SCOUT-[A-Z0-9-]+|MCP-SURVEY-[A-Z0-9-]+|INCIDENT-[0-9-]+[A-Z0-9-]*)\.md\b")


def ledger_rows(root):
    """Parse docs/WITHHELD.md -> (withheld patterns, all declared patterns).

    Every table in the file declares paths in its FIRST column. A row is a WITHHELD row when the table
    sits under a heading that says WITHHELD (and not "NOT WITHHELD"). Prose is not a declaration: only
    the first cell of a table row is.
    """
    path = os.path.join(root, LEDGER)
    withheld, declared = [], []
    if not os.path.isfile(path):
        return None, None
    section_withheld = False
    for line in open(path, encoding="utf-8", errors="ignore"):
        if line.startswith("#"):
            section_withheld = "WITHHELD" in line.upper() and "NOT WITHHELD" not in line.upper()
            continue
        if not line.lstrip().startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 3:
            continue
        for one in [c.strip() for c in re.findall(r"`([^`]+)`", cells[1])]:
            if "/" not in one or one.endswith("/") or one.startswith(".") or "*" == one:
                continue
            declared.append(one)
            if section_withheld:
                withheld.append(one)
    return withheld, declared


def scan(root):
    """Yield (relpath, lineno, text, cited_path) for every citation in every scanned file."""
    for base, dirs, files in os.walk(root):
        rel = os.path.relpath(base, root)
        if any(rel == p or rel.startswith(p + os.sep) for p in SKIP_PATHS):
            dirs[:] = []; continue
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(files):
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel in SKIP_FILES:
                continue
            if not (name in SCAN_NAMES or name.lower().endswith(SCAN_SUFFIX)):
                continue
            try:
                text = open(full, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                seen = []
                for m in PATH_RE.findall(line) + BARE_RE.findall(line):
                    if m not in seen:
                        seen.append(m)
                for m in seen:
                    yield rel, lineno, line, m


def covered(cited, patterns):
    """Is this cited path declared by one of the ledger's patterns? A bare `NAME.md` citation is covered
    by a declared `reports/<NAME>.md`, and by a declared family such as `reports/SCOUT-*.md`. A pattern
    whose basename is a bare '*' covers only paths that share its directory (never every bare name)."""
    base = os.path.basename(cited)
    for p in patterns:
        if cited == p:
            return True
        if "*" in p:
            if fnmatch.fnmatch(cited, p):
                return True
            if "/" not in cited and os.path.basename(p) != "*" and fnmatch.fnmatch(base, os.path.basename(p)):
                return True
        elif "/" not in cited and os.path.basename(p) == base:
            return True
    return False


def main(argv):
    quiet = "--quiet" in argv
    args = [a for a in argv[1:] if not a.startswith("--")]
    root = os.path.abspath(args[0]) if args else os.getcwd()

    withheld, declared = ledger_rows(root)
    if declared is None:
        print(f"RESULT FAIL[{LEDGER} missing] — nothing to reconcile against in {root}")
        return 3

    tree = [os.path.relpath(os.path.join(b, f), root).replace(os.sep, "/")
            for b, d, fs in os.walk(root)
            if not (set(os.path.relpath(b, root).split(os.sep)) & SKIP_DIRS)
            and not any(os.path.relpath(b, root) == p or os.path.relpath(b, root).startswith(p + os.sep) for p in SKIP_PATHS)
            for f in fs]

    def glob_hit(pattern, near):
        """A citation may be a glob (annot/*.py): it is present if any file in the tree matches it."""
        if "*" not in pattern:
            return False
        for f in tree:
            if fnmatch.fnmatch(f, pattern) or fnmatch.fnmatch(f, os.path.join(near, pattern).replace(os.sep, "/")):
                return True
        return False

    present = resolved = named = 0
    undeclared, bare = [], []
    by_name = {}
    for f in tree:
        by_name.setdefault(os.path.basename(f), []).append(f)

    for rel, lineno, line, cited in scan(root):
        near = os.path.dirname(rel)
        hit = os.path.isfile(os.path.join(root, cited))
        if not hit:
            hit = os.path.isfile(os.path.join(root, near, cited))
            if hit:
                resolved += 1
        else:
            present += 1
        if not hit:
            hit = glob_hit(cited, near)
            if hit:
                present += 1
        if not hit:
            # a package-relative reference (adapters/pubmed.py inside the literature package, a store
            # path like digest/gaps.md): resolved when a file of that name lives in the tree
            hit = os.path.basename(cited) in by_name and "*" not in cited
            if hit:
                named += 1
        if hit:
            continue
        if not covered(cited, declared):
            undeclared.append((rel, lineno, cited, line.strip()[:110]))
        elif covered(cited, withheld) and (rel.endswith(READER_SUFFIX) or rel in READER_NAMES):
            if "†" not in line and (rel, lineno) not in [(r, n) for r, n, _ in bare]:
                bare.append((rel, lineno, cited))

    if not quiet:
        for rel, lineno, cited, text in undeclared:
            print(f"UNDECLARED  {rel}:{lineno}  {cited}\n            {text}")
        for rel, lineno, cited in bare:
            print(f"NO-FOOTNOTE {rel}:{lineno}  {cited}  (a withheld path needs '†' here)")
        print(f"scanned {root}")
        print(f"  citations present                {present}")
        print(f"  citations resolved from the file  {resolved}   (a short docs/<X>.md inside symbiont/ = symbiont/docs/<X>.md)")
        print(f"  citations resolved by name        {named}   (a package-relative or store-relative reference: adapters/pubmed.py, digest/gaps.md)")
        print(f"  withheld rows declared            {len(withheld)}")
        print(f"  declared paths                    {len(declared)}")
        print(f"  UNDECLARED                        {len(undeclared)}")
        print(f"  withheld citations without '†'     {len(bare)}")

    if undeclared:
        print(f"RESULT FAIL[undeclared={len(undeclared)}]  {root}")
        return 1
    if bare:
        print(f"RESULT FAIL[no-footnote={len(bare)}]  {root}")
        return 2
    print(f"RESULT RECONCILED  citations={present + resolved + named} declared={len(declared)} withheld={len(withheld)}  {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
