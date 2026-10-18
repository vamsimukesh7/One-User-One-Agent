# Replay result

3005 unique decisions; 1056 published actions ({'in_app_message': 261, 'home_page': 736, 'push': 51, 'whatsapp': 8}); 1506 goal judgments; 1504 enrolled viewers.

Goal attainment: {'failed': 1175, 'succeeded': 329, 'inconclusive': 2}.

Simulator policy problems: 0.

No-action reasons: {'randomized_holdout': 1265, 'insufficient_unique_available_titles_for_hero_and_row': 162, 'stale_or_invalid_clock_trigger': 430, 'verification_rejected:session_idempotency': 80, 'verification_rejected:series_content': 7, 'store_timeout: fail_closed_no_personalization': 5}.

End-to-end replay latency (processing + explicitly simulated dependency wait + queue): p95 20.33 ms, max 2006.42 ms; 0 decisions over 60 seconds.

This accelerated single-worker replay includes supplied controls and mock provider faults. It is not a production capacity benchmark. See latency_report.json for measured CPU/wall service time separately from synthetic dependency waits.

Primary ITT: agent 266/758 (35.09%); holdout 249/746 (33.38%). Risk difference +1.71 percentage points (95% CI -3.08 to +6.49).

The simulator is batch-based: its replay-day outcomes become available only after actions have been generated. Live pack events drive online replanning; simulated events drive subsequent judgment and learning without retroactively rewriting decisions.
