# All twelve goals

G01–G12 now have implemented eligibility and outcome evaluation. There is no G00 in the supplied goal catalogue.

- G06: current lapsed subscription plus a documented lapse within 30 days; success requires a non-trial paid purchase within the goal window. The user approved the 30-day definition.
- G10: complete two different catalogue episodes on two different IST calendar days within seven days. Duplicate events, repeat completion of one episode, same-day-only completion, unknown episode IDs and other viewers do not satisfy it. The user approved this definition.
- G11: active subscriber with explicitly declared TV instrumentation; success requires an actual `tv_app_open` within the goal window. Missing telemetry is not counted as success. The supplied pack has no TV events, so default replay does not select G11. Automated fixtures exercise it separately.
- G01–G05, G07–G09 and G12 retain their existing implementations.

Configuration and versioned definitions persist in SQLite and are recorded in decisions and the run manifest. CLI overrides are explicit; they do not change the supplied pack.

## Run on Windows

From PowerShell:

```powershell
cd C:\Users\User\One-User-One-Agent
git pull
py -m pip install -e .
py -m one_agent replay --pack data/one_user_one_agent_pack --out runs/all-goals-demo
py scripts/validate_run.py --pack data/one_user_one_agent_pack --run runs/all-goals-demo
```

Use a new output directory for each replay. Optional `--lapse-days` and `--engagement` flags record alternative definitions. Only when a real complete TV observation feed is available, add `--tv-telemetry-available --extra-events path/to/events.jsonl`. Additional events must follow the pack event schema; the flag alone does not supply or fabricate observations. Notification delivery remains local/mock.

## Validation

100 automated tests pass, including boundary conditions, deduplication, absent telemetry, G06/G11 engine transitions, and configuration persistence across restarts. Clean virtualenv tests also pass.

The updated sample replay has 2,679 decisions, 928 actions and 1,506 judgments. Independent validation reproduces all five injected model faults and reports zero policy violations. It independently checks the distinct episodes and days for successful G10 judgments.

Primary ITT: 254/758 treatment versus 234/746 holdout; +2.14 percentage points, 95% CI −2.59 to +6.86. This does not establish a statistically conclusive improvement. Replay latency is not a production capacity benchmark.

Judgments by goal in the supplied replay (zero selection is not missing implementation):

- G01: 110
- G02: 71
- G03: 1
- G04: 0
- G05: 15
- G06: 113
- G07: 54
- G08: 447
- G09: 115
- G10: 489
- G11: 0
- G12: 91

The additional stress replay passes independent validation: 3,005 decisions, 1,056 actions, 1,506 judgments, all five faults reproduced and zero policy violations. This uses the existing increased-trigger fixture, not a production capacity test.
