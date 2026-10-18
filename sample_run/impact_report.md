# Impact report

Replay primary — completion within 72h: agent 250/758 (32.98%); holdout 234/746 (31.37%). Risk difference +1.61 percentage points (95% CI -3.10 to +6.32).

Replay secondary — paid purchase within 7d: agent 21/758 (2.77%); holdout 29/746 (3.89%). Risk difference -1.12 percentage points (95% CI -3.01 to +0.73).

Past pilot — randomized intention to treat: agent 349/1912 (18.25%); holdout 387/1979 (19.56%). Risk difference -1.30 percentage points (95% CI -3.76 to +1.16).

Past pilot — served versus everyone else (NOT causal): agent 87/217 (40.09%); holdout 649/3674 (17.66%). Risk difference +22.43 percentage points (95% CI +16.01 to +29.17).

The internal 2.3× headline conditions on serving, a post-randomization selection. Viewers who return, have usable evidence, and can receive an action differ from unserved viewers. Retain every randomized viewer in the selected cohort in their assigned arm, including abstentions and unexposed viewers.

Replay enrollment is the first fresh trigger, before the agent acts. The primary endpoint is common to both arms and never depends on which adaptive goal or content was chosen. Stale-only viewers are excluded by the same pre-action rule in both arms. The secondary paid endpoint is exploratory and includes existing subscribers; it is not a first-purchase estimate.

Intervals use user-level unpaired Newcombe/Wilson risk differences. No significance claim is made when the interval spans zero. This is a single synthetic seed, not evidence of production impact. Individual exposure associations are diagnostic, never proof that the agent caused a purchase.

Goal attainment appears separately in outcomes.jsonl. Success requires an actual signal inside its window. Same-session home_page_shown with a known action ID establishes an exposure association. Unlinked purchases (including scripted organic purchases) get no individual intervention credit.

Observation watermark: 2026-10-15T18:30:00.000000Z. Mature negatives assume the supplied simulator feed is complete. Unmatured or superseded goals are inconclusive. Censoring counts appear in impact_report.json.
