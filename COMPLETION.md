# Implemented remaining requirements

## Changes

- Transactional, idempotent and restart-safe rule controls. Structured eligibility and success corrections target matching active plans and pending failed judgments; explicit applicability is respected. Historical decisions retain their rule version. Unknown semantics fail closed and stale controls cannot roll rules back.
- Persistent outcome judgments with unique evidence, exact goal windows, late-arrival handling and explicit incomplete-observation results. Playback starts count only for goals whose declared success is a play, never as an episode completion or conversion.
- Stable randomized assignments, unique viewer-level cohort enrollment, explicit censoring and hand-tested Newcombe/Wilson confidence intervals. The independent validator now recomputes both endpoints from original assignments and events.
- G07 episode reconciliation against catalogue episode IDs, covered history, a fixed target series and a matching success signal.
- Local push, approved WhatsApp-template and in-app actions alongside homepages. Both initial and dispatch verification enforce applicable consent, locale, availability, quiet hours, caps, template/copy, session, rule and call-budget constraints. No real external sends.
- Append-only transactional history and read-only `audit --at` reconstruction. Separate event observation times prevent late arrivals or batch outcomes from appearing as earlier knowledge. Additive migrations preserve legacy state and flag missing historical coverage.
- Bounded provider execution supports Windows spawn as well as fork; startup failure, unavailable storage, slow verification and restart interruption fail closed. Minimal viewer records survive safe abstentions when the primary database is available.

## Changed implementation files

`engine.py`, `store.py`, `policy.py`, `verifier.py`, `outcomes.py`, `measure.py`, `replay.py`, `runtime.py`, and `cli.py`; new `rules.py`, `goals.py`, `channels.py`, and `audit.py`. The artifact validator, portable stress fixture builder, Windows/Linux CI matrix, setup documentation and generated sample evidence were updated. The original input pack and simulator were byte-for-byte unchanged (18 files verified).

## Executed tests and replay

85 automated tests passed in a fresh Python 3.12 virtual environment after editable installation, without PYTHONPATH. This includes the existing regressions and new tests in `test_extensions.py`, `test_rule_outcome_measurement.py`, and `test_replay_integration.py`. The CLI integration test uses a clearly isolated tiny synthetic fixture and simulator test double; reported impact uses only the supplied pack and unchanged supplied simulator. Windows CI is configured; no local Windows execution is claimed.

Full replay: 2,679 decisions; 542 actions (503 homepages, 17 push, 9 WhatsApp, 13 in-app); 921 judgments (266 succeeded, 653 failed, 2 inconclusive); 1,504 enrolled viewers. All five named model faults were reproduced. Independent validation passed with zero simulator policy violations. Aggregate p95 was 10.95 ms, maximum 2003.94 ms; zero decisions above 60 seconds or five minutes. Dependency waits are explicitly simulated; these are single-worker replay timings.

Stress replay: 3,005 decisions and 608 actions; independent validation passed with zero policy violations and zero decisions above 60 seconds. Aggregate p95 16.30 ms, maximum 2004.35 ms. Synthetic duplicated sessions test operational handling, not causal impact.

Reopened SQLite contains 1,746 viewer records, 921 persisted judgments and 15,290 committed entity versions; no pending actions remain. The read-only point-in-time command was exercised against the final run. Test output, audit example and stress reports are in `validation/`.

## Honest impact and remaining exclusions

Completion ITT: 250/758 treatment versus 234/746 holdout. Difference +1.61 percentage points; 95% CI −3.10 to +6.32. Benefit is inconclusive. No favorable result was substituted.

G06, G10 and G11 remain safely excluded because the supplied data does not define “recently,” operationalize “meaningful engagement,” or supply TV telemetry. No live LLM, frontend, real notification provider or cloud deployment was added; the latest request explicitly permits the local mock workflow. Other boundaries, including legacy historical audit coverage and single-writer operation, are documented in KNOWN_LIMITATIONS.md.

## Reproduce from the project root

```powershell
py -m pip install -e .
py -m unittest discover -s tests -v
py -m one_agent replay --pack data/one_user_one_agent_pack --out runs/final-check
py scripts/validate_run.py --pack data/one_user_one_agent_pack --run runs/final-check
py -m one_agent audit --db runs/final-check/state.sqlite3 --viewer U-01991 --at "2026-10-01T12:00:00Z"
```

Extract the original pack as explained in README first, and use a fresh output directory. Use `python` instead of `py` on Linux/macOS.
