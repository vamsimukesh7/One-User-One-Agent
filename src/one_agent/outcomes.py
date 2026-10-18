"""Goal attainment and exposure association are separate from causal inference."""
from datetime import timedelta
from .common import day, event_time, ts
from .policy import invalidation
from .goals import UNSUPPORTED


def matching_events(plan, events, pack):
    if plan['goal_id'] in UNSUPPORTED or plan.get('unsupported_rule') or not plan.get('success',{}).get('event'):return []
    start, end = ts(plan['started_at']), ts(plan['deadline'])
    if plan.get('closed_at') and plan['status']=='superseded':
        end = min(end, ts(plan['closed_at']))
    if invalidation(plan, events) or plan.get('invalidated') or plan.get('closed_reason')=='corrected_rule_revoked_eligibility':
        return []
    result = [];seen=set()
    for e in sorted(events,key=lambda e:e['received_at']):
        if e['event_id'] in seen:continue
        seen.add(e['event_id'])
        if e['user_id'] != plan['user_id']:
            continue
        effective, valid = event_time(e)
        if not valid or not start < effective <= end or ts(e['received_at']) < start:
            continue
        if e['event'] != plan['success']['event']:
            continue
        g, props = plan['goal_id'], e.get('properties',{})
        if g in {'G04','G05','G06'} and props.get('is_trial') is not False:
            continue
        if g == 'G03' and day(effective) <= day(start):
            continue
        if g == 'G07':
            title=pack.catalogue.get(props.get('title_id'))
            if not title or title['content_slug']!=plan.get('target_content_slug'):continue
        if g == 'G08':
            title = pack.catalogue.get(props.get('title_id'))
            if not title or not day(start)-timedelta(days=30) <= ts(title['release_date']+'T00:00:00Z').date() <= day(start):
                continue
        result.append(e)
    result.sort(key=lambda e: e['received_at'])
    if plan['goal_id']=='G09' and len({day(event_time(e)[0]) for e in result}) < 3:
        return []
    return result


def judge(plan, events, pack, observation_end, action_ids=(), observation_complete=True):
    events = [e for e in events if ts(e['received_at']) <= observation_end]
    signals = matching_events(plan, events, pack)
    exposed = [e for e in events if e['user_id']==plan['user_id'] and ((e['event']=='home_page_shown'
               and e.get('properties',{}).get('personalised') is True
               and e.get('properties',{}).get('page_action_id') in action_ids) or
               (e['event'] in {'message_clicked','in_app_message_shown'} and e.get('properties',{}).get('action_id') in action_ids))]
    associated = []
    for e in signals:
        if any(x.get('session_id') and x.get('session_id')==e.get('session_id') and ts(x['received_at']) <= event_time(e)[0] for x in exposed):
            associated.append(e['event_id'])
    if plan['goal_id'] in UNSUPPORTED or plan.get('unsupported_rule') or not plan.get('success',{}).get('event'):
        status,reason='inconclusive','unsupported_or_missing_success_signal'
    elif plan.get('closed_reason')=='corrected_rule_revoked_eligibility':
        status,reason='inconclusive','corrected_rule_revoked_eligibility'
    elif invalidation(plan,events) or plan.get('invalidated'):
        status, reason = 'inconclusive', 'initial_eligibility_disproved_by_late_evidence'
    elif signals:
        status, reason = 'succeeded', 'observed_success_signal_in_goal_window'
    elif plan['status']=='superseded':
        status, reason = 'inconclusive', 'goal_superseded_by_changed_evidence'
    elif not observation_complete:
        status,reason='inconclusive','success_observation_incomplete'
    elif observation_end >= ts(plan['deadline']):
        status, reason = 'failed', 'no_success_signal_by_deadline_under_complete_feed_assumption'
    else:
        status, reason = 'inconclusive', 'observation_window_not_mature'
    return {'plan_id':plan['plan_id'],'user_id':plan['user_id'],'goal_id':plan['goal_id'],
            'rule_version':plan['rule_version'],'started_at':plan['started_at'],'deadline':plan['deadline'],
            'status':status,'reason':reason,'success_signal':plan['success'],
            'signal_refs':['event:'+e['event_id'] for e in signals],
            'exposure_refs':['event:'+e['event_id'] for e in exposed],
            'associated_signal_refs':['event:'+i for i in associated],
            'intervention_judgment': 'observed_association_not_individual_causation' if associated else 'inconclusive_no_linked_success',
            'attribution_note':'Temporal same-session exposure is association only. Randomized ITT estimates population effect.'}
