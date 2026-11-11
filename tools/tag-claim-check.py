#!/usr/bin/env python3
"""TAG-CLAIM CHECK — a doc that names a release tag must name a ref that EXISTS.

WHY (req-P2-THE-RELEASE-IS-FALSIFIABLE-TAG-AND-R): CHANGELOG.md said the release was "named by the
tag `v0.1.0` on the publish commit" while `git ls-remote --tags` against the published repository
returned nothing. The claim was unfalsifiable in the sense that mattered — no command failed when it
became false. This is that command.

WHAT IT COUNTS. It reads every `*.md` in the tree and looks for TWO positive claim shapes:
    S1  the word `tagged`
    S2  `tag` followed, on the same line, by a version `vX.Y.Z`
Each claimed version must exist as a ref in at least one receipt source (`--repo` a git working copy,
`--remote` a URL or a remote name). Anything else is exit 1.

WHAT IT CANNOT READ: negation. A sentence worded about the *ref* ("no `v0.1.0` ref exists in this
repository") or about the command that lists them ("`git ls-remote --tags` returns nothing") is not a
claim shape and is not flagged — that is deliberate: the rule for a denial is to word it about the
ref, and the rule for a claim is to make it true. A bare `tagged` with no version is reported as an
unverifiable claim (exit 1), because nothing can be checked against a word.

USAGE
    python3 tools/tag-claim-check.py [TREE] [--repo GIT_DIR] [--remote URL_OR_REMOTE] [--quiet]
        TREE      default: the current directory
        --repo    a git working copy whose tags are a receipt (e.g. the release mirror)
        --remote  a URL or a configured remote name; its tags are read with `git ls-remote --tags`
EXIT
    0  no tag claim in the tree, or every claimed version exists in a receipt source
    1  TAG-CLAIM-WITHOUT-REF: a doc names a release tag that no receipt source has
    2  UNVERIFIABLE: no receipt source given, or every source unreachable — a release claim that
       nobody can check is not a checked release, so this fails closed rather than passing quietly
    3  the tree does not exist
"""
import os
import re
import subprocess
import sys

S1 = re.compile(r"\btagged\b")
S2 = re.compile(r"\btag\b\s*[:=`'\"]?\s*`?(v?\d+\.\d+\.\d+)")


def refs_from_repo(path):
    r = subprocess.run(["git", "-C", path, "tag", "-l"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("git -C %s tag -l :: %s" % (path, (r.stderr or "").strip()[:160]))
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def refs_from_remote(remote):
    r = subprocess.run(["git", "ls-remote", "--tags", remote], capture_output=True, text=True,
                       timeout=120)
    if r.returncode != 0:
        raise RuntimeError("git ls-remote --tags %s :: %s" % (remote, (r.stderr or "").strip()[:160]))
    out = []
    for line in r.stdout.splitlines():
        ref = line.split("\t")[-1].strip()
        if ref.startswith("refs/tags/"):
            out.append(ref[len("refs/tags/"):].replace("^{}", ""))
    return sorted(set(out))


def main(argv):
    args = [a for a in argv]
    tree, repo, remote, quiet = ".", None, None, False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--repo" and i + 1 < len(args):
            repo = args[i + 1]; i += 2
        elif a == "--remote" and i + 1 < len(args):
            remote = args[i + 1]; i += 2
        elif a == "--quiet":
            quiet = True; i += 1
        elif a in ("-h", "--help"):
            print(__doc__); return 0
        elif a.startswith("-"):
            print("tag-claim-check: unknown argument %r (see --help)" % a); return 2
        else:
            tree = a; i += 1

    if not os.path.isdir(tree):
        print("TAG-CLAIM: no tree at %s" % tree); return 3

    claims = []          # (path, lineno, text, version|None)
    docs = 0
    for root, _dirs, files in os.walk(tree):
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            docs += 1
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                rel = os.path.relpath(path, tree)
                bare = S1.search(line)
                hits = S2.findall(line)
                if bare and not hits:
                    claims.append((rel, n, line.strip(), None))
                for v in hits:
                    claims.append((rel, n, line.strip(), v if v.startswith("v") else "v" + v))

    if not quiet:
        print("TAG-CLAIM: scanned %d *.md under %s" % (docs, tree))
        print("TAG-CLAIM: claim shapes S1=%r S2=%r" % (S1.pattern, S2.pattern))

    if not claims:
        print("RESULT TAG-CLAIM NONE — 0 claim(s) in %d *.md; nothing claims a release tag" % docs)
        return 0

    sources, source_errors = {}, []
    if repo:
        try:
            sources["repo:" + repo] = refs_from_repo(repo)
        except Exception as e:                       # noqa: BLE001 — reported, not raised
            source_errors.append(str(e))
    if remote:
        try:
            sources["remote:" + remote] = refs_from_remote(remote)
        except Exception as e:                       # noqa: BLE001
            source_errors.append(str(e))

    for err in source_errors:
        print("TAG-CLAIM: source unreachable — %s" % err)
    if not sources:
        print("RESULT TAG-CLAIM UNVERIFIABLE — %d claim(s) and no receipt source answered "
              "(pass --repo and/or --remote; see --help)" % len(claims))
        return 2

    bad = []
    for rel, n, text, version in claims:
        if version is None:
            bad.append((rel, n, "no version named — nothing can be checked against the word 'tagged'", text))
            continue
        have = [k for k, refs in sources.items() if version in refs]
        if not have:
            bad.append((rel, n, "%s exists in no receipt source (%s)"
                        % (version, ", ".join("%s: %d tag(s)" % (k, len(v)) for k, v in sources.items())),
                        text))
        elif not quiet:
            print("  ok %s:%d — %s present in %s" % (rel, n, version, ", ".join(have)))

    if bad:
        for rel, n, why, text in bad:
            print("TAG-CLAIM-WITHOUT-REF %s:%d — %s" % (rel, n, why))
            print("    | %s" % text[:200])
        print("RESULT TAG-CLAIM RED — %d claim(s), %d with no ref; remedy: make the ref and push it, "
              "or word the line about the ref that does not exist" % (len(claims), len(bad)))
        return 1

    print("RESULT TAG-CLAIM GREEN — %d claim(s), every one backed by a ref in %s"
          % (len(claims), ", ".join(sources)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
