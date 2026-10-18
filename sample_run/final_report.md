# Replay result

2679 unique decisions; 541 published home pages; 910 goal judgments; 1504 enrolled viewers.

Goal attainment: {'failed': 632, 'succeeded': 276, 'inconclusive': 2}.

Simulator policy problems: 0.

No-action reasons: {'no_supported_measurable_eligible_goal': 889, 'insufficient_unique_available_titles_for_hero_and_row': 100, 'randomized_holdout': 665, 'stale_or_invalid_clock_trigger': 424, 'verification_rejected:session_idempotency': 55, 'store_timeout: fail_closed_no_personalization': 5}.

End-to-end replay latency (processing + explicitly simulated dependency wait + queue): p95 6.58 ms, max 2002.70 ms; 0 decisions over 60 seconds.

This accelerated single-worker replay includes supplied controls and mock provider faults. It is not a production capacity benchmark. See latency_report.json for measured CPU/wall service time separately from synthetic dependency waits.

Primary ITT: agent 251/758 (33.11%); holdout 234/746 (31.37%). Risk difference +1.75 percentage points (95% CI -2.98 to +6.45).

The simulator is batch-based: its replay-day outcomes become available only after actions have been generated. Live pack events drive online replanning; simulated events drive subsequent judgment and learning without retroactively rewriting decisions.
