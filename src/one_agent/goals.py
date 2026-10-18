"""Versioned operational definitions; never synthesize missing viewer telemetry."""
from datetime import timedelta
from .common import event_time, iso, stable_id

ENGAGEMENT = {
    'two_episodes_two_days': {'event':'episode_completed','min_distinct_episodes':2,'min_distinct_days':2},
    'one_episode': {'event':'episode_completed','min_distinct_episodes':1,'min_distinct_days':1},
    'three_play_days': {'event':'play_started','min_distinct_days':3},
}


def configure(pack, lapse_days=30, engagement='two_episodes_two_days', tv_telemetry_available=False):
    if type(lapse_days) is not int or not 1<=lapse_days<=365:raise ValueError('G06 recency must be 1..365 days')
    if engagement not in ENGAGEMENT:raise ValueError('Unknown G10 engagement definition')
    pack.tv_telemetry_available=bool(tv_telemetry_available)
    pack.goal_configuration={'g06_lapse_days':lapse_days,'g10_engagement':engagement,
        'tv_telemetry_available':bool(tv_telemetry_available),
        'definition_source':'User-approved operational definitions (G06: 30 days; G10: 2 different episodes on 2 IST days); CLI overrides are recorded explicitly; no viewer facts invented'}
    patches={
        'G06':{'eligibility':{'lapse_within_days':lapse_days}},
        'G10':{'success':dict(ENGAGEMENT[engagement],condition=engagement,window_hours=168)},
        'G11':{'success':{'telemetry_available':bool(tv_telemetry_available)}},
    }
    for gid,sections in patches.items():
        if gid not in pack.goals:continue
        if gid=='G10':
            pack.goals[gid]['success'].pop('min_distinct_episodes',None)
            pack.goals[gid]['success'].pop('min_distinct_days',None)
        for section,fields in sections.items():pack.goals[gid][section].update(fields)
        pack.goals[gid]['definition_source']=pack.goal_configuration['definition_source'] if gid!='G11' else 'Supplied TV goal; operator-declared instrumentation capability'
        pack.rule_versions[gid]=stable_id('implementation-v1',gid,str(sections))


def supported(goal_id, rule):
    if not rule or not rule.get('success',{}).get('event'):return False
    if goal_id=='G06':return bool(rule.get('eligibility',{}).get('lapse_within_days'))
    if goal_id=='G10':
        success=rule['success']
        return success.get('event') in {'episode_completed','play_started'} and bool(success.get('min_distinct_days')) and (success['event']=='play_started' or bool(success.get('min_distinct_episodes')))
    return goal_id in {'G01','G02','G03','G04','G05','G07','G08','G09','G11','G12'}


def series_candidates(pack, events, now):
    """Known started multi-episode series, with an uncompleted episode and 3d inactivity."""
    started={};completed={};sources={}
    for e in events:
        when,valid=event_time(e)
        if not valid or when>now:continue
        props=e.get('properties',{});t=pack.catalogue.get(props.get('title_id'))
        if not t or t.get('content_type') not in {'show','micro_drama'}:continue
        slug=t['content_slug'];episode=props.get('episode_id')
        if episode not in {x.get('episode_id') for x in pack.episodes[slug]}:continue
        if e['event'] in {'play_started','episode_completed'}:
            sources.setdefault(slug,[]).append({'ref':'event:'+e['event_id'],'observed_at':e['received_at']})
        if e['event']=='play_started':started[slug]=max(started.get(slug,when),when)
        if e['event']=='episode_completed':completed.setdefault(slug,set()).add(episode)
    result=[]
    for slug,last in started.items():
        episodes=sorted(pack.episodes[slug],key=lambda x:(int(x['season']),int(x['episode'])))
        remaining=[x for x in episodes if x['episode_id'] not in completed.get(slug,set())]
        if len(episodes)>1 and remaining and now-last>=timedelta(days=3):
            result.append({'content_slug':slug,'next_episode_id':remaining[0]['episode_id'],
                           'last_play_at':iso(last),'sources':sources.get(slug,[])})
    return sorted(result,key=lambda x:(x['last_play_at'],x['content_slug']),reverse=True)


def extension_facts(pack, events, now):
    lapses=[{'at':iso(event_time(e)[0]),'source':{'ref':'event:'+e['event_id'],'observed_at':e['received_at']}} for e in events
            if e['event']=='subscription_lapsed' and event_time(e)[1] and event_time(e)[0]<=now]
    return {'lapses':lapses,'unfinished_series':series_candidates(pack,events,now),
            'tv_telemetry_available':pack.goals.get('G11',{}).get('success',{}).get('telemetry_available',False)}
