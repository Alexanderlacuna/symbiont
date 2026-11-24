"""PROOF of the admission policy (req-P1-ADMISSION-POLICY-the-N-stable-cycles).

Every fixture below is built in a throwaway directory and exercised through the REAL command
(`python3 -m symbiont.admission_policy source_admit ...`) as a subprocess — the same code path an
operator or an agent takes. Nothing here touches the network, the shipped sources.yml, the
shipped store, or the admission pipeline's files (symbiont/admission.py belongs to the parallel
SOURCE-HEALTH row and is never imported here).

  S1  N unset (the SHIPPED reports/symbiont-policy.yml)  -> self-admission REFUSED BY NAME,
      fail closed: the owner sets N, the station only reads it
  S2  N=2, a source stable for 3 cycles, every card check ok -> SELF-ADMITTED, exit 0, and the
      admission record quotes 'by policy, rule stable_cycles_before_self_admit, N=2'
  S3  one card check failing (cost) -> never self-admits regardless of N (tested at N=2 AND N=1)
  S4  a malformed policy file -> fail closed, exit 2, the parse error NAMED
  S5  a missing policy file     -> fail closed, exit 2, 'missing' named
  S6  a quarantined source      -> never self-admitted; the way back is the readmit card
  S7  an unstable source (the newest cycle failed its count check) -> not stable, refused
  S8  the caps, when the owner sets them, are read and enforced (max_sources_active /
      max_new_sources_per_cycle), and are silent when unset
  S9  the GATED STEP ITSELF consults the policy (`python3 -m symbiont.admission admit`, the CLI
      beneath the source_admit MCP tool): with N=2 and 3 stable cycles and every card check ok it
      admits with NO gate word and its receipt says 'by policy, rule ... N=2'; a source failing
      one card check still needs the operator's word (REFUSED[gate] without it, admitted with
      it, the receipt recording which); a malformed policy file fails closed for self-admission
      ONLY — the operator's word still admits — and N unset is named at the gate too

Usage: python3 -m symbiont.selftest_admission_policy [--keep]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile

from . import admission_policy, contract

FARM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED_POLICY = admission_policy.DEFAULT_POLICY
SID = "fixture_trial"


# ------------------------------------------------------------------ fixture builders
def write_run(store: str, run_id: str, started_at: str, sid: str, verify_ok: bool = True,
              skipped: str | None = None, refused: dict | None = None,
              readmitted: bool = False) -> str:
    """One run record, shaped exactly as the loop writes them (the fields the policy reader reads)."""
    src = dict(id=sid)
    if skipped:
        src["skipped"] = dict(reason=skipped, detail="fixture")
    if refused:
        src["refused"] = refused
    if readmitted:
        src["readmitted"] = dict(card="/fixture/card.md", changed="fixture re-admission")
    src["verify"] = dict(ok=verify_ok, ours=3, source_count=3, url="http://fixture/count", detail="")
    rec = dict(run_id=run_id, question="admission fixture", started_at=started_at,
               finished_at=started_at, since="auto", replay=[], sources_path="/fixture/sources.yml",
               sources={sid: src}, skipped={}, findings=[], refusals=[], xref={}, publish={},
               status="PUBLISHED" if verify_ok else "REFUSED", cost=dict(requests=1))
    path = os.path.join(store, "runs", f"{run_id}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, sort_keys=True)
    return path


def trial_store(tmp: str, cycles: int = 3) -> str:
    """A store whose run records show <sid> stable for <cycles> consecutive cycles."""
    store = os.path.join(tmp, "store")
    for i in range(cycles):
        at = dt.datetime(2026, 11, 10, 3 + i, 15, tzinfo=dt.timezone.utc).isoformat()
        write_run(store, f"cycle-{i + 1}", at, SID)
    return store


GOOD_CARD = """source: fixture_trial
changed: trial complete, every card check green
check licence: ok
check count_match: ok
check sample_rows: ok
check xref_rate: ok
check cost: ok
"""

BAD_COST_CARD = GOOD_CARD.replace("check cost: ok", "check cost: over budget (12 requests > cap 4)")


def policy_text(n, max_active=None, max_new=None) -> str:
    ma = "null" if max_active is None else max_active
    mn = "null" if max_new is None else max_new
    return f"""version: 1

