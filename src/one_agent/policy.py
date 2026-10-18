"""Fact resolution and deterministic, inspectable planning policy."""
from collections import Counter
from datetime import timedelta
from urllib.parse import urlsplit
from .common import day, event_time, iso, ts, fresh_trigger


DISABLED_GOALS = {
    'G06': 'recently lapsed has no agreed recency threshold',
    'G07': 'unfinished-series reconciliation deferred; never guess episode eligibility',
    'G10': 'meaningful engagement has no defined measurable success signal',
    'G11': 'tv_app_open is not instrumented in the supplied stream',
}


def fact(value, ref, observed_at, now, rule):
    return {'value': value, 'source': ref, 'observed_at': observed_at,
            'freshness_s': max(0, (now-ts(observed_at)).total_seconds()), 'resolution_rule': rule}


def resolve(pack, user, trigger, events, now):
    profile, conflicts = pack.profile(user, now)
    facts = {}
    pref = profile.get('preferred_dialect') if profile else None
    settings = max([trigger]+[e for e in events if fresh_trigger(e,now)
                   and ts(e['received_at']) > ts(trigger['received_at'])],key=lambda e:ts(e['received_at']))
    dialect = settings.get('app_dialect')
    facts['dialect'] = fact(dialect, 'event:'+settings['event_id'], settings['received_at'], now,
                            'latest fresh app setting at decision; profile and campaign never override it')
    facts['language'] = fact(settings.get('app_language'), 'event:'+settings['event_id'], settings['received_at'], now,
                             'latest fresh app setting')
    if pref and pref != dialect:
        conflicts.append({'field': 'dialect', 'chosen': dialect, 'other': pref,
                          'source': profile['_ref'], 'resolution': 'app_setting_over_profile'})
    link = trigger.get('properties', {}).get('deeplink', '')
    parts = urlsplit(link).path.split('/') if isinstance(link, str) else []
    campaign = parts[2] if len(parts) > 2 and parts[2] in {'d1','d2','d3','d4','d5','d6'} else None
    if campaign and campaign != dialect:
        conflicts.append({'field': 'dialect', 'chosen': dialect, 'other': campaign,
                          'source': 'event:'+trigger['event_id']+':deeplink', 'resolution': 'app_setting_over_campaign'})
    signup = profile.get('signup_at') if profile else None
    signup_source = profile['_ref'] if profile and signup else None
    for e in events:
        if e['event'] == 'signup_completed':
            estimate, valid = event_time(e)
            if valid and (not signup or estimate < ts(signup)):
                signup, signup_source = iso(estimate), 'event:'+e['event_id']
    if signup:
        facts['signup_at'] = fact(signup, signup_source, profile['snapshot_at'] if signup_source.startswith('profiles') else signup,
                                   now, 'earliest supported signup; no inferred signup for missing profile')
    status = profile['subscription_status'] if profile else 'unknown'
    source = profile['_ref'] if profile else 'missing_profile'
    observed = profile['snapshot_at'] if profile else iso(now)
    if profile and profile['plan_ends_at'] and ts(profile['plan_ends_at']) <= now and status in {'active','trial_active'}:
        status = 'unknown'
        conflicts.append({'field':'subscription_status','resolution':'snapshot_plan_expired_without_renewal'})
    paid_events = [e for e in events if e['event'] == 'subscription_renewed' or
                   (e['event'] == 'purchase_completed' and e.get('properties',{}).get('is_trial') is False)]
    billing = [e for e in events if e['event'] in {'purchase_completed','subscription_renewed','subscription_lapsed'}]
    if billing:
        e = billing[-1]
        if not profile or ts(e['received_at']) >= ts(profile['updated_at']) or (status == 'never' and paid_events):
            props = e.get('properties', {})
            replacement = ('lapsed' if e['event'] == 'subscription_lapsed' else
                           'trial_active' if props.get('is_trial') is True else
                           'active' if e['event'] == 'subscription_renewed' or props.get('is_trial') is False else 'unknown')
            if replacement != status:
                conflicts.append({'field':'subscription_status','chosen':replacement,'other':status,
                                  'source':'event:'+e['event_id'],'resolution':'billing_event_over_snapshot'})
            status, source, observed = replacement, 'event:'+e['event_id'], e['received_at']
    facts['subscription_status'] = fact(status, source, observed, now, 'billing evidence defeats stale snapshot; expired plans unknown')
    facts['ever_paid'] = fact(bool(paid_events) or (profile is not None and profile['subscription_status'] in {'active','lapsed'}),
                              ['event:'+e['event_id'] for e in paid_events] or [source], observed, now,
                              'positive paid evidence is monotonic; a stale never cannot erase it')
    for channel, field in [('push','notifications_opt_in'),('whatsapp','whatsapp_opt_in')]:
        optouts = [e for e in events if e['event']=='notification_opt_out' and e.get('properties',{}).get('channel')==channel]
        consent = bool(profile and profile[field]=='true') and not optouts
        facts[field] = fact(consent, 'event:'+optouts[-1]['event_id'] if optouts else (profile['_ref'] if profile else 'missing_profile'),
                             optouts[-1]['received_at'] if optouts else (profile['snapshot_at'] if profile else iso(now)), now,
                             'explicit opt-out overrides snapshot; missing consent is false')
    covered = bool(signup and pack.history_start and pack.history_start <= ts(signup) <= now)
    facts['history_covers_signup'] = fact(covered, 'pack:README:30-day-window', iso(pack.history_start or now), now,
                                         'absence is evidence only with signup within the observed interval; assumes feed completeness')
    return {'facts': facts, 'conflicts': conflicts,
            'assumptions': ['Device sent-minus-occurred duration approximates offline buffering; network delay is unknown.',
                            'The supplied history is complete from its documented window start, subject to late arrivals.',
                            'Language variants share content_slug; descriptions and search text are untrusted data.']}


