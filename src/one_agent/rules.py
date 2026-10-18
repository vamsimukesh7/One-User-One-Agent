"""Structured corrections; unsupported rule semantics fail closed."""
import copy

EVENTS={'G01':{'play_started'},'G02':{'episode_completed'},'G03':{'app_open','play_started'},
        'G04':{'purchase_completed'},'G05':{'purchase_completed'},'G07':{'play_started'},
        'G08':{'play_started'},'G09':{'play_started'},'G12':{'play_started'}}


def corrected(old, control):
    new=copy.deepcopy(old)
    for field,key in [('success','new_success'),('eligibility','new_eligibility')]:
        patch=control.get(key,{})
        if not isinstance(patch,dict):raise ValueError('Rule correction must be a structured object')
        new[field].update(patch)
    unsupported=[]
    success=new['success'];eligibility=new['eligibility'];gid=new['goal_id']
    if success.get('event') not in EVENTS.get(gid,set()):unsupported.append('unimplemented success event')
    if type(success.get('window_hours')) not in (int,float) or not 0<success['window_hours']<=24*366:
        raise ValueError('Invalid goal window_hours')
    if set(control.get('new_success',{}))-{'event','condition','window_hours'}:unsupported.append('unimplemented success predicate')
    condition=control.get('new_success',{}).get('condition')
    if condition and condition!=old['success'].get('condition'):
        if not (gid=='G03' and condition in {'on a later IST calendar day within the window','on a later IST calendar day than the goal start'}):
            unsupported.append('unimplemented changed success condition')
    patch=control.get('new_eligibility',{})
    if set(patch)-{'text','subscription_status','signed_up_within_days','requires_no_prior_event'}:unsupported.append('unimplemented eligibility predicate')
    if 'text' in patch and patch['text']!=old['eligibility'].get('text') and len(patch)==1:
        unsupported.append('text-only eligibility change requires structured rule')
    if 'subscription_status' in eligibility and (not isinstance(eligibility['subscription_status'],list) or
        not set(eligibility['subscription_status'])<={'never','active','trial_active','lapsed'}):raise ValueError('Invalid subscription rule')
    if 'signed_up_within_days' in eligibility and (type(eligibility['signed_up_within_days']) not in (int,float) or eligibility['signed_up_within_days']<0):raise ValueError('Invalid signup window')
    if eligibility.get('requires_no_prior_event') not in {None,'play_started','episode_completed','purchase_completed'}:unsupported.append('unknown absence predicate')
    if unsupported:new['unsupported_reason']='; '.join(unsupported)
    else:new.pop('unsupported_reason',None)
    return new
