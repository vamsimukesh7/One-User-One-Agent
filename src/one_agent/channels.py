"""Deterministic channel policy and independent message constraints."""
import json
from .common import day, IST, ts

CHANNELS={'home_page','push','whatsapp','in_app_message'}


def title_ids(action):
    if action['type']=='home_page':
        return [action.get('hero_title_id')]+[t for r in action.get('rows',[]) for t in r.get('title_ids',[])]
    return [action.get('title_id')]


def quiet(at):
    hour=at.astimezone(IST).hour
    return hour>=22 or hour<8


def daily_count(store,user,at,channels,exclude=None):
    return sum(1 for row in store.db.execute('SELECT body FROM actions WHERE user_id=?',(user,))
               for a in [json.loads(row[0])]
               if a['action_id']!=exclude and a['type'] in channels
               and day(ts(a.get('send_at') or a['published_at']))==day(at))


def copy_for(pack,channel,title):
    # Catalogue text is a value, never an instruction. No price/deadline claims.
    name=title.get('title',title['title_id'])
    if channel=='whatsapp':
        template=next((t for t in pack.policy.get('whatsapp',{}).get('approved_templates',[])
                       if t['template_id']=='wa_recommend_title_v1' and t['language']==title['language']),None)
        if not template:return None
        return {'template_id':template['template_id'],
                'variables':{'title_name':name,'dialect_name':title['dialect']}}
    return {'text':{'title':'Aapke liye','body':name[:120]},'deeplink':'ott://title/'+title['title_id']}


def choose(pack,store,context,plan,at,preferred=None):
    if preferred:return preferred
    goal=plan['goal_id'];f=context['facts']
    if goal in {'G04','G05','G06','G07'} and not quiet(at) and not daily_count(store,plan['user_id'],at,{'push','whatsapp'}):
        order=['whatsapp','push'] if goal=='G07' else ['push','whatsapp']
        for c in order:
            field={'push':'notifications_opt_in','whatsapp':'whatsapp_opt_in'}[c]
            if c in pack.policy and f[field]['value']:return c
    if goal in {'G04','G05','G07'} and 'in_app_message' in pack.policy and not daily_count(store,plan['user_id'],at,{'in_app_message'}):
        return 'in_app_message'
    return 'home_page'


def message_checks(pack,store,action,context,at):
    c=action['type'];failed=[];f=context['facts']
    if c not in {'push','whatsapp','in_app_message'}:return ['supported_message_channel']
    if c not in pack.policy:return ['configured_message_channel']
    if c in {'push','whatsapp'}:
        if not f[pack.policy[c]['consent_field']]['value']:failed.append('message_consent')
        if quiet(at):failed.append('quiet_hours')
        if daily_count(store,action['user_id'],at,{'push','whatsapp'},action['action_id']):failed.append('shared_daily_cap')
    elif daily_count(store,action['user_id'],at,{'in_app_message'},action['action_id']):failed.append('in_app_daily_cap')
    title=pack.catalogue.get(action.get('title_id'))
    if not title or not pack.available(title,at):failed.append('message_title_available')
    if title:
        if title['dialect']!=f['dialect']['value'] or title['language']!=f['language']['value']:failed.append('message_locale')
        expected=copy_for(pack,c,title)
        if expected is None or any(action.get(k)!=v for k,v in (expected or {}).items()):failed.append('approved_copy_and_template')
        forbidden=pack.policy.get('push',{}).get('copy',{}).get('forbidden_phrases',[])
        rendered=json.dumps(action.get('text') or action.get('variables') or {},ensure_ascii=False).lower()
        if any(s.lower() in rendered for s in forbidden):failed.append('forbidden_copy')
    if c!='whatsapp':
        text=action.get('text',{});policy=pack.policy.get('push',{}).get('copy',{})
        if len(text.get('title',''))>policy.get('title_max_chars',40) or len(text.get('body',''))>policy.get('body_max_chars',120):failed.append('copy_length')
    return failed