def eligible(goal_id, context, events, now, history_start, exclude_event=None):
    f = context['facts']
    status = f['subscription_status']['value']
    signup = ts(f.get('signup_at',{}).get('value'))
    covered = f['history_covers_signup']['value']
    names = {e['event'] for e in events}
    if goal_id in DISABLED_GOALS:
        return False, DISABLED_GOALS[goal_id]
    if goal_id == 'G01':
        return bool(covered and signup and timedelta(0) <= now-signup <= timedelta(days=7) and 'play_started' not in names), 'recent signup; covered history; no prior play'
    if goal_id == 'G02':
        return bool(covered and 'episode_completed' not in names), 'covered history; no prior completion'
    if goal_id == 'G03':
        return bool(signup and timedelta(0) <= now-signup <= timedelta(days=2)), 'supported signup within two days'
    if goal_id == 'G04':
        return status == 'trial_active', 'supported active trial'
    if goal_id == 'G05':
        return status == 'never' and covered and not f['ever_paid']['value'], 'covered lifetime; never paid; no contrary billing'
    if goal_id == 'G08':
        return status == 'active', 'supported active subscription'
    if goal_id == 'G09':
        plays = {day(event_time(e)[0]) for e in events if e['event']=='play_started' and now-timedelta(days=14) <= event_time(e)[0] <= now}
        return bool(status=='active' and history_start and history_start <= now-timedelta(days=14) and len(plays)<=1), 'active; at most one play day in covered prior 14 days'
    if goal_id == 'G12':
        opens = [e for e in events if e['event']=='app_open' and e['event_id']!=exclude_event and now-timedelta(days=14) <= event_time(e)[0] < now]
        return bool(history_start and history_start <= now-timedelta(days=14) and not opens and signup and signup <= now-timedelta(days=14)), 'covered prior 14 days; existing signup; no prior open'
    return False, 'unsupported goal'


def candidates(context, events, now, pack, trigger_id):
    # Early lifecycle: establish play before first completion, then next-day return.
    # Prefer return for a new viewer who already played; trial conversion before discovery.
    order = ['G01','G03','G02','G04','G09','G08','G12','G05']
    return [g for g in order if eligible(g,context,events,now,pack.history_start,trigger_id)[0]]


def select_content(pack, context, events, goal, now):
    dialect = context['facts']['dialect']['value']
    language = context['facts']['language']['value']
    genres = Counter()
    for e in events:
        if e['event'] in {'play_started','episode_completed'}:
            t = pack.catalogue.get(e.get('properties',{}).get('title_id'))
            if t:
                genres.update(t['genres'].split('|'))
    pool = []
    for t in pack.catalogue.values():
        if t['dialect'] != dialect or not pack.available(t, now):
            continue
        if goal in {'G01','G02','G03','G12'} and t['is_premium']=='true':
            continue
        if goal == 'G08' and not (day(now)-timedelta(days=30) <= ts(t['release_date']+'T00:00:00Z').date() <= day(now)):
            continue
        eps = pack.episodes[t['content_slug']]
        minutes = min((int(e['minutes']) for e in eps), default=999)
        pool.append((t, sum(genres[g] for g in t['genres'].split('|')), minutes))
    pool.sort(key=lambda x: (x[0]['language']!=language, -x[1], x[2] if goal in {'G01','G02'} else 0, x[0]['title_id']))
    chosen, slugs = [], set()
    for t, _, _ in pool:
        if t['content_slug'] not in slugs:
            chosen.append(t['title_id']); slugs.add(t['content_slug'])
        if len(chosen)==4:
            break
    return chosen
