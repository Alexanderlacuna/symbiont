# SYMBIONT — the station's checks. `make check` is OFFLINE: every fixture is served from
# 127.0.0.1 by the tests themselves, no external source is contacted, so a stranger can see the
# station work with the sources firewalled. The nine tests are the design's T1..T9
# (reports/DESIGN-STATION.md† §9); which pass today and which are expected to fail is stated in
# the README and in each line below.
# † held back from this public subset — every withheld path, with why and how to request it, is a
# row in docs/WITHHELD.md (reconcile with: python3 tools/withheld-reconcile.py).
PYTHON ?= python3

# ── HYGIENE — the tree carries no editor or rollback debris
#    (req-P1-THE-PUBLIC-TREE-HAS-NO-DEBRIS-AND-NAM). The checked set is what a reader receives: the git
#    INDEX when this tree has one (`git ls-files`), the files on disk otherwise (a dry publish build has
#    no .git until it is pushed). One match is REFUSED and the offender is quoted by path. guix.scm's
#    packaging filter and .github/workflows/check.yml's grep refuse the same names, so the repository
#    and the build filter cannot drift apart.
HYGIENE_MATCH = (\.bak-|~$$|\.orig$$|\.rej$$|\.pyc$$)

hygiene:
	@set -eu; \
	if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
	  list=$$(git ls-files); via='git ls-files (the git index)'; \
	else \
	  list=$$(find . -path ./.git -prune -o \( -name __pycache__ -o -name .pytest_cache -o -name node_modules \) -prune -o -type f -print | sed 's|^\./||'); \
	  via='find . (this tree has no git index)'; \
	fi; \
	total=$$(printf '%s\n' "$$list" | grep -c . || true); \
	debris=$$(printf '%s\n' "$$list" | grep -E '$(HYGIENE_MATCH)' || true); \
	if [ -n "$$debris" ]; then \
	  echo "make check: HYGIENE REFUSED — $${total} path(s) checked via $$via; debris found:"; \
	  printf '%s\n' "$$debris" | sed 's/^/    OFFENDER: /'; \
	  echo "make check: *.bak-* · *~ · *.orig · *.rej · *.pyc are editor or rollback leftovers; .gitignore refuses them and nothing ships them."; \
	  exit 1; \
	fi; \
	echo "make check: HYGIENE GREEN — 0 debris among $${total} path(s) checked via $$via"

check: hygiene dependency-version
	$(PYTHON) -m symbiont.selftest_arc	# T1 idempotency · T2 counts · T3 corruption · T4 projection (miniature, offline)
	$(PYTHON) -m symbiont.selftest_sources	# T5 budget · T6 licence-unknown · T7 staleness · T9 source-gone+quarantine+card
	@echo "T8 AGENT — EXPECTED FAIL: not built yet (design §9 names it; an agent must answer from tool output with every sentence mapping to an accession). Everything else above must be PASS or make fails."
	$(PYTHON) -m symbiont.selftest_health	# supporting: source health, the one OBS command (12/12)
	$(PYTHON) -m symbiont.selftest_digests	# the four digests: schema from two real runs, gaps+suggest, the refusal rules (15/15)
	$(PYTHON) -m symbiont.selftest_admission_policy	# supporting: the gated admission step, fail-closed (20/20)
	$(PYTHON) -m symbiont.selftest_retention	# retention + supersede: as-of/current door, superseded_by, archive-or-refuse (14/14)
	$(PYTHON) -m symbiont.selftest_sandbox	# the research layer's sandbox: write allowlist · the two NETWORK TIERS (kernel-enforced) · throwaway store · roles read-only (35/35 — incl. the hostile ctypes fixture F5 and the two tiers F6/F7)
	@echo "make check: T1-T7 and T9 GREEN (offline) · T8 expected-fail, named above"

# regenerate the committed miniature store a stranger reads before fetching anything
fixtures-store:
	$(PYTHON) -m symbiont.selftest_arc --write-fixture-store symbiont/fixtures/store

# THE STATION'S END-TO-END ACCEPTANCE RUN (req-P1-THE-STATIONS-END-TO-END-ACCEPTANCE):
# clone -> offline quickstart -> (optional) live fetch -> digests -> doors -> sandbox ->
# manifest -> stub MCP consumer -> receipt -> network DOWN. One command, one transcript.
acceptance:
	./acceptance.sh

