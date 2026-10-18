"""Corrections, attribution, durable history and hand-verifiable randomized examples."""
import copy
import json
import unittest
from collections import defaultdict
from datetime import timedelta
import test_agent as fixtures
from test_agent import event, NOW
from one_agent.common import iso
from one_agent.engine import Engine
from one_agent.store import Store
from one_agent.audit import inspect
from one_agent.outcomes import judge
from one_agent.measure import compare, endpoint, wilson


class RuleAndOutcomeTests(unittest.TestCase):
    setUp=fixtures.AgentTests.setUp
    tearDown=fixtures.AgentTests.tearDown

    def start_return(self):
        self.engine.observe(event('past',kind='play_started',at=NOW-timedelta(hours=1)))
        self.engine.decide(event())
        return self.store.active('test-viewer')

    def correction(self, **fields):
        return dict({'control_id':'rule-1','type':'goal_rule_corrected','at':iso(NOW+timedelta(minutes=1)),
            'goal_id':'G03','new_success':{'event':'play_started'}},**fields)

    def test_correction_is_idempotent_across_restart(self):
        p=self.start_return();c=self.correction();self.engine.control(c)
        self.engine.control(c);self.store.close();self.store=Store(self.path);self.engine=Engine(fixtures.fixture_pack(),self.store)
        self.engine.control(c)
        plan=self.store.active('test-viewer')
        self.assertEqual(len(plan['rule_history']),1)
        self.assertEqual(plan['deadline'],p['deadline'])
        self.assertEqual(len([a for a in self.store.records('audit') if a['type']=='rule_correction']),1)

    def test_correction_does_not_mutate_other_goals_or_historical_decisions(self):
        self.start_return()
        self.pack.profiles['other']=copy.deepcopy(self.pack.profiles['test-viewer'])
        self.pack.arms['other']='agent';self.engine.decide(event('other',user='other'))
        old=self.store.active('other');decision=copy.deepcopy(self.store.decision('trigger-1'))
        self.engine.control(self.correction())
        self.assertEqual(self.store.active('other'),old)
        self.assertEqual(self.store.decision('trigger-1'),decision)
        self.assertEqual(self.store.active('test-viewer')['success']['event'],'play_started')

    def test_corrected_eligibility_revokes_only_affected_plan(self):
        self.start_return();self.engine.control(self.correction(new_eligibility={'signed_up_within_days':0}))
        self.assertIsNone(self.store.active('test-viewer'))
        p=self.store.records('plans')[0]
        result=judge(p,[],self.pack,NOW+timedelta(days=3))
        self.assertEqual(result['status'],'inconclusive')
        self.assertEqual(result['reason'],'corrected_rule_revoked_eligibility')
        d,_=self.engine.decide(event('again',at=NOW+timedelta(minutes=2)))
        self.assertNotEqual(d['goal']['id'],'G03')

    def test_pending_judgment_gets_corrected_success(self):
        p=self.start_return();p.update(status='failed',closed_at=p['deadline']);self.store.save_plan(p)
        self.engine.control(self.correction());p=self.store.records('plans')[0]
        self.assertEqual(p['success']['event'],'play_started')
        app=event('return',at=NOW+timedelta(days=1))
        self.assertEqual(judge(p,[app],self.pack,NOW+timedelta(days=3))['status'],'failed')
        play=event('watch',kind='play_started',at=NOW+timedelta(days=1))
        self.assertEqual(judge(p,[play],self.pack,NOW+timedelta(days=3))['status'],'succeeded')

    def test_unknown_correction_fails_closed(self):
        self.start_return();self.engine.control(self.correction(new_success={'event':'invented_conversion'}))
        p=self.store.records('plans')[0]
        self.assertEqual(judge(p,[],self.pack,NOW+timedelta(days=3))['status'],'inconclusive')
        d,_=self.engine.decide(event('new',at=NOW+timedelta(minutes=2)))
        self.assertNotIn('G03',d['eligible_candidates'])

    def test_late_stale_correction_cannot_roll_back_rule(self):
        self.start_return();self.engine.control(self.correction())
        old=self.correction(control_id='old',at=iso(NOW),new_success={'event':'app_open'})
        self.engine.control(old)
        self.assertEqual(self.pack.goals['G03']['success']['event'],'play_started')

    def test_failed_control_rolls_back_memory_and_database(self):
        self.start_return();old=copy.deepcopy(self.pack.goals)
        with self.assertRaises(ValueError):self.engine.control(self.correction(new_success={'window_hours':-1}))
        self.assertEqual(self.pack.goals,old)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM applied_controls').fetchone()[0],0)

    def test_stale_pending_action_cannot_publish_after_rule_change(self):
        self.start_return();d=self.store.decision('trigger-1');self.engine.control(self.correction())
        self.engine.finalize_publication(d,NOW+timedelta(minutes=2),1000)
        self.assertEqual(d['action'],'NO_ACTION');self.assertEqual(self.store.published_actions(),[])

    def test_corrected_deadline_is_measured_from_original_start(self):
        p=self.start_return();self.engine.control(self.correction(new_success={'event':'play_started','window_hours':24}))
        self.assertEqual(self.store.active('test-viewer')['deadline'],iso(NOW+timedelta(days=1)))
        self.assertEqual(self.store.active('test-viewer')['started_at'],p['started_at'])

    def test_goal_success_requires_its_own_signal(self):
        self.engine.observe(event('play-before',kind='play_started',at=NOW-timedelta(hours=1)))
        self.pack.profiles['test-viewer'][0]['signup_at']=iso(NOW-timedelta(days=10))
        d,_=self.engine.decide(event());self.assertEqual(d['goal']['id'],'G02')
        p=self.store.active('test-viewer')
        play=event('only-play',kind='play_started',at=NOW+timedelta(hours=1))
        self.assertEqual(judge(p,[play],self.pack,NOW+timedelta(days=4))['status'],'failed')
        self.assertEqual(judge(p,[],self.pack,NOW+timedelta(hours=1))['status'],'inconclusive')

    def test_late_duplicate_success_is_once_and_persisted(self):
        self.engine.decide(event());p=self.store.active('test-viewer')
        e=event('late-success',kind='play_started',at=NOW+timedelta(days=2),occurred_at=iso(NOW+timedelta(hours=1)))
        result=judge(p,[e,e],self.pack,NOW+timedelta(days=3))
        self.assertEqual(result['status'],'succeeded');self.assertEqual(len(result['signal_refs']),1)
        self.store.save_outcome(result);self.store.close();self.store=Store(self.path)
        self.assertEqual(self.store.records('outcomes'),[result])
        too_late=event('after-deadline',kind='play_started',at=NOW+timedelta(days=2))
        self.assertEqual(judge(p,[too_late],self.pack,NOW+timedelta(days=3))['status'],'failed')

    def test_holdout_arm_cannot_change_across_restart(self):
        self.pack.arms['test-viewer']='holdout';self.engine.decide(event())
        self.store.close();self.store=Store(self.path);self.pack.arms['test-viewer']='agent';self.engine=Engine(self.pack,self.store)
        with self.assertRaises(ValueError):self.engine.decide(event('changed',at=NOW+timedelta(seconds=1)))
        self.assertEqual(self.store.published_actions(),[])

    def test_budget_status_over_five_minutes(self):
        d,_=self.engine.decide(event(),queue_delay_ms=301000)
        self.engine.finalize_publication(d,NOW+timedelta(seconds=301),301000)
        self.assertEqual(d['status'],'DEADLINE_MISSED');self.assertEqual(self.store.published_actions(),[])

    def test_audit_history_rollback_and_reopen(self):
        self.engine.decide(event());self.store.db.commit()
        with self.assertRaises(RuntimeError):
            with self.store.db:
                p=self.store.active('test-viewer');p['status']='failed';self.store.save_plan(p)
                raise RuntimeError('simulated interrupted transaction')
        self.store.close();self.store=Store(self.path)
        self.assertEqual(inspect(self.path,'test-viewer',iso(NOW))['plans'][0]['status'],'open')

    def test_each_supported_goal_uses_its_declared_signal(self):
        for gid,signal,hours in [('G01','play_started',24),('G02','episode_completed',72),('G03','play_started',48),
                ('G04','purchase_completed',168),('G05','purchase_completed',168),('G07','play_started',72),
                ('G08','play_started',168),('G09','play_started',336),('G12','play_started',168)]:
            with self.subTest(goal=gid):
                p={'plan_id':gid,'goal_id':gid,'user_id':'test-viewer','started_at':iso(NOW),'deadline':iso(NOW+timedelta(hours=hours)),
                   'status':'open','rule_version':'fixture','success':{'event':signal},'target_content_slug':'slug0'}
                signals=[event('signal',kind=signal,at=NOW+timedelta(hours=20),properties={'is_trial':False,'title_id':'T0','episode_id':'e1'})]
                if gid=='G09':signals += [event('day'+str(d),kind=signal,at=NOW+timedelta(days=d),properties={'title_id':'T0'}) for d in (2,3)]
                self.assertEqual(judge(p,signals,self.pack,NOW+timedelta(days=15))['status'],'succeeded')
                self.assertEqual(judge(p,[],self.pack,NOW+timedelta(days=15))['status'],'failed')
                self.assertEqual(judge(p,[],self.pack,NOW+timedelta(days=15),observation_complete=False)['status'],'inconclusive')

    def test_dispatch_storage_error_never_exports_pending_action(self):
        from unittest.mock import patch
        import sqlite3
        d,_=self.engine.decide(event())
        with patch.object(self.store,'events',side_effect=sqlite3.OperationalError('unavailable')):
            self.engine.finalize_publication(d,NOW+timedelta(seconds=1),1000)
        self.assertEqual(d['action'],'NO_ACTION');self.assertEqual(self.store.published_actions(),[])
        self.assertTrue(self.store.failure_path.exists())
        self.store.close();self.store=Store(self.path);self.engine=Engine(self.pack,self.store)
        self.assertEqual(self.store.records('actions'),[])

    def test_dispatch_rechecks_newer_dialect(self):
        d,_=self.engine.decide(event())
        self.engine.observe(event('setting',kind='app_dialect_changed',at=NOW+timedelta(seconds=1),app_dialect='d2'))
        self.engine.finalize_publication(d,NOW+timedelta(seconds=2),2000)
        self.assertEqual(d['action'],'NO_ACTION');self.assertEqual(self.store.published_actions(),[])

    def test_dispatch_respects_remaining_tool_budget(self):
        self.engine=Engine(self.pack,self.store,max_tool_calls=5)
        d,_=self.engine.decide(event());self.engine.finalize_publication(d,NOW+timedelta(seconds=1),1000)
        self.assertEqual(d['action'],'NO_ACTION')
        self.assertEqual(d['no_action_reason'],'insufficient_dispatch_tool_budget')
        self.assertEqual(d['calls']['tool_calls'],5)

    def test_slow_verification_cannot_publish_past_deadline(self):
        import time
        from unittest.mock import patch
        self.engine=Engine(self.pack,self.store,deadline_ms=30)
        d,_=self.engine.decide(event())
        original=self.engine.verifier.check
        def delayed(*args,**kwargs):
            time.sleep(.04)
            return original(*args,**kwargs)
        with patch.object(self.engine.verifier,'check',side_effect=delayed):
            self.engine.finalize_publication(d,NOW,0)
        self.assertEqual(d['status'],'DEADLINE_MISSED');self.assertEqual(self.store.published_actions(),[])


