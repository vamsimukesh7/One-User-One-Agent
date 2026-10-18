"""Rejects proposals independently. Model eligibility/confidence never authorizes action."""
from .common import fresh_trigger, ts
from .policy import eligible, prior_events, invalidation


class Verifier:
    def __init__(self, pack, store):
        self.pack, self.store = pack, store

    def check(self, action, plan, context, events, trigger, at, elapsed_ms, limits):
        passed, failed = [], []
        def test(name, condition):
            (passed if condition else failed).append(name)
        test('latency_budget', elapsed_ms <= limits['deadline_ms'])
        test('call_budgets', limits['model_calls'] <= limits['max_model_calls'] and limits['tool_calls'] <= limits['max_tool_calls'])
        test('fresh_trigger', fresh_trigger(trigger, at))
        test('randomized_holdout', self.pack.arms.get(trigger['user_id']) == 'agent')
        test('supported_action_type', action.get('type') == 'home_page')
        # External messages fail closed: consent/quiet-hours/caps/templates cannot be bypassed.
        test('messaging_policy', action.get('type') not in {'push','whatsapp','in_app_message'})
        # Recompute eligibility from facts/events, not proposal. Continuation uses original
        # signup window but current status and event history (elapsed window alone is not revocation).
        eligibility_at = ts(plan['started_at'])
        prior = prior_events(plan, events)
        ok, _ = eligible(plan['goal_id'], context, prior, eligibility_at, self.pack.history_start, plan['trigger_event_id'])
        test('eligibility', ok and not invalidation(plan,events))
        test('rule_version', plan['rule_version'] == self.pack.rule_versions[plan['goal_id']])
        settings = max([trigger]+[e for e in events if fresh_trigger(e,at) and ts(e['received_at']) > ts(trigger['received_at'])],key=lambda e:ts(e['received_at']))
        test('dialect', action.get('dialect') == settings.get('app_dialect') == context['facts']['dialect']['value'] and action.get('dialect') in {'d1','d2','d3','d4','d5','d6'})
        rows = action.get('rows',[])
        policy = self.pack.policy['home_page']['rows']
        test('row_count', 1 <= len(rows) <= policy['max_rows'])
        test('row_size', all(policy['min_titles_per_row'] <= len(r.get('title_ids',[])) <= policy['max_titles_per_row'] for r in rows))
        test('row_type', all(r.get('row_type') in policy['row_types'] for r in rows))
        ids = [action['hero_title_id']] if action.get('hero_title_id') else []
        ids += [i for r in rows for i in r.get('title_ids',[])]
        titles = [self.pack.catalogue.get(i) for i in ids]
        test('catalogue_exists', bool(ids) and all(titles))
        test('content_dialect', all(t and t['dialect']==action.get('dialect') for t in titles))
        test('content_language', all(t and t['language']==context['facts']['language']['value'] for t in titles))
        test('availability_at_publish', all(t and self.pack.available(t,at) for t in titles))
        slugs = [t['content_slug'] for t in titles if t]
        test('unique_content_slug', len(slugs)==len(set(slugs)))
        test('session_idempotency', self.store.db.execute('SELECT 1 FROM actions WHERE user_id=? AND session_id=?',
                                                         (trigger['user_id'],trigger.get('session_id'))).fetchone() is None)
        return {'passed':passed,'failed':failed}
