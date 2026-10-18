"""Independent end-to-end assertions against raw pack data and exported artifacts."""
import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
import math
from pathlib import Path


def load(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def ts(value):
    return datetime.fromisoformat(value.replace('Z','+00:00'))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--pack',default='data/one_user_one_agent_pack')
    ap.add_argument('--run',default='sample_run')
    args=ap.parse_args();pack=Path(args.pack);run=Path(args.run)
    decisions=load(run/'decisions.jsonl');actions=load(run/'actions.jsonl');outcomes=load(run/'outcomes.jsonl')
    triggers={}
    for e in sorted(load(pack/'replay/triggers.jsonl'),key=lambda e:e['deliver_at']):
        triggers.setdefault(e['event_id'],e)
    arms={r['user_id']:r['arm'] for r in csv.DictReader((pack/'replay/arms.csv').open())}
    catalogue={r['title_id']:r for r in csv.DictReader((pack/'data/catalogue.csv').open())}
    withdrawals={r['title_id']:ts(r['at']) for r in load(pack/'replay/control_events.jsonl') if r['type']=='title_withdrawn'}
    assert len(decisions)==len(triggers), 'Missing or duplicate decisions'
    assert len({d['decision_id'] for d in decisions})==len(decisions)
    assert len({a['action_id'] for a in actions})==len(actions)
    decision_index={d['decision_id']:d for d in decisions}
    policy=json.loads((pack/'config/interventions.json').read_text())
    seen_sessions=set();daily=Counter();ist=timezone(timedelta(hours=5,minutes=30))
    for action in actions:
        d=decision_index[action['decision_id']];trigger=triggers[action['trigger_event_id']]
        assert arms[action['user_id']]=='agent', 'Holdout contamination'
        assert action['type'] in {'home_page','push','whatsapp','in_app_message'}
        assert d['action']==action['type'].upper() and not d['checks']['failed']
        assert d['status']=='PUBLISHED'
        published=ts(action['published_at']);delivered=ts(trigger['deliver_at'])
        assert 0 <= (published-delivered).total_seconds() <= 60, 'Publication missed SLA'
        assert action['dialect']==trigger['app_dialect']
        session=(action['user_id'],trigger['session_id'])
        assert session not in seen_sessions, 'Duplicate session action';seen_sessions.add(session)
        if action['type']=='home_page':
            assert 1<=len(action['rows'])<=6
            ids=[action['hero_title_id']]
            for row in action['rows']:
                assert 3<=len(row['title_ids'])<=10
                ids.extend(row['title_ids'])
        else:
            ids=[action['title_id']];channel=action['type']
            assert ts(action['send_at'])==published
            date=published.astimezone(ist).date()
            group='external' if channel in {'push','whatsapp'} else 'in_app'
            daily[action['user_id'],date,group]+=1
            assert daily[action['user_id'],date,group]<=1,'Message cap exceeded'
            if channel in {'push','whatsapp'}:
                assert 8<=published.astimezone(ist).hour<22,'Quiet-hour violation'
                field=policy[channel]['consent_field']
                assert d['facts'][field]['value'] is True,'Consent missing'
            if channel=='whatsapp':
                template=next(t for t in policy[channel]['approved_templates'] if t['template_id']==action['template_id'])
                title=catalogue[action['title_id']]
                assert template['language']==title['language']
                assert set(action['variables'])==set(template['variables'])
                assert action['variables']=={'title_name':title['title'],'dialect_name':title['dialect']}
            else:
                assert len(action['text']['title'])<=policy['push']['copy']['title_max_chars']
                assert len(action['text']['body'])<=policy['push']['copy']['body_max_chars']
                assert action['deeplink']=='ott://title/'+action['title_id']
            rendered=json.dumps(action.get('text',action.get('variables',{}))).lower()
            assert not any(x in rendered for x in policy['push']['copy']['forbidden_phrases'])
        slugs=[]
        for tid in ids:
            title=catalogue[tid];slugs.append(title['content_slug'])
            assert title['dialect']==action['dialect']
            assert title['language']==trigger['app_language']
            assert ts(title['available_from'])<=published
            assert not title['available_until'] or published<ts(title['available_until'])
            assert tid not in withdrawals or published<withdrawals[tid]
        assert len(slugs)==len(set(slugs))
    event_index={}
    paths=[pack/'data/events.jsonl',pack/'replay/live_events.jsonl',pack/'replay/triggers.jsonl',run/'sim/outcome_events.jsonl']
    manifest=json.loads((run/'run_manifest.json').read_text())
    if manifest.get('extra_events'):paths.append(Path(manifest['extra_events']))
    for path in paths:
        for e in load(path):
            prior=event_index.get(e['event_id'])
            if prior is None or ts(e['received_at']) < ts(prior['received_at']):
                event_index[e['event_id']]=e
    for d in decisions:
        assert d['calls']['model_calls']<=d['calls']['max_model_calls']
        assert d['calls']['tool_calls']<=d['calls']['max_tool_calls']
        if d['action']=='NO_ACTION':
            assert d['no_action_reason']
        for ref in d['evidence']:
            if ref['ref'].startswith('event:'):
                e=event_index[ref['ref'][6:]]
                assert ts(e['received_at'])<=ts(d['published_at']), 'Evidence from the future'
    for outcome in outcomes:
        if outcome['status']=='succeeded':
            assert outcome['signal_refs'], 'Success without evidence'
        for ref in outcome['signal_refs']:
            e=event_index[ref[6:]]
            assert e['user_id']==outcome['user_id']
            assert e['event']==outcome['success_signal']['event']
            effective=ts(e['received_at'])-(ts(e['sent_at'])-ts(e['occurred_at']))
            assert ts(outcome['started_at'])<effective<=ts(outcome['deadline'])
        if outcome['goal_id']=='G10' and outcome['status']=='succeeded':
            signals=[event_index[ref[6:]] for ref in outcome['signal_refs']]
            rule=outcome['success_signal']
            ist=timezone(timedelta(hours=5,minutes=30))
            days={(ts(e['received_at'])-(ts(e['sent_at'])-ts(e['occurred_at']))).astimezone(ist).date() for e in signals}
            assert len(days)>=rule['min_distinct_days']
            assert len({e['properties']['episode_id'] for e in signals})>=rule.get('min_distinct_episodes',0)
        if outcome['associated_signal_refs']:
            assert outcome['exposure_refs']
    by_trigger={d['trigger']['event_id']:d for d in decisions}
    faults=load(pack/'replay/model_faults.jsonl')
    for fault in faults:
        d=by_trigger[fault['trigger_event_id']]
        if fault['fault']=='timeout':
            assert any('model_timeout' in s for s in d['provider_notes'])
        else:
            assert d['provider_output']==fault['model_output'], 'Fault output was not reproduced'
    # Recompute viewer-level ITT from raw events, independent of the agent's helpers.
    cohorts=load(run/'cohorts.jsonl')
    assert len({c['user_id'] for c in cohorts})==len(cohorts),'Duplicate randomized denominator'
    fresh_entries={}
    for e in triggers.values():
        duration=ts(e['sent_at'])-ts(e['occurred_at'])
        age=(ts(e['deliver_at'])-(ts(e['received_at'])-duration)).total_seconds()
        if duration.total_seconds()>=0 and 0<=age<=600:
            user=e['user_id'];at=ts(e['deliver_at'])
            fresh_entries[user]=min(fresh_entries.get(user,at),at)
    assert {c['user_id'] for c in cohorts}==set(fresh_entries)
    by_user=defaultdict(list)
    for e in event_index.values():by_user[e['user_id']].append(e)
    impact=json.loads((run/'impact_report.json').read_text());watermark=ts(impact['observation_end'])
    def wilson_bounds(k,n):
        z=1.95996398454;p=k/n;den=1+z*z/n
        center=(p+z*z/(2*n))/den
        margin=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
        return center-margin,center+margin
    for label,signal,hours in [('primary_completion_72h','episode_completed',72),('secondary_paid_7d','purchase_completed',168)]:
        counts=Counter();successes=Counter()
        for c in cohorts:
            user=c['user_id'];arm=c['arm'];start=ts(c['entry_at']);end=start+timedelta(hours=hours)
            assert arm==arms[user] and start==fresh_entries[user]
            if end>watermark:continue
            counts[arm]+=1
            found=False
            for e in by_user[user]:
                if e['event']!=signal or ts(e['sent_at'])<ts(e['occurred_at']) or ts(e['received_at'])>watermark:continue
                effective=ts(e['received_at'])-(ts(e['sent_at'])-ts(e['occurred_at']))
                if start<effective<=end and (signal!='purchase_completed' or e.get('properties',{}).get('is_trial') is False):found=True
            successes[arm]+=int(found)
        result=impact['replay'][label]
        for arm,prefix in [('agent','treatment'),('holdout','holdout')]:
            assert result[prefix+'_n']==counts[arm]
            assert result[prefix+'_successes']==successes[arm]
        pt=successes['agent']/counts['agent'];pc=successes['holdout']/counts['holdout'];delta=pt-pc
        assert abs(result['risk_difference']-delta)<1e-12
        lt,ut=wilson_bounds(successes['agent'],counts['agent']);lc,uc=wilson_bounds(successes['holdout'],counts['holdout'])
        expected=[delta-math.hypot(pt-lt,uc-pc),delta+math.hypot(ut-pt,pc-lc)]
        assert all(abs(x-y)<1e-12 for x,y in zip(result['ci95'],expected))
    snapshots={p.stem:json.loads(p.read_text()) for p in (run/'viewer_state').glob('*.json')}
    assert set(snapshots)=={d['viewer_id'] for d in decisions},'Viewer snapshots missing or stale'
    judged={o['plan_id']:o for o in outcomes}
    for v in snapshots.values():
        assert v['active_plan_id']==next((p['plan_id'] for p in v['plans'] if p['status']=='open'),None)
        assert sum(p['status']=='open' for p in v['plans'])<=1
        for p in v['plans']:
            assert p['final_outcome']==judged[p['plan_id']]
            if p['final_outcome']['status']!='inconclusive':assert p['status']==p['final_outcome']['status']
    report=json.loads((run/'sim/simulator_report.json').read_text())
    assert report['policy_problem_count']==0
    assert report['counts'].get('home_page_accepted',0)==sum(a['type']=='home_page' for a in actions)
    assert report['counts']['actions_total']==len(actions)
    assert sum(1 for _ in (run/'viewer_state').glob('*.json'))>0
    print(f'PASS: {len(decisions)} decisions, {len(actions)} actions, {len(outcomes)} judgments; all {len(faults)} model faults reproduced; zero policy violations.')


if __name__=='__main__':
    main()
