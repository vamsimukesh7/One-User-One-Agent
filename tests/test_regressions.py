"""Regression assertions from the independent audit, using isolated synthetic fixtures."""
import copy
import json
import sqlite3
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import test_agent as fixtures
from test_agent import fixture_pack, event, NOW
from one_agent.common import iso
from one_agent.engine import Engine
from one_agent.outcomes import judge, matching_events
from one_agent.policy import resolve
from one_agent.provider import ProviderResult
from one_agent.runtime import validate_decision
from one_agent.store import Store


class AuditRegressions(unittest.TestCase):
    setUp = fixtures.AgentTests.setUp
    tearDown = fixtures.AgentTests.tearDown

    def test_late_historical_play_invalidates_goal_and_action(self):
        self.engine.decide(event())
        self.engine.observe(event('old',kind='play_started',at=NOW+timedelta(minutes=1),occurred_at=iso(NOW-timedelta(hours=1))))
        first=self.store.records('plans')[0]
        self.assertEqual(first['status'],'inconclusive')
        self.assertTrue(first['invalidated'])
        d,_=self.engine.decide(event('again',at=NOW+timedelta(minutes=2)))
        self.assertNotEqual(d['goal']['id'],'G01')

    def test_late_evidence_revises_already_succeeded_first_goal(self):
        self.engine.decide(event())
        self.engine.observe(event('play',kind='play_started',at=NOW+timedelta(seconds=30)))
        self.assertEqual(self.store.records('plans')[0]['status'],'succeeded')
        self.engine.observe(event('old',kind='play_started',at=NOW+timedelta(minutes=1),occurred_at=iso(NOW-timedelta(hours=1))))
        p=self.store.records('plans')[0]
        self.assertEqual(p['status'],'inconclusive')
        r=judge(p,self.store.events('test-viewer',iso(NOW+timedelta(minutes=1))),self.pack,NOW+timedelta(days=2))
        self.assertEqual(r['status'],'inconclusive')

    def test_old_purchase_does_not_reactivate_newer_lapse(self):
        self.engine.observe(event('lapse',kind='subscription_lapsed',at=NOW-timedelta(minutes=30)))
        self.engine.observe(event('oldbuy',kind='purchase_completed',at=NOW-timedelta(minutes=1),occurred_at=iso(NOW-timedelta(days=2)),properties={'is_trial':False}))
        d,_=self.engine.decide(event())
        self.assertEqual(d['facts']['subscription_status']['value'],'lapsed')
        self.assertTrue(d['facts']['ever_paid']['value'])

    def test_real_sqlite_reader_failure_records_abstention(self):
        with patch.object(self.store,'events',side_effect=sqlite3.OperationalError('reader unavailable')):
            d,_=self.engine.decide(event())
        self.assertEqual(d['action'],'NO_ACTION')
        self.assertIn('data_dependency_failure',d['no_action_reason'])
        validate_decision(d)
        self.assertIsNotNone(self.store.decision('trigger-1'))
        self.assertFalse(self.store.records('actions'))

    def test_actual_blocking_provider_has_deadline(self):
        class Blocking:
            def propose(self,*args):
                time.sleep(3)
                return ProviderResult({'goal_id':'G01'})
        self.engine=Engine(self.pack,self.store,provider=Blocking(),deadline_ms=150)
        begin=time.perf_counter();d,_=self.engine.decide(event());elapsed=(time.perf_counter()-begin)*1000
        self.assertLess(elapsed,150)
        self.assertTrue(any('model_timeout' in x for x in d['provider_notes']))
        self.assertEqual(d['action'],'HOME_PAGE')

    def test_pending_restart_cancels_and_deduplicates(self):
        d,_=self.engine.decide(event())
        self.assertEqual(d['status'],'PENDING')
        self.assertEqual(self.store.published_actions(),[])
        self.store.close();self.store=Store(self.path);self.engine=Engine(self.pack,self.store)
        d,duplicate=self.engine.decide(event())
        self.assertTrue(duplicate)
        self.assertEqual(d['status'],'ABSTAINED')
        self.assertEqual(self.store.records('actions'),[])

    def test_single_active_plan_conflict_does_not_delete_history(self):
        self.engine.decide(event());original=self.store.active('test-viewer')
        competing=dict(original,plan_id='competing')
        with self.assertRaises(sqlite3.IntegrityError):self.store.save_plan(competing)
        self.assertEqual(self.store.active('test-viewer')['plan_id'],original['plan_id'])

    def test_viewer_projection_closes_with_plan(self):
        self.engine.decide(event())
        self.engine.observe(event('success',kind='play_started',at=NOW+timedelta(seconds=30)))
        self.assertIsNone(self.store.records('viewers')[0]['active_plan_id'])

    def test_schema_all_early_exit_paths(self):
        self.engine=Engine(self.pack,self.store,max_tool_calls=0)
        d,_=self.engine.decide(event());validate_decision(d)
        self.assertIn('dialect',d);self.assertIn('event_age_s',d['trigger'])

    def test_signup_fact_uses_receipt_time(self):
        self.pack.profiles['test-viewer']=[]
        signup=event('signup',kind='signup_completed',at=NOW-timedelta(minutes=1),occurred_at=iso(NOW-timedelta(hours=1)))
        self.engine.observe(signup);d,_=self.engine.decide(event())
        self.assertEqual(d['facts']['signup_at']['observed_at'],signup['received_at'])

    def test_language_mismatch_never_publishes(self):
        for title in self.pack.catalogue.values():title['language']='en'
        d,_=self.engine.decide(event(app_language='hin'))
        self.assertEqual(d['action'],'NO_ACTION')

    def test_wrong_viewer_signal_cannot_succeed_goal(self):
        self.engine.decide(event());p=self.store.active('test-viewer')
        r=judge(p,[event('foreign',user='someone-else',kind='play_started',at=NOW+timedelta(minutes=1))],self.pack,NOW+timedelta(days=2))
        self.assertEqual(r['status'],'failed')

    def test_G08_continuation_never_uses_newer_than_goal_release(self):
        profile=self.pack.profiles['test-viewer'][0];profile['subscription_status']='active';profile['signup_at']=iso(NOW-timedelta(days=100))
        for i in [1,2]:self.engine.observe(event('past'+str(i),kind='play_started',at=NOW-timedelta(days=i)))
        d,_=self.engine.decide(event());self.assertEqual(d['goal']['id'],'G08')
        tomorrow=NOW+timedelta(days=1)
        for title in self.pack.catalogue.values():title.update(release_date='2026-10-02',available_from=iso(tomorrow))
        d,_=self.engine.decide(event('next',at=tomorrow+timedelta(seconds=1)))
        self.assertEqual(d['action'],'NO_ACTION')

    def test_failed_goal_cooldown_uses_recorded_outcome(self):
        self.engine.decide(event());p=self.store.active('test-viewer')
        p.update(status='failed',closed_at=iso(NOW));self.store.save_plan(p)
        d,_=self.engine.decide(event('retry',at=NOW+timedelta(minutes=1)))
        self.assertNotIn('G01',d['eligible_candidates'])

    def test_provider_not_called_for_stale_trigger(self):
        d,_=self.engine.decide(event(occurred_at=iso(NOW-timedelta(days=2))))
        self.assertEqual(d['calls']['model_calls'],0)

    def test_over_limit_budgets_rejected(self):
        with self.assertRaises(ValueError):Engine(self.pack,self.store,max_model_calls=9)
        with self.assertRaises(ValueError):Engine(self.pack,self.store,max_tool_calls=21)
