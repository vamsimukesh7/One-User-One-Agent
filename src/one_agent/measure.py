import json
import math
from collections import Counter, defaultdict
from datetime import timedelta
from .common import csv_rows, day, event_time, iso, ts, write_json, write_jsonl
from .outcomes import judge


def wilson(successes, total):
    if not total:
        return (0.0,1.0)
    z, p = 1.95996398454, successes/total
    denom = 1+z*z/total
    center = (p+z*z/(2*total))/denom
    margin = z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denom
    return center-margin,center+margin


def compare(treatment, holdout):
    """User-level ITT risk difference, unpaired Newcombe/Wilson 95% interval."""
    nt,nc = len(treatment),len(holdout)
    st,sc = sum(treatment),sum(holdout)
    if not nt or not nc:
        return {'estimable':False,'treatment_n':nt,'holdout_n':nc}
    pt,pc = st/nt,sc/nc
    lt,ut = wilson(st,nt); lc,uc = wilson(sc,nc)
    diff = pt-pc
    return {'estimable':True,'treatment_n':nt,'treatment_successes':st,'treatment_rate':pt,
            'holdout_n':nc,'holdout_successes':sc,'holdout_rate':pc,'risk_difference':diff,
            'ci95':[diff-math.sqrt((pt-lt)**2+(uc-pc)**2),diff+math.sqrt((ut-pt)**2+(pc-lc)**2)]}


def format_effect(label, result):
    if not result['estimable']:
        return f'{label}: not estimable (one arm empty).'
    return (f"{label}: agent {result['treatment_successes']}/{result['treatment_n']} "
            f"({result['treatment_rate']:.2%}); holdout {result['holdout_successes']}/{result['holdout_n']} "
            f"({result['holdout_rate']:.2%}). Risk difference {result['risk_difference']*100:+.2f} percentage points "
            f"(95% CI {result['ci95'][0]*100:+.2f} to {result['ci95'][1]*100:+.2f}).")