class MeasurementExamples(unittest.TestCase):
    def test_hand_calculated_rates_and_interval(self):
        r=compare([True,True,False],[True,False])
        self.assertEqual(r['treatment_n'],3);self.assertEqual(r['holdout_n'],2)
        self.assertAlmostEqual(r['risk_difference'],1/6)
        self.assertLess(r['ci95'][0],0);self.assertGreater(r['ci95'][1],0)
        lo,hi=wilson(5,10)
        self.assertAlmostEqual(lo,.2365930905,places=8);self.assertAlmostEqual(hi,.7634069095,places=8)

    def test_repeated_triggers_do_not_inflate_denominator(self):
        cohorts=[{'user_id':'a','arm':'agent','entry_at':iso(NOW)}, {'user_id':'b','arm':'holdout','entry_at':iso(NOW)}]
        e=event('done',user='a',kind='episode_completed',at=NOW+timedelta(hours=1))
        r=endpoint(cohorts+[cohorts[0]],{'a':[e,e]},'episode_completed',72,NOW+timedelta(days=4))
        self.assertEqual(r['treatment_n'],1);self.assertEqual(r['treatment_successes'],1)
        self.assertEqual(r['holdout_n'],1);self.assertEqual(r['holdout_successes'],0)

    def test_incomplete_and_unmatured_outcomes_are_censored(self):
        cohorts=[{'user_id':'a','arm':'agent','entry_at':iso(NOW),'observation_complete':False},
                 {'user_id':'b','arm':'holdout','entry_at':iso(NOW+timedelta(days=2))}]
        r=endpoint(cohorts,{},'episode_completed',72,NOW+timedelta(days=3))
        self.assertFalse(r['estimable']);self.assertEqual(r['censored'],{'agent':1,'holdout':1})

    def test_foreign_late_and_outside_window_events_do_not_count(self):
        cohorts=[{'user_id':'a','arm':'agent','entry_at':iso(NOW)},{'user_id':'b','arm':'holdout','entry_at':iso(NOW)}]
        events={'a':[event('other',user='b',kind='episode_completed',at=NOW+timedelta(hours=1)),
                     event('outside',user='a',kind='episode_completed',at=NOW+timedelta(days=4))]}
        r=endpoint(cohorts,events,'episode_completed',72,NOW+timedelta(days=5))
        self.assertEqual(r['treatment_successes'],0);self.assertEqual(r['holdout_successes'],0)
