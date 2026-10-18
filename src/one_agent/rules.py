"""Structured corrections; unsupported rule semantics fail closed."""
import copy
from .goals import ENGAGEMENT

EVENTS={'G01':{'play_started'},'G02':{'episode_completed'},'G03':{'app_open','play_started'},
        'G06':{'purchase_completed'},'G10':{'episode_completed','play_started'},'G04':{'purchase_completed'},'G05':{'purchase_completed'},'G07':{'play_started'},
        'G11':{'tv_app_open'},'G08':{'play_started'},'G09':{'play_started'},'G12':{'play_started'}}


def corrected(old, control):
    new=copy.deepcopy(old)
    for field,key in [('success','new_success'),('eligibility','new_eligibility')]:
        patch=control.get(key,{})
        if not isinstance(patch,dict):raise ValueError('Rule correction must be a structured object')
        new[field].update(patch)
    if new['goal_id']=='G10' and control.get('new_success',{}).get('condition') in ENGAGEMENT:
        preset=ENGAGEMENT[control['new_success']['condition']]
        new['success'].pop('min_distinct_episodes',None)
        new['success'].update(preset)
    unsupported=[]
    success=new['success'];eligibility=new['eligibility'];gid=new['goal_id']
    if success.get('event') not in EVENTS.get(gid,set()):unsupported.append('unimplemented success event')
    if type(success.get('window_hours')) not in (int,float) or not 0<success['window_hours']<=24*366:
        raise ValueError('Invalid goal window_hours')
    if set(control.get('new_success',{}))-{'event','condition','window_hours','min_distinct_days','min_distinct_episodes','telemetry_available'}:unsupported.append('unimplemented success predicate')
    condition=control.get('new_success',{}).get('condition')
    if condition and condition!=old['success'].get('condition'):
        if not ((gid=='G03' and condition in {'on a later IST calendar day within the window','on a later IST calendar day than the goal start'}) or (gid=='G10' and condition in ENGAGEMENT)):
            unsupported.append('unimplemented changed success condition')
    patch=control.get('new_eligibility',{})
    if set(patch)-{'text','subscription_status','signed_up_within_days','requires_no_prior_event','lapse_within_days'}:unsupported.append('unimplemented eligibility predicate')
    if 'text' in patch and patch['text']!=old['eligibility'].get('text') and len(patch)==1:
        unsupported.append('text-only eligibility change requires structured rule')
    if 'subscription_status' in eligibility and (not isinstance(eligibility['subscription_status'],list) or
        not set(eligibility['subscription_status'])<={'never','active','trial_active','lapsed'}):raise ValueError('Invalid subscription rule')
    if 'signed_up_within_days' in eligibility and (type(eligibility['signed_up_within_days']) not in (int,float) or eligibility['signed_up_within_days']<0):raise ValueError('Invalid signup window')
    if 'lapse_within_days' in eligibility and (type(eligibility['lapse_within_days']) is not int or not 1<=eligibility['lapse_within_days']<=365):raise ValueError('Invalid lapse recency')
    for key in ('min_distinct_days','min_distinct_episodes'):
        if key in success and (type(success[key]) is not int or not 1<=success[key]<=365):raise ValueError('Invalid engagement threshold')
    if 'telemetry_available' in success and type(success['telemetry_available']) is not bool:raise ValueError('Telemetry availability must be boolean')
    if eligibility.get('requires_no_prior_event') not in {None,'play_started','episode_completed','purchase_completed'}:unsupported.append('unknown absence predicate')
    if unsupported:new['unsupported_reason']='; '.join(unsupported)
    else:new.pop('unsupported_reason',None)
    return new