def reports(pack, store, out, observation_end, simulator_report, latency):
    outcomes = []
    by_user = defaultdict(list)
    for row in store.db.execute('SELECT user_id,body FROM events ORDER BY received_at,event_id'):
        by_user[row[0]].append(json.loads(row[1]))
    for plan in store.records('plans'):
        result = judge(plan,by_user[plan['user_id']],pack,observation_end,plan['actions'])
        outcomes.append(result)
        plan['final_outcome'] = result
        if plan['status']=='open' and result['status']!='inconclusive':
            plan['status'] = result['status']
        store.save_plan(plan)
    write_jsonl(out/'outcomes.jsonl',outcomes)
    endpoints = {}
    for name, event_name, hours in [('primary_completion_72h','episode_completed',72),('secondary_paid_7d','purchase_completed',168)]:
        arms = {'agent':[],'holdout':[]}; censored=Counter()
        for cohort in store.records('cohorts'):
            arm = cohort['arm']
            if arm not in arms:
                continue
            start = ts(cohort['entry_at']); end = start+timedelta(hours=hours)
            if end > observation_end:
                censored[arm] += 1; continue
            success = any(e['event']==event_name and event_time(e)[1] and start < event_time(e)[0] <= end
                          and ts(e['received_at']) <= observation_end
                          and (event_name!='purchase_completed' or e.get('properties',{}).get('is_trial') is False)
                          for e in by_user[cohort['user_id']])
            arms[arm].append(success)
        endpoints[name] = dict(compare(arms['agent'],arms['holdout']),censored=dict(censored))
    pilot_assignment = {r['user_id']:r for r in csv_rows(pack.root/'pilot/pilot_assignment.csv')}
    pilot_outcomes = csv_rows(pack.root/'pilot/pilot_outcomes.csv')
    if len({r['user_id'] for r in pilot_outcomes}) != len(pilot_outcomes):
        raise ValueError('Duplicate pilot outcome users')
    if set(pilot_assignment) != {r['user_id'] for r in pilot_outcomes}:
        raise ValueError('Pilot outcomes do not cover every randomized assignment')
    pilot_arms={'treatment':[],'holdout':[]}; served=[]; others=[]
    for row in pilot_outcomes:
        assignment = pilot_assignment[row['user_id']]
        if row['arm'] != assignment['arm']:
            raise ValueError('Pilot assignment/outcome arm disagreement')
        success = row['succeeded']=='true'
        pilot_arms[assignment['arm']].append(success)
        (served if assignment['served']=='true' else others).append(success)
    pilot = compare(pilot_arms['treatment'],pilot_arms['holdout'])
    descriptive = compare(served,others)
    impact = {'estimand':'ITT among viewers at first fresh trigger, assigned arm regardless of action or exposure',
              'primary_endpoint':'any episode_completed in 72 hours after enrollment; specified before outcome simulation',
              'replay':endpoints,'pilot_itt':pilot,'pilot_served_vs_other_noncausal':descriptive,
              'interval':'Newcombe unpaired Wilson 95% risk-difference interval; viewer is randomization unit',
              'observation_end':iso(observation_end),'causal_scope':'synthetic randomized simulator only; no production uplift claim'}
    write_json(out/'impact_report.json',impact)
    text = ['# Impact report','',format_effect('Replay primary — completion within 72h',endpoints['primary_completion_72h']),
            '',format_effect('Replay secondary — paid purchase within 7d',endpoints['secondary_paid_7d']),
            '',format_effect('Past pilot — randomized intention to treat',pilot),
            '',format_effect('Past pilot — served versus everyone else (NOT causal)',descriptive),'',
            'The internal 2.3× headline conditions on serving, a post-randomization selection. Viewers who return, '
            'have usable evidence, and can receive an action differ from unserved viewers. Retain every randomized '
            'viewer in the selected cohort in their assigned arm, including abstentions and unexposed viewers.', '',
            'Replay enrollment is the first fresh trigger, before the agent acts. The primary endpoint is common '
            'to both arms and never depends on which adaptive goal or content was chosen. Stale-only viewers are '
            'excluded by the same pre-action rule in both arms. The secondary paid endpoint is exploratory and '
            'includes existing subscribers; it is not a first-purchase estimate.', '',
            'Intervals use user-level unpaired Newcombe/Wilson risk differences. No significance claim is made '
            'when the interval spans zero. This is a single synthetic seed, not evidence of production impact. '
            'Individual exposure associations are diagnostic, never proof that the agent caused a purchase.', '',
            'Goal attainment appears separately in outcomes.jsonl. Success requires an actual signal inside its '
            'window. Same-session home_page_shown with a known action ID establishes an exposure association. '
            'Unlinked purchases (including scripted organic purchases) get no individual intervention credit.', '',
            f'Observation watermark: {iso(observation_end)}. Mature negatives assume the supplied simulator feed is complete. '
            'Unmatured or superseded goals are inconclusive. Censoring counts appear in impact_report.json.']
    (out/'impact_report.md').write_text('\n'.join(text)+'\n')
    judgments = defaultdict(list)
    for result in outcomes:
        judgments[result['user_id']].append(result)
    state_dir = out/'viewer_state'; state_dir.mkdir(exist_ok=True)
    plans_by_user = defaultdict(list)
    for plan in store.records('plans'):
        plans_by_user[plan['user_id']].append(plan)
    for viewer in store.records('viewers'):
        plans = plans_by_user[viewer['user_id']]
        viewer['plans'] = plans
        viewer['active_plan_id'] = next((p['plan_id'] for p in plans if p['status']=='open'),None)
        viewer['learning'] += [{'goal_id':r['goal_id'],'result':r['status'],'intervention':r['intervention_judgment'],
                                'lesson':'Do not equate publishing with seeing or observing success with causation.'} for r in judgments[viewer['user_id']]]
        store.db.execute('INSERT OR REPLACE INTO viewers VALUES (?,?)',(viewer['user_id'],json.dumps(viewer)))
        write_json(state_dir/(viewer['user_id']+'.json'),viewer)
    decisions=store.records('decisions'); actions=store.records('actions')
    reasons=Counter(d['no_action_reason'] for d in decisions if d['action']=='NO_ACTION')
    write_json(out/'latency_report.json',latency)
    final = ['# Replay result','',f'{len(decisions)} unique decisions; {len(actions)} published home pages; '
             f'{len(outcomes)} goal judgments; {len(store.records("cohorts"))} enrolled viewers.', '',
             f'Goal attainment: {dict(Counter(r["status"] for r in outcomes))}.', '',
             f'Simulator policy problems: {simulator_report["policy_problem_count"]}.', '',
             f'No-action reasons: {dict(reasons)}.', '',
             f'End-to-end replay latency (processing + explicitly simulated dependency wait + queue): '
             f'p95 {latency["p95_ms"]:.2f} ms, max {latency["max_ms"]:.2f} ms; '
             f'{latency["over_60s"]} decisions over 60 seconds.', '',
             'This accelerated single-worker replay includes supplied controls and mock provider faults. '
             'It is not a production capacity benchmark. See latency_report.json for measured CPU/wall service '
             'time separately from synthetic dependency waits.', '',format_effect('Primary ITT',endpoints['primary_completion_72h']), '',
             'The simulator is batch-based: its replay-day outcomes become available only after actions have '
             'been generated. Live pack events drive online replanning; simulated events drive subsequent '
             'judgment and learning without retroactively rewriting decisions.']
    (out/'final_report.md').write_text('\n'.join(final)+'\n')
    store.db.commit()
    return impact
