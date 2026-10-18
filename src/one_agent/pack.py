import json
from collections import defaultdict
from pathlib import Path
from .common import csv_rows, jsonl, ts


class Pack:
    def __init__(self, root):
        self.root = Path(root)
        self.goals = {g['goal_id']: g for g in json.loads((self.root/'config/goals.json').read_text())['goals']}
        self.policy = json.loads((self.root/'config/interventions.json').read_text())
        self.catalogue = {t['title_id']: t for t in csv_rows(self.root/'data/catalogue.csv')}
        self.profiles = defaultdict(list)
        for i, row in enumerate(csv_rows(self.root/'data/profiles_snapshot.csv'), 2):
            row['_ref'] = f'profiles_snapshot.csv:{i}'
            self.profiles[row['user_id']].append(row)
        self.episodes = defaultdict(list)
        for row in csv_rows(self.root/'data/episodes.csv'):
            self.episodes[row['content_slug']].append(row)
        self.arms = {r['user_id']: r['arm'] for r in csv_rows(self.root/'replay/arms.csv')}
        self.faults = {r['trigger_event_id']: r for r in jsonl(self.root/'replay/model_faults.jsonl')}
        self.controls = list(jsonl(self.root/'replay/control_events.jsonl'))
        self.withdrawn = {}
        self.rule_versions = {g: '2026-09-30' for g in self.goals}
        self.history_start = None

    def profile(self, user, now):
        rows = [r for r in self.profiles[user] if ts(r['snapshot_at']) <= now and ts(r['updated_at']) <= now]
        if not rows:
            return None, []
        rows.sort(key=lambda r: (r['updated_at'], r['_ref']), reverse=True)
        latest = [r for r in rows if r['updated_at'] == rows[0]['updated_at']]
        # Equal-time contradictions are unresolved; no arbitrary CSV-row tie breaker.
        fields = ('subscription_status', 'preferred_dialect', 'signup_at', 'notifications_opt_in', 'whatsapp_opt_in')
        if any(len({r[k] for r in latest}) > 1 for k in fields):
            return None, [{'field': 'profile', 'resolution': 'ambiguous_equal_version', 'sources': [r['_ref'] for r in latest]}]
        conflicts = [{'field': k, 'chosen': rows[0][k], 'other': r[k], 'source': r['_ref'],
                      'resolution': 'largest_updated_at_within_received_snapshot'}
                     for r in rows[1:] for k in fields if r[k] != rows[0][k]]
        return rows[0], conflicts

    def available(self, title, at):
        return (ts(title['available_from']) <= at
                and (not title['available_until'] or at < ts(title['available_until']))
                and (title['title_id'] not in self.withdrawn or at < self.withdrawn[title['title_id']]))
