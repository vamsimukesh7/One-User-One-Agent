# Replay result

2679 unique decisions; 542 published actions ({'home_page': 503, 'in_app_message': 13, 'push': 17, 'whatsapp': 9}); 921 goal judgments; 1504 enrolled viewers.

Goal attainment: {'failed': 653, 'succeeded': 266, 'inconclusive': 2}.

Simulator policy problems: 0.

No-action reasons: {'no_supported_measurable_eligible_goal': 875, 'insufficient_unique_available_titles_for_hero_and_row': 101, 'randomized_holdout': 672, 'stale_or_invalid_clock_trigger': 424, 'verification_rejected:session_idempotency': 55, 'verification_rejected:series_content': 5, 'store_timeout: fail_closed_no_personalization': 5}.

End-to-end replay latency (processing + explicitly simulated dependency wait + queue): p95 10.95 ms, max 2003.94 ms; 0 decisions over 60 seconds.

This accelerated single-worker replay includes supplied controls and mock provider faults. It is not a production capacity benchmark. See latency_report.json for measured CPU/wall service time separately from synthetic dependency waits.

Primary ITT: agent 250/758 (32.98%); holdout 234/746 (31.37%). Risk difference +1.61 percentage points (95% CI -3.10 to +6.32).

The simulator is batch-based: its replay-day outcomes become available only after actions have been generated. Live pack events drive online replanning; simulated events drive subsequent judgment and learning without retroactively rewriting decisions.
