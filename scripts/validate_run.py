"""Independent end-to-end assertions against raw pack data and exported artifacts."""
import argparse
import csv
import json
from datetime import datetime, timedelta
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
    seen_sessions=set()
    for action in actions:
        d=decision_index[action['decision_id']];trigger=triggers[action['trigger_event_id']]
        assert arms[action['user_id']]=='agent', 'Holdout contamination'
        assert action['type']=='home_page', 'Unexpected external message'
        assert d['action']=='HOME_PAGE' and not d['checks']['failed']
        published=ts(action['published_at']);delivered=ts(trigger['deliver_at'])
        assert 0 <= (published-delivered).total_seconds() <= 60, 'Publication missed SLA'
        assert action['dialect']==trigger['app_dialect']
        session=(action['user_id'],trigger['session_id'])
        assert session not in seen_sessions, 'Duplicate session action';seen_sessions.add(session)
        assert 1<=len(action['rows'])<=6
        ids=[action['hero_title_id']]
        for row in action['rows']:
            assert 3<=len(row['title_ids'])<=10
            ids.extend(row['title_ids'])
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
    for path in [pack/'data/events.jsonl',pack/'replay/live_events.jsonl',pack/'replay/triggers.jsonl',run/'sim/outcome_events.jsonl']:
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
    report=json.loads((run/'sim/simulator_report.json').read_text())
    assert report['policy_problem_count']==0
    assert report['counts']['home_page_accepted']==len(actions)
    assert sum(1 for _ in (run/'viewer_state').glob('*.json'))>0
    print(f'PASS: {len(decisions)} decisions, {len(actions)} actions, {len(outcomes)} judgments; all {len(faults)} model faults reproduced; zero policy violations.')


if __name__=='__main__':
    main()
