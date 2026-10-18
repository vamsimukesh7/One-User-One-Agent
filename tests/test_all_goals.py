"""Executable definitions for G06/G10 and honest instrumented G11 outcomes."""
import copy
import unittest
from datetime import timedelta
import test_agent as fixtures
from test_agent import event, NOW
from one_agent.common import iso
from one_agent.goals import configure
from one_agent.engine import Engine
from one_agent.store import Store
from one_agent.policy import resolve, candidates
from one_agent.outcomes import judge
from one_agent.rules import corrected


class AllGoals(unittest.TestCase):
    def setUp(self):
        fixtures.AgentTests.setUp(self)
        self.pack.goals.update({gid:{'goal_id':gid,'eligibility':elig,'success':{'event':signal,'condition':condition,'window_hours':hours}}
            for gid,elig,signal,condition,hours in [
                ('G06',{'subscription_status':['lapsed']},'purchase_completed','any paid plan',336),
                ('G10',{},None,'engages meaningfully',168),
                ('G11',{'subscription_status':['active']},'tv_app_open','any',336)]})
        configure(self.pack)
        for tid,title in self.pack.catalogue.items():
            self.pack.episodes[title['content_slug']]=[{'episode_id':tid+'-e1','minutes':'4','season':'1','episode':'1'},
                {'episode_id':tid+'-e2','minutes':'4','season':'1','episode':'2'}]
    tearDown=fixtures.AgentTests.tearDown

    def plan(self,gid):
        rule=self.pack.goals[gid]
        return {'plan_id':'plan-'+gid,'user_id':'test-viewer','goal_id':gid,'status':'open',
            'started_at':iso(NOW),'deadline':iso(NOW+timedelta(hours=rule['success']['window_hours'])),
            'success':copy.deepcopy(rule['success']),'eligibility_rule':copy.deepcopy(rule['eligibility']),
            'rule_version':self.pack.rule_versions[gid],'actions':[]}

    def choices(self,events):
        c=resolve(self.pack,'test-viewer',event(),events,NOW)
        return candidates(c,events,NOW,self.pack,'trigger-1')

    def completion(self,key,days=1,episode='T0-e1',user='test-viewer'):
        return event(key,user=user,kind='episode_completed',at=NOW+timedelta(days=days),
                     properties={'title_id':'T0','episode_id':episode})

    def test_g06_requires_a_documented_recent_lapse(self):
        self.pack.profiles['test-viewer'][0]['subscription_status']='lapsed'
        self.assertNotIn('G06',self.choices([]))
        at_boundary=event('lapse',kind='subscription_lapsed',at=NOW-timedelta(days=30))
        self.assertIn('G06',self.choices([at_boundary]))
        too_old=event('old',kind='subscription_lapsed',at=NOW-timedelta(days=30,seconds=1))
        self.assertNotIn('G06',self.choices([too_old]))

    def test_g06_does_not_reactivate_paid_or_trial_purchase(self):
        p=self.plan('G06')
        trial=event('trial',kind='purchase_completed',at=NOW+timedelta(days=1),properties={'is_trial':True})
        self.assertEqual(judge(p,[trial],self.pack,NOW+timedelta(days=15))['status'],'failed')
        paid=event('paid',kind='purchase_completed',at=NOW+timedelta(days=1),properties={'is_trial':False})
        self.assertEqual(judge(p,[paid,paid],self.pack,NOW+timedelta(days=15))['signal_refs'],['event:paid'])
        self.assertEqual(judge(p,[],self.pack,NOW+timedelta(days=1))['status'],'inconclusive')

    def test_g06_is_selected_and_purchase_replans(self):
        profile=self.pack.profiles['test-viewer'][0]
        profile.update(signup_at=iso(NOW-timedelta(days=100)),subscription_status='lapsed')
        self.engine.observe(event('lapse',kind='subscription_lapsed',at=NOW-timedelta(days=1)))
        d,_=self.engine.decide(event());self.assertEqual(d['goal']['id'],'G06')
        self.engine.observe(event('buy',kind='purchase_completed',at=NOW+timedelta(minutes=1),properties={'is_trial':False}))
        self.assertEqual(self.store.records('outcomes')[0]['status'],'succeeded')
        later,_=self.engine.decide(event('after',at=NOW+timedelta(minutes=2)))
        self.assertNotEqual(later['goal']['id'],'G06')

    def test_g06_configurable_recency_and_rule_correction(self):
        configure(self.pack,lapse_days=7)
        self.pack.profiles['test-viewer'][0]['subscription_status']='lapsed'
        lapse=event('lapse',kind='subscription_lapsed',at=NOW-timedelta(days=8))
        self.assertNotIn('G06',self.choices([lapse]))
        rule=corrected(self.pack.goals['G06'],{'new_eligibility':{'lapse_within_days':14}})
        self.assertNotIn('unsupported_reason',rule)
        self.pack.goals['G06']=rule;self.assertIn('G06',self.choices([lapse]))

    def test_g10_requires_two_distinct_episodes_on_two_ist_days(self):
        p=self.plan('G10');a=self.completion('a');b=self.completion('b',2,'T0-e2')
        self.assertEqual(judge(p,[a,b],self.pack,NOW+timedelta(days=8))['status'],'succeeded')
        self.assertEqual(judge(p,[a,a],self.pack,NOW+timedelta(days=8))['status'],'failed')
        same_day=self.completion('same-day',1,'T0-e2')
        self.assertEqual(judge(p,[a,same_day],self.pack,NOW+timedelta(days=8))['status'],'failed')
        same_episode=self.completion('repeat',2,'T0-e1')
        self.assertEqual(judge(p,[a,same_episode],self.pack,NOW+timedelta(days=8))['status'],'failed')

    def test_g10_rejects_unknown_episodes_foreign_users_and_play_only(self):
        p=self.plan('G10');a=self.completion('a')
        for invalid in [self.completion('unknown',2,'invented'),self.completion('foreign',2,'T0-e2','another'),
                        event('play-only',kind='play_started',at=NOW+timedelta(days=2),properties={'title_id':'T0','episode_id':'T0-e2'})]:
            self.assertEqual(judge(p,[a,invalid],self.pack,NOW+timedelta(days=8))['status'],'failed')

    def test_g10_late_receipt_and_window_boundaries(self):
        p=self.plan('G10');a=self.completion('a')
        late=event('late',kind='episode_completed',at=NOW+timedelta(days=8),occurred_at=iso(NOW+timedelta(days=7)),
                   properties={'title_id':'T0','episode_id':'T0-e2'})
        self.assertEqual(judge(p,[a,late],self.pack,NOW+timedelta(days=9))['status'],'succeeded')
        after=self.completion('outside',8,'T0-e2')
        self.assertEqual(judge(p,[a,after],self.pack,NOW+timedelta(days=9))['status'],'failed')
        self.assertEqual(judge(p,[a],self.pack,NOW+timedelta(days=1))['status'],'inconclusive')

    def test_g10_alternative_definitions(self):
        configure(self.pack,engagement='one_episode')
        self.assertEqual(judge(self.plan('G10'),[self.completion('a')],self.pack,NOW+timedelta(days=8))['status'],'succeeded')
        configure(self.pack,engagement='three_play_days')
        events=[event('p'+str(d),kind='play_started',at=NOW+timedelta(days=d),properties={'title_id':'T0','episode_id':'T0-e1'}) for d in (1,2,3)]
        self.assertEqual(judge(self.plan('G10'),events,self.pack,NOW+timedelta(days=8))['status'],'succeeded')

    def test_g10_selectable_without_inventing_lifetime_history(self):
        self.pack.profiles['test-viewer']=[]
        self.assertIn('G10',self.choices([]))
        d,_=self.engine.decide(event());self.assertEqual(d['goal']['id'],'G10')
        self.assertIn('user-approved',d['goal']['definition_source'].lower())

    def test_g11_requires_declared_instrumentation_for_selection(self):
        self.pack.profiles['test-viewer'][0]['subscription_status']='active'
        self.assertNotIn('G11',self.choices([]))
        configure(self.pack,tv_telemetry_available=True)
        self.assertIn('G11',self.choices([]))

    def test_g11_never_equates_mobile_open_with_tv_success(self):
        p=self.plan('G11');mobile=event('mobile',at=NOW+timedelta(days=1))
        self.assertEqual(judge(p,[mobile],self.pack,NOW+timedelta(days=15))['status'],'inconclusive')
        tv=event('tv',kind='tv_app_open',at=NOW+timedelta(days=1))
        self.assertEqual(judge(p,[tv,tv],self.pack,NOW+timedelta(days=15))['signal_refs'],['event:tv'])

    def test_g11_engine_persists_real_tv_success(self):
        configure(self.pack,tv_telemetry_available=True)
        self.pack.profiles['test-viewer'][0].update(subscription_status='active',signup_at=iso(NOW-timedelta(days=100)))
        self.pack.goals={gid:rule for gid,rule in self.pack.goals.items() if gid=='G11'}
        d,_=self.engine.decide(event())
        self.assertEqual(d['goal']['id'],'G11')
        self.engine.observe(event('real-tv',kind='tv_app_open',at=NOW+timedelta(days=1)))
        outcomes=self.store.records('outcomes')
        self.assertEqual(outcomes[-1]['status'],'succeeded')
        self.assertEqual(outcomes[-1]['signal_refs'],['event:real-tv'])

    def test_g11_complete_instrumented_window_can_fail(self):
        configure(self.pack,tv_telemetry_available=True);p=self.plan('G11')
        self.assertEqual(judge(p,[],self.pack,NOW+timedelta(days=15))['status'],'failed')
        self.assertEqual(judge(p,[],self.pack,NOW+timedelta(days=15),observation_complete=False)['status'],'inconclusive')
        late=event('late-tv',kind='tv_app_open',at=NOW+timedelta(days=15))
        self.assertEqual(judge(p,[late],self.pack,NOW+timedelta(days=16))['status'],'failed')

    def test_configuration_and_versions_survive_restart(self):
        configure(self.pack,lapse_days=14,engagement='one_episode',tv_telemetry_available=True)
        self.engine=Engine(self.pack,self.store);before=copy.deepcopy(self.pack.rule_versions)
        self.store.close();self.store=Store(self.path)
        fresh=fixtures.fixture_pack()
        fresh.goals.update(copy.deepcopy({g:self.pack.goals[g] for g in ('G06','G10','G11')}))
        configure(fresh)
        self.engine=Engine(fresh,self.store)
        self.assertEqual(fresh.goal_configuration['g06_lapse_days'],14)
        self.assertEqual(fresh.goal_configuration['g10_engagement'],'one_episode')
        self.assertTrue(fresh.tv_telemetry_available)
        self.assertEqual(fresh.rule_versions,before)

    def test_invalid_configuration_is_rejected(self):
        for days in (0,-1,366,True):
            with self.assertRaises(ValueError):configure(self.pack,lapse_days=days)
        with self.assertRaises(ValueError):configure(self.pack,engagement='vague engagement')
