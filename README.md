# One User, One Agent

A local lifecycle agent for the supplied OTT assignment. Each viewer owns one durable goal and plan. The agent resolves evidence, independently verifies an action or abstains, and judges actual outcomes against randomized holdout. It supports homepage, push, approved WhatsApp template, and in-app actions in the supplied simulator. No messages are sent to real people.

Python 3.10+ with SQLite is required. The runtime uses only the standard library. The default mock provider reproduces supplied model faults and requires no personal API key. No frontend, hosted model, or deployment is required.

## Run on Windows

From PowerShell, enter the project root (not a `runs` subfolder):

```powershell
cd C:\Users\User\One-User-One-Agent
py -m pip install -e .
py -m zipfile -e "$env:USERPROFILE\Downloads\one_user_one_agent_data_pack.zip" data
py -m one_agent replay --pack data/one_user_one_agent_pack --out runs/demo
py scripts/validate_run.py --pack data/one_user_one_agent_pack --run runs/demo
py -m unittest discover -s tests -v
```

Adjust the ZIP path to its actual location. Extract the pack once. Every replay needs a new or empty output directory. On Linux/macOS, use `python` instead of `py`; alternatively run with `PYTHONPATH=src` without installation.

```powershell
notepad runs\demo\final_report.md
```

The replay executes the supplied simulator unchanged. `--seed` changes simulator randomness. `--max-model-calls 8 --max-tool-calls 20` configure ceilings; zero model calls uses deterministic planning. Insufficient data-call budgets cause explicit abstention, including when there is no remaining budget for dispatch verification.

## Inspect a viewer at a point in time

```powershell
py -m one_agent audit --db runs/demo/state.sqlite3 --viewer U-01991
py -m one_agent audit --db runs/demo/state.sqlite3 --viewer U-01991 --at "2026-10-01T12:00:00Z"
```

The audit command is read-only. `--at` reconstructs committed decisions, plans, actions, viewer projections and outcomes known by that time, plus received evidence. It excludes later deliveries and batch simulator evidence until the final observation watermark. Facts retain their last resolution timestamps; they are not fabricated retroactively. Older databases without version history support current-state inspection but require a new replay for `--at`.

## Actions and verification

Homepages use a hero and three distinct other content slugs in the viewer's current language and dialect. G07 targets its reconciled unfinished series. The policy considers consented push/WhatsApp for conversion and series-return goals, falls back to an in-app message where appropriate, or uses a homepage. Missing content, uncertain eligibility, stale triggers, holdout assignment, exhausted budgets and rejected verification produce recorded abstentions.

Every message is checked for consent where required, 22:00–08:00 IST quiet hours, the shared push/WhatsApp daily cap, the separate in-app cap, locale, availability, approved variables and honest copy. Only the supplied recommendation WhatsApp template is used; no invented pricing or deadlines. A published request is not a delivery or exposure. The simulator only queues in-app messages, so it cannot demonstrate an in-app exposure effect.

For isolated channel testing, `--channel home_page`, `--channel push`, `--channel whatsapp`, or `--channel in_app_message` requests that channel. All verification still applies; the flag cannot bypass holdout, consent, caps, or content checks.

## Goal coverage

| Goal | Handling |
|---|---|
| G01 | Recent signup, covered lifetime, no prior play; a subsequent play within 24h. |
| G02 | Covered lifetime, no prior completion; a completion within 72h. A playback start is not completion. |
| G03 | Recent signup; later IST-day return within 48h; affected plans adopt the supplied correction to `play_started`. |
| G04 | Supported active trial; non-trial purchase within 7d. |
| G05 | Covered never-paid history; first non-trial purchase within 7d. |
| G06 | Safely excluded: “recently lapsed” has no supplied threshold. |
| G07 | Covered lifetime; catalogue episode IDs reconcile starts/completions for an unfinished series inactive at least 3d; success is a play of the same content slug within 72h. |
| G08 | Active subscriber; a play of content released within 30d before the original goal start, within 7d. |
| G09 | Active subscriber, at most one play day in covered prior 14d; success requires three distinct IST play days within 14d. |
| G10 | Safely excluded: “meaningful engagement” has no measurable success definition. |
| G11 | Safely excluded: `tv_app_open` is absent from supplied telemetry. |
| G12 | Covered 14d without an app open for an established viewer; subsequent play within 7d. |

Absence claims explicitly assume the supplied observation window is complete. Late contradictory historical evidence can invalidate earlier first-time goals. Unsupported goals never silently become eligible. Unimplemented rule-correction semantics fail closed.

## Generated evidence

`sample_run/` contains an executed replay: `decisions.jsonl`, `actions.jsonl`, `outcomes.jsonl`, `audit.jsonl`, `cohorts.jsonl`, `viewer_state/`, simulator events, latency, impact and final reports. A fresh run also generates `state.sqlite3`, including transactional version history and persisted outcome judgments. Raw input pack and SQLite files are excluded from Git. Supply the original pack when sharing the repository.

The independent validator rechecks actions against raw content, triggers and assignments, recomputes viewer-level ITT rates and confidence intervals, and checks viewer/plan/outcome consistency. The randomized primary endpoint is completion within 72h of first fresh delivery, regardless of whether an action was served. Goal attainment and exposure association are separate from population-level causal evidence. An interval spanning zero does not establish improvement.

## Reproduce the traffic check

```powershell
py scripts/make_stress_pack.py --pack data/one_user_one_agent_pack --out runs/stress_pack
py -m one_agent replay --pack runs/stress_pack --out runs/stress
py scripts/validate_run.py --pack runs/stress_pack --run runs/stress
```

This triples the busiest ten-minute evening window using isolated synthetic session copies. It is a single-worker operational replay, not a production-concurrency benchmark or valid impact experiment.

See `ARCHITECTURE.md`, `AI_COLLABORATION.md`, `KNOWN_LIMITATIONS.md`, and `COMPLETION.md` for the implementation and executed validation.
