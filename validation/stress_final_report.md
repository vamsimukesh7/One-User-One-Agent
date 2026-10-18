# Replay result

3005 unique decisions; 609 published home pages; 910 goal judgments; 1504 enrolled viewers.

Goal attainment: {'failed': 623, 'succeeded': 285, 'inconclusive': 2}.

Simulator policy problems: 0.

No-action reasons: {'no_supported_measurable_eligible_goal': 1057, 'insufficient_unique_available_titles_for_hero_and_row': 106, 'randomized_holdout': 739, 'stale_or_invalid_clock_trigger': 430, 'verification_rejected:session_idempotency': 59, 'store_timeout: fail_closed_no_personalization': 5}.

End-to-end replay latency (processing + explicitly simulated dependency wait + queue): p95 9.69 ms, max 2004.13 ms; 0 decisions over 60 seconds.

This accelerated single-worker replay includes supplied controls and mock provider faults. It is not a production capacity benchmark. See latency_report.json for measured CPU/wall service time separately from synthetic dependency waits.

Primary ITT: agent 262/758 (34.56%); holdout 249/746 (33.38%). Risk difference +1.19 percentage points (95% CI -3.60 to +5.96).

The simulator is batch-based: its replay-day outcomes become available only after actions have been generated. Live pack events drive online replanning; simulated events drive subsequent judgment and learning without retroactively rewriting decisions.
