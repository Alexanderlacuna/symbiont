# SYMBIONT — the station's checks. `make check` is OFFLINE: every fixture is served from
# 127.0.0.1 by the tests themselves, no external source is contacted, so a stranger can see the
# station work with the sources firewalled. The nine tests are the design's T1..T9
# (reports/DESIGN-STATION.md† §9); which pass today and which are expected to fail is stated in
# the README and in each line below.
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

check: hygiene
	$(PYTHON) -m symbiont.selftest_arc	# T1 idempotency · T2 counts · T3 corruption · T4 projection (miniature, offline)
	$(PYTHON) -m symbiont.selftest_sources	# T5 budget · T6 licence-unknown · T7 staleness · T9 source-gone+quarantine+card
	@echo "T8 AGENT — EXPECTED FAIL: not built yet (design §9 names it; an agent must answer from tool output with every sentence mapping to an accession). Everything else above must be PASS or make fails."
	$(PYTHON) -m symbiont.selftest_health	# supporting: source health, the one OBS command (12/12)
	$(PYTHON) -m symbiont.selftest_admission_policy	# supporting: the gated admission step, fail-closed (20/20)
	@echo "make check: T1-T7 and T9 GREEN (offline) · T8 expected-fail, named above"

# regenerate the committed miniature store a stranger reads before fetching anything
fixtures-store:
	$(PYTHON) -m symbiont.selftest_arc --write-fixture-store symbiont/fixtures/store

.PHONY: check hygiene fixtures-store

† withheld from this public tree; see docs/WITHHELD.md.