policy:
  stable_cycles_before_self_admit: {n}
  max_sources_active: {ma}
  max_new_sources_per_cycle: {mn}
  licence_must_be_read: true
  card_checks_required:
    - licence
    - count_match
    - sample_rows
    - xref_rate
    - cost
  quarantine_after_count_mismatches: 2
  quarantine_after_rate_breach: true
"""


def one_source_config(path: str, sid: str = "other_source", quarantined: bool = False) -> str:
    """A minimal VALID sources.yml with one enabled source (the shipped schema, one entry)."""
    q = ("\n    quarantined:\n      reason: fixture quarantine\n      at: 2026-11-10T01:00:00+00:00\n"
         "      status: count-mismatch" if quarantined else "\n    quarantined: null")
    text = ("version: 1\n\nsources:\n  - id: " + sid + "\n    adapter: ensembl_biomart\n"
            "    enabled: true\n    base_url: http://fixture.example/mart\n    endpoints:\n"
            "      mart: \".\"\n    storage:\n      mart:\n        class: store\n"
            "        reason: \"fixture: storage permitted\"\n    rate_limit:\n"
            "      requests_per_minute: 60\n"
            "      min_interval_s: 1\n    licence_url: http://fixture.example/licence\n"
            "    licence_text: \"fixture terms\"\n    staleness:\n      policy_days: 30\n"
            "    budget:\n      requests: 10\n      bytes: 1000000\n      wall_s: 100" + q + "\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def admit(store: str, card: str, policy: str, sid: str = SID, config: str | None = None):
    cmd = [sys.executable, "-m", "symbiont.admission_policy", "source_admit",
           "--source", sid, "--card", card, "--store", store, "--policy", policy]
    if config:
        cmd += ["--sources-config", config]
    return subprocess.run(cmd, cwd=FARM, capture_output=True, text=True)


STUB_ADAPTER = '''"""A drafted adapter stub for the S9 fixtures (the gated step's load check)."""
class _Adapter:
    name = "fixture_trial"
    role = "id_authority"
    def configure(self, cfg):
        return cfg
    def discover(self, cfg, fetcher):
        return {}
    def fetch(self, cfg, fetcher, disc):
        return {}
    def normalise(self, disc, raw):
        return {}
    def verify(self, cfg, counts, fetcher):
        return {}

ADAPTER = _Adapter()
'''


def gate_fixture(tmp: str, card: str, policy: str, cycles: int = 3) -> list[str]:
    """Everything the GATED STEP needs on disk, in a throwaway tree: the source registered
    enabled:false, the admission card, the drafted adapter, and a store whose runs show the
    source stable for <cycles> cycles. Returns the argv of `symbiont.admission admit` with NO
    gate word — the caller appends --gate when it wants the operator's path."""
    root = os.path.join(tmp, "gate")
    store = trial_store(os.path.join(tmp, "gate"))
    adm = os.path.join(root, "admission")
    os.makedirs(os.path.join(adm, SID), exist_ok=True)
    with open(os.path.join(adm, SID, "card.md"), "w", encoding="utf-8") as fh:
        fh.write(card)
    drafts = os.path.join(root, "drafts")
    os.makedirs(drafts, exist_ok=True)
    with open(os.path.join(drafts, f"{SID}.py"), "w", encoding="utf-8") as fh:
        fh.write(STUB_ADAPTER)
    adapters = os.path.join(root, "adapters")
    os.makedirs(adapters, exist_ok=True)
    cfg = os.path.join(root, "sources.yml")
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write("version: 1\n\nsources:\n  - id: " + SID + "\n    adapter: " + SID + "\n"
                 "    enabled: false\n    base_url: http://fixture.example/mart\n"
                 "    endpoints:\n      mart: \".\"\n    storage:\n      mart:\n"
                 "        class: store\n        reason: \"fixture: storage permitted\"\n"
                 "    rate_limit:\n"
                 "      requests_per_minute: 60\n      min_interval_s: 1\n"
                 "    licence_url: http://fixture.example/licence\n"
                 "    licence_text: \"fixture terms\"\n    staleness:\n      policy_days: 30\n"
                 "    budget:\n      requests: 10\n      bytes: 1000000\n      wall_s: 100\n"
                 "    quarantined: null\n")
    gate = os.path.join(root, "gate.txt")
    with open(gate, "w", encoding="utf-8") as fh:
        fh.write("# fixture gate\n" + GATE_WORD + "\n")
    return [sys.executable, "-m", "symbiont.admission", "admit", SID,
            "--store", store, "--admission-dir", adm, "--drafts-dir", drafts,
            "--adapters-dir", adapters, "--sources-config", cfg, "--gate-file", gate,
            "--policy", policy]


GATE_WORD = "i want to be able to check health of the sources via agent"


def admit_gated(argv: list[str], gate: str | None = None):
    cmd = list(argv)
    if gate is not None:
        cmd += ["--gate", gate]
    return subprocess.run(cmd, cwd=FARM, capture_output=True, text=True)


# ------------------------------------------------------------------ the battery
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="symbiont.selftest_admission_policy")
    ap.add_argument("--keep", action="store_true", help="keep the fixture directory, print its path")
    a = ap.parse_args(argv)

    tmp = tempfile.mkdtemp(prefix="admission-policy-fixtures.")
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  PASS: {name}")
        else:
            failed += 1
            print(f"  FAIL: {name} — {detail}")

    def w(path: str, text: str) -> str:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    try:
        # S1 — the SHIPPED policy: N unset -> refuse BY NAME, fail closed ------------------
        pol = admission_policy.load_policy(SHIPPED_POLICY)
        check("S1 the shipped reports/symbiont-policy.yml loads and leaves N unset (the owner "
              "has not set it; the station invents nothing)",
              pol[admission_policy.RULE] is None, f"N={pol[admission_policy.RULE]!r}")
        store1 = trial_store(os.path.join(tmp, "s1"))
        card1 = w(os.path.join(tmp, "s1", "card.md"), GOOD_CARD)
        p = admit(store1, card1, SHIPPED_POLICY)
        check("S1 with N unset the station refuses self-admission BY NAME, exit 2",
              p.returncode == 2 and f"REFUSED[admit] {SID}" in p.stdout
              and "sets no stable_cycles_before_self_admit" in p.stdout
              and "fail closed" in p.stdout,
              f"exit={p.returncode} out={(p.stdout.strip().splitlines() or [''])[-1][:90]}")

        # S2 — N=2, stable for 3 cycles -> self-admit, the record quotes the rule and N -------
        d2 = os.path.join(tmp, "s2")
        store2 = trial_store(d2)
        pol2 = w(os.path.join(d2, "policy-n2.yml"), policy_text(2))
        card2 = w(os.path.join(d2, "card.md"), GOOD_CARD)
        p = admit(store2, card2, pol2)
        rec_path = os.path.join(store2, "admissions", f"{SID}.json")
        line_ok = False
        if os.path.isfile(rec_path):
            rec = json.load(open(rec_path, encoding="utf-8"))
            line_ok = (rec.get("line") == "self-admitted by policy, rule "
                       "stable_cycles_before_self_admit, N=2" and rec.get("N") == 2
                       and rec.get("rule") == "stable_cycles_before_self_admit"
                       and rec.get("stable_cycles") == 3)
        check("S2 with N=2 and a source stable for 3 cycles it self-admits, exit 0, and the "
              "admission record quotes the rule and the number",
              p.returncode == 0 and "ADMITTED" in p.stdout
              and "by policy, rule stable_cycles_before_self_admit, N=2" in p.stdout
              and os.path.isfile(rec_path) and line_ok,
              f"exit={p.returncode} record={os.path.isfile(rec_path)} line_ok={line_ok}")
        check("S2 the self-admission lands in the source's digest too",
              os.path.isfile(os.path.join(store2, "digest", f"{SID}.md"))
              and "self-admitted" in open(os.path.join(store2, "digest", f"{SID}.md"),
                                          encoding="utf-8").read())

        # S3 — one card check failing -> never self-admits regardless of N -----------------
        d3 = os.path.join(tmp, "s3")
        store3 = trial_store(d3)
        bad_card = w(os.path.join(d3, "card-bad-cost.md"), BAD_COST_CARD)
        p_n2 = admit(store3, bad_card, w(os.path.join(d3, "policy-n2.yml"), policy_text(2)))
        p_n1 = admit(store3, bad_card, w(os.path.join(d3, "policy-n1.yml"), policy_text(1)))
        check("S3 a source failing one card check never self-admits regardless of N "
              "(refused at N=2 AND N=1, the failed check named, no record written)",
              p_n2.returncode == 2 and p_n1.returncode == 2
              and "check cost failed" in p_n2.stdout
              and "never self-admits regardless of N" in p_n2.stdout
              and not os.path.exists(os.path.join(store3, "admissions")),
              f"exit_n2={p_n2.returncode} exit_n1={p_n1.returncode}")

        # S4 — a malformed policy file -> fail closed, the parse error named ---------------
        d4 = os.path.join(tmp, "s4")
        store4 = trial_store(d4)
        bad_pol = w(os.path.join(d4, "policy-malformed.yml"),
                    "version: 1\n\npolicy: {\n  stable_cycles_before_self_admit: 2\n")
        p = admit(store4, w(os.path.join(d4, "card.md"), GOOD_CARD), bad_pol)
        check("S4 a malformed policy file fails closed, exit 2, the parse error named",
              p.returncode == 2 and "REFUSED[policy]" in p.stdout and "malformed" in p.stdout
              and "the parse error named:" in p.stdout
              and len(p.stdout.strip()) > 60,
              f"exit={p.returncode} out={(p.stdout.strip().splitlines() or [''])[-1][:90]}")
        # S4b — a policy with an unknown key is refused by name (config/code disagreement)
        unk_pol = w(os.path.join(d4, "policy-unknown-key.yml"),
                    policy_text(2) + "  invented_number: 7\n")
        p = admit(store4, w(os.path.join(d4, "card.md"), GOOD_CARD), unk_pol)
        check("S4b a policy with an unknown key is refused by name, fail closed",
              p.returncode == 2 and "unknown key(s) ['invented_number']" in p.stdout,
              f"exit={p.returncode}")

        # S5 — a missing policy file -> fail closed ----------------------------------------
        d5 = os.path.join(tmp, "s5")
        store5 = trial_store(d5)
        p = admit(store5, w(os.path.join(d5, "card.md"), GOOD_CARD),
                  os.path.join(d5, "policy-absent.yml"))
        check("S5 a missing policy file fails closed, exit 2, 'missing' named",
              p.returncode == 2 and "REFUSED[policy]" in p.stdout and "is missing" in p.stdout
              and "fail closed" in p.stdout,
              f"exit={p.returncode}")

        # S6 — a quarantined source is never self-admitted ---------------------------------
        d6 = os.path.join(tmp, "s6")
        store6 = trial_store(d6)
        cfg6 = one_source_config(os.path.join(d6, "sources.yml"), sid=SID, quarantined=True)
        p = admit(store6, w(os.path.join(d6, "card.md"), GOOD_CARD),
                  w(os.path.join(d6, "policy-n2.yml"), policy_text(2)), config=cfg6)
        check("S6 a quarantined source is never self-admitted; the way back is the readmit card",
              p.returncode == 2 and "quarantined since" in p.stdout
              and "--readmit" in p.stdout
              and not os.path.exists(os.path.join(store6, "admissions")),
              f"exit={p.returncode}")

        # S7 — an unstable source (the newest cycle failed) is not stable -------------------
        d7 = os.path.join(tmp, "s7")
        store7 = trial_store(d7, cycles=3)
        at = dt.datetime(2026, 11, 10, 7, 15, tzinfo=dt.timezone.utc).isoformat()
        write_run(store7, "cycle-4", at, SID, verify_ok=False)
        p = admit(store7, w(os.path.join(d7, "card.md"), GOOD_CARD),
                  w(os.path.join(d7, "policy-n2.yml"), policy_text(2)))
        check("S7 an unstable source (newest cycle failed its count check) is refused: "
              "the chain is broken, stable for 0",
              p.returncode == 2 and "stable for 0 consecutive cycle(s)" in p.stdout,
              f"exit={p.returncode} out={(p.stdout.strip().splitlines() or [''])[-1][:90]}")
        # S7b — a skipped run does not touch the chain: 3 stable + one later skipped run still admits
        store7b = os.path.join(tmp, "s7b")
        at2 = dt.datetime(2026, 11, 10, 8, 15, tzinfo=dt.timezone.utc).isoformat()
        write_run(store7b, "cycle-4", at2, SID, skipped="not-selected")
        for i in range(3):
            a3 = dt.datetime(2026, 11, 10, 3 + i, 15, tzinfo=dt.timezone.utc).isoformat()
            write_run(store7b, f"cycle-{i + 1}", a3, SID)
        p = admit(store7b, w(os.path.join(d7, "card.md"), GOOD_CARD),
                  w(os.path.join(d7, "policy-n3.yml"), policy_text(3)))
        check("S7b a skipped run is not a cycle: 3 stable cycles still self-admit at N=3",
              p.returncode == 0, f"exit={p.returncode}")

        # S8 — the caps are read and enforced when set, silent when unset -------------------
        d8 = os.path.join(tmp, "s8")
        store8 = trial_store(d8)
        cfg8 = one_source_config(os.path.join(d8, "sources.yml"), sid="other_source")
        p = admit(store8, w(os.path.join(d8, "card.md"), GOOD_CARD),
                  w(os.path.join(d8, "policy-cap-active.yml"), policy_text(2, max_active=1)),
                  config=cfg8)
        check("S8 max_sources_active=1 with one source already active refuses the admission, "
              "the cap named",
              p.returncode == 2 and "max_sources_active=1 and 1 active" in p.stdout,
              f"exit={p.returncode}")
        store8b = os.path.join(tmp, "s8b")
        for i in range(2):
            a8 = dt.datetime(2026, 11, 10, 3 + i, 15, tzinfo=dt.timezone.utc).isoformat()
            write_run(store8b, f"cycle-{i + 1}", a8, SID)
        adir = os.path.join(store8b, "admissions")
        os.makedirs(adir, exist_ok=True)
        today = dt.datetime.now(dt.timezone.utc).isoformat()
        with open(os.path.join(adir, "other_source.json"), "w", encoding="utf-8") as fh:
            json.dump(dict(source="other_source", at=today, line="fixture admission today"), fh)
        p = admit(store8b, w(os.path.join(d8, "card.md"), GOOD_CARD),
                  w(os.path.join(d8, "policy-cap-new.yml"), policy_text(2, max_new=1)))
        check("S8b max_new_sources_per_cycle=1 with one admission already today refuses, "
              "the cap named",
              p.returncode == 2 and "max_new_sources_per_cycle=1 and 1 source(s) already" in p.stdout,
              f"exit={p.returncode}")
        p = admit(trial_store(os.path.join(tmp, "s8c")),
                  w(os.path.join(d8, "card.md"), GOOD_CARD),
                  w(os.path.join(d8, "policy-caps-null.yml"),
                    policy_text(2, max_active="null", max_new="null")), config=cfg8)
        check("S8c caps unset (null) are read and not invented: the admission proceeds",
              p.returncode == 0, f"exit={p.returncode}")

        # S9 — the GATED STEP ITSELF consults the policy (`symbiont.admission admit`) ------
        os.makedirs(os.path.join(tmp, "s9"), exist_ok=True)
        pol_n2 = w(os.path.join(tmp, "s9", "policy-n2.yml"), policy_text(2))
        p = admit_gated(gate_fixture(os.path.join(tmp, "s9a"), GOOD_CARD, pol_n2))
        rec_path = os.path.join(tmp, "s9a", "gate", "admission", SID, "admitted.json")
        rec = json.load(open(rec_path, encoding="utf-8")) if os.path.isfile(rec_path) else {}
        check("S9a the gated step self-admits with NO gate word when the policy allows it, and "
              "its receipt quotes 'by policy, rule stable_cycles_before_self_admit, N=2'",
              p.returncode == 0 and "ADMITTED" in p.stdout
              and "by policy, rule stable_cycles_before_self_admit, N=2" in p.stdout
              and rec.get("by") == "policy"
              and (rec.get("policy") or {}).get("line")
              == "self-admitted by policy, rule stable_cycles_before_self_admit, N=2"
              and rec.get("gate_word") is None,
              f"exit={p.returncode} by={rec.get('by')} out={(p.stdout.strip().splitlines() or [''])[-1][:90]}")
        check("S9a the source really is enabled:true in sources.yml after the policy admission",
              "enabled: true" in open(os.path.join(tmp, "s9a", "gate", "sources.yml"),
                                      encoding="utf-8").read(),
              "sources.yml not flipped")

        p = admit_gated(gate_fixture(os.path.join(tmp, "s9b"), BAD_COST_CARD, pol_n2))
        check("S9b a source failing one card check never self-admits at the gated step: "
              "REFUSED[gate] without the operator's word, the card refused by name",
              p.returncode == 2 and "REFUSED[gate]" in p.stdout
              and "check cost failed" in p.stdout
              and not os.path.exists(os.path.join(tmp, "s9b", "gate", "admission", SID,
                                                 "admitted.json")),
              f"exit={p.returncode} out={(p.stdout.strip().splitlines() or [''])[-1][:90]}")
        p = admit_gated(gate_fixture(os.path.join(tmp, "s9c"), BAD_COST_CARD, pol_n2),
                        gate=GATE_WORD)
        rec_path = os.path.join(tmp, "s9c", "gate", "admission", SID, "admitted.json")
        rec = json.load(open(rec_path, encoding="utf-8")) if os.path.isfile(rec_path) else {}
        check("S9c WITH the operator's word the same source is admitted, and the receipt records "
              "that the policy did not self-admit it (by=operator, the policy's refusal named)",
              p.returncode == 0 and rec.get("by") == "operator"
              and (rec.get("policy") or {}).get("refusal", {}).get("code") == "card"
              and rec.get("gate_word") == GATE_WORD,
              f"exit={p.returncode} by={rec.get('by')} refusal={(rec.get('policy') or {}).get('refusal')}")

        os.makedirs(os.path.join(tmp, "s9d"), exist_ok=True)
        bad_pol9 = w(os.path.join(tmp, "s9d", "policy-malformed.yml"),
                     "version: 1\n\npolicy: {\n  stable_cycles_before_self_admit: 2\n")
        p = admit_gated(gate_fixture(os.path.join(tmp, "s9e"), GOOD_CARD, bad_pol9),
                        gate=GATE_WORD)
        rec_path = os.path.join(tmp, "s9e", "gate", "admission", SID, "admitted.json")
        rec = json.load(open(rec_path, encoding="utf-8")) if os.path.isfile(rec_path) else {}
        check("S9d a malformed policy file fails closed for SELF-admission ONLY: the parse error "
              "is named on stdout and the operator's word still admits (by=operator)",
              p.returncode == 0 and "does NOT self-admit [policy]" in p.stdout
              and "fail closed, nothing self-admits, the parse error named:" in p.stdout
              and rec.get("by") == "operator"
              and (rec.get("policy") or {}).get("refusal", {}).get("code") == "policy",
              f"exit={p.returncode} by={rec.get('by')}")
        p = admit_gated(gate_fixture(os.path.join(tmp, "s9f"), GOOD_CARD, SHIPPED_POLICY))
        check("S9e with N unset the gated step names it and demands the operator's word "
              "(REFUSED[gate], n-unset named, nothing admitted)",
              p.returncode == 2 and "REFUSED[gate]" in p.stdout and "n-unset" in p.stdout
              and "the policy sets no stable_cycles_before_self_admit" in p.stdout,
              f"exit={p.returncode} out={(p.stdout.strip().splitlines() or [''])[-1][:110]}")

        print(f"RESULT: {passed} passed, {failed} failed"
              + (f"  (fixtures kept: {tmp})" if a.keep else ""))
        return 0 if failed == 0 else 1
    finally:
        if not a.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
