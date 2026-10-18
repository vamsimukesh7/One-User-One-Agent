import copy
import json
import time
import sqlite3
from datetime import timedelta
from .common import iso, stable_id, ts, fresh_trigger, event_time
from .outcomes import matching_events
from .policy import resolve, candidates, select_content, eligible, invalidation
from .provider import ReplayProvider, ProviderResult, store_delay
from .verifier import Verifier
from .runtime import bounded_propose, validate_decision


class Engine:
    def __init__(self, pack, store, provider=None, max_model_calls=8, max_tool_calls=20, deadline_ms=60000):
        if not 0 <= max_model_calls <= 8 or not 0 <= max_tool_calls <= 20:
            raise ValueError('Budgets must be within 0..8 model calls and 0..20 tool calls')
        self.pack, self.store = pack, store
        self.provider = provider or ReplayProvider(pack.faults, pack.controls)
        self.limits = {'max_model_calls':max_model_calls,'max_tool_calls':max_tool_calls,'deadline_ms':deadline_ms}
        self.verifier = Verifier(pack,store)
        saved = store.db.execute("SELECT value FROM metadata WHERE key='runtime_rules'").fetchone()
        if saved:
            runtime = json.loads(saved[0])
            pack.goals = runtime['goals']
            pack.rule_versions = runtime['rule_versions']
            pack.withdrawn = {k:ts(v) for k,v in runtime['withdrawn'].items()}

        self.recover_pending()

    def recover_pending(self):
        rows = self.store.db.execute("SELECT body FROM decisions").fetchall()
        for row in rows:
            decision = json.loads(row[0])
            if decision.get('status') == 'PENDING':
                self.finalize_publication(decision,ts(decision['published_at']),decision['decided_in_ms'],
                                          [{'type':'restart_pending_action_cancelled'}])

    def _abstain(self, trigger, now, reason, started, queue_ms=0, wait_ms=0):
        effective, valid = event_time(trigger)
        elapsed = (time.perf_counter()-started)*1000 + queue_ms + wait_ms
        decision = {'decision_id':stable_id('D',trigger['event_id']),'viewer_id':trigger['user_id'],
            'trigger':{'event_id':trigger['event_id'],'event':trigger['event'],
                'received_at':trigger['received_at'],'deliver_at':trigger.get('deliver_at',trigger['received_at']),
                'event_age_s':(now-effective).total_seconds(),'clock_estimate_valid':valid},
            'goal':None,'action':'NO_ACTION','no_action_reason':reason,
            'dialect':{'value':trigger.get('app_dialect'),'source':'event:'+trigger['event_id'],
                       'observed_at':trigger['received_at'],'conflicts':[]},
            'content':[],'confidence':0.0,'confidence_reason':'Insufficient verified evidence: '+reason,
            'evidence':[{'ref':'event:'+trigger['event_id'],'observed_at':trigger['received_at'],
                         'freshness_s':max(0,(now-ts(trigger['received_at'])).total_seconds())}],
            'assumptions':[],'conflicts':[],'facts':{},'provider_notes':[],
            'checks':{'passed':[],'failed':[reason]},'calls':dict(self.limits,model_calls=0,tool_calls=0),
            'decided_in_ms':elapsed,'simulated_dependency_wait_ms':wait_ms,'queue_delay_ms':queue_ms,
            'published_at':iso(now+timedelta(milliseconds=elapsed-queue_ms)),
            'status':'DEADLINE_MISSED' if elapsed>self.limits['deadline_ms'] else 'ABSTAINED'}
        validate_decision(decision)
        try:
            with self.store.db:
                self.store.db.execute('INSERT OR IGNORE INTO decisions VALUES (?,?,?)',
                    (trigger['event_id'],trigger['user_id'],json.dumps(decision)))
        except sqlite3.Error:
            decision['persistence']='independent_failure_journal'
            self.store.emergency(decision)
        return decision, False

    def decide(self, trigger, now=None, queue_delay_ms=0):
        started=time.perf_counter()
        now=now or ts(trigger.get('deliver_at',trigger['received_at']))
        try:
            result=self._decide(trigger,now,queue_delay_ms)
            validate_decision(result[0])
            return result
        except (sqlite3.Error, OSError) as exc:
            self.store.db.rollback()
            return self._abstain(trigger,now,'data_dependency_failure:'+type(exc).__name__,started,queue_delay_ms)

    def observe(self, event):
        inserted = self.store.ingest(event)
        if not inserted:
            return
        if event['event'] in {'play_started','episode_completed','purchase_completed','subscription_renewed'}:
            events=self.store.events(event['user_id'],event['received_at'])
            for row in self.store.db.execute("SELECT body FROM plans WHERE user_id=? AND goal_id IN ('G01','G02','G05')",(event['user_id'],)).fetchall():
                old=json.loads(row[0]);reason=invalidation(old,events)
                if reason and not old.get('invalidated'):
                    old.update(status='inconclusive',invalidated=True,closed_at=event['received_at'],closed_reason=reason)
                    self.store.save_plan(old)
                    self.store.log({'type':'goal_invalidated','plan_id':old['plan_id'],'at':event['received_at'],
                                    'reason':reason,'evidence':['event:'+event['event_id']]})
            self.store.sync_viewer(event['user_id'],event['received_at'])
        plan = self.store.active(event['user_id'])
        if plan:
            events = self.store.events(event['user_id'],event['received_at'])
            signals = matching_events(plan,events,self.pack)
            if signals:
                plan.update(status='succeeded', closed_at=event['received_at'], signal_refs=['event:'+e['event_id'] for e in signals])
                self.store.save_plan(plan)
                self.store.log({'type':'goal_closed','at':event['received_at'],'user_id':event['user_id'],
                                'plan_id':plan['plan_id'],'reason':'new_success_signal','evidence':plan['signal_refs']})
            elif event['event'] in {'purchase_completed','subscription_renewed','subscription_lapsed'}:
                required = self.pack.goals[plan['goal_id']]['eligibility'].get('subscription_status')
                context = resolve(self.pack,event['user_id'],event,events,ts(event['received_at']))
                if required and context['facts']['subscription_status']['value'] not in required:
                    plan.update(status='superseded',closed_at=event['received_at'],closed_reason='billing_changed_eligibility')
                    self.store.save_plan(plan)
                    self.store.log({'type':'plan_revised','at':event['received_at'],'user_id':event['user_id'],
                                    'plan_id':plan['plan_id'],'reason':'billing_changed_eligibility',
                                    'evidence':['event:'+event['event_id']]})

        self.store.sync_viewer(event['user_id'],event['received_at'])

    def control(self, control):
        at = control['at']
        if control['type']=='title_withdrawn':
            self.pack.withdrawn[control['title_id']] = ts(at)
            affected = [a['action_id'] for a in self.store.records('actions')
                        if control['title_id'] in [a.get('hero_title_id')]+[t for r in a.get('rows',[]) for t in r['title_ids']]
                        and ts(a['published_at'])+timedelta(minutes=120) > ts(at)]
            self.store.log({'type':'catalogue_withdrawal','at':at,'title_id':control['title_id'],
                            'affected_action_ids':affected,'resolution':'block future promotion; platform catalogue validation needed for already-live pages'})
        elif control['type']=='goal_rule_corrected':
            gid = control['goal_id']
            old = copy.deepcopy(self.pack.goals[gid]['success'])
            self.pack.goals[gid]['success'].update(control['new_success'])
            self.pack.rule_versions[gid] = 'correction:'+at
            rows = self.store.db.execute("SELECT body FROM plans WHERE goal_id=? AND status='open'",(gid,)).fetchall()
            affected = []
            for row in rows:
                plan = json.loads(row[0])
                # Do not rewrite closed goals or unrelated viewers. Preserve deadline.
                if ts(plan['deadline']) < ts(at):
                    continue
                plan['success'] = copy.deepcopy(self.pack.goals[gid]['success'])
                plan['rule_version'] = self.pack.rule_versions[gid]
                plan.setdefault('rule_history',[]).append({'at':at,'old_success':old,'new_success':plan['success']})
                self.store.save_plan(plan); affected.append(plan['plan_id'])
            self.store.log({'type':'rule_correction','at':at,'goal_id':gid,'affected_plan_ids':affected,
                            'old_success':old,'new_success':self.pack.goals[gid]['success']})
        else:
            self.store.log({'type':'dependency_control','control':control})
        self.store.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('runtime_rules', json.dumps({
            'goals':self.pack.goals,'rule_versions':self.pack.rule_versions,
            'withdrawn':{k:iso(v) for k,v in self.pack.withdrawn.items()}})))
        self.store.db.commit()

    def finalize_publication(self, decision, dispatch_at, elapsed_ms, intervening_updates=()):
        """Finalize the local outbox at dispatch time; fail closed on evidence races.

        A late billing/consent/catalogue/rule event during a dependency wait means
        the snapshot is no longer safe to publish. The next trigger can replan.
        """
        reason = None
        if elapsed_ms > self.limits['deadline_ms']:
            reason = 'publication_deadline_exceeded'
        if intervening_updates:
            reason = 'evidence_changed_during_dependency_wait'
        aid = stable_id('A',decision['trigger']['event_id'])
        row = self.store.db.execute('SELECT body FROM actions WHERE action_id=?',(aid,)).fetchone()
        with self.store.db:
            if row:
                action = json.loads(row[0])
                ids = [action['hero_title_id']]+[t for r in action['rows'] for t in r['title_ids']]
                if not all(self.pack.available(self.pack.catalogue[t],dispatch_at) for t in ids):
                    reason = 'availability_changed_before_dispatch'
                if reason:
                    self.store.db.execute('DELETE FROM actions WHERE action_id=?',(aid,))
                    plan_row=self.store.db.execute('SELECT body FROM plans WHERE plan_id=?',
                        (decision['goal']['plan_id'],)).fetchone()
                    plan=json.loads(plan_row[0]);plan['actions'].remove(aid);self.store.save_plan(plan)
                    decision.update(action='NO_ACTION',content=[],no_action_reason=reason,status='ABSTAINED')
                    decision['checks']['failed'].append(reason)
                else:
                    action['published_at']=iso(dispatch_at)
                    self.store.db.execute("UPDATE actions SET published_at=?,body=?,dispatch_state='PUBLISHED' WHERE action_id=?",
                        (action['published_at'],json.dumps(action),aid))
            if decision['action']=='HOME_PAGE':
                decision['status']='PUBLISHED'
            if elapsed_ms>self.limits['deadline_ms']:
                decision['status']='DEADLINE_MISSED'
            decision['published_at']=iso(dispatch_at)
            decision['decided_in_ms']=elapsed_ms
            decision['measured_processing_ms']=elapsed_ms-decision.get('queue_delay_ms',0)-decision['simulated_dependency_wait_ms']
            self.store.db.execute('UPDATE decisions SET body=? WHERE event_id=?',
                (json.dumps(decision),decision['trigger']['event_id']))

    def _decide(self, trigger, now=None, queue_delay_ms=0):
        started = time.perf_counter()
        now = now or ts(trigger.get('deliver_at',trigger['received_at']))
        existing = self.store.decision(trigger['event_id'])
        if existing:
            self.store.log({'type':'duplicate_trigger','event_id':trigger['event_id'],'original_decision_id':existing['decision_id'], 'at':iso(now)})
            self.store.db.commit()
            return existing, True
        delivery = ts(trigger.get('deliver_at',trigger['received_at']))
        if fresh_trigger(trigger,delivery):
            cohort = {'user_id':trigger['user_id'],'arm':self.pack.arms.get(trigger['user_id'],'unknown'),
                      'entry_at':iso(delivery),'primary_endpoint':'episode_completed_within_72h',
                      'eligibility':'first fresh trigger at delivery; all randomized arms; independent of serving'}
            self.store.db.execute('INSERT OR IGNORE INTO cohorts VALUES (?,?)',
                                  (trigger['user_id'],json.dumps(cohort)))
        # Journal/idempotency writes are control-plane work, not optional data tools.
        if self.limits['max_tool_calls'] < 5:
            return self._abstain(trigger,now,'insufficient_tool_budget',started,queue_delay_ms)
        delay, failure = store_delay(trigger,self.pack.controls,now)
        if failure:
            return self._abstain(trigger,now,failure+': fail_closed_no_personalization',started,queue_delay_ms,delay)
        # All publishing paths below share a transaction and unique event/session keys.
        with self.store.db:
            self.observe(trigger)
            events = self.store.events(trigger['user_id'],iso(now))
            context = resolve(self.pack,trigger['user_id'],trigger,events,now)
            choices = candidates(context,events,now,self.pack,trigger['event_id'])
            previous_failures = [json.loads(r[0]) for r in self.store.db.execute(
                "SELECT body FROM plans WHERE user_id=? AND status='failed'",(trigger['user_id'],))]
            for failed in previous_failures:
                closed=ts(failed.get('closed_at',failed['deadline']))
                new_evidence=any(e['event'] in {'play_started','episode_completed','purchase_completed','subscription_renewed','search'}
                    and ts(e['received_at'])>closed for e in events)
                if not new_evidence and now<closed+timedelta(hours=24):
                    choices=[g for g in choices if g!=failed['goal_id']]
            did = stable_id('D',trigger['event_id'])
            limits = dict(self.limits,model_calls=0,tool_calls=3)
            reason, notes = None, []
            proposal = None
            named_fault = trigger['event_id'] in self.pack.faults
            useful_advice = fresh_trigger(trigger,now) and bool(choices) and self.pack.arms.get(trigger['user_id'])=='agent'
            if self.limits['max_model_calls'] > 0 and (useful_advice or named_fault):
                limits['model_calls'] += 1
                try:
                    response = bounded_propose(self.provider,trigger,choices,now,
                        min(2000,max(0,(self.limits['deadline_ms']-queue_delay_ms-(time.perf_counter()-started)*1000)/2)))
                except (TimeoutError, ConnectionError, OSError) as exc:
                    response = ProviderResult(None,'provider_transport_failure:'+type(exc).__name__)
                delay += response.simulated_wait_ms
                proposal = response.output
                if response.error:
                    notes.append(response.error+': deterministic evidence-only fallback')
                elif not isinstance(proposal,dict):
                    notes.append('malformed_model_output_rejected')
                elif proposal.get('eligible') is not None or proposal.get('goal_id') not in choices:
                    notes.append('model_claim_untrusted; eligibility recomputed; no billing claim imported')
            else:
                notes.append('model_call_skipped: no actionable uncertainty or budget; deterministic policy')
            if failure:
                reason = failure+': fail_closed_no_personalization'
            if not fresh_trigger(trigger,now):
                reason = 'stale_or_invalid_clock_trigger'
            if limits['tool_calls']+2 > self.limits['max_tool_calls']:
                reason = 'insufficient_tool_budget'
            plan = self.store.active(trigger['user_id'])
            if plan and ts(plan['deadline']) <= now:
                plan.update(status='failed',closed_at=plan['deadline'])
                self.store.save_plan(plan); plan = None
            if plan:
                gid = plan['goal_id']
                # Lifecycle changes revoke subscription-specific plans immediately.
                required = self.pack.goals[gid]['eligibility'].get('subscription_status')
                if required and context['facts']['subscription_status']['value'] not in required:
                    plan.update(status='superseded',closed_at=iso(now),closed_reason='subscription_changed')
                    self.store.save_plan(plan)
                    self.store.log({'type':'plan_revised','user_id':trigger['user_id'],'at':iso(now),'plan_id':plan['plan_id'], 'reason':'subscription_changed'})
                    plan = None
            if not plan and choices and reason is None:
                gid = choices[0]
                # Advisor may choose among independently eligible candidates only.
                if isinstance(proposal,dict) and proposal.get('goal_id') in choices and not proposal.get('eligible'):
                    gid = proposal['goal_id']
                plan = {'plan_id':stable_id('P',trigger['event_id'],gid),'user_id':trigger['user_id'],
                        'goal_id':gid,'status':'open','started_at':iso(now),
                        'deadline':iso(now+timedelta(hours=self.pack.goals[gid]['success']['window_hours'])),
                        'success':copy.deepcopy(self.pack.goals[gid]['success']), 'rule_version':self.pack.rule_versions[gid],
                        'trigger_event_id':trigger['event_id'],'eligibility_evidence':context,'actions':[]}
                self.store.save_plan(plan)
            if not plan and reason is None:
                reason = 'no_supported_measurable_eligible_goal'
            if self.pack.arms.get(trigger['user_id'])!='agent' and reason is None:
                reason = 'randomized_holdout' if self.pack.arms.get(trigger['user_id'])=='holdout' else 'missing_randomized_arm'
            action, checks = None, {'passed':[],'failed':[]}
            if plan and reason is None:
                ids = select_content(self.pack,context,events,plan['goal_id'],now,plan['started_at'])
                if len(ids)<4:
                    reason = 'insufficient_unique_available_titles_for_hero_and_row'
                else:
                    action = {'action_id':stable_id('A',trigger['event_id']),'decision_id':did,
                              'user_id':trigger['user_id'],'trigger_event_id':trigger['event_id'],
                              'type':'home_page','dialect':context['facts']['dialect']['value'],
                              'hero_title_id':ids[0], 'rows':[{'row_type':'new_releases' if plan['goal_id']=='G08' else 'genre', 'title_ids':ids[1:]}]}
            processing_ms = (time.perf_counter()-started)*1000
            elapsed_ms = processing_ms+delay+queue_delay_ms
            publish_at = now+timedelta(milliseconds=processing_ms+delay)
            if action:
                limits['tool_calls'] += 2
                action['published_at'] = iso(publish_at)
                verifying_plan = dict(plan,continued=plan['trigger_event_id']!=trigger['event_id'])
                checks = self.verifier.check(action,verifying_plan,context,events,trigger,publish_at,elapsed_ms,limits)
                if checks['failed']:
                    reason = 'verification_rejected:'+','.join(checks['failed']); action = None
            if action:
                self.store.db.execute("INSERT INTO actions(action_id,user_id,session_id,published_at,body,dispatch_state) VALUES (?,?,?,?,?,'PENDING')",
                                      (action['action_id'],trigger['user_id'],trigger.get('session_id'),action['published_at'],json.dumps(action)))
                plan['actions'].append(action['action_id']); self.store.save_plan(plan)
            effective, valid = event_time(trigger)
            evidence = [{'ref':'event:'+trigger['event_id'],'observed_at':trigger['received_at'],
                         'freshness_s':max(0,(now-ts(trigger['received_at'])).total_seconds())}]
            for f in context['facts'].values():
                for ref in f['source'] if isinstance(f['source'],list) else [f['source']]:
                    evidence.append({'ref':ref,'observed_at':f['observed_at'],'freshness_s':f['freshness_s']})
            observations={e['ref']:e for f in context['facts'].values() for e in f.get('source_observations',[])}
            evidence=[observations.get(e['ref'],e) for e in evidence]
            if plan:
                evidence.append({'ref':'goal_rule:'+plan['goal_id']+':'+plan['rule_version'],'observed_at':plan['rule_version'].removeprefix('correction:') if plan['rule_version'].startswith('correction:') else None,'freshness_s':max(0,(now-ts(plan['rule_version'].removeprefix('correction:'))).total_seconds()) if plan['rule_version'].startswith('correction:') else None})
            if action:
                for tid in [action['hero_title_id']]+[t for r in action['rows'] for t in r['title_ids']]:
                    evidence.append({'ref':'catalogue:'+tid,'observed_at':None,'freshness_s':None,'note':'static pack record; availability rechecked at publish'})
            quality={
                'fresh_trigger':fresh_trigger(trigger,now),
                'supported_goal':plan is not None,
                'known_subscription':context['facts']['subscription_status']['value']!='unknown',
                'resolved_dialect':context['facts']['dialect']['value'] in {'d1','d2','d3','d4','d5','d6'},
                'history_coverage':context['facts']['history_covers_signup']['value']}
            if reason and not checks['failed']:
                checks['failed'].append(reason)
            decision = {'decision_id':did,'viewer_id':trigger['user_id'],
                        'trigger':{'event':trigger['event'],'event_id':trigger['event_id'],'received_at':trigger['received_at'],
                                   'deliver_at':trigger.get('deliver_at',trigger['received_at']), 'event_age_s':(now-effective).total_seconds(),
                                   'clock_estimate_valid':valid},
                        'goal':{'id':plan['goal_id'],'plan_id':plan['plan_id'],'deadline':plan['deadline'],'success':plan['success'], 'rule_version':plan['rule_version']} if plan else None,
                        'action':'HOME_PAGE' if action else 'NO_ACTION','no_action_reason':reason,
                        'dialect':dict(context['facts']['dialect'],conflicts=[c for c in context['conflicts'] if c['field']=='dialect']),
                        'content':[action['hero_title_id']]+action['rows'][0]['title_ids'] if action else [],
                        'confidence':sum(quality.values())/len(quality),
                        'confidence_reason':'Evidence completeness score (not success probability): '+json.dumps(quality,sort_keys=True),
                        'evidence_quality':quality,
                        'evidence':evidence,'assumptions':context['assumptions'],'conflicts':context['conflicts'],
                        'facts':context['facts'],'eligible_candidates':choices,'provider_output':proposal,'provider_notes':notes,
                        'checks':checks,'decided_in_ms':elapsed_ms,'measured_processing_ms':processing_ms,
                        'simulated_dependency_wait_ms':delay,'queue_delay_ms':queue_delay_ms,'calls':limits,
                        'published_at':iso(publish_at),'status':'PENDING' if action else 'ABSTAINED'}
            self.store.db.execute('INSERT INTO decisions VALUES (?,?,?)',(trigger['event_id'],trigger['user_id'],json.dumps(decision)))
            prior_viewer=self.store.db.execute('SELECT body FROM viewers WHERE user_id=?',(trigger['user_id'],)).fetchone()
            lessons=json.loads(prior_viewer[0]).get('learning',[]) if prior_viewer else []
            self.store.db.execute('INSERT OR REPLACE INTO viewers VALUES (?,?)',(trigger['user_id'],json.dumps({
                'user_id':trigger['user_id'],'as_of':iso(now),'context':context,'active_plan_id':plan['plan_id'] if plan and plan['status']=='open' else None,
                'latest_decision_id':did,'learning':lessons+notes})))
        # Transaction/export overhead is measured by the harness separately.
        return decision, False
