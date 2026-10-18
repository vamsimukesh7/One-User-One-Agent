"""Behavioral coverage for model, channel, goal and historical audit extensions."""
import copy
import json
import unittest
from datetime import timedelta
from unittest.mock import patch
import test_agent as fixtures
from test_agent import event, NOW
from one_agent.common import iso
from one_agent.engine import Engine
from one_agent.policy import resolve, candidates
from one_agent.outcomes import judge
from one_agent.audit import inspect


class Extensions(unittest.TestCase):
    setUp=fixtures.AgentTests.setUp
    tearDown=fixtures.AgentTests.tearDown

    def setup_extensions(self):
        self.pack.goals.update({g:{'goal_id':g,'eligibility':{'subscription_status':['lapsed']} if g=='G06' else {},
            'success':{'event':ev,'window_hours':hours}} for g,ev,hours in [
                ('G06','purchase_completed',336),('G07','play_started',72),('G10',None,168),('G11','tv_app_open',336)]})
        self.pack.rule_versions.update({g:"2026-09-30" for g in self.pack.goals})
        self.pack.policy.update({'push':{'consent_field':'notifications_opt_in','copy':{'title_max_chars':40,'body_max_chars':120,'forbidden_phrases':['last chance','hurry','guaranteed']}},
          'whatsapp':{'consent_field':'whatsapp_opt_in','approved_templates':[{'template_id':'wa_recommend_title_v1','language':'hin'}]},
          'in_app_message':{'max_per_day':1}})
        for t in self.pack.catalogue.values():t.update(title='A real title',content_type='show')

    def forced(self,channel):
        self.engine=Engine(self.pack,self.store,channel=channel)

    def test_push_publishes_with_consent(self):
        self.setup_extensions();self.forced('push')
        d,_=self.engine.decide(event());self.assertEqual(d['action'],'PUSH')
        self.engine.finalize_publication(d,NOW+timedelta(milliseconds=10),10)
        a=self.store.published_actions()[0]
        self.assertEqual(a['send_at'],a['published_at']);self.assertFalse(d['checks']['failed'])

    def test_push_requires_consent(self):
        self.setup_extensions();self.forced('push')
        self.pack.profiles['test-viewer'][0]['notifications_opt_in']='false'
        d,_=self.engine.decide(event());self.assertEqual(d['action'],'NO_ACTION')
        self.assertIn('message_consent',d['checks']['failed'])

    def test_optout_before_dispatch_cancels_pending_message(self):
        self.setup_extensions();self.forced('push');d,_=self.engine.decide(event())
        self.engine.observe(event('optout',kind='notification_opt_out',at=NOW+timedelta(seconds=1),properties={'channel':'push'}))
        self.engine.finalize_publication(d,NOW+timedelta(seconds=2),2000)
        self.assertEqual(d['action'],'NO_ACTION');self.assertEqual(self.store.published_actions(),[])

    def test_shared_cap_across_push_and_whatsapp_and_restart(self):
        self.setup_extensions();self.pack.profiles['test-viewer'][0]['whatsapp_opt_in']='true'
        self.forced('push');d,_=self.engine.decide(event());self.engine.finalize_publication(d,NOW+timedelta(milliseconds=5),5)
        self.forced('whatsapp');d,_=self.engine.decide(event('again',at=NOW+timedelta(minutes=1)))
        self.assertEqual(d['action'],'NO_ACTION');self.assertIn('shared_daily_cap',d['checks']['failed'])

    def test_quiet_hours_block_external_messages_but_allow_inapp(self):
        self.setup_extensions();night=NOW.replace(hour=17)
        self.forced('push');d,_=self.engine.decide(event(at=night))
        self.assertIn('quiet_hours',d['checks']['failed'])
        self.forced('in_app_message');d,_=self.engine.decide(event('inside',at=night+timedelta(seconds=1)))
        self.assertEqual(d['action'],'IN_APP_MESSAGE')

    def test_daily_cap_resets_on_next_ist_day(self):
        self.setup_extensions();self.forced('push')
        d,_=self.engine.decide(event());self.engine.finalize_publication(d,NOW+timedelta(milliseconds=5),5)
        d,_=self.engine.decide(event('tomorrow',at=NOW+timedelta(days=1)))
        self.assertEqual(d['action'],'PUSH')

    def test_inapp_cap_does_not_consume_push_cap(self):
        self.setup_extensions();self.forced('in_app_message')
        d,_=self.engine.decide(event());self.engine.finalize_publication(d,NOW+timedelta(milliseconds=5),5)
        d,_=self.engine.decide(event('inside2',at=NOW+timedelta(minutes=1)))
        self.assertIn('in_app_daily_cap',d['checks']['failed'])
        self.forced('push');d,_=self.engine.decide(event('push',at=NOW+timedelta(minutes=2)))
        self.assertEqual(d['action'],'PUSH')

    def test_whatsapp_template_and_variables_are_verified(self):
        self.setup_extensions();self.pack.profiles['test-viewer'][0]['whatsapp_opt_in']='true';self.forced('whatsapp')
        d,_=self.engine.decide(event());a=self.store.records('actions')[0]
        self.assertEqual(d['action'],'WHATSAPP')
        a['variables']['title_name']='invented promotion'
        from one_agent.channels import message_checks
        checks=message_checks(self.pack,self.store,a,resolve(self.pack,'test-viewer',event(),[],NOW),NOW)
        self.assertIn('approved_copy_and_template',checks)

    def test_missing_whatsapp_language_template_abstains(self):
        self.setup_extensions();self.forced('whatsapp')
        self.pack.policy['whatsapp']['approved_templates'][0]['language']='tam'
        d,_=self.engine.decide(event());self.assertEqual(d['no_action_reason'],'no_approved_template_for_language')

    def test_message_holdout_never_publishes(self):
        self.setup_extensions();self.forced('push');self.pack.arms['test-viewer']='holdout'
        d,_=self.engine.decide(event());self.assertEqual(d['action'],'NO_ACTION');self.assertFalse(self.store.records('actions'))

    def test_message_copy_rejects_manipulative_catalogue_title(self):
        self.setup_extensions();self.forced('push')
        for title in self.pack.catalogue.values():title['title']='Hurry guaranteed'
        d,_=self.engine.decide(event());self.assertIn('forbidden_copy',d['checks']['failed'])

    def test_unconfigured_goals_remain_excluded(self):
        self.setup_extensions();self.pack.profiles['test-viewer'][0]['subscription_status']='lapsed'
        e=event('lapse',kind='subscription_lapsed',at=NOW-timedelta(days=2))
        c=resolve(self.pack,'test-viewer',event(),[e],NOW)
        self.assertTrue({'G06','G10','G11'}.isdisjoint(candidates(c,[e],NOW,self.pack,'trigger-1')))

    def series_plan(self):
        self.setup_extensions();p=self.pack.profiles['test-viewer'][0]
        p.update(signup_at=iso(NOW-timedelta(days=10)))
        self.pack.episodes['slug0']=[{'episode_id':'e1','season':'1','episode':'1','minutes':'4'}, {'episode_id':'e2','season':'1','episode':'2','minutes':'4'}]
        self.engine.observe(event('started',kind='play_started',at=NOW-timedelta(days=4),properties={'title_id':'T0','episode_id':'e1'}))
        self.engine.observe(event('completed',kind='episode_completed',at=NOW-timedelta(days=4),properties={'title_id':'T0','episode_id':'e1'}))
        d,_=self.engine.decide(event());self.assertEqual(d['goal']['id'],'G07')
        return self.store.active('test-viewer')

    def test_g07_targets_unfinished_series_and_matching_success(self):
        p=self.series_plan();self.assertEqual(p['next_episode_id'],'e2')
        from one_agent.channels import title_ids
        self.assertEqual(title_ids(self.store.records('actions')[0])[0],'T0')
        other=event('other',kind='play_started',at=NOW+timedelta(hours=1),properties={'title_id':'T1','episode_id':'e2'})
        self.assertEqual(judge(p,[other],self.pack,NOW+timedelta(days=4))['status'],'failed')
        good=event('same',kind='play_started',at=NOW+timedelta(hours=1),properties={'title_id':'T0','episode_id':'e2'})
        self.assertEqual(judge(p,[good],self.pack,NOW+timedelta(days=4))['status'],'succeeded')

    def test_g07_completed_series_is_not_eligible(self):
        self.series_plan()
        events=self.store.events('test-viewer',iso(NOW))
        events.append(event('last',kind='episode_completed',at=NOW-timedelta(days=3),properties={'title_id':'T0','episode_id':'e2'}))
        c=resolve(self.pack,'test-viewer',event(),events,NOW)
        self.assertNotIn('G07',candidates(c,events,NOW,self.pack,'trigger-1'))

    def test_unconfigured_goal_cannot_claim_success_or_failure(self):
        self.setup_extensions()
        for gid in ('G06','G10'):
            p={'goal_id':gid,'plan_id':'unsupported','user_id':'test-viewer','started_at':iso(NOW),
               'deadline':iso(NOW+timedelta(days=14)),'status':'open','rule_version':'2026-09-30','success':self.pack.goals[gid]['success']}
            result=judge(p,[event('tv',kind='tv_app_open',at=NOW+timedelta(days=1))],self.pack,NOW+timedelta(days=15))
            self.assertEqual(result['status'],'inconclusive')

    def test_audit_preserves_pending_published_and_closed_states(self):
        d,_=self.engine.decide(event());self.engine.finalize_publication(d,NOW+timedelta(seconds=1),1000)
        self.engine.observe(event('success',kind='play_started',at=NOW+timedelta(minutes=1)))
        self.store.db.commit()
        self.assertEqual(inspect(self.path,'test-viewer',iso(NOW))['decisions'][0]['status'],'PENDING')
        middle=inspect(self.path,'test-viewer',iso(NOW+timedelta(seconds=2)))
        self.assertEqual(middle['decisions'][0]['status'],'PUBLISHED');self.assertEqual(middle['plans'][0]['status'],'open')
        self.assertEqual(inspect(self.path,'test-viewer',iso(NOW+timedelta(minutes=2)))['plans'][0]['status'],'succeeded')

    def test_audit_excludes_late_deliveries_and_batch_simulation(self):
        self.engine.decide(event())
        e=event('late',kind='play_started',at=NOW-timedelta(hours=1),deliver_at=iso(NOW+timedelta(minutes=5)))
        self.engine.observe(e)
        self.store.ingest(event('batch',at=NOW),observed_at=NOW+timedelta(days=15));self.store.db.commit()
        before=inspect(self.path,'test-viewer',iso(NOW+timedelta(minutes=1)))
        self.assertNotIn('late',[e['event']['event_id'] for e in before['known_events']])
        self.assertNotIn('batch',[e['event']['event_id'] for e in before['known_events']])
        self.assertEqual(before['plans'][0]['status'],'open')

    def test_audit_tracks_corrected_rule_and_cancelled_action(self):
        self.engine.observe(event('played',kind='play_started',at=NOW-timedelta(hours=1)))
        d,_=self.engine.decide(event());self.engine.control({'type':'goal_rule_corrected','at':iso(NOW+timedelta(minutes=1)),
            'goal_id':'G03','new_success':{'event':'play_started'}})
        self.engine.finalize_publication(d,NOW+timedelta(minutes=2),120000)
        before=inspect(self.path,'test-viewer',iso(NOW))
        after=inspect(self.path,'test-viewer',iso(NOW+timedelta(minutes=3)))
        self.assertEqual(before['plans'][0]['success']['event'],'app_open')
        self.assertEqual(after['plans'][0]['success']['event'],'play_started')
        self.assertEqual(after['actions'],[])
