# SYMBIONT — the station's checks. `make check` is OFFLINE: every fixture is served from
# 127.0.0.1 by the tests themselves, no external source is contacted, so a stranger can see the
# station work with the sources firewalled. The nine tests are the design's T1..T9
# (reports/DESIGN-STATION.md §9); which pass today and which are expected to fail is stated in
# the README and in each line below.
PYTHON ?= python3

check:
	$(PYTHON) -m symbiont.selftest_arc	# T1 idempotency · T2 counts · T3 corruption · T4 projection (miniature, offline)
	$(PYTHON) -m symbiont.selftest_sources	# T5 budget · T6 licence-unknown · T7 staleness · T9 source-gone+quarantine+card
	@echo "T8 AGENT — EXPECTED FAIL: not built yet (design §9 names it; an agent must answer from tool output with every sentence mapping to an accession). Everything else above must be PASS or make fails."
	$(PYTHON) -m symbiont.selftest_health	# supporting: source health, the one OBS command (12/12)
	$(PYTHON) -m symbiont.selftest_admission_policy	# supporting: the gated admission step, fail-closed (20/20)
	@echo "make check: T1-T7 and T9 GREEN (offline) · T8 expected-fail, named above"

# regenerate the committed miniature store a stranger reads before fetching anything
fixtures-store:
	$(PYTHON) -m symbiont.selftest_arc --write-fixture-store symbiont/fixtures/store

.PHONY: check fixtures-store
