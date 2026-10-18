"""Versioned operational definitions; never synthesize missing viewer telemetry."""
from datetime import timedelta
from .common import event_time, iso

UNSUPPORTED = {
    'G06':'recently lapsed has no specified recency threshold',
    'G10':'meaningful engagement has no defined success event or measurable condition',
    'G11':'tv_app_open is absent from supplied telemetry',
}


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
    return {'unfinished_series':series_candidates(pack,events,now)}