# ── THE ONE-VERSION ASSERTION (req-P1-ONE-DEPENDENCY-ONE-VERSION-AND-A-CHECK) ───────────────────
# guix.scm used to carry its acceptance test as an INSTRUCTION to the reader ("grep PyYAML 6.0.2
# across those three files"). An instruction is a comment standing where an assertion should be: it
# was run late, it returned ZERO hits, and nothing noticed. This target is that instruction as an
# assertion, and `make check` runs it. It fails when
#   · a file names the dependency with NO version  (a versionless mention is not a spec)
#   · two files name DIFFERENT versions            (and it prints every file with its version)
#   · a file names a RANGE, not one version        (>= · <= · ~= · < · >)
# THE TREE it reads is what ships: the research record (reports/), scratch builds (build/), repos/,
# quarantine/, the store and vendored caches are excluded by the same names the shipper excludes
# them by (its "THE EXCLUDED SET"). In the built public tree that excluded set is empty, so the same
# target is a plain tree-wide grep there.
# NOTE (req-P2-THE-GATE-RUNS-ON-PUSH-NOT-ON-HONOUR): this comment may not cite a farm-only path by
# name — in the exported tree that path does not exist, docs/WITHHELD.md does not declare it, and
# check.yml's (e) step refuses the tree for a citation that resolves nowhere. That is how this line
# was found: the workflow ran, named the path, and would not go green while the comment lied.
_VERSION_INC := --include=*.md --include=*.toml --include=*.scm
_VERSION_EXCL := --exclude-dir=.git --exclude-dir=reports --exclude-dir=build --exclude-dir=repos \
	--exclude-dir=quarantine --exclude-dir=archive --exclude-dir=cache --exclude-dir=node_modules \
	--exclude-dir=__pycache__ --exclude-dir=.venv --exclude-dir=store

dependency-version:
	@M=$$(grep -rli $(_VERSION_INC) $(_VERSION_EXCL) -e 'pyyaml' . | LC_ALL=C sort); \
	if [ -z "$$M" ]; then echo "make check: ONE-VERSION REFUSED — NO file in the tree names the dependency; the check is reading the wrong tree"; exit 1; fi; \
	R=$$(grep -rHoiE $(_VERSION_INC) $(_VERSION_EXCL) -e 'pyyaml[[:space:]]*(>=|<=|~=|>|<)[[:space:]]*v?[0-9]' .); \
	if [ -n "$$R" ]; then echo "make check: ONE-VERSION REFUSED — a RANGE is not one version:"; printf '%s\n' "$$R" | sed 's/^/    OFFENDER: /'; exit 1; fi; \
	T=$$(grep -rHoiE $(_VERSION_INC) $(_VERSION_EXCL) -e 'pyyaml[[:space:]]*(==)?[[:space:]]*v?[0-9]+(\.[0-9]+)+' .); \
	V=$$(printf '%s\n' "$$T" | sed -e 's/^.*://' -e 's/^[^0-9]*//' | grep . | LC_ALL=C sort -u); \
	N=$$(printf '%s\n' "$$V" | grep -c .); \
	if [ "$$N" -gt 1 ]; then \
	  echo "make check: ONE-VERSION REFUSED — the ONE dependency is stated $$N different ways across the tree:"; \
	  printf '%s\n' "$$T" | awk -F: '{v=$$2; sub(/^[^0-9]*/,"",v); print "    OFFENDER: " $$1 " names " v}' | LC_ALL=C sort -u; \
	  exit 1; \
	fi; \
	MISS=""; for f in $$M; do printf '%s\n' "$$T" | grep -qF "$$f:" || MISS="$$MISS $$f"; done; \
	if [ -n "$$MISS" ]; then echo "make check: ONE-VERSION REFUSED — these files name the dependency and NO version:$$MISS"; exit 1; fi; \
	echo "make check: ONE-VERSION GREEN — $$(printf '%s\n' "$$M" | grep -c .) files name the dependency; one version: $$(printf '%s\n' "$$V")"

.PHONY: check hygiene dependency-version fixtures-store acceptance
