# Replay result

2679 unique decisions; 928 published actions ({'in_app_message': 259, 'home_page': 610, 'push': 51, 'whatsapp': 8}); 1506 goal judgments; 1504 enrolled viewers.

Goal attainment: {'failed': 1187, 'succeeded': 317, 'inconclusive': 2}.

Simulator policy problems: 0.

No-action reasons: {'randomized_holdout': 1089, 'insufficient_unique_available_titles_for_hero_and_row': 154, 'stale_or_invalid_clock_trigger': 424, 'verification_rejected:session_idempotency': 74, 'verification_rejected:series_content': 5, 'store_timeout: fail_closed_no_personalization': 5}.

End-to-end replay latency (processing + explicitly simulated dependency wait + queue): p95 15.08 ms, max 2006.71 ms; 0 decisions over 60 seconds.

This accelerated single-worker replay includes supplied controls and mock provider faults. It is not a production capacity benchmark. See latency_report.json for measured CPU/wall service time separately from synthetic dependency waits.

Primary ITT: agent 254/758 (33.51%); holdout 234/746 (31.37%). Risk difference +2.14 percentage points (95% CI -2.59 to +6.86).

The simulator is batch-based: its replay-day outcomes become available only after actions have been generated. Live pack events drive online replanning; simulated events drive subsequent judgment and learning without retroactively rewriting decisions.
