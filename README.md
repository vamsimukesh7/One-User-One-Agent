# One User, One Agent

A local, runnable lifecycle agent for the supplied OTT assignment. Each viewer has one durable goal and plan. Decisions cite evidence, independently verified home pages are published to an outbox, and observed outcomes are compared with randomized holdout. No API key, hosted model, external service, or third-party runtime dependency is required.

## Run the submission

Requires Python 3.10+ with SQLite (validated with Python 3.12). From the repository root, extract the supplied ZIP under `data/` if it is not already present:

```bash
python -m zipfile -e /path/to/one_user_one_agent_data_pack.zip data
```

Run the complete decide → act → simulate → judge → measure workflow with one command:

```bash
PYTHONPATH=src python -m one_agent replay --pack data/one_user_one_agent_pack --out runs/demo
```

This executes the supplied simulator unchanged. Output directories must be empty or new; existing runs are preserved. Change `--seed` for an independent simulator seed. `--max-model-calls 8 --max-tool-calls 20` are configurable ceilings. Setting model calls to zero uses the deterministic policy directly; the default mock reproduces every provided model fault.

Run the automated checks:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python scripts/validate_run.py --pack data/one_user_one_agent_pack --run sample_run
```

The second command validates the full included replay against the original inputs and simulator events. After a new replay, substitute its output directory. The data pack, SQLite databases, and large per-viewer/simulator exports remain local and are ignored by Git. Give evaluators the supplied pack alongside this repository; the run command regenerates all artifacts. No data is sent to external APIs.

Inspect a viewer, including all decisions, plans, actions and final learning:

```bash
PYTHONPATH=src python -m one_agent audit --db sample_run/state.sqlite3 --viewer U-01991
```

## Read the result

- `sample_run/final_report.md`: replay counts, failures, latency and interpretation.
- `sample_run/impact_report.md`: randomized replay and pilot effects, intervals and selection-bias explanation.
- `sample_run/decisions.jsonl`: one auditable decision per unique trigger, including every abstention.
- `sample_run/actions.jsonl`: simulator-compatible action outbox, home pages only.
- `sample_run/outcomes.jsonl`: goal attainment and separately labeled exposure associations.
- `sample_run/audit.jsonl`: duplicate deliveries, targeted rule updates, goal closure and operational changes.
- `sample_run/cohorts.jsonl`: arm-independent enrollment at first fresh delivery.
- `sample_run/latency_report.json`: actual service timing separated from explicitly modeled dependency waits.
- `sample_run/viewer_state/` and `state.sqlite3`: complete durable state, generated on every run.
- `sample_run/sim/`: untouched simulator output, generated on every run.

## Decisions and deliberate scope

App settings on a fresh trigger determine the displayed dialect. Profile and campaign disagreements stay visible. Billing events can override stale subscription snapshots. Consent defaults to false and opt-outs override snapshots, although this implementation sends no messages. Device clock offset is handled by anchoring `sent_at - occurred_at` to server receipt; events buffered more than ten minutes do not trigger personalization.

Goal selection is deterministic and inspectable. The replay model is an untrusted advisor that can choose only an independently supported candidate; its assertions cannot create viewer facts. A heuristic confidence value expresses evidence adequacy, not a calibrated probability of success.

| Goal | Handling |
|---|---|
| G01 first play | Requires recent signup and observed history covering signup, with no prior play. |
| G02 first completion | Requires observed history covering signup and no prior completion. |
| G03 next-day return | Requires recent signup; open goals adopt the supplied correction from app-open to play on a later IST day. |
| G04 trial to paid | Requires supported trial status; a non-trial purchase closes the goal. |
| G05 first paid | Requires never-paid status, history covering signup, and no contrary paid evidence. |
| G06 reactivation | Disabled: “recently” has no agreed threshold. |
| G07 resume series | Deferred: episode reconciliation is intentionally outside this implementation. |
| G08 new release | Active subscribers; select and judge titles released in the prior 30 days. |
| G09 weekly habit | Active subscribers with at most one play day in covered prior 14 days; success needs three distinct IST play days. |
| G10 meaningful engagement | Disabled: success is not measurable as specified. |
| G11 TV activation | Disabled: required success event is not instrumented. |
| G12 return after absence | Requires 14 days of observed history without a prior app open and an older supported signup. |

For older viewers, absence in a 30-day export cannot prove “never.” Those first-time goals are refused. Holdout viewers can own shadow goals for audit but receive no action. Missing evidence, stale triggers, exhausted budgets, duplicate sessions and failed verification all have explicit no-action reasons.

See `ARCHITECTURE.md` for trust boundaries and timing, `KNOWN_LIMITATIONS.md` for remaining scope, and `AI_COLLABORATION.md` for how the implementation was checked.
